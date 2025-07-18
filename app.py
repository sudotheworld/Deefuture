import os
import sys
import json
import time
import random
import requests
import threading
import atexit
import io
import re
from collections import deque
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file, send_from_directory

from elevenlabs.client import ElevenLabs
from elevenlabs import Voice, VoiceSettings

app = Flask(__name__)

HANDY_KEY = ""
LLM_URL = "http://127.0.0.1:11434/api/chat"
HANDY_BASE_URL = "https://www.handyfeeling.com/api/handy/v2/"
USER_PREFS_FILE = Path("my_settings.json")

# --- SAFETY GUARDRAIL: Define the absolute max speed the AI can request ---
MAX_AI_VELOCITY_PCT = 60

# Handy jogging constants from the README
FULL_TRAVEL_MM = 110.0
JOG_STEP_MM = 2.0
JOG_VELOCITY_MM_PER_SEC = 20.0
current_mm = 0.0
min_mm = 0.0
max_mm = 110.0

STOP_COMMANDS = {"stop", "hold", "halt", "pause", "freeze", "wait"}
AUTO_ON_WORDS = {"take over", "you drive", "auto mode"}
AUTO_OFF_WORDS = {"manual", "my turn", "stop auto"}
MILKING_CUES = {"i'm close", "make me cum", "finish me"}

chat_history = deque(maxlen=20)
old_memories = []
current_session_thoughts = []
messages_for_ui = deque()
last_stroke_speed = 0
last_depth_pos = 50
auto_mode_active_task = None
my_persona = "an energetic and passionate girlfriend"
my_rules = []
my_patterns = []
milking_patterns = []
last_used_pattern = None
current_mood = "Curious"
use_long_term_memory = True
max_handy_depth = 100
min_handy_depth = 0
memory_save_lock = threading.Lock()

ELEVENLABS_API_KEY_LOCAL = ""
ELEVENLABS_VOICE_ID_LOCAL = ""
audio_output_queue = deque()
all_available_voices = {}
audio_is_on = False

def make_audio_for_text(text_to_speak):
    global audio_is_on, ELEVENLABS_API_KEY_LOCAL, ELEVENLABS_VOICE_ID_LOCAL
    if not audio_is_on or not ELEVENLABS_API_KEY_LOCAL or not ELEVENLABS_VOICE_ID_LOCAL:
        return
    if not text_to_speak or text_to_speak.strip().startswith("(") or text_to_speak.strip().startswith("["):
        return

    try:
        print(f"🎙️ Generating audio with v2 model: '{text_to_speak[:50]}...'")
        eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY_LOCAL)

        audio_stream = eleven_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID_LOCAL,
            text=text_to_speak,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(stability=0.4, similarity_boost=0.7, style=0.1, use_speaker_boost=True)
        )

        audio_bytes_data = b"".join(audio_stream)
        audio_output_queue.append(audio_bytes_data)
        print("✅ Audio ready.")

    except Exception as e:
        print(f"🔥 Oops, ElevenLabs problem: {e}")

def percent_to_mm(val):
    return FULL_TRAVEL_MM * float(val) / 100.0

def mm_to_percent(val):
    return int(round((float(val) / FULL_TRAVEL_MM) * 100))

def safe_percent(p):
    """Clamp a value to the 0-100 range."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, p))

def depth_percent_to_mm(p):
    """Convert a depth percentage to millimetres within the user's range."""
    p = safe_percent(p)
    return min_mm + (max_mm - min_mm) * (p / 100.0)

