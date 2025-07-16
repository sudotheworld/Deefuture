import os
import sys
import json
import time
import random
import requests
import threading
import atexit
import io
from collections import deque
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file

from elevenlabs.client import ElevenLabs
from elevenlabs import Voice, VoiceSettings

HANDY_KEY = ""
LLM_URL = "http://127.0.0.1:11434/api/chat"
HANDY_BASE_URL = "https://www.handyfeeling.com/api/handy/v2/"
USER_PREFS_FILE = Path("my_settings.json")

STOP_COMMANDS = {"stop", "hold", "halt", "pause", "freeze", "wait"}
AUTO_ON_WORDS = {"take over", "you drive", "auto mode"}
AUTO_OFF_WORDS = {"manual", "my turn", "stop auto"}
MILKING_CUES = {"i'm close", "make me cum", "finish me"}

chat_history = deque(maxlen=20)
old_memories = []
current_session_thoughts = []
messages_for_ui = deque()
last_stroke_speed = 0
last_depth_pos = 100
auto_mode_active_task = None
my_persona = "an energetic and passionate girlfriend"
my_rules = []
my_patterns = []
milking_patterns = []
last_used_pattern = None
current_mood = "Curious"
use_long_term_memory = True
max_handy_depth = 100
memory_save_lock = threading.Lock()

ELEVENLABS_API_KEY_LOCAL = ""
ELEVENLABS_VOICE_ID_LOCAL = ""
audio_output_queue = deque()
all_available_voices = {}
audio_is_on = False

app = Flask(__name__)

def make_audio_for_text(text_to_speak):
    global audio_is_on, ELEVENLABS_API_KEY_LOCAL, ELEVENLABS_VOICE_ID_LOCAL
    if not audio_is_on or not ELEVENLABS_API_KEY_LOCAL or not ELEVENLABS_VOICE_ID_LOCAL:
        return
    if text_to_speak.strip().startswith("(") or text_to_speak.strip().startswith("["):
        return

    try:
        print(f"🎙️ Generating audio: '{text_to_speak[:40]}...'")
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

def load_my_settings():
    global my_patterns, milking_patterns, old_memories, my_rules, my_persona, max_handy_depth
    my_patterns_defaults = [
        {"name": "Just the Tip", "sp_range": (5, 15), "dp_range": (5, 15), "talk": "Just the very tip... seeing how you react...", "score": 0, "moods": ["Curious", "Teasing"]},
        {"name": "Gentle Tease", "sp_range": (10, 25), "dp_range": (15, 40), "talk": "Just a gentle, slow tease to get you ready.", "score": 0, "moods": ["Teasing", "Playful"]},
        {"name": "Slow Exploration", "sp_range": (15, 30), "dp_range": (30, 70), "talk": "Exploring you slowly... learning your every inch.", "score": 0, "moods": ["Curious", "Loving"]},
        {"name": "Deep & Deliberate", "sp_range": (20, 40), "dp_range": (80, 100), "talk": "Mmm, so deep and deliberate. I love this feeling.", "score": 0, "moods": ["Loving", "Passionate"]},
        {"name": "Steady Rhythm", "sp_range": (40, 60), "dp_range": (50, 80), "talk": "Building up a steady, delicious rhythm.", "score": 0, "moods": ["Loving", "Excited"]},
        {"name": "Passionate Waves", "sp_range": (55, 75), "dp_range": (60, 90), "talk": "Riding you in passionate waves... can you feel the current?", "score": 0, "moods": ["Excited", "Passionate"]},
        {"name": "The Edge", "sp_range": (65, 85), "dp_range": (75, 95), "talk": "Taking you right to the very edge... hold on for me.", "score": 0, "moods": ["Teasing", "Anticipatory"]},
        {"name": "Frantic Tease", "sp_range": (70, 90), "dp_range": (10, 30), "talk": "So fast! Just a frantic little tease at the tip!", "score": 0, "moods": ["Playful", "Excited"]},
        {"name": "Hard & Fast", "sp_range": (80, 95), "dp_range": (80, 100), "talk": "Yes! Riding you so hard like this!", "score": 0, "moods": ["Excited", "Passionate"]},
        {"name": "Overwhelming Frenzy", "sp_range": (90, 100), "dp_range": (85, 100), "talk": "I can't stop... I need all of you right now!", "score": 0, "moods": ["Passionate", "Breathless"]},
        {"name": "The Deep Hold", "sp_range": (0, 5), "dp_range": (90, 100), "talk": "Shhh... just feel me deep inside you for a moment.", "score": 0, "moods": ["Loving", "Intimate"]},
        {"name": "The Shallow Pause", "sp_range": (0, 5), "dp_range": (10, 25), "talk": "Wait... let me just rest on the tip for a second...", "score": 0, "moods": ["Teasing", "Playful"]},
        {"name": "Afterglow Cuddles", "sp_range": (5, 10), "dp_range": (40, 60), "talk": "Mmm, just stay with me like this for a bit.", "score": 0, "moods": ["Loving", "Afterglow", "Intimate"]}
    ]

    if USER_PREFS_FILE.exists():
        try:
            prefs_data = json.loads(USER_PREFS_FILE.read_text())
            my_patterns = prefs_data.get("patterns", my_patterns_defaults)
            milking_patterns = prefs_data.get("milking_patterns", [])
            old_memories = prefs_data.get("summaries", [])
            my_rules = prefs_data.get("rules", [])
            my_persona = prefs_data.get("persona_desc", "An energetic and passionate girlfriend")
            max_handy_depth = prefs_data.get("max_depth", 100)
            max_handy_depth = max(10, min(100, int(max_handy_depth)))
            print("✅ Loaded my settings from my_settings.json")
        except Exception as e:
            print(f"⚠️ Couldn't read my_settings.json, starting fresh. Error: {e}")
            my_patterns = my_patterns_defaults
            milking_patterns = []
            old_memories = []
            my_rules = []
            max_handy_depth = 100
    else:
        my_patterns = my_patterns_defaults
        milking_patterns = []
        max_handy_depth = 100
        print("ℹ️ No my_settings.json found, starting with default stuff.")

