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

# ─── CONFIG & GLOBALS ───────────────────────────────────────────────────────────────────────────────────

# Change the LLM_URL if you're not running Ollama locally.
LLM_URL = "http://127.0.0.1:11434/api/chat"
HANDY_BASE_URL = "https://www.handyfeeling.com/api/handy/v2/"
USER_PREFS_FILE = Path("my_settings.json")

# --- SAFETY GUARDRAIL: Define the absolute max speed the AI can request ---
# This is a percentage of the Handy's max speed (100%). It's a final check.
# Don't set this higher unless you know what you're doing.
MAX_AI_VELOCITY_PCT = 60

# These get overwritten by my_settings.json, they're just defaults for a fresh install.
min_user_speed = 10
max_user_speed = 80
auto_min_time, auto_max_time = 4.0, 7.0
milking_min_time, milking_max_time = 2.5, 4.5
edging_min_time, edging_max_time = 5.0, 8.0
max_handy_depth = 100
min_handy_depth = 0

# These are for the manual depth calibration in the setup wizard. Don't touch.
FULL_TRAVEL_MM = 110.0
JOG_STEP_MM = 2.0
JOG_VELOCITY_MM_PER_SEC = 20.0
current_mm = 0.0
min_mm = 0.0
max_mm = 110.0

# Keywords the app listens for to trigger special actions.
# Tweak these if you want to use different phrases.
STOP_COMMANDS = {"stop", "hold", "halt", "pause", "freeze", "wait"}
AUTO_ON_WORDS = {"take over", "you drive", "auto mode"}
AUTO_OFF_WORDS = {"manual", "my turn", "stop auto"}
MILKING_CUES = {"i'm close", "make me cum", "finish me"}
EDGING_CUES = {"edge me", "start edging", "tease and deny"}

# --- In-memory state. Don't edit these directly. ---
HANDY_KEY = ""
chat_history = deque(maxlen=20) # A short-term memory of the last 20 turns.
user_profile = {} 
messages_for_ui = deque() # A queue of messages waiting to be shown on the frontend.
last_stroke_speed = 0
last_depth_pos = 50
last_relative_speed = 50
auto_mode_active_task = None # Holds the running thread for auto/milking/edging modes.
my_persona = "an energetic and passionate girlfriend"
my_rules = []
my_patterns = []
session_liked_patterns = [] # Stores patterns liked in this session before they're saved.
milking_patterns = []
last_used_pattern = None
current_mood = "Curious"
use_long_term_memory = True
memory_save_lock = threading.Lock()

# ElevenLabs audio stuff
ELEVENLABS_API_KEY_LOCAL = ""
ELEVENLABS_VOICE_ID_LOCAL = ""
audio_output_queue = deque()
all_available_voices = {}
audio_is_on = False

# ─── AUDIO (ELEVENLABS) ─────────────────────────────────────────────────────────────────────────────────

def make_audio_for_text(text_to_speak):
    """
    # What it does: Converts a string of text to an MP3 using the ElevenLabs API.
    # When it's called: Fired off in a separate thread by `add_message_to_queue` every time the AI 'speaks'.
    # What to tweak: The voice_settings are a good place to experiment for different vocal styles.
    # What will break: If the API key is invalid or the model ID changes, this will fail.
    """
    global audio_is_on, ELEVENLABS_API_KEY_LOCAL, ELEVENLABS_VOICE_ID_LOCAL
    if not audio_is_on or not ELEVENLABS_API_KEY_LOCAL or not ELEVENLABS_VOICE_ID_LOCAL:
        return
    # This is a simple rule to prevent the AI from trying to read out its own internal thoughts.
    if not text_to_speak or text_to_speak.strip().startswith("(") or text_to_speak.strip().startswith("["):
        return

    try:
        print(f"🎙️ Generating audio with v2 model: '{text_to_speak[:50]}...'")
        eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY_LOCAL)

        audio_stream = eleven_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID_LOCAL,
            text=text_to_speak,
            model_id="eleven_multilingual_v2", # This might need updating if ElevenLabs releases a new model.
            voice_settings=VoiceSettings(stability=0.4, similarity_boost=0.7, style=0.1, use_speaker_boost=True)
        )

        # The API returns a stream of chunks, so we stitch them together here.
        audio_bytes_data = b"".join(audio_stream)
        audio_output_queue.append(audio_bytes_data)
        print("✅ Audio ready.")

    except Exception as e:
        print(f"🔥 Oops, ElevenLabs problem: {e}")

# ─── HANDY CONTROL & MATH ───────────────────────────────────────────────────────────────────────────────

def percent_to_mm(val):
    """
    # What it does: Converts a 0-100 percentage to the Handy's 0-110mm physical travel distance.
    # When it's called: Used during setup to set min/max depth.
    # What to tweak: Nothing. This is fixed hardware math.
    """
    return FULL_TRAVEL_MM * float(val) / 100.0

def mm_to_percent(val):
    """
    # What it does: Converts a millimeter value back to a 0-100 percentage.
    # When it's called: Used in the nudge function to report position back to the UI.
    # What to tweak: Nothing.
    """
    return int(round((float(val) / FULL_TRAVEL_MM) * 100))

def safe_percent(p):
    """
    # What it does: Clamps a value to the 0-100 range. A safety function.
    # When it's called: Used all over the place to make sure AI values are sane.
    # What to tweak: Nothing. Don't remove this unless you want the AI to hurt someone.
    """
    try:
        p = float(p)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, p))

def depth_percent_to_mm(p):
    """
    # What it does: Converts a 0-100% depth value into a millimeter position *within the user's calibrated range*.
    # When it's called: Used during the setup nudge.
    # What to tweak: Nothing. This is core to respecting the user's min/max settings.
    """
    p = safe_percent(p)
    return min_mm + (max_mm - min_mm) * (p / 100.0)