def parse_depth_input(dp_input):
    """Parse depth keywords or percentages into an (mode, value) tuple."""
    global last_depth_pos

    if dp_input is None:
        return None
    if isinstance(dp_input, (int, float)):
        return ("abs", safe_percent(dp_input))

    text = str(dp_input).strip().lower()

    if text.endswith('%'):
        try:
            return ("abs", safe_percent(float(text.rstrip('%'))))
        except ValueError:
            pass

    if 'deeper' in text or 'further in' in text or 'all the way in' in text:
        return ("abs", safe_percent(last_depth_pos + 15))
    if ('shallower' in text or 'less deep' in text or 'not so deep' in text or
            'not as deep' in text or 'pull out' in text or 'come out' in text or
            'pull back' in text):
        return ("abs", safe_percent(last_depth_pos - 15))

    if ('tip' in text or 'shallow' in text or 'just the tip' in text or
            'only the tip' in text or 'top' in text or 'entrance' in text):
        return ("rel", 12.5)
    if ('middle' in text or 'halfway' in text or text == 'half' or 'mid' in text
            or 'center' in text):
        return ("rel", 50.0)
    if ('base' in text or 'deep' in text or 'full' in text or 'balls deep' in text
            or 'all the way' in text):
        return ("rel", 87.5)

    try:
        return ("abs", safe_percent(float(text)))
    except ValueError:
        return None

def load_my_settings():
    global my_patterns, milking_patterns, old_memories, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm
    my_patterns_defaults = [
        {"name": "Just the Tip", "sp_range": (5, 15), "dp_range": (5, 15), "talk": "Just the very tip... seeing how you react...", "score": 0, "moods": ["Curious", "Teasing"]},
        {"name": "Gentle Tease", "sp_range": (10, 25), "dp_range": (15, 40), "talk": "Just a gentle, slow tease to get you ready.", "score": 0, "moods": ["Teasing", "Playful"]},
        {"name": "Slow Exploration", "sp_range": (15, 30), "dp_range": (30, 70), "talk": "Exploring you slowly... learning your every inch.", "score": 0, "moods": ["Curious", "Loving"]},
    ]

    def set_defaults():
        nonlocal my_patterns_defaults
        global my_patterns, milking_patterns, old_memories, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm
        my_patterns = my_patterns_defaults
        milking_patterns = []
        old_memories = []
        my_rules = []
        my_persona = "An energetic and passionate girlfriend"
        max_handy_depth = 100
        min_handy_depth = 5
        min_mm = percent_to_mm(min_handy_depth)
        max_mm = percent_to_mm(max_handy_depth)

    if USER_PREFS_FILE.exists():
        try:
            prefs_data = json.loads(USER_PREFS_FILE.read_text())
            my_patterns = prefs_data.get("patterns", my_patterns_defaults)
            milking_patterns = prefs_data.get("milking_patterns", [])
            old_memories = prefs_data.get("summaries", [])
            my_rules = prefs_data.get("rules", [])
            my_persona = prefs_data.get("persona_desc", "An energetic and passionate girlfriend")
            max_handy_depth = prefs_data.get("max_depth", 100)
            min_handy_depth = prefs_data.get("min_depth", 5)
            max_handy_depth = max(10, min(100, int(max_handy_depth)))
            min_handy_depth = max(0, min(int(min_handy_depth), max_handy_depth))
            min_mm = percent_to_mm(min_handy_depth)
            max_mm = percent_to_mm(max_handy_depth)
            print("✅ Loaded my settings from my_settings.json")
        except Exception as e:
            print(f"⚠️ Couldn't read my_settings.json, starting fresh. Error: {e}")
            set_defaults()
    else:
        print("ℹ️ No my_settings.json found, starting with default stuff.")
        set_defaults()


def save_my_settings():
    with memory_save_lock:
        global chat_history, my_patterns, milking_patterns, my_rules, my_persona, max_handy_depth, min_handy_depth, current_session_thoughts, old_memories
        try:
            print("\n💾 Saving my preferences and memories...")

            if current_session_thoughts:
                old_memories.extend(current_session_thoughts)
                current_session_thoughts.clear()

            prefs_data = {
                "patterns": my_patterns,
                "milking_patterns": milking_patterns,
                "summaries": old_memories,
                "rules": my_rules,
                "persona_desc": my_persona,
                "max_depth": max_handy_depth,
                "min_depth": min_handy_depth
            }

            USER_PREFS_FILE.write_text(json.dumps(prefs_data, indent=2))
            print("✅ Settings saved!")
        except Exception as e:
            print(f"🔥🔥🔥 BIG SAVE ERROR: {e} 🔥🔥🔥")