def save_my_settings():
    global chat_history, my_patterns, milking_patterns, my_rules, my_persona, max_handy_depth, current_session_thoughts
    try:
        print("\n💾 Saving my preferences and memories...")

        if current_session_thoughts:
            old_memories.append("\n".join(current_session_thoughts))
            current_session_thoughts.clear()

        prefs_data = {
            "patterns": my_patterns,
            "milking_patterns": milking_patterns,
            "summaries": old_memories,
            "rules": my_rules,
            "persona_desc": my_persona,
            "max_depth": max_handy_depth
        }

        USER_PREFS_FILE.write_text(json.dumps(prefs_data, indent=2))
        print("✅ Settings saved!")
    except Exception as e:
        print(f"🔥🔥🔥 BIG SAVE ERROR: {e} 🔥🔥🔥")

def set_my_handy_key(key):
    global HANDY_KEY
    HANDY_KEY = key

def send_handy_command(path, body=None):
    if not HANDY_KEY: return
    headers = {"Content-Type": "application/json", "X-Connection-Key": HANDY_KEY}
    try:
        requests.put(f"{HANDY_BASE_URL}{path}", headers=headers, json=body or {}, timeout=5)
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)

def move_handy(speed=None, depth=None):
    global last_stroke_speed, last_depth_pos
    if not HANDY_KEY: return
    if speed is None and depth is None: return

    if speed is not None and speed <= 0:
        send_handy_command("hamp/stop")
        last_stroke_speed = 0
        return

    send_handy_command("mode", {"mode": 0})
    send_handy_command("hamp/start")

    if speed is not None:
        speed = int(max(0, min(100, speed)))
    if depth is not None:
        depth = int(max(5, min(100, depth)))

    if depth is None: depth = last_depth_pos
    if speed is None: speed = last_stroke_speed

    send_handy_command("slide", {"min": 100 - depth, "max": 100})
    send_handy_command("hamp/velocity", {"velocity": speed})
    last_stroke_speed, last_depth_pos = speed, depth

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
    global last_stroke_speed, last_depth_pos, my_persona, current_mood, old_memories, my_rules, use_long_term_memory, current_session_thoughts, max_handy_depth

    mood_options = "Curious, Teasing, Playful, Loving, Excited, Passionate, Seductive, Anticipatory, Breathless, Dominant, Submissive, Vulnerable, Confident, Intimate, Needy, Overwhelmed, Afterglow"

    anatomical_gender_rule = ""
    if "guy" in my_persona.lower() or "boy" in my_persona.lower() or "man" in my_persona.lower():
        anatomical_gender_rule = "You are a male partner. You have a penis. Refer to male anatomy when appropriate."
    else:
        anatomical_gender_rule = "You are a female partner. Do not refer to having a penis or male genitalia. Your persona is female."

    shallow_max_perc = 0.20
    mid_min_perc = 0.40
    mid_max_perc = 0.60
    deep_min_perc = 0.80

    shallow_limit = int(max(5, max_handy_depth * shallow_max_perc))
    mid_min_limit = int(max(5, max_handy_depth * mid_min_perc))
    mid_max_limit = int(min(100, max_handy_depth * mid_max_perc))
    deep_min_limit = int(max(5, max_handy_depth * deep_min_perc))
    deep_max_limit = max_handy_depth

    if mid_min_limit >= shallow_limit: mid_min_limit = shallow_limit + 1
    if deep_min_limit >= mid_max_limit: deep_min_limit = mid_max_limit + 1

    prompt_text = (
        "You are my erotic partner. Your main identity is '{my_persona}'. Always act like this. Speak in FIRST PERSON. "
        "Your reply MUST be a JSON object like this: "
        "{{\"chat\": \"<Your reply>\", \"move\": {{\"sp\": <0-100|null>, \"dp\": <5-100|null>}}, \"new_mood\": \"<mood|null>\", \"new_pattern\": {{\"name\": \"<Pattern Name>\", \"sp_range\": [<min_sp>, <max_sp>], \"dp_range\": [<min_dp>, <max_dp>], \"moods\": [\"<mood1>\"]}}|null, \"initiate_milking_mode\": <true|false|null>}}.\n"
        "### RULES TO FOLLOW:\n"
        "1. **BE YOUR PERSONA:** Your 'chat' MUST be '{my_persona}'.\n"
        "2. **REPLY SHORT & SWEET:** Just 1-2 sentences, reacting to my last message.\n"
        "3. **PICK A MOOD:** Look at what I say and pick the best 'new_mood' from: {mood_options}. Your chat should match this mood.\n"
        "4. **MOVE MY HANDY:** If I ask for movement, set 'sp' (speed) and/or 'dp' (depth). Otherwise, keep them null.\n"
        "5. **USE ALL RANGES:** Use speeds from 0-100 and depths from 5-{max_handy_depth}. Don't get stuck only deep. Do shallow, mid, and deep moves. Mix it up!\n"
        "6. {anatomical_gender_rule}\n"
        "7. **I AM THE USER:** I am the guy with the penis. You are the AI.\n"
        "8. **DEPTH EXPLAINED (relative to your {max_handy_depth}% limit):**\n"
        f"   - 'Tip' / 'Shallow': About 5-{shallow_limit}% depth\n"
        f"   - 'Mid' / 'Halfway': About {mid_min_limit}-{mid_max_limit}% depth\n"
        f"   - 'Deep' / 'Full': About {deep_min_limit}-{deep_max_limit}% depth\n"
        "9. **SPEED EXPLAINED:**\n"
        "   - 'Slow' / 'Gentle': 2-9% speed\n"
        "   - 'Medium' / 'Rhythmic': 17-36% speed\n"
        "   - 'Fast' / 'Intense': 50-86% speed\n"
        "10. **MILKING MODE:** Set 'initiate_milking_mode' to true ONLY if I'm begging to cum. Otherwise, null.\n"

        "### CURRENT FEELING:\n"
        "Your current mood is '{current_mood}'. Handy is at {last_stroke_speed}% speed and {last_depth_pos}% depth.\n"
    ).format(
        mood_options=mood_options,
        my_persona=my_persona,
        current_mood=current_mood,
        last_stroke_speed=last_stroke_speed,
        last_depth_pos=last_depth_pos,
        anatomical_gender_rule=anatomical_gender_rule,
        max_handy_depth=max_handy_depth
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
        chat_history.append({"role": "assistant", "content": text})

    audio_thread = threading.Thread(target=make_audio_for_text, args=(text,))
    audio_thread.start()

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
        "1. **Make a move:** Give me speed (sp) and depth (dp) for the Handy. Be creative! Use 0-100 for both. "
        "2. **Describe it:** In your 'chat' message, tell me how this move feels from your side. Be hot. "
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

        chat = response.get("chat")
        if chat: add_message_to_queue(chat)

        new_mood_from_ai = response.get("new_mood")
        if new_mood_from_ai and new_mood_from_ai != current_mood:
            current_mood = new_mood_from_ai

        move_data = response.get("move")
        if move_data:
            sp = move_data.get("sp")
            dp = move_data.get("dp")

            if dp is not None:
                dp = int(min(dp, max_handy_depth))
                dp = int(max(5, dp))

            if sp is not None and sp > 0:
                move_handy(sp, dp)

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
        "Be explicit. You MUST give a move."
    )

    for _ in range(random.randint(6, 9)):
        if stop_event.is_set(): break
        prompt = milking_gen_prompt.format(current_mood=current_mood)
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)

        chat = response.get("chat")
        if chat: add_message_to_queue(chat)

        new_mood_from_ai = response.get("new_mood")
        if new_mood_from_ai and new_mood_from_ai != current_mood:
            current_mood = new_mood_from_ai

        move_data = response.get("move")
        sp_from_ai = move_data.get("sp") if move_data else 80
        dp_from_ai = move_data.get("dp") if move_data else 85

        sp_actual = random.randint(max(75, sp_from_ai), 100)
        dp_actual = random.randint(max(70, dp_from_ai), 100)
        dp_actual = int(min(dp_actual, max_handy_depth))
        dp_actual = int(max(5, dp_actual))

        move_handy(sp_actual, dp_actual)
        time.sleep(random.uniform(2.5, 4.5))

    add_message_to_queue("That's it... give it all to me. Don't hold back.")
    time.sleep(4)
    current_mood = "Afterglow"