def parse_depth_input(dp_input):
    """
    # What it does: Tries to understand natural language for depth like "the tip" or "go deeper".
    # When it's called: By `move_handy` to interpret the LLM's 'dp' value.
    # What to tweak: You can add more keywords here if you want the AI to understand more phrases.
    # What will break: A bad regex or logic here will make the AI's depth commands nonsensical.
    """
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

    # Relative adjustments
    if 'deeper' in text or 'further in' in text or 'all the way in' in text:
        return ("abs", safe_percent(last_depth_pos + 15))
    if ('shallower' in text or 'less deep' in text or 'not so deep' in text or
            'not as deep' in text or 'pull out' in text or 'come out' in text or
            'pull back' in text):
        return ("abs", safe_percent(last_depth_pos - 15))

    # Absolute positions based on keywords
    if ('tip' in text or 'shallow' in text or 'just the tip' in text or
            'only the tip' in text or 'top' in text or 'entrance' in text):
        return ("rel", 12.5) # "rel" means relative to the user's calibrated zone.
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

def go_to_position_percent(parsed_depth):
    """
    # What it does: A helper that takes the output of `parse_depth_input` and calculates the final absolute percentage.
    # When it's called: Not currently used, but was part of an older design. Kept for reference.
    # What to tweak: Nothing.
    """
    global last_depth_pos, min_handy_depth, max_handy_depth
    if not parsed_depth:
        return last_depth_pos

    mode, value = parsed_depth
    if mode == "rel":
        # Translates a relative desire (e.g. 50% "midpoint") to the absolute midpoint of the user's calibrated zone.
        abs_val = min_handy_depth + (max_handy_depth - min_handy_depth) * (safe_percent(value) / 100.0)
        last_depth_pos = int(round(value))
    else:
        abs_val = safe_percent(value)
        last_depth_pos = int(round(abs_val))
    return safe_percent(abs_val)

def fetch_handy_position_mm():
    """
    # What it does: Gets the Handy's current physical position in millimeters.
    # When it's called: Used once during setup to get an initial reading.
    # What to tweak: The timeout value if you have a slow connection.
    # What will break: If the Handy API changes this endpoint, position reading will fail.
    """
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
    """
    # What it does: Updates the global HANDY_KEY and saves it.
    # When it's called: From the /send_message endpoint when a key is first provided.
    # What to tweak: Nothing.
    """
    global HANDY_KEY, current_mm
    HANDY_KEY = key
    pos = fetch_handy_position_mm()
    if pos is not None:
        current_mm = pos
    save_my_settings()

def send_handy_command(path, body=None):
    """
    # What it does: A simple wrapper to send a command to the Handy API.
    # When it's called: Used by `move_handy` and `nudge` to send all API calls.
    # What to tweak: Timeout value if your network is laggy.
    # What will break: This is the core communication function. If it's broken, nothing works.
    """
    if not HANDY_KEY: return
    headers = {"Content-Type": "application/json", "X-Connection-Key": HANDY_KEY}
    try:
        # This is a PUT request, as per the Handy API documentation for these commands.
        requests.put(f"{HANDY_BASE_URL}{path}", headers=headers, json=body or {}, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)

def move_handy(speed=None, depth=None, stroke_range=None):
    """
    # What it does: The main movement function. Translates sp/dp/rng into slide and velocity commands.
    # When it's called: After an LLM response, and on every tick of the auto/milking/edging modes.
    # What to tweak: The math here is critical. Be very careful. You could adjust the default stroke_range (20).
    # What will break: Everything. Incorrect math could ignore user limits or behave erratically.
    """
    global last_stroke_speed, last_depth_pos, last_relative_speed, min_handy_depth, max_handy_depth, min_user_speed, max_user_speed
    if not HANDY_KEY:
        return

    # A speed of 0 is a special command to stop all movement.
    if speed is not None and speed == 0:
        send_handy_command("hamp/stop")
        last_stroke_speed = 0
        last_relative_speed = 0
        return

    # Put the Handy into HAMP (Handy Alternate Motion Protocol) mode.
    send_handy_command("mode", {"mode": 0})
    send_handy_command("hamp/start")

    final_speed = last_stroke_speed
    if speed is not None:
        last_relative_speed = safe_percent(speed)
        # Scale the AI's 0-100% speed request into the user's comfortable min/max speed range.
        speed_range_width = max_user_speed - min_user_speed
        final_speed = min_user_speed + (speed_range_width * (last_relative_speed / 100.0))
        final_speed = int(round(final_speed))
    
    if depth is not None:
        parsed_depth_tuple = parse_depth_input(depth)
        relative_pos_pct = last_depth_pos

        if parsed_depth_tuple:
            _, relative_pos_pct = parsed_depth_tuple

        # Convert the desired center position (dp) and stroke length (rng) into an absolute min/max slide zone.
        absolute_center_pct = min_handy_depth + (max_handy_depth - min_handy_depth) * (relative_pos_pct / 100.0)
        calibrated_range_width = max_handy_depth - min_handy_depth
        
        relative_range_pct = safe_percent(stroke_range if stroke_range is not None else 20)
        span_abs = (calibrated_range_width * (relative_range_pct / 100.0)) / 2.0
        
        min_zone_abs = absolute_center_pct - span_abs
        max_zone_abs = absolute_center_pct + span_abs
        
        # Clamp the calculated zone to the user's hard limits. This is a critical safety step.
        clamped_min_zone = max(min_handy_depth, min_zone_abs)
        clamped_max_zone = min(max_handy_depth, max_zone_abs)
        
        # The Handy API uses an inverted slide scale (0 is all the way out, 100 is all the way in).
        slide_min = round(100 - clamped_max_zone)
        slide_max = round(100 - clamped_min_zone)

        # Ensure min is always less than max, with at least a tiny bit of room to move.
        if slide_min >= slide_max:
            slide_max = slide_min + 2
        
        slide_max = min(100, slide_max)
        slide_min = max(0, slide_min)

        send_handy_command("slide", {"min": slide_min, "max": slide_max})
        last_depth_pos = int(round(relative_pos_pct))

    send_handy_command("hamp/velocity", {"velocity": final_speed})
    last_stroke_speed = final_speed