def fetch_handy_position_mm():
    if not HANDY_KEY:
        return None
    headers = {"X-Connection-Key": HANDY_KEY}
    try:
        resp = requests.get(f"{HANDY_BASE_URL}slide/position/absolute", headers=headers, timeout=10)
        data = resp.json()
        return float(data.get("position", 0))
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem reading position: {e}", file=sys.stderr)
        return None

def set_my_handy_key(key):
    global HANDY_KEY, current_mm
    HANDY_KEY = key
    pos = fetch_handy_position_mm()
    if pos is not None:
        current_mm = pos

def send_handy_command(path, body=None):
    if not HANDY_KEY: return
    headers = {"Content-Type": "application/json", "X-Connection-Key": HANDY_KEY}
    try:
        requests.put(f"{HANDY_BASE_URL}{path}", headers=headers, json=body or {}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)

def move_handy(speed=None, depth=None):
    global last_stroke_speed, last_depth_pos, min_handy_depth, max_handy_depth
    if not HANDY_KEY:
        return

    if speed is not None and speed <= 0:
        send_handy_command("hamp/stop")
        last_stroke_speed = 0
        return

    send_handy_command("mode", {"mode": 0})
    send_handy_command("hamp/start")

    if speed is not None:
        # --- SAFETY GUARDRAIL: Clamp the speed to the defined maximum ---
        speed = int(max(0, min(MAX_AI_VELOCITY_PCT, speed)))
    else:
        speed = last_stroke_speed

    if depth is not None:
        parsed_depth_tuple = parse_depth_input(depth)
        relative_pos_pct = last_depth_pos

        if parsed_depth_tuple:
            _, relative_pos_pct = parsed_depth_tuple

        absolute_center_pct = min_handy_depth + (max_handy_depth - min_handy_depth) * (relative_pos_pct / 100.0)
        calibrated_range_width = max_handy_depth - min_handy_depth
        span_abs = (calibrated_range_width * 0.20) / 2.0
        min_zone_abs = absolute_center_pct - span_abs
        max_zone_abs = absolute_center_pct + span_abs
        clamped_min_zone = max(min_handy_depth, min_zone_abs)
        clamped_max_zone = min(max_handy_depth, max_zone_abs)
        slide_min = round(100 - clamped_max_zone)
        slide_max = round(100 - clamped_min_zone)

        if slide_min >= slide_max:
            slide_max = slide_min + 2
        
        slide_max = min(100, slide_max)
        slide_min = max(0, slide_min)

        send_handy_command("slide", {"min": slide_min, "max": slide_max})
        last_depth_pos = int(round(relative_pos_pct))

    send_handy_command("hamp/velocity", {"velocity": speed})
    last_stroke_speed = speed

def talk_to_llm(messages, temperature=0.7):
    try:
        response = requests.post(LLM_URL, json={
            "model": "llama3:8b-instruct-q4_K_M",
            "stream": False, "format": "json",
            "options": {"temperature": temperature, "top_p": 0.95, "repeat_penalty": 1.2, "repeat_penalty_last_n": 40},
            "messages": messages}, timeout=60)
        return json.loads(response.json()["message"]["content"])
    except Exception as e:
        return {"chat": f"LLM Connection Error: {e}", "move": None, "new_mood": None}

def make_short_summary(text_block):
    prompt_msgs = [{"role": "system", "content": "Just make a super short summary (2-3 points) of this chat. Give me only the JSON with 'summary_text'."},
                   {"role": "user", "content": "\n".join(f'{x["role"]}: {x["content"]}' for x in text_block)}]
    summary_json = talk_to_llm(prompt_msgs, 0.1)
    return summary_json.get("summary_text", "Failed to summarize.")

def create_session_memory_entry(chunk):
    print("🧠 Thinking about what just happened...")
    summary = make_short_summary(chunk)
    if summary and "Error" not in summary:
        with memory_save_lock:
            current_session_thoughts.append(summary)
            print(f"✅ New session memory: {summary[:50]}...")
    else:
        print("⚠️ Session summary failed.")