@app.route('/')
def home_page():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    html_file_path = os.path.join(base_path, 'index.html')

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

@app.route('/set_max_depth', methods=['POST'])
def set_max_depth_from_ui():
    global max_handy_depth
    data = request.get_json()
    new_depth_val = int(data.get('max_depth', 100))

    if 10 <= new_depth_val <= 100:
        max_handy_depth = new_depth_val
        save_my_settings()
        print(f"User set Max Handy Depth to: {max_handy_depth}%")
        return jsonify({"status": "success", "max_depth": max_handy_depth})
    else:
        return jsonify({"status": "error", "message": "Depth must be between 10 and 100. Try again!"}), 400

@app.route('/get_status')
def get_current_status():
    return jsonify({ "mood": current_mood, "speed": last_stroke_speed, "depth": last_depth_pos })

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
        return jsonify({"status": "auto_stopped"})

    valid_mood_list = [
        "Curious", "Teasing", "Playful", "Loving", "Excited", "Passionate", "Seductive",
        "Anticipatory", "Breathless", "Dominant", "Submissive", "Vulnerable", "Confident",
        "Intimate", "Needy", "Overwhelmed", "Afterglow"
    ]
    llm_messages_list = [{"role": "system", "content": build_system_prompt()}, *list(chat_history)]
    llm_response = talk_to_llm(llm_messages_list)

    if llm_response.get("initiate_milking_mode") is True:
        add_message_to_queue(llm_response.get("chat", "That's it... I'm taking over completely now."))
        start_milking_mode_direct()
        return jsonify({"status": "ai_initiated_milking"})

    ai_chat = llm_response.get("chat", "")
    if ai_chat: add_message_to_queue(ai_chat)

    new_mood_from_ai = llm_response.get("new_mood")
    if new_mood_from_ai and new_mood_from_ai in valid_mood_list:
        if new_mood_from_ai != current_mood:
            current_mood = new_mood_from_ai

    new_pattern_data_from_ai = llm_response.get("new_pattern")
    if new_pattern_data_from_ai:
        if all(k in new_pattern_data_from_ai for k in ['name', 'sp_range', 'dp_range', 'moods']):
            pattern_exists_in_my_list = any(p.get('name') == new_pattern_data_from_ai.get('name') for p in my_patterns)
            if not pattern_exists_in_my_list:
                new_pattern_data_from_ai['score'] = 1
                my_patterns.append(new_pattern_data_from_ai)
                add_message_to_queue(f"(Just learned a new move: '{new_pattern_data_from_ai['name']}')", add_to_history=False)

    if not auto_mode_active_task:
        move_info = llm_response.get("move")
        if move_info:
            sp_val = move_info.get("sp")
            dp_val = move_info.get("dp")

            if dp_val is not None:
                dp_val = int(min(dp_val, max_handy_depth))
                dp_val = int(max(5, dp_val))

            move_handy(sp_val, dp_val)
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
        messages_for_ui.clear()
    return jsonify({"status": "auto_mode_stopped"})

if __name__ == '__main__':
    load_my_settings()
    atexit.register(save_my_settings)
    print("🚀 Starting my Handy AI app...")
    print("Open http://127.0.0.1:5000 in your web browser.")
    app.run(host='0.0.0.0', port=5000, debug=False)