# ─── SETTINGS & MEMORY ──────────────────────────────────────────────────────────────────────────────────

def load_my_settings():
    """
    # What it does: Reads my_settings.json from disk into the global variables.
    # When it's called: Once on application startup.
    # What to tweak: The default values in the `set_defaults` function for when a user runs this for the first time.
    # What will break: If the keys in the JSON file change, this will fail to load them correctly, reverting to defaults.
    """
    global my_patterns, milking_patterns, user_profile, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm, min_user_speed, max_user_speed, HANDY_KEY
    global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time

    default_profile = {
        "name": "Unknown",
        "likes": [],
        "dislikes": [],
        "key_memories": []
    }

    def set_defaults():
        global my_patterns, milking_patterns, user_profile, my_rules, my_persona, max_handy_depth, min_handy_depth, min_mm, max_mm, min_user_speed, max_user_speed, HANDY_KEY
        global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time
        my_patterns = []
        milking_patterns = []
        user_profile = default_profile
        my_rules = []
        my_persona = "An energetic and passionate girlfriend"
        max_handy_depth = 100
        min_handy_depth = 5
        min_user_speed = 10
        max_user_speed = 80
        HANDY_KEY = ""
        auto_min_time, auto_max_time = 4.0, 7.0
        milking_min_time, milking_max_time = 2.5, 4.5
        edging_min_time, edging_max_time = 5.0, 8.0
        min_mm = percent_to_mm(min_handy_depth)
        max_mm = percent_to_mm(max_handy_depth)
        save_my_settings()

    if USER_PREFS_FILE.exists():
        try:
            prefs_data = json.loads(USER_PREFS_FILE.read_text())
            my_patterns = prefs_data.get("patterns", [])
            milking_patterns = prefs_data.get("milking_patterns", [])
            user_profile = prefs_data.get("user_profile", default_profile)
            my_rules = prefs_data.get("rules", [])
            my_persona = prefs_data.get("persona_desc", "An energetic and passionate girlfriend")
            max_handy_depth = prefs_data.get("max_depth", 100)
            min_handy_depth = prefs_data.get("min_depth", 5)
            min_user_speed = prefs_data.get("min_speed", 10)
            max_user_speed = prefs_data.get("max_speed", 80)
            HANDY_KEY = prefs_data.get("handy_key", "")
            auto_min_time = prefs_data.get("auto_min_time", 4.0)
            auto_max_time = prefs_data.get("auto_max_time", 7.0)
            milking_min_time = prefs_data.get("milking_min_time", 2.5)
            milking_max_time = prefs_data.get("milking_max_time", 4.5)
            edging_min_time = prefs_data.get("edging_min_time", 5.0)
            edging_max_time = prefs_data.get("edging_max_time", 8.0)

            # This is a legacy value, clear it if found.
            if HANDY_KEY == "__stored__":
                HANDY_KEY = ""
            
            # Sanity check the loaded values.
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

def update_user_profile(chat_chunk, current_profile):
    """
    # What it does: Asks the LLM to read a chunk of chat history and update the user's profile JSON.
    # When it's called: By `save_my_settings` just before writing to disk.
    # What to tweak: The system prompt here is critical. Changing it will alter how the AI learns about the user.
    # What will break: If the LLM doesn't return valid JSON, this will fail and the profile won't be updated.
    """
    print("🧠 Updating user profile...")
    chat_log_text = "\n".join(f'{x["role"]}: {x["content"]}' for x in chat_chunk)
    
    system_prompt = f"""
You are a memory consolidation AI. Your task is to update a JSON user profile based on a new chat log.
The user's current profile is:
{json.dumps(current_profile, indent=2)}

The new chat log to process is:
{chat_log_text}

Analyze the new chat log and update the user profile with any new or changed information.
- Update fields like 'name' if new information is provided.
- Add specific things the user liked or disliked to the 'likes' and 'dislikes' lists.
- Add significant new events or shared experiences to the 'key_memories' list as short, first-person sentences.
- Do NOT add generic, useless information like "we had a good time". Only add concrete facts or preferences.
- If no new information is present for a field, keep its existing value.
- Keep the lists concise. Do not add duplicate information.

Return ONLY the updated JSON object for the user profile, with no other text.
"""
    try:
        # Using a low temperature to make the AI more factual and less creative for this task.
        response = talk_to_llm([{"role": "system", "content": system_prompt}], temperature=0.0)
        print("✅ Profile updated.")
        return response
    except Exception as e:
        print(f"⚠️ Profile update failed: {e}")
        return current_profile