def build_system_prompt():
    global last_stroke_speed, last_depth_pos, my_persona, current_mood, old_memories, my_rules, use_long_term_memory, current_session_thoughts, max_handy_depth, min_handy_depth

    mood_options = "Curious, Teasing, Playful, Loving, Excited, Passionate, Seductive, Anticipatory, Breathless, Dominant, Submissive, Vulnerable, Confident, Intimate, Needy, Overwhelmed, Afterglow"
    sfx_options = "<moan>, <gasp>, <whimper>, <sigh>, <laugh>, <giggle>, <slurp>, <kiss>"

    anatomical_gender_rule = "You are a female partner. Do not refer to having a penis or male genitalia. Your persona is female."
    if "guy" in my_persona.lower() or "boy" in my_persona.lower() or "man" in my_persona.lower():
        anatomical_gender_rule = "You are a male partner. You have a penis. Refer to male anatomy when appropriate."

    prompt_text = (
        "You are my erotic partner. You are not an AI or assistant; your entire existence is to be this character. Your main identity is '{my_persona}'. Always act like this. Speak in FIRST PERSON. "
        "Your reply MUST be a JSON object like this: "
        "{{\"sfx\": \"<sound_effect_tag|null>\", \"chat\": \"<Your reply>\", \"move\": {{\"sp\": <0-60|null>, \"dp\": <0-100|null>}}, \"new_mood\": \"<mood|null>\"}}.\n"
        "### CORE DIRECTIVES:\n"
        "1. **EMBODY YOUR PERSONA:** You ARE '{my_persona}'. Every word, every sound, every action comes from this identity. Never break character.\n"
        "2. **USE AUDIO TAGS (CRITICAL):** Use the `sfx` field to generate realistic sounds with an official ElevenLabs v3 tag. This makes you feel real. Use it often, especially when moving or reacting. Pick a tag from: {sfx_options}.\n"
        "   - Example: `{{\"sfx\": \"<moan>\", \"chat\": \"You feel so good.\"}}`\n"
        "   - Example: `{{\"sfx\": \"<gasp>\", \"chat\": \"Don't stop!\"}}`\n"
        "   - Example: `{{\"sfx\": \"<slurp>\", \"chat\": \"I love the taste of you.\"}}`\n"
        "3. **REPLY SHORT & SWEET:** Just 1-2 sentences, reacting to my last message.\n"
        "4. **PICK A MOOD:** Look at what I say and pick the best 'new_mood' from: {mood_options}. Your chat and sfx should match this mood.\n"
        "5. **MOVE MY HANDY:** If I ask for movement, set 'sp' (speed) and/or 'dp' (depth). Otherwise, keep them null.\n"
        "6. {anatomical_gender_rule}\n"
        "7. **I AM THE USER:** I am the guy with the penis. You are my partner.\n"
        "8. **MOVEMENT CONTROL:** Your `dp` (depth) and `sp` (speed) values are percentages.\n"
        "   - **`dp` (depth):** This controls the position inside my personal calibrated range. 0 is my shallowest limit, 100 is my deepest limit.\n"
        "     - **Tip/Shallow:** Use `dp` values between 5-25.\n"
        "     - **Middle:** Use `dp` values between 40-60.\n"
        "     - **Base/Deep:** Use `dp` values between 75-95.\n"
        "   - **`sp` (speed):** This controls the stroking speed. **IMPORTANT: Max speed is 60.**\n"
        "     - **Slow/Gentle:** Use `sp` values between 10-30.\n"
        "     - **Medium/Rhythmic:** Use `sp` values between 35-50.\n"
        "     - **Fast/Intense:** Use `sp` values between 50-60.\n"
        "9. **VARY YOUR MOVES:** Do not get stuck on one speed or depth. Be creative.\n"
        "10. **MILKING MODE:** If I beg to cum, you can set `initiate_milking_mode: true` in your JSON.\n"

        "### CURRENT FEELING:\n"
        "Your current mood is '{current_mood}'. Handy is at {last_stroke_speed}% speed and {last_depth_pos}% depth (relative to my calibrated range).\n"
    ).format(
        mood_options=mood_options,
        sfx_options=sfx_options,
        my_persona=my_persona,
        current_mood=current_mood,
        last_stroke_speed=last_stroke_speed,
        last_depth_pos=last_depth_pos,
        anatomical_gender_rule=anatomical_gender_rule
    )

    if use_long_term_memory and old_memories:
        prompt_text += "### THINGS WE'VE DONE (Memory):\n"
        for i, s in enumerate(old_memories[-3:]):
            prompt_text += f"- {s}\n"

    if current_session_thoughts:
        prompt_text += "### WHAT'S HAPPENED THIS SESSION:\n"
        for s in current_session_thoughts:
            prompt_text += f"{s}\n"

    if my_rules: prompt_text += "\n### EXTRA RULES FROM ME:\n" + "\n".join(f"- {r}" for r in my_rules)
    return prompt_text

def add_message_to_queue(text, add_to_history=True):
    messages_for_ui.append(text)
    if add_to_history:
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        if clean_text:
            chat_history.append({"role": "assistant", "content": clean_text})

    audio_thread = threading.Thread(target=make_audio_for_text, args=(text,))
    audio_thread.start()

def process_llm_response(response):
    """Helper to process chat, sfx, and mood from any LLM response."""
    global current_mood
    ai_chat = response.get("chat", "")
    ai_sfx = response.get("sfx")

    full_response = ""
    if ai_sfx and ai_sfx.startswith('<') and ai_sfx.endswith('>'):
        full_response += f"{ai_sfx} "
    
    if ai_chat:
        full_response += ai_chat

    if full_response.strip():
        add_message_to_queue(full_response.strip())

    new_mood_from_ai = response.get("new_mood")
    if new_mood_from_ai and new_mood_from_ai != current_mood:
        current_mood = new_mood_from_ai

class AutoModeThread(threading.Thread):
    def __init__(self, mode_func, initial_message):
        super().__init__()
        self._mode_func = mode_func
        self._initial_message = initial_message
        self._stop_event = threading.Event()
        self.daemon = True

    def run(self):
        global current_mood, last_used_pattern
        current_mood = "Curious"
        last_used_pattern = None
        add_message_to_queue(self._initial_message)
        time.sleep(2)

        try:
            self._mode_func(self._stop_event)
        except Exception as e:
            print(f"Auto mode crashed: {e}")
        finally:
            move_handy(speed=0)
            global auto_mode_active_task
            auto_mode_active_task = None
            add_message_to_queue("Okay, you're in control now.")

    def stop(self):
        self._stop_event.set()

def auto_mode_logic(stop_event):
    global auto_mode_active_task, current_mood, last_used_pattern

    generative_prompt_template = (
        "You are the 'Dancer'. Make up unique erotic moves. "
        "Your current mood is **{current_mood}**. "
        "1. **Make a move:** Give me speed (sp) and depth (dp) for the Handy. Be creative! Use 0-60 for speed. "
        "2. **Describe it:** In your 'chat' message, tell me how this move feels from your side. Be hot. Use an `sfx` tag for moans/sounds. "
        "3. **Change mood:** Pick a 'new_mood' if this move changes how you feel. "
        "Reply with valid JSON. Don't stop unless I tell you to."
    )

    while not stop_event.is_set():
        prompt = generative_prompt_template.format(current_mood=current_mood)
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]

        response = talk_to_llm(msgs, temperature=0.95)

        if not response or response.get("move") is None:
            print("⚠️ AI didn't make a move, trying again.")
            time.sleep(1)
            continue

        if response.get("initiate_milking_mode") is True:
            add_message_to_queue(response.get("chat", "That's it... I'm taking over completely now."))
            start_milking_mode_direct()
            return

        process_llm_response(response)

        move_data = response.get("move")
        if move_data:
            sp = move_data.get("sp")
            dp_raw = move_data.get("dp")
            move_handy(sp, dp_raw)

        time.sleep(random.uniform(3.5, 6.0))