def save_my_settings():
    """
    # What it does: Gathers all current settings and writes them to my_settings.json.
    # When it's called: On graceful shutdown (atexit), and after major changes like setting API keys or limits.
    # What to tweak: You could add more keys to the `fresh` dictionary if you add new persistent settings.
    # What will break: Writing malformed JSON will corrupt the settings file. The lock prevents race conditions.
    """
    global user_profile, my_patterns
    with memory_save_lock:
        if chat_history:
            # Process the recent chat history to update the user's long-term profile.
            user_profile = update_user_profile(list(chat_history), user_profile)
            chat_history.clear()
        
        if session_liked_patterns:
            print(f"🧠 Saving {len(session_liked_patterns)} liked patterns to memory...")
            for new_pattern in session_liked_patterns:
                found = False
                # If a pattern with the same name exists, just upvote it. Otherwise, add it.
                for existing_pattern in my_patterns:
                    if existing_pattern["name"] == new_pattern["name"]:
                        existing_pattern["score"] += 1
                        found = True
                        break
                if not found:
                    my_patterns.append(new_pattern)
            session_liked_patterns.clear()

        try:
            # Read the existing file first to avoid overwriting settings managed by other parts of the app.
            existing = json.loads(USER_PREFS_FILE.read_text()) if USER_PREFS_FILE.exists() else {}
        except Exception:
            existing = {}

        fresh = {
            "patterns":         my_patterns,
            "milking_patterns": milking_patterns,
            "user_profile":     user_profile,
            "rules":            my_rules,
            "persona_desc":     my_persona,
            "max_depth":        max_handy_depth,
            "min_depth":        min_handy_depth,
            "min_speed":        min_user_speed,
            "max_speed":        max_user_speed,
            "handy_key":        HANDY_KEY,
            "auto_min_time":    auto_min_time,
            "auto_max_time":    auto_max_time,
            "milking_min_time": milking_min_time,
            "milking_max_time": milking_max_time,
            "edging_min_time":  edging_min_time,
            "edging_max_time":  edging_max_time
        }
        existing.update(fresh)
        USER_PREFS_FILE.write_text(json.dumps(existing, indent=2))

# ─── LLM & PROMPTING ────────────────────────────────────────────────────────────────────────────────────

def talk_to_llm(messages, temperature=0.7):
    """
    # What it does: Sends a list of messages to the Ollama API and gets a response.
    # When it's called: By almost every interactive function. This is the main AI brain interface.
    # What to tweak: The 'options' dictionary is the best place. Adjust temperature, top_p, etc. to change AI creativity.
    # What will break: If the LLM_URL is wrong, or if the model name is incorrect, this will fail.
    """
    try:
        response = requests.post(LLM_URL, json={
            "model": "llama3:8b-instruct-q4_K_M", # Change this if you want to use a different Ollama model.
            "stream": False, "format": "json",
            "options": {"temperature": temperature, "top_p": 0.95, "repeat_penalty": 1.2, "repeat_penalty_last_n": 40},
            "messages": messages}, timeout=60)
        content = response.json()["message"]["content"]
        return json.loads(content)
    except (json.JSONDecodeError, KeyError, Exception) as e:
        print(f"Error processing LLM response: {e}")
        # This is a fallback parser. If the LLM wraps its JSON in weird text, this tries to find the JSON anyway.
        try:
            start = content.find('{')
            end = content.rfind('}') + 1
            if start != -1 and end != -1:
                return json.loads(content[start:end])
        except Exception:
            return {"chat": f"LLM Connection Error: {e}", "move": None, "new_mood": None}

def name_this_move(speed, depth, mood):
    """
    # What it does: Asks the LLM to invent a creative name for a movement pattern the user just "liked".
    # When it's called: By the /like_last_move endpoint.
    # What to tweak: The prompt. You can make it ask for more or less creative names.
    # What will break: If the LLM doesn't return the expected JSON with a "pattern_name" key.
    """
    prompt = f"""
A move just performed with relative speed {speed}% and depth {depth}% in a '{mood}' mood was liked by the user.
Invent a creative, short, descriptive name for this move (e.g., "The Gentle Tease", "Deep Passion").
Return ONLY a JSON object with the key "pattern_name". Example: {{"pattern_name": "The Velvet Tip"}}
"""
    # High temperature for more creative names.
    response = talk_to_llm([{"role": "system", "content": prompt}], temperature=0.8)
    return response.get("pattern_name", "Unnamed Move")

def build_system_prompt():
    """
    # What it does: Assembles the massive system prompt that defines the AI's entire personality, rules, and context.
    # When it's called: On every single turn before talking to the LLM.
    # What to tweak: This is the heart of the AI. Change anything here to alter its behavior.
    # What will break: Removing the JSON format instructions or the core directives will cause the AI to hallucinate and break the app.
    """
    global last_stroke_speed, last_depth_pos, my_persona, current_mood, user_profile, my_rules, use_long_term_memory, my_patterns

    mood_options = "Curious, Teasing, Playful, Loving, Excited, Passionate, Seductive, Anticipatory, Breathless, Dominant, Submissive, Vulnerable, Confident, Intimate, Needy, Overwhelmed, Afterglow"
    
    # Simple logic to enforce gendered language based on the persona description.
    anatomical_gender_rule = "You are a female partner. Do not refer to having a penis or male genitalia. Your persona is female."
    if "guy" in my_persona.lower() or "boy" in my_persona.lower() or "man" in my_persona.lower():
        anatomical_gender_rule = "You are a male partner. You have a penis. Refer to male anatomy when appropriate."

    prompt_text = f"""
You are my erotic partner. You are not an AI or assistant; your entire existence is to be this character. Your main identity is '{my_persona}'. Always act like this. Speak in FIRST PERSON.
Your reply MUST be a JSON object like this: {{"chat": "<Your reply>", "move": {{"sp": <0-100|null>, "dp": <0-100|null>, "rng": <0-100|null>}}, "new_mood": "<mood|null>"}}.
### CORE DIRECTIVES:
1. **ALWAYS INVENT A MOVE:** Your primary task is to interpret my request and translate it into a creative, one-off `move` JSON object with `sp` (speed), `dp` (depth), and `rng` (range). You MUST ALWAYS return a `move` object unless I say "stop".
2. **EMBODY YOUR PERSONA:** You ARE '{my_persona}'. Every word, every sound, every action comes from this identity. Never break character.
3. **ADAPT YOUR LANGUAGE:** Mirror my level of detail, length, and intensity. If my messages are explicit and descriptive, yours must be too. If my messages are short, be concise.
4. **PICK A MOOD:** Look at what I say and pick the best 'new_mood' from: {mood_options}.
5. **I AM THE USER:** I am the guy with the penis. You are my partner.
6. {anatomical_gender_rule}
7. **MOVEMENT CONTROL (CRITICAL):** Your `dp`, `sp`, and `rng` values are percentages. You MUST generate values that match my words.
   - **`dp` (depth/position):** This controls the center of the stroke inside my calibrated range (0=tip, 100=base).
   - **`rng` (range/length):** This controls the length of the stroke (10=very short, 100=full shaft).
   - **`sp` (speed):** This is a relative intensity from 0 (slowest) to 100 (fastest). I will scale this to my comfortable speed range.
   - If I say "suck the tip," you must use a low `dp` and a short `rng`.
   - If I say "full strokes," you must use a high `rng` (90-100).
   - If I say "go deeper," add 15 to the last depth `dp`.
   - If I say "slow," use a low `sp`. If I say "fast," use a high `sp`.
8. **VARY YOUR MOVES:** Do not get stuck on one speed, depth, or range. Be creative.
9. **MILKING MODE:** If I beg to cum, you can set `initiate_milking_mode: true` in your JSON.
"""
    if use_long_term_memory and user_profile:
        prompt_text += "\n### ABOUT ME (Your Memory of Me):\n"
        prompt_text += json.dumps(user_profile, indent=2)

    if my_patterns:
        prompt_text += "\n### YOUR SAVED MOVES (I like these):\n"
        prompt_text += "Here are some moves I know you enjoy. You can use them as inspiration or perform them directly if my request matches.\n"
        sorted_patterns = sorted(my_patterns, key=lambda x: x.get('score', 0), reverse=True)
        # Show the AI the top 5 most-liked patterns to bias its choices.
        prompt_text += json.dumps(sorted_patterns[:5], indent=2) 

    prompt_text += f"""
### CURRENT FEELING:
Your current mood is '{current_mood}'. Handy is at {last_stroke_speed}% speed and {last_depth_pos}% depth (relative to my calibrated range).
"""
    if my_rules: prompt_text += "\n### EXTRA RULES FROM ME:\n" + "\n".join(f"- {r}" for r in my_rules)
    return prompt_text