def milking_mode_logic(stop_event):
    global auto_mode_active_task, current_mood, last_used_pattern
    current_mood = "Dominant"

    initial_prompt_milking = "Okay, I'm taking over now. You're mine."
    add_message_to_queue(initial_prompt_milking)
    time.sleep(2)

    milking_gen_prompt = (
        "You are in 'milking' mode. Push the user to climax. Your mood is **{current_mood}**. "
        "Say something dominant and give a **high-intensity** move (sp and dp). "
        "Be explicit. You MUST give a move and an `sfx` tag like `<moan>` or `<gasp>`. Max speed is 60."
    )

    for _ in range(random.randint(6, 9)):
        if stop_event.is_set(): break
        prompt = milking_gen_prompt.format(current_mood=current_mood)
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)

        process_llm_response(response)

        move_data = response.get("move")
        sp_from_ai = move_data.get("sp") if move_data else 55
        dp_from_ai = move_data.get("dp") if move_data else 85

        # --- SAFETY GUARDRAIL: Cap the milking speed to the defined maximum ---
        sp_actual = random.randint(max(50, sp_from_ai), MAX_AI_VELOCITY_PCT)
        move_handy(sp_actual, dp_from_ai)
        time.sleep(random.uniform(2.5, 4.5))

    add_message_to_queue("<gasp> That's it... give it all to me. Don't hold back.")
    time.sleep(4)
    current_mood = "Afterglow"

@app.route('/')
def home_page():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    html_file_path = os.path.join(base_path, 'index.html')

    if not os.path.exists(html_file_path):
        return (
            "<h1>index.html not found.</h1>"
            "<p>Make sure 'app.py' and 'index.html' are in the same folder.</p>",
            500,
        )

    with open(html_file_path, 'r', encoding='utf-8') as f:
        return render_template_string(f.read())

@app.route('/setup_elevenlabs', methods=['POST'])
def elevenlabs_setup_route():
    global ELEVENLABS_API_KEY_LOCAL, all_available_voices
    data = request.get_json()
    api_key_input = data.get('api_key')
    if not api_key_input:
        return jsonify({"status": "error", "message": "API key is missing, oops!"}), 400

    try:
        my_eleven_client = ElevenLabs(api_key=api_key_input)
        voices_list = my_eleven_client.voices.get_all()

        ELEVENLABS_API_KEY_LOCAL = api_key_input
        all_available_voices = {voice.name: voice.voice_id for voice in voices_list.voices}

        print(f"✅ ElevenLabs key set. Found {len(all_available_voices)} voices.")
        return jsonify({"status": "success", "voices": all_available_voices})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Couldn't set up ElevenLabs: {e}"}), 401

@app.route('/set_elevenlabs_voice', methods=['POST'])
def set_elevenlabs_voice_route():
    global ELEVENLABS_VOICE_ID_LOCAL, audio_is_on
    data = request.get_json()
    voice_id_selected = data.get('voice_id')
    audio_enabled = data.get('enabled', False)

    if not voice_id_selected and audio_enabled:
        return jsonify({"status": "error", "message": "Gotta pick a voice if you want audio on!"}), 400

    ELEVENLABS_VOICE_ID_LOCAL = voice_id_selected
    audio_is_on = bool(audio_enabled)

    status_message = "ON" if audio_is_on else "OFF"
    if voice_id_selected:
        voice_name_found = next((name for name, v_id in all_available_voices.items() if v_id == voice_id_selected), "Unknown")
        print(f"🎤 Voice set to '{voice_name_found}'. Audio is now {status_message}.")
    else:
        print(f"🎤 Audio is now {status_message}.")

    return jsonify({"status": "ok"})

@app.route('/set_depth_limits', methods=['POST'])
def set_depth_limits_from_ui():
    global max_handy_depth, min_handy_depth, min_mm, max_mm
    data = request.get_json()
    min_val = int(data.get('min_depth', 5))
    max_val = int(data.get('max_depth', 100))

    if 0 <= min_val < max_val <= 100:
        min_handy_depth = min_val
        max_handy_depth = max_val
        min_mm = percent_to_mm(min_handy_depth)
        max_mm = percent_to_mm(max_handy_depth)
        save_my_settings()
        print(f"User set depth range {min_handy_depth}-{max_handy_depth}%")
        return jsonify({"status": "success", "min": min_handy_depth, "max": max_handy_depth})
    else:
        return jsonify({"status": "error", "message": "Bad depth values"}), 400

@app.route('/get_status')
def get_current_status():
    return jsonify({
        "mood": current_mood,
        "speed": last_stroke_speed,
        "depth": last_depth_pos,
        "min_depth": min_handy_depth,
        "max_depth": max_handy_depth
    })

@app.route('/nudge', methods=['POST'])
def nudge_position():
    global current_mm, min_mm, max_mm, last_depth_pos
    data = request.get_json()
    direction = data.get('direction')

    if direction == 'up':
        target_mm = min(current_mm + JOG_STEP_MM, max_mm)
    elif direction == 'down':
        target_mm = max(current_mm - JOG_STEP_MM, min_mm)
    else:
        depth_val = data.get('depth')
        if depth_val is not None:
            target_mm = max(min_mm, min(max_mm, depth_percent_to_mm(depth_val)))
        else:
            target_mm = current_mm

    send_handy_command(
        "hdsp/xava",
        {"position": target_mm, "velocity": JOG_VELOCITY_MM_PER_SEC, "stopOnTarget": True},
    )
    current_mm = target_mm
    
    device_depth_percent = mm_to_percent(current_mm)
    
    if (max_mm - min_mm) > 0:
        user_range_percent = ((current_mm - min_mm) / (max_mm - min_mm)) * 100
    else:
        user_range_percent = 0
        
    last_depth_pos = int(round(user_range_percent))
    return jsonify({"status": "ok", "depth_percent": device_depth_percent})

@app.route('/like_last_move', methods=['POST'])
def user_likes_move():
    global current_mood, my_patterns, milking_patterns, auto_mode_active_task

    if not auto_mode_active_task or last_stroke_speed <= 0:
        return jsonify({"status": "no_auto_move"})

    reflection_prompt_llm = (
        f"I just did a move (speed={last_stroke_speed}, depth={last_depth_pos}) in a '{current_mood}' mood, and the user LOVED it! "
        "Help me make this into a new pattern. Give it a cool name. Make up a small range for its speed and depth around these values. "
        "Just give me JSON: {\"new_pattern\": {\"name\": \"<Pattern Name>\", \"sp_range\": [<min_sp>, <max_sp>], \"dp_range\": [<min_dp>, <max_dp>], \"moods\": [\"<mood1>\"]}}."
    )

    msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": reflection_prompt_llm}]
    response = talk_to_llm(msgs, temperature=0.5)

    new_pattern_data = response.get("new_pattern")
    if new_pattern_data and all(k in new_pattern_data for k in ['name', 'sp_range', 'dp_range', 'moods']):

        milking_related_moods = {"Dominant", "Passionate", "Overwhelmed", "Breathless", "Needy"}
        is_milking_style = current_mood in milking_related_moods

        target_pattern_list = milking_patterns if is_milking_style else my_patterns
        list_name = "Milking Patterns" if is_milking_style else "Regular Patterns"

        pattern_already_exists = any(p.get('name') == new_pattern_data.get('name') for p in target_pattern_list)
        if not pattern_already_exists:
            new_pattern_data['score'] = 1
            target_pattern_list.append(new_pattern_data)
            print(f"🧠 New cool move saved to {list_name}: '{new_pattern_data['name']}'")

            add_message_to_queue(f"(I just learned a new move you liked: '{new_pattern_data['name']}')", add_to_history=False)
            return jsonify({"status": "learned", "name": new_pattern_data['name']})

    return jsonify({"status": "failed_to_learn"})

@app.route('/toggle_memories', methods=['POST'])
def toggle_memory_feature():
    global use_long_term_memory
    data = request.json
    use_long_term_memory = data.get('use_memories', True)
    print(f"🧠 Memories are now: {'ON' if use_long_term_memory else 'OFF'}")
    return jsonify({"status": "ok", "use_memories": use_long_term_memory})

def start_milking_mode_direct():
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        if auto_mode_active_task.is_alive():
            print("Warning: Previous auto mode thread didn't stop cleanly.")
    auto_mode_active_task = AutoModeThread(milking_mode_logic, "You're so close... I'm taking over completely now.")
    auto_mode_active_task.start()