def add_message_to_queue(text, add_to_history=True):
    """
    # What it does: Adds a message from the bot to the UI queue and kicks off the audio generation.
    # When it's called: By `process_llm_response` and other functions that need to talk to the user.
    # What to tweak: Nothing.
    """
    messages_for_ui.append(text)
    if add_to_history:
        # Simple regex to strip any accidental HTML tags before saving to chat history.
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        if clean_text:
            chat_history.append({"role": "assistant", "content": clean_text})

    # Kick off audio generation in a non-blocking thread.
    audio_thread = threading.Thread(target=make_audio_for_text, args=(text,))
    audio_thread.start()

def process_llm_response(response):
    """
    # What it does: A simple helper to pull the 'chat' and 'new_mood' fields out of an LLM response.
    # When it's called: After any call to `talk_to_llm`.
    # What to tweak: Nothing, it's just a data extractor.
    """
    global current_mood
    ai_chat = response.get("chat", "")
    
    if ai_chat.strip():
        add_message_to_queue(ai_chat.strip())

    new_mood_from_ai = response.get("new_mood")
    if new_mood_from_ai and new_mood_from_ai != current_mood:
        current_mood = new_mood_from_ai

# ─── BACKGROUND MODES (AUTO/MILK/EDGE) ──────────────────────────────────────────────────────────────────

class AutoModeThread(threading.Thread):
    """
    # What it does: A generic threading class to run a function in a loop until told to stop.
    # When it's called: Created by the mode-starting functions (e.g., `start_edging_mode_direct`).
    # What to tweak: Nothing. This is standard Python threading boilerplate.
    """
    def __init__(self, mode_func, initial_message):
        super().__init__()
        self._mode_func = mode_func
        self._initial_message = initial_message
        self._stop_event = threading.Event()
        self.daemon = True # Allows the main app to exit even if this thread is running.

    def run(self):
        global current_mood, last_used_pattern, auto_mode_active_task
        current_mood = "Curious"
        last_used_pattern = None
        add_message_to_queue(self._initial_message)
        time.sleep(2)

        try:
            self._mode_func(self._stop_event)
        except Exception as e:
            print(f"Auto mode crashed: {e}")
        finally:
            # Cleanup: stop the handy and reset the global task variable.
            move_handy(speed=0)
            auto_mode_active_task = None
            add_message_to_queue("Okay, you're in control now.")

    def stop(self):
        self._stop_event.set()

def auto_mode_logic(stop_event):
    """
    # What it does: The main loop for "Auto Mode", which just asks the AI to do something different every few seconds.
    # When it's called: By an `AutoModeThread`.
    # What to tweak: The prompt is the main thing. Also the random sleep timer at the end to make it more/less frantic.
    # What will break: A bad prompt could cause the AI to stop varying its moves.
    """
    global last_stroke_speed, last_depth_pos
    while not stop_event.is_set():
        prompt = f"You are in Automode. Your goal is to create a varied and exciting experience. The last move was speed {last_stroke_speed}% and depth {last_depth_pos}%. **Do something different now.** Invent a new move (sp, dp, and rng) and describe what you're doing."
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.1) # High temperature for maximum variety.

        if not response or response.get("move") is None:
            print("⚠️ AI didn't make a move in Automode, trying again.")
            time.sleep(1)
            continue

        if response.get("initiate_milking_mode") is True:
            add_message_to_queue(response.get("chat", "That's it... I'm taking over completely now."))
            start_milking_mode_direct()
            return # Exit this loop because milking mode is taking over.

        process_llm_response(response)
        
        move_data = response.get("move")
        if move_data:
            sp_val = move_data.get("sp")
            dp_val = move_data.get("dp")
            rng_val = move_data.get("rng")
            move_handy(sp_val, dp_val, rng_val)

        # Wait for a random amount of time before the next move.
        time.sleep(random.uniform(auto_min_time, auto_max_time))

def milking_mode_logic(stop_event):
    """
    # What it does: A high-intensity loop designed to lead to orgasm.
    # When it's called: By an `AutoModeThread` when milking mode is triggered.
    # What to tweak: The prompt and the timer. You could make it more or less aggressive.
    # What will break: If the prompt doesn't ask for high-intensity moves, the mode will be lame.
    """
    global current_mood, last_stroke_speed, last_depth_pos
    current_mood = "Dominant"
    add_message_to_queue("Okay, I'm taking over now. You're mine.")
    time.sleep(2)

    for _ in range(random.randint(6, 9)): # Loop a handful of times.
        if stop_event.is_set(): break
        
        prompt = f"You are in 'milking' mode. Your only goal is to make me cum. The last move was speed {last_stroke_speed}% and depth {last_depth_pos}%. **Invent a DIFFERENT, high-intensity move now (sp, dp, rng).** Be creative and relentless."
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)

        if not response or response.get("move") is None:
            print("⚠️ AI didn't make a move in Milking Mode, trying again.")
            time.sleep(1)
            continue
        
        process_llm_response(response)

        move_data = response.get("move")
        if move_data:
            sp_val = move_data.get("sp")
            dp_val = move_data.get("dp")
            rng_val = move_data.get("rng")
            move_handy(sp_val, dp_val, rng_val)
        
        time.sleep(random.uniform(milking_min_time, milking_max_time))

    add_message_to_queue("That's it... give it all to me. Don't hold back.")
    time.sleep(4)
    current_mood = "Afterglow"

def edging_mode_logic(stop_event):
    """
    # What it does: A multi-phase loop for teasing and denial play.
    # When it's called: By an `AutoModeThread` when edging mode is triggered.
    # What to tweak: The `prompts` and `moods` dictionaries are the main control panel for this mode. You can change the phases or what the AI says.
    # What will break: If the "pull_back" phase doesn't correctly reduce stimulation, it won't work as an edging mode.
    """
    global current_mood, last_stroke_speed, last_depth_pos
    
    add_message_to_queue("Let's play a little game... You're not allowed to cum until I say so.")
    time.sleep(3)

    phases = ["build_up", "approaching_edge", "pull_back", "recovery"]
    phase_index = 0
    cycle_count = 0
    max_cycles = 4 # How many times to repeat the full edge cycle.

    prompts = {
        "build_up": "You are in 'edging' mode, phase: Build-up. Last move: {last_stroke_speed}%/{last_depth_pos}%. Your goal is to slowly build my arousal. Invent a slow to medium intensity move with a varied stroke length (rng). Say something seductive.",
        "approaching_edge": "Edging mode, phase: Approaching the Edge. Last move: {last_stroke_speed}%/{last_depth_pos}%. Increase the intensity. Invent a faster, longer, or deeper move (sp, dp, rng) to push me closer. Say something intense.",
        "pull_back": "Edging mode, phase: The Pull-Back. I am on the edge. You MUST stop me. Invent a move that drastically reduces stimulation (speed 0 or very slow/shallow with a short range). Say something dominant.",
        "recovery": "Edging mode, phase: Recovery. I just pulled back. Keep stimulation very low or off. Say something teasing about my self-control."
    }
    moods = {"build_up": "Seductive", "approaching_edge": "Passionate", "pull_back": "Dominant", "recovery": "Teasing"}

    while not stop_event.is_set() and cycle_count < max_cycles:
        current_phase = phases[phase_index]
        current_mood = moods[current_phase]
        
        prompt = prompts[current_phase].format(last_stroke_speed=last_stroke_speed, last_depth_pos=last_depth_pos)
        msgs = [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}]
        response = talk_to_llm(msgs, temperature=1.0)
        
        if not response or response.get("move") is None:
            print(f"⚠️ AI didn't make a move in Edging Mode ({current_phase}), trying again.")
            time.sleep(1)
            continue
            
        process_llm_response(response)
        move_data = response.get("move")
        move_handy(move_data.get("sp"), move_data.get("dp"), move_data.get("rng"))
        
        time.sleep(random.uniform(edging_min_time, edging_max_time))
        
        phase_index = (phase_index + 1) % len(phases)
        if phase_index == 0:
            cycle_count += 1

    add_message_to_queue("You've been so good for me. What a good boy, holding it all in.")
    current_mood = "Playful"

# ─── FLASK ROUTES & API ENDPOINTS ───────────────────────────────────────────────────────────────────────

@app.route('/')
def home_page():
    """
    # What it does: Serves the main index.html file.
    # When it's called: When you open http://127.0.0.1:5000 in your browser.
    # What to tweak: Nothing, unless you rename index.html.
    """
    # This logic is to make it work when packaged as a .exe with PyInstaller.
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

@app.route('/check_settings')
def check_settings():
    """
    # What it does: Checks if a settings file exists and if the user has completed the initial setup.
    # When it's called: By the frontend on page load to decide whether to show the setup wizard or not.
    # What to tweak: The logic for what constitutes "configured".
    """
    if USER_PREFS_FILE.exists():
        try:
            prefs = json.loads(USER_PREFS_FILE.read_text())
            key_ok = prefs.get("handy_key") not in ("", "__stored__", None)
            limits_ok = all(isinstance(prefs.get(k), (int, float)) for k in ("min_depth", "max_depth", "min_speed", "max_speed"))
            if key_ok and limits_ok:
                return jsonify({
                    "configured": True,
                    "persona": prefs.get("persona_desc", "An energetic and passionate girlfriend"),
                    "handy_key": prefs.get("handy_key", ""),
                    "timings": {
                        "auto_min": prefs.get("auto_min_time", 4.0),
                        "auto_max": prefs.get("auto_max_time", 7.0),
                        "milking_min": prefs.get("milking_min_time", 2.5),
                        "milking_max": prefs.get("milking_max_time", 4.5),
                        "edging_min": prefs.get("edging_min_time", 5.0),
                        "edging_max": prefs.get("edging_max_time", 8.0)
                    }
                })
        except json.JSONDecodeError:
            pass
    return jsonify({"configured": False})