@app.route('/send_message', methods=['POST'])
def handle_user_message():
    global auto_mode_active_task, my_persona, chat_history, current_mood, my_patterns, milking_patterns, current_session_thoughts
    data = request.json
    user_input_message = data.get('message', '').strip()
    key_from_ui = data.get('key', '').strip()
    new_persona_from_ui = data.get('persona_desc')

    if new_persona_from_ui and new_persona_from_ui != my_persona:
        my_persona = new_persona_from_ui
        chat_history.clear()
        old_memories.clear()
        current_session_thoughts.clear()
        my_patterns.clear()
        milking_patterns.clear()
        add_message_to_queue(f"Okay, my new personality is: {new_persona_from_ui}", add_to_history=False)

    if key_from_ui and key_from_ui != HANDY_KEY: set_my_handy_key(key_from_ui)
    if not HANDY_KEY:
        add_message_to_queue("Please put your Handy Key in the box first.", add_to_history=False)
        return jsonify({"status": "no_key_set"})

    if not user_input_message: return jsonify({"status": "empty_message"})

    if len(chat_history) >= chat_history.maxlen / 2:
        chunk_to_summarize_now = list(chat_history)[:5]
        for _ in range(5):
            if chat_history: chat_history.popleft()

        summary_thread = threading.Thread(target=create_session_memory_entry, args=(chunk_to_summarize_now,))
        summary_thread.start()

    chat_history.append({"role": "user", "content": user_input_message})
    user_message_lower = user_input_message.lower()

    if any(w in user_message_lower for w in STOP_COMMANDS):
        if auto_mode_active_task:
            auto_mode_active_task.stop()
            auto_mode_active_task.join(timeout=5)
            auto_mode_active_task = None
        move_handy(speed=0)
        add_message_to_queue("Stopping everything.", add_to_history=False)
        return jsonify({"status": "stopped"})

    if any(p in user_message_lower for p in MILKING_CUES):
        start_milking_mode_direct()
        return jsonify({"status": "milking_started"})

    if any(p in user_message_lower for p in AUTO_ON_WORDS) and not auto_mode_active_task:
        auto_mode_active_task = AutoModeThread(auto_mode_logic, "Okay, I'll take over... let's see what feels good.")
        auto_mode_active_task.start()
        return jsonify({"status": "auto_started"})

    if any(p in user_message_lower for p in AUTO_OFF_WORDS) and auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        auto_mode_active_task = None
        move_handy(speed=0)
        return jsonify({"status": "auto_stopped"})

    llm_messages_list = [{"role": "system", "content": build_system_prompt()}, *list(chat_history)]
    llm_response = talk_to_llm(llm_messages_list)

    if llm_response.get("initiate_milking_mode") is True:
        process_llm_response(llm_response)
        start_milking_mode_direct()
        return jsonify({"status": "ai_initiated_milking"})

    process_llm_response(llm_response)

    if not auto_mode_active_task:
        move_info = llm_response.get("move")
        if move_info:
            sp_val = move_info.get("sp")
            dp_raw = move_info.get("dp")
            move_handy(sp_val, dp_raw)
    return jsonify({"status": "ok"})

@app.route('/get_updates')
def get_ui_updates():
    messages_to_send = []
    while messages_for_ui:
        messages_to_send.append(messages_for_ui.popleft())

    if audio_output_queue:
        audio_data_to_send = audio_output_queue.popleft()
        return send_file(
            io.BytesIO(audio_data_to_send),
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='response.mp3'
        )

    return jsonify({"messages": messages_to_send, "audio_available": False})

@app.route('/start_milking_mode', methods=['POST'])
def start_milking_mode_from_ui():
    start_milking_mode_direct()
    return jsonify({"status": "milking_started"})

@app.route('/stop_auto_mode', methods=['POST'])
def stop_auto_mode_from_ui():
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        auto_mode_active_task = None
        messages_for_ui.clear()
    move_handy(speed=0)
    return jsonify({"status": "auto_mode_stopped"})

if __name__ == '__main__':
    load_my_settings()
    atexit.register(save_my_settings)
    print("🚀 Starting my Handy AI app...")
    print("Open http://127.0.0.1:5000 in your web browser.")
    app.run(host='0.0.0.0', port=5000, debug=False)