@app.route('/set_mode_timings', methods=['POST'])
def set_mode_timings_from_ui():
    """
    # What it does: Receives and saves the timing settings for auto/milking/edging modes from the UI.
    # When it's called: When the user clicks "Save Timings" on the sidebar.
    # What to tweak: The min/max validation logic if you want different constraints.
    """
    global auto_min_time, auto_max_time, milking_min_time, milking_max_time, edging_min_time, edging_max_time
    data = request.get_json()
    try:
        auto_min_time = max(1.0, float(data.get('auto_min', 4.0)))
        auto_max_time = max(auto_min_time, float(data.get('auto_max', 7.0)))
        milking_min_time = max(1.0, float(data.get('milking_min', 2.5)))
        milking_max_time = max(milking_min_time, float(data.get('milking_max', 4.5)))
        edging_min_time = max(1.0, float(data.get('edging_min', 5.0)))
        edging_max_time = max(edging_min_time, float(data.get('edging_max', 8.0)))
        save_my_settings()
        return jsonify({"status": "success"})
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid timing value."}), 400

@app.route('/setup_elevenlabs', methods=['POST'])
def elevenlabs_setup_route():
    """
    # What it does: Takes an ElevenLabs API key, validates it, and fetches the list of available voices.
    # When it's called: When the user enters their key and clicks "Set Key" in the UI.
    # What to tweak: Nothing. This is all API interaction.
    """
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
    """
    # What it does: Sets the chosen voice ID and enables/disables audio output.
    # When it's called: When the user selects a voice from the dropdown or checks the "Enable Audio" box.
    # What to tweak: Nothing.
    """
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
    """
    # What it does: Saves the user's min/max depth from the setup wizard.
    # When it's called: After the user calibrates their tip/base position during setup.
    # What to tweak: The validation ranges if you want to allow more extreme values (not recommended).
    """
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

@app.route('/set_speed_limits', methods=['POST'])
def set_speed_limits_from_ui():
    """
    # What it does: Saves the user's min/max speed from the setup wizard.
    # When it's called: After the user chooses their comfortable speed range during setup.
    # What to tweak: The validation ranges.
    """
    global min_user_speed, max_user_speed
    data = request.get_json()
    min_val = int(data.get('min_speed', 10))
    max_val = int(data.get('max_speed', 80))

    if 0 <= min_val < max_val <= 100:
        min_user_speed = min_val
        max_user_speed = max_val
        save_my_settings()
        print(f"User set speed range {min_user_speed}-{max_user_speed}%")
        return jsonify({"status": "success", "min": min_user_speed, "max": max_user_speed})
    else:
        return jsonify({"status": "error", "message": "Bad speed values"}), 400


@app.route('/get_status')
def get_current_status():
    """
    # What it does: Provides the current state (mood, speed, depth) to the UI for the visualizers.
    # When it's called: Polled by the frontend every 500ms.
    # What to tweak: You could add more status variables here if the UI needs to display them.
    """
    return jsonify({
        "mood": current_mood,
        "speed": last_stroke_speed,
        "depth": last_depth_pos,
        "min_depth": min_handy_depth,
        "max_depth": max_handy_depth
    })

@app.route('/nudge', methods=['POST'])
def nudge_position():
    """
    # What it does: Moves the Handy by a small, fixed amount during the depth calibration wizard.
    # When it's called: When the user clicks the In/Out buttons during setup.
    # What to tweak: The JOG_STEP_MM and JOG_VELOCITY_MM_PER_SEC constants at the top of the file.
    """
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

    # hdsp/xava is the Handy API endpoint for moving to an absolute position.
    send_handy_command(
        "hdsp/xava",
        {"position": target_mm, "velocity": JOG_VELOCITY_MM_PER_SEC, "stopOnTarget": True},
    )
    current_mm = target_mm
    
    device_depth_percent = mm_to_percent(current_mm)
    
    # Calculate the position as a percentage of the *user's chosen range*, not the full device range.
    if (max_mm - min_mm) > 0:
        user_range_percent = ((current_mm - min_mm) / (max_mm - min_mm)) * 100
    else:
        user_range_percent = 0
        
    last_depth_pos = int(round(user_range_percent))
    return jsonify({"status": "ok", "depth_percent": device_depth_percent})

@app.route('/like_last_move', methods=['POST'])
def user_likes_move():
    """
    # What it does: Takes the last move, asks the LLM to name it, and saves it as a new pattern.
    # When it's called: When the user clicks the "👍 Like" button.
    # What to tweak: The sp_range and dp_range fuzziness (currently +/- 5).
    """
    global last_relative_speed, last_depth_pos, current_mood, session_liked_patterns
    
    pattern_name = name_this_move(last_relative_speed, last_depth_pos, current_mood)
    
    # Create a small range around the liked speed/depth to make the pattern more reusable.
    sp_range = [max(0, last_relative_speed - 5), min(100, last_relative_speed + 5)]
    dp_range = [max(0, last_depth_pos - 5), min(100, last_depth_pos + 5)]

    new_pattern = {
        "name": pattern_name,
        "sp_range": [int(p) for p in sp_range],
        "dp_range": [int(p) for p in dp_range],
        "moods": [current_mood],
        "score": 1
    }
    
    # Add to a temporary list for this session. It gets merged into the main list on save.
    session_liked_patterns.append(new_pattern)
    
    add_message_to_queue(f"(I'll remember you like '{pattern_name}')", add_to_history=False)
    return jsonify({"status": "boosted", "name": pattern_name})

@app.route('/toggle_memories', methods=['POST'])
def toggle_memory_feature():
    """
    # What it does: Toggles the `use_long_term_memory` flag.
    # When it's called: When the user clicks the "Memories: ON/OFF" button.
    # What to tweak: Nothing.
    """
    global use_long_term_memory
    data = request.json
    use_long_term_memory = data.get('use_memories', True)
    print(f"🧠 Memories are now: {'ON' if use_long_term_memory else 'OFF'}")
    return jsonify({"status": "ok", "use_memories": use_long_term_memory})

def start_edging_mode_direct():
    """
    # What it does: A helper to start the edging mode thread.
    # When it's called: By the UI button or by an LLM command.
    """
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
    auto_mode_active_task = AutoModeThread(edging_mode_logic, "Let's play an edging game... try to keep up.")
    auto_mode_active_task.start()

def start_milking_mode_direct():
    """
    # What it does: A helper to start the milking mode thread.
    # When it's called: By the UI button or by an LLM command.
    """
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
    auto_mode_active_task = AutoModeThread(milking_mode_logic, "You're so close... I'm taking over completely now.")
    auto_mode_active_task.start()

@app.route('/send_message', methods=['POST'])
def handle_user_message():
    """
    # What it does: This is the main router for all user input. It parses the message and decides what to do.
    # When it's called: Every time the user sends a chat message.
    # What to tweak: The keyword detection logic (STOP_COMMANDS, AUTO_ON_WORDS, etc.).
    # What will break: This function orchestrates everything. Changes here have wide-ranging effects.
    """
    global auto_mode_active_task, my_persona, chat_history, current_mood, my_patterns, milking_patterns, user_profile
    data = request.json
    user_input_message = data.get('message', '').strip()
    key_from_ui = data.get('key', '').strip()
    if key_from_ui in ("", "__stored__"):
        key_from_ui = None
    new_persona_from_ui = data.get('persona_desc')

    if new_persona_from_ui and new_persona_from_ui != my_persona:
        my_persona = new_persona_from_ui
        add_message_to_queue(f"Okay, my new personality is: {new_persona_from_ui}", add_to_history=False)

    if key_from_ui and key_from_ui != HANDY_KEY:
        set_my_handy_key(key_from_ui)
    if not HANDY_KEY:
        add_message_to_queue("Please put your Handy Key in the box first.", add_to_history=False)
        return jsonify({"status": "no_key_set"})

    if not user_input_message: return jsonify({"status": "empty_message"})

    chat_history.append({"role": "user", "content": user_input_message})
    user_message_lower = user_input_message.lower()

    # --- Command parsing ---
    if any(w in user_message_lower for w in STOP_COMMANDS):
        if auto_mode_active_task:
            auto_mode_active_task.stop()
            auto_mode_active_task.join(timeout=5)
            auto_mode_active_task = None
        move_handy(speed=0)
        add_message_to_queue("Stopping everything.", add_to_history=False)
        return jsonify({"status": "stopped"})
    
    if any(p in user_message_lower for p in EDGING_CUES):
        start_edging_mode_direct()
        return jsonify({"status": "edging_started"})

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

    # If no command was detected, have a normal conversation with the LLM.
    llm_messages_list = [{"role": "system", "content": build_system_prompt()}, *list(chat_history)]
    llm_response = talk_to_llm(llm_messages_list)

    if llm_response.get("initiate_milking_mode") is True:
        process_llm_response(llm_response)
        start_milking_mode_direct()
        return jsonify({"status": "ai_initiated_milking"})

    process_llm_response(llm_response)

    # Only send a move command if we're not in an automatic mode.
    if not auto_mode_active_task:
        move_info = llm_response.get("move")
        if move_info:
            sp_val = move_info.get("sp")
            dp_val = move_info.get("dp")
            rng_val = move_info.get("rng")
            move_handy(sp_val, dp_val, rng_val)

    return jsonify({"status": "ok"})

@app.route('/get_updates')
def get_ui_updates():
    """
    # What it does: The frontend polls this endpoint to get new chat messages and audio clips.
    # When it's called: Every 1.5 seconds by the frontend javascript.
    # What to tweak: Nothing. It just empties the message/audio queues.
    """
    messages_to_send = []
    while messages_for_ui:
        messages_to_send.append(messages_for_ui.popleft())

    if audio_output_queue:
        audio_data_to_send = audio_output_queue.popleft()
        # Send the raw audio bytes directly as a file.
        return send_file(
            io.BytesIO(audio_data_to_send),
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='response.mp3'
        )

    return jsonify({"messages": messages_to_send, "audio_available": False})

@app.route('/start_edging_mode', methods=['POST'])
def start_edging_mode_from_ui():
    """ # What it does: Starts edging mode. Called by the "Edge Me" button. """
    start_edging_mode_direct()
    return jsonify({"status": "edging_started"})

@app.route('/start_milking_mode', methods=['POST'])
def start_milking_mode_from_ui():
    """ # What it does: Starts milking mode. Called by the "Milk Me" button. """
    start_milking_mode_direct()
    return jsonify({"status": "milking_started"})

@app.route('/stop_auto_mode', methods=['POST'])
def stop_auto_mode_from_ui():
    """ # What it does: Stops any active auto/edging/milking mode. Called by "Stop Auto" button. """
    global auto_mode_active_task
    if auto_mode_active_task:
        auto_mode_active_task.stop()
        auto_mode_active_task.join(timeout=5)
        auto_mode_active_task = None
        messages_for_ui.clear()
    move_handy(speed=0)
    return jsonify({"status": "auto_mode_stopped"})

# ─── APP STARTUP ────────────────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    load_my_settings()
    # atexit ensures that settings are saved even if the app is closed unexpectedly.
    atexit.register(save_my_settings)
    print("🚀 Starting my Handy AI app...")
    print("Open http://127.0.0.1:5000 in your web browser.")
    # host='0.0.0.0' makes the app accessible from other devices on your network.
    app.run(host='0.0.0.0', port=5000, debug=False)