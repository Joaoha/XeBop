# =========================================================================
#  Be More Agent 🤖
#  A Local, Offline-First AI Agent for Raspberry Pi
#
#  Copyright (c) 2026 brenpoly
#  Licensed under the MIT License
#  Source: https://github.com/brenpoly/be-more-agent
#
#  DISCLAIMER:
#  This software is provided "as is", without warranty of any kind.
#  This project is a generic framework and includes no copyrighted assets.
# =========================================================================

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import threading
import time
import json
import os
import subprocess
import random
import re
import sys
import select
import traceback
import atexit
import datetime
import warnings
import wave
import struct
import shutil
import uuid

# Suppress harmless library warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="duckduckgo_search")

# Core dependencies
import sounddevice as sd
import numpy as np
import scipy.signal 

# --- AI ENGINES ---
import openwakeword
from openwakeword.model import Model
import ollama

# --- WEB SEARCH (Using your working import) ---
from duckduckgo_search import DDGS

# --- GREETER PIPELINE ---
from greeter.config import load_layered_config
from greeter.directory import resolve_directory_path
from greeter.flow import Employee, GreeterFlow, FlowState, load_employees, resolve_phrase
from greeter.notify import make_notifier
from greeter.visitor_log import VisitorLog

# =========================================================================
# 1. CONFIGURATION & CONSTANTS
# =========================================================================

CONFIG_FILE = "config.json"
SECRETS_FILE = "secrets.json"  # gitignored; deep-merged over config.json (passwords, client secret)
MEMORY_FILE = "memory.json"
BMO_IMAGE_FILE = "current_image.jpg"
GREETER_PERSONA_FILE = "prompts/greeter_persona.txt"
EMPLOYEES_FILE = "employees.json"
ON_THEIR_WAY_DISPLAY_S = 3.0

# HARDWARE SETTINGS
INPUT_DEVICE_NAME = None

# Branding defaults — swappable via config.json "branding" block.
DEFAULT_BRANDING = {
    "agent_name": "XeBop",
    "opening_line": "Hi there! Welcome. What's your name?",
    "wake_word": {
        "model_path": "./wakeword.onnx",
        "threshold": 0.5,
    },
    "faces_dir": "faces",
    "voice_model": "piper/en_GB-semaine-medium.onnx",
}

DEFAULT_CONFIG = {
    "text_model": "gemma3:1b",
    "vision_model": "moondream",
    "voice_model": "piper/en_GB-semaine-medium.onnx",
    "chat_memory": True,
    "camera_rotation": 0,
    "system_prompt_extras": "",
    "input_device": None,
    "input_sample_rate": None,
    "output_device": None,
    "aplay_device": None,
    "phrases": {},  # overrides for greeter lines; see greeter.flow.DEFAULT_PHRASES
    "directory": {
        "source": "local",          # "local" (employees.json) | "m365" (synced cache)
        "m365": {
            "tenant_id": "",
            "client_id": "",
            "host_channel": "email",  # how to notify a synced host: "email" | "teams"
            "cache_path": "m365_directory.json",
            # client_secret lives in secrets.json, not here
        },
    },
    "branding": DEFAULT_BRANDING,
}


def _branding(config):
    user = (config.get("branding") or {})
    merged = {**DEFAULT_BRANDING, **user}
    merged["wake_word"] = {**DEFAULT_BRANDING["wake_word"], **(user.get("wake_word") or {})}
    if "voice_model" not in (config.get("branding") or {}) and "voice_model" in config:
        merged["voice_model"] = config["voice_model"]
    return merged


BRANDING = _branding({})  # placeholder, reset after CURRENT_CONFIG is built below

# LLM SETTINGS
OLLAMA_OPTIONS = {
    'keep_alive': '-1',     
    'num_thread': 4,
    'temperature': 0.7,     
    'top_k': 40,
    'top_p': 0.9
}

def load_config():
    """Build the runtime config: DEFAULT_CONFIG < config.json < secrets.json.

    secrets.json is gitignored and mirrors the config tree, so secrets
    (SMTP password, M365 client secret, web UI password hash) stay out of
    the tracked config.json. See greeter.config for the merge logic.
    """
    return load_layered_config(DEFAULT_CONFIG, CONFIG_FILE, SECRETS_FILE)

CURRENT_CONFIG = load_config()
TEXT_MODEL = CURRENT_CONFIG["text_model"]
VISION_MODEL = CURRENT_CONFIG["vision_model"]
BRANDING = _branding(CURRENT_CONFIG)
WAKE_WORD_MODEL = BRANDING["wake_word"]["model_path"]
WAKE_WORD_THRESHOLD = float(BRANDING["wake_word"]["threshold"])

def resolve_input_device(config):
    requested = config.get("input_device")
    if requested in (None, "", "default"):
        return None

    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"[AUDIO] Device query failed: {e}", flush=True)
        return None

    if isinstance(requested, int) or (isinstance(requested, str) and requested.isdigit()):
        index = int(requested)
        if 0 <= index < len(devices):
            return index
        print(f"[AUDIO] Input device index not found: {index}", flush=True)
        return None

    requested_lower = str(requested).lower()
    for idx, dev in enumerate(devices):
        print(f"[AUDIO DEBUG] Index {idx}: {dev.get('name')} (In: {dev.get('max_input_channels')})", flush=True) # DEBUG LINE
        if dev.get("max_input_channels", 0) > 0 and requested_lower in dev.get("name", "").lower():
            return idx

    print(f"[AUDIO] Input device name not found: {requested}", flush=True)
    return None

INPUT_DEVICE_NAME = resolve_input_device(CURRENT_CONFIG)
if INPUT_DEVICE_NAME is not None:
    try:
        device_info = sd.query_devices(INPUT_DEVICE_NAME)
        print(f"[AUDIO] Using input device: {device_info.get('name', INPUT_DEVICE_NAME)}", flush=True)
    except Exception:
        print(f"[AUDIO] Using input device index: {INPUT_DEVICE_NAME}", flush=True)

def resolve_output_device(config):
    requested = config.get("output_device")
    if requested in (None, "", "default"):
        return None
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"[AUDIO] Device query failed: {e}", flush=True)
        return None
    if isinstance(requested, int) or (isinstance(requested, str) and requested.isdigit()):
        index = int(requested)
        if 0 <= index < len(devices):
            return index
        print(f"[AUDIO] Output device index not found: {index}", flush=True)
        return None
    requested_lower = str(requested).lower()
    for idx, dev in enumerate(devices):
        if dev.get("max_output_channels", 0) > 0 and requested_lower in dev.get("name", "").lower():
            return idx
    print(f"[AUDIO] Output device name not found: {requested}", flush=True)
    return None

OUTPUT_DEVICE_NAME = resolve_output_device(CURRENT_CONFIG)
if OUTPUT_DEVICE_NAME is not None:
    try:
        device_info = sd.query_devices(OUTPUT_DEVICE_NAME)
        print(f"[AUDIO] Using output device: {device_info.get('name', OUTPUT_DEVICE_NAME)}", flush=True)
    except Exception:
        print(f"[AUDIO] Using output device index: {OUTPUT_DEVICE_NAME}", flush=True)

def choose_input_samplerate(device, preferred=None):
    candidates = []
    if preferred:
        candidates.append(preferred)
    try:
        device_info = sd.query_devices(device)
        print(f"[AUDIO DEBUG] Device Info: {device_info}", flush=True) # DEBUG
        if "default_samplerate" in device_info:
            candidates.append(int(device_info["default_samplerate"]))
    except Exception as e:
        print(f"[AUDIO DEBUG] Query failed: {e}", flush=True)
        pass

    candidates.extend([48000, 44100, 32000, 16000])
    seen = set()
    for rate in candidates:
        if not rate or rate in seen:
            continue
        seen.add(rate)
        try:
            sd.check_input_settings(device=device, samplerate=rate, channels=1, dtype="int16")
            return rate
        except Exception:
            continue

    return int(candidates[0]) if candidates else 44100

class BotStates:
    IDLE = "idle"             
    LISTENING = "listening"   
    THINKING = "thinking"     
    SPEAKING = "speaking"     
    ERROR = "error"           
    CAPTURING = "capturing" 
    WARMUP = "warmup"       

# --- SYSTEM PROMPT (greeter persona) ---
def _load_greeter_persona():
    try:
        with open(GREETER_PERSONA_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[INIT] Greeter persona load failed: {e}", flush=True)
        return "You are XeBop, a friendly office reception robot."

SYSTEM_PROMPT = _load_greeter_persona() + "\n\n" + CURRENT_CONFIG.get("system_prompt_extras", "")

# Sound Directories
greeting_sounds_dir = "sounds/greeting_sounds"
ack_sounds_dir = "sounds/ack_sounds"
thinking_sounds_dir = "sounds/thinking_sounds"
error_sounds_dir = "sounds/error_sounds"

# =========================================================================
# 2. GUI CLASS
# =========================================================================

class BotGUI:
    BG_WIDTH, BG_HEIGHT = 800, 480
    OVERLAY_WIDTH, OVERLAY_HEIGHT = 400, 300

    def __init__(self, master):
        self.master = master
        master.title("Pi Assistant")
        master.attributes('-fullscreen', True)
        master.update_idletasks()
        screen_w = master.winfo_screenwidth()
        screen_h = master.winfo_screenheight()
        if screen_w > 0 and screen_h > 0:
            self.BG_WIDTH, self.BG_HEIGHT = screen_w, screen_h
        master.bind('<Escape>', self.exit_fullscreen)
        
        # Inputs
        master.bind('<Return>', self.handle_ptt_toggle)
        master.bind('<space>', self.handle_speaking_interrupt)
        atexit.register(self.safe_exit)
        
        # State
        self.current_state = BotStates.WARMUP
        self.current_volume = 0 
        self.animations = {}
        self.current_frame_index = 0
        self.current_overlay_image = None
        
        self.permanent_memory = self.load_chat_history()
        self.session_memory = []
        self.thinking_sound_active = threading.Event()
        
        self.last_ptt_time = 0 
        self.ptt_event = threading.Event()       
        self.recording_active = threading.Event() 
        self.interrupted = threading.Event() 
        
        self.tts_queue = []          
        self.tts_queue_lock = threading.Lock() 
        self.tts_thread = None       
        self.tts_active = threading.Event()
        self.current_audio_process = None 
        self.exiting = False
        
        # --- GREETER PIPELINE INITIALIZATION ---
        print("[INIT] Loading greeter directory + notifier...", flush=True)
        directory_path = resolve_directory_path(CURRENT_CONFIG, EMPLOYEES_FILE)
        try:
            self.directory = load_employees(directory_path)
            source = (CURRENT_CONFIG.get("directory") or {}).get("source", "local")
            print(f"[INIT] Loaded {len(self.directory)} employees from {directory_path} (source={source}).", flush=True)
        except Exception as e:
            # Missing/corrupt cache (e.g. M365 never synced) must degrade to an
            # empty directory, never block boot.
            print(f"[CRITICAL] Failed to load employees from {directory_path}: {e}", flush=True)
            self.directory = []

        self.notifier = make_notifier(CURRENT_CONFIG)

        log_cfg = CURRENT_CONFIG.get("visitor_log") or {}
        self.visitor_log = VisitorLog(
            path=log_cfg.get("path", "visitor_log.jsonl"),
            mode=log_cfg.get("mode", "minimal"),
            retention_days=int(log_cfg.get("retention_days", 7)),
            salt=log_cfg.get("salt", ""),
        )

        # --- WAKE WORD INITIALIZATION ---
        print("[INIT] Loading Wake Word...", flush=True)
        self.oww_model = None
        if os.path.exists(WAKE_WORD_MODEL):
            try:
                self.oww_model = Model(wakeword_model_paths=[WAKE_WORD_MODEL])
                print("[INIT] Wake Word Loaded.", flush=True)
            except TypeError:
                try:
                    self.oww_model = Model(wakeword_models=[WAKE_WORD_MODEL])
                    print("[INIT] Wake Word Loaded (New API).", flush=True)
                except Exception as e:
                    print(f"[CRITICAL] Failed to load model: {e}")
            except Exception as e:
                print(f"[CRITICAL] Failed to load model: {e}")
        else:
            print(f"[CRITICAL] Model not found: {WAKE_WORD_MODEL}")

        # GUI Setup
        self.background_label = tk.Label(master)
        self.background_label.place(x=0, y=0, width=self.BG_WIDTH, height=self.BG_HEIGHT)
        self.background_label.bind('<Button-1>', self.toggle_hud_visibility) 
        
        self.overlay_label = tk.Label(master, bg='black')
        self.overlay_label.bind('<Button-1>', self.toggle_hud_visibility)
        
        self.response_text = tk.Text(master, height=6, width=60, wrap=tk.WORD, 
                                     state=tk.DISABLED, bg="#ffffff", fg="#000000", font=('Arial', 12)) 
        
        self.status_var = tk.StringVar(value="Initializing...")
        self.status_label = ttk.Label(master, textvariable=self.status_var, background="#2e2e2e", foreground="white")
        
        self.exit_button = ttk.Button(master, text="Exit & Save", command=self.safe_exit)

        self.load_animations()
        self.update_animation() 
        
        threading.Thread(target=self.safe_main_execution, daemon=True).start()

    # --- HELPERS ---

    def extract_json_from_text(self, text):
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return None
        except: return None

    def safe_exit(self):
        if self.exiting:
            return
        self.exiting = True
        print("\n--- SHUTDOWN SEQUENCE ---", flush=True)
        if self.current_audio_process:
            try:
                self.current_audio_process.terminate()
                self.current_audio_process.wait(timeout=1)
            except: pass

        self.recording_active.clear()
        self.thinking_sound_active.clear()
        self.tts_active.clear() 
        
        self.save_chat_history()
        
        try:
            ollama.generate(model=TEXT_MODEL, prompt="", keep_alive=0)
        except: pass
        try:
            sd.stop()
        except: pass

        try:
            self.master.quit()
        except Exception:
            pass
        
    def exit_fullscreen(self, event=None):
        self.master.attributes('-fullscreen', False)
        self.safe_exit()

    def toggle_hud_visibility(self, event=None):
        try:
            if self.response_text.winfo_ismapped():
                self.response_text.place_forget()
                self.status_label.place_forget()
                self.exit_button.place_forget()
            else:
                self.response_text.place(relx=0.5, rely=0.82, anchor=tk.S)
                self.status_label.place(relx=0.5, rely=1.0, anchor=tk.S, relwidth=1)
                self.exit_button.place(x=10, y=10)
        except tk.TclError: pass

    def handle_ptt_toggle(self, event=None):
        current_time = time.time()
        if current_time - self.last_ptt_time < 0.5: 
            return 
        self.last_ptt_time = current_time

        if self.recording_active.is_set():
            print("[PTT] Toggle OFF", flush=True)
            self.recording_active.clear() 
        else:
            if self.current_state == BotStates.IDLE or "Wait" in self.status_var.get():
                print("[PTT] Toggle ON", flush=True)
                self.recording_active.set() 
                self.ptt_event.set()

    def handle_speaking_interrupt(self, event=None):
        if self.current_state == BotStates.SPEAKING or self.current_state == BotStates.THINKING:
            self.interrupted.set()
            self.thinking_sound_active.clear()
            with self.tts_queue_lock:
                self.tts_queue.clear()
            if self.current_audio_process:
                try: self.current_audio_process.terminate()
                except: pass
            self.set_state(BotStates.IDLE, "Interrupted.")

    def load_animations(self):
        base_path = BRANDING.get("faces_dir", "faces")
        states = ["idle", "listening", "thinking", "speaking", "error", "capturing", "warmup"] 
        for state in states:
            folder = os.path.join(base_path, state)
            self.animations[state] = []
            if os.path.exists(folder):
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.png')])
                for f in files:
                    src = Image.open(os.path.join(folder, f))
                    scale = min(self.BG_WIDTH / src.width, self.BG_HEIGHT / src.height)
                    fw, fh = max(1, int(src.width * scale)), max(1, int(src.height * scale))
                    canvas = Image.new('RGB', (self.BG_WIDTH, self.BG_HEIGHT), color='black')
                    canvas.paste(src.resize((fw, fh)), ((self.BG_WIDTH - fw) // 2, (self.BG_HEIGHT - fh) // 2))
                    self.animations[state].append(ImageTk.PhotoImage(canvas))
            if not self.animations[state]:
                if state in self.animations.get("idle", []):
                     self.animations[state] = self.animations["idle"]
                else:
                    # Blue screen fallback
                    blank = Image.new('RGB', (self.BG_WIDTH, self.BG_HEIGHT), color='#0000FF')
                    self.animations[state].append(ImageTk.PhotoImage(blank))

    def update_animation(self):
        frames = self.animations.get(self.current_state, []) or self.animations.get(BotStates.IDLE, [])
        if not frames:
            self.master.after(500, self.update_animation)
            return

        if self.current_state == BotStates.SPEAKING:
            if len(frames) > 1:
                self.current_frame_index = random.randint(1, len(frames) - 1)
            else:
                self.current_frame_index = 0 
        else:
            self.current_frame_index = (self.current_frame_index + 1) % len(frames)

        self.background_label.config(image=frames[self.current_frame_index])
        
        speed = 50 if self.current_state == BotStates.SPEAKING else 500
        self.master.after(speed, self.update_animation)

    def set_state(self, state, msg="", cam_path=None):
        def _update():
            if msg: print(f"[STATE] {state.upper()}: {msg}", flush=True)
            if self.current_state != state:
                self.current_state = state
                self.current_frame_index = 0
            if msg: self.status_var.set(msg)
            if cam_path and os.path.exists(cam_path) and state in [BotStates.THINKING, BotStates.SPEAKING]:
                try:
                    img = Image.open(cam_path).resize((self.OVERLAY_WIDTH, self.OVERLAY_HEIGHT))
                    self.current_overlay_image = ImageTk.PhotoImage(img)
                    self.overlay_label.config(image=self.current_overlay_image)
                    self.overlay_label.place(x=200, y=90)
                except: pass
            else:
                self.overlay_label.place_forget()
        self.master.after(0, _update)

    def append_to_text(self, text, newline=True):
        def _update():
            self.response_text.config(state=tk.NORMAL)
            if newline: 
                self.response_text.insert(tk.END, text + "\n")
            else: 
                self.response_text.insert(tk.END, text)
            
            self.response_text.see(tk.END)
            self.response_text.config(state=tk.DISABLED)
            
        self.master.after(0, _update)

    def _stream_to_text(self, chunk):
        def update_text_stream():
            self.response_text.config(state=tk.NORMAL)
            self.response_text.insert(tk.END, chunk)
            self.response_text.see(tk.END) 
            self.response_text.config(state=tk.DISABLED)
        self.master.after(0, update_text_stream)

    # =========================================================================
    # 3. GREETER SESSION
    # =========================================================================

    def _enqueue_speech(self, text):
        """Push a line to the TTS queue and mirror it to the on-screen HUD."""
        if not text:
            return
        self.append_to_text(f"BOT: {text}")
        with self.tts_queue_lock:
            self.tts_queue.append(text)

    def _capture_one_utterance(self, trigger_source):
        """Listen once and return transcribed text (or empty string)."""
        self.set_state(BotStates.LISTENING, "I'm listening!")
        if trigger_source == "PTT":
            audio_file = self.record_voice_ptt()
        else:
            audio_file = self.record_voice_adaptive()
        if not audio_file:
            return ""
        text = self.transcribe_audio(audio_file)
        if text:
            self.append_to_text(f"YOU: {text}")
        return text

    def run_greeter_session(self, first_trigger_source):
        """Run one visitor conversation: greet → ask host → confirm → notify."""
        flow = GreeterFlow(
            directory=self.directory,
            notifier=self.notifier,
            event_logger=self.visitor_log.record,
            on_check_in=self._on_check_in,
            open_visit_lookup=self.visitor_log.find_open_visit,
            on_check_out=self._on_check_out,
            opening_line=BRANDING.get("opening_line"),
            phrases=CURRENT_CONFIG.get("phrases") or {},
        )

        opening = flow.start()
        self.set_state(BotStates.SPEAKING, "Greeting visitor...")
        self._enqueue_speech(opening.say)

        trigger_source = first_trigger_source
        max_turns = 8  # belt + suspenders cap on a stuck conversation

        for _ in range(max_turns):
            self.wait_for_tts()
            if self.interrupted.is_set():
                self.interrupted.clear()
                break

            user_text = self._capture_one_utterance(trigger_source)
            # Subsequent turns within a session use adaptive listening (no PTT).
            trigger_source = "ADAPTIVE"
            if not user_text:
                self._enqueue_speech(resolve_phrase(CURRENT_CONFIG.get("phrases") or {}, "didnt_catch"))
                continue

            result = flow.handle(user_text)
            self.set_state(BotStates.SPEAKING, "Speaking...")
            self._enqueue_speech(result.say)

            if result.done:
                self.wait_for_tts()
                self.set_state(BotStates.IDLE, "Done")
                time.sleep(ON_THEIR_WAY_DISPLAY_S)
                break

        self.wait_for_tts()
        self.set_state(BotStates.IDLE, "Ready")

    # =========================================================================
    # 3a. (legacy) ACTION ROUTER — unused by greeter; kept for tooling reuse
    # =========================================================================

    def execute_action_and_get_result(self, action_data):
        raw_action = action_data.get("action", "").lower().strip()
        value = action_data.get("value") or action_data.get("query")
        
        VALID_TOOLS = {
            "get_time", "search_web", "capture_image"
        }
        
        ALIASES = {
            "google": "search_web", "browser": "search_web", "news": "search_web",         
            "search_news": "search_web", "look": "capture_image", "see": "capture_image", 
            "check_time": "get_time"
        }

        action = ALIASES.get(raw_action, raw_action)
        print(f"ACTION: {raw_action} -> {action}", flush=True)

        if action not in VALID_TOOLS:
            if value and isinstance(value, str) and len(value.split()) > 1:
                return f"CHAT_FALLBACK::{value}"
            return "INVALID_ACTION"

        if action == "get_time":
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"The current time is {now}."
        
        elif action == "search_web":
            print(f"Searching web for: {value}...", flush=True)
            try:
                # 'us-en' region is often more stable for CLI queries
                with DDGS() as ddgs:
                    results = []
                    # 1. News search
                    try:
                        results = list(ddgs.news(value, region='us-en', max_results=1))
                        if results: 
                            print(f"[DEBUG] Found News: {results[0].get('title')}", flush=True)
                    except Exception as e: 
                        print(f"[DEBUG] News Search Error: {e}", flush=True)
                    
                    # 2. Text fallback
                    if not results:
                        print("[DEBUG] No news found, trying text search...", flush=True)
                        try: 
                            results = list(ddgs.text(value, region='us-en', max_results=1))
                            if results: 
                                print(f"[DEBUG] Found Text: {results[0].get('title')}", flush=True)
                        except Exception as e:
                             print(f"[DEBUG] Text Search Error: {e}", flush=True)

                    if results:
                        r = results[0]
                        # Safe get
                        title = r.get('title', 'No Title')
                        body = r.get('body', r.get('snippet', 'No Body'))
                        return f"SEARCH RESULTS for '{value}':\nTitle: {title}\nSnippet: {body[:300]}"
                    else: 
                        print(f"[DEBUG] Search returned 0 results.", flush=True)
                        return "SEARCH_EMPTY"
            except Exception as e:
                print(f"[DEBUG] Connection/Library Error: {e}", flush=True)
                return "SEARCH_ERROR"
        
        elif action == "capture_image":
             return "IMAGE_CAPTURE_TRIGGERED"

        return None

    # =========================================================================
    # 4. CORE LOGIC
    # =========================================================================

    def safe_main_execution(self):
        try:
            self.warm_up_logic()
            self.tts_active.set()
            self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
            self.tts_thread.start()
            
            while True:
                trigger_source = self.detect_wake_word_or_ptt()
                if self.interrupted.is_set():
                    self.interrupted.clear()
                    self.set_state(BotStates.IDLE, "Resetting...")
                    continue

                self.run_greeter_session(trigger_source)

                # Cooldown after a session so the tail of our own TTS / room
                # echo doesn't immediately retrigger the wake-word detector.
                time.sleep(1.5)

        except Exception as e:
            traceback.print_exc()
            self.set_state(BotStates.ERROR, f"Fatal Error: {str(e)[:40]}")

    def warm_up_logic(self):
        self.set_state(BotStates.WARMUP, "Warming up brains...")
        try:
            ollama.generate(model=TEXT_MODEL, prompt="", keep_alive=-1)
        except Exception as e:
            print(f"Failed to load {TEXT_MODEL}: {e}", flush=True)
        self.play_sound(self.get_random_sound(greeting_sounds_dir))
        print("Models loaded.", flush=True)

    def detect_wake_word_or_ptt(self):
        self.set_state(BotStates.IDLE, "Waiting...")
        self.ptt_event.clear()
        
        if self.oww_model: self.oww_model.reset()

        if self.oww_model is None:
            self.ptt_event.wait()
            self.ptt_event.clear()
            return "PTT"

        CHUNK_SIZE = 1280
        OWW_SAMPLE_RATE = 16000

        input_rate = choose_input_samplerate(INPUT_DEVICE_NAME, CURRENT_CONFIG.get("input_sample_rate"))
        use_resampling = (input_rate != OWW_SAMPLE_RATE)
        input_chunk_size = int(CHUNK_SIZE * (input_rate / OWW_SAMPLE_RATE)) if use_resampling else CHUNK_SIZE

        stream_args = {
            "samplerate": input_rate, 
            "channels": 1, 
            "dtype": 'int16', 
            "blocksize": input_chunk_size, 
            "device": INPUT_DEVICE_NAME
        }

        # Try to find a compatible block size and sample rate
        try:
            # First attempt: standard settings
            self._listen_loop(stream_args, input_chunk_size, CHUNK_SIZE, use_resampling)
        except StopIteration as si:
            return str(si)
        except Exception as e:
            print(f"[AUDIO] Stream failed with defaults: {e}. Retrying with loose settings...", flush=True)
            try:
                # Second attempt: Let PortAudio decide blocksize (0) and latency
                stream_args["blocksize"] = 0 
                stream_args["latency"] = "high"
                # If blocksize is variable, we must read specific amounts manually or handle buffering.
                # Simplest fallback: Just attempt small fixed block
                stream_args["blocksize"] = 1024
                use_resampling = True
                
                self._listen_loop(stream_args, 1024, CHUNK_SIZE, use_resampling)
            except StopIteration as si:
                return str(si)
            except Exception as e2:
                print(f"[CRITICAL] Wake Word Stream Error: {e2}")
                self.ptt_event.wait()
                return "PTT"
        
        return "WAKE"

    def _listen_loop(self, stream_args, input_chunk_size, target_chunk_size, use_resampling):
        # Force software backend (no mmap) via environment variable if possible, 
        # but here we can try to hint loop settings.
        # However, the most effective fix for ALSA mmap issues is often just asking for 'blocksize=0' 
        # and letting portaudio manage the buffering, OR very small chunks.
        
        # Let's try to be less aggressive with reads.
        
         with sd.InputStream(**stream_args) as stream:
                print(f"[AUDIO] Listening with rate {stream_args['samplerate']} and block {stream_args['blocksize']}", flush=True)
                
                # Pre-allocate buffer for speed
                # If blocksize is 0, we read what is available.

                # "Press Enter to talk" only makes sense on an interactive TTY.
                # Under systemd/autostart stdin is /dev/null, which select()
                # always reports readable (instant EOF) — that would fire CLI
                # triggers in a loop and bypass the wake word. Guard on isatty.
                try:
                    interactive_stdin = bool(sys.stdin) and sys.stdin.isatty()
                except Exception:
                    interactive_stdin = False

                while True:
                    if self.ptt_event.is_set():
                        self.ptt_event.clear()
                        raise StopIteration("PTT")

                    if interactive_stdin:
                        rlist, _, _ = select.select([sys.stdin], [], [], 0.001)
                        if rlist:
                            sys.stdin.readline()
                            raise StopIteration("CLI")

                    # If fallback mode (blocksize 0), read fixed amount
                    read_size = input_chunk_size
                    if stream_args.get('blocksize') == 0:
                        read_size = 1024 # Safe small read
                    
                    try:
                        data, overflow = stream.read(read_size)
                        if overflow:
                            print("!", end="", flush=True) 
                            # If we overflow excessively, raise error to trigger fallback to SAFE MODE (PulseAudio/Software)
                            # We can use a simple counter attached to the function or object, but here raising immediately 
                            # after a few in a row is safest.
                            raise RuntimeError("Audio Buffer Overflow - Triggering Safe Mode")
                    except Exception as e:
                        # Convert uncatchable PaErrorCode wrapper to standard Exception if needed
                        # But honestly, `raise e` should work... unless it's a SystemExit?
                        # Let's wrap it in a new exception to be sure it bubbles up
                        raise RuntimeError(f"Audio read failed: {e}")

                    audio_data = np.frombuffer(data, dtype=np.int16)

                    # Ensure flattening for openwakeword compatibility
                    if audio_data.ndim > 1:
                        audio_data = audio_data.flatten()

                    if use_resampling:
                        # FAST RESAMPLING: Nearest-neighbor slicing instead of scipy.signal.resample
                        # This avoids the CPU bottleneck that causes overflow (!!!!!!!) on Raspberry Pi
                        step = len(audio_data) / target_chunk_size
                        indices = np.arange(0, len(audio_data), step)[:target_chunk_size].astype(int)
                        audio_data = audio_data[indices]
                    
                    # Convert to float for model prediction without needing heavy resampling logic
                    # The wake word model needs 16000, which we just faked above.
                    
                    current_max = np.max(np.abs(audio_data))

                    # openwakeword is STATEFUL: it builds a rolling mel/embedding
                    # buffer and needs every consecutive frame. Gating predict()
                    # on volume (the old `if current_max > 200`) leaves gaps that
                    # stop the score from ever building, so the wake word never
                    # fires. Always feed frames; the Pi 5 handles it fine.
                    self.oww_model.predict(audio_data)
                    for mdl in self.oww_model.prediction_buffer.keys():
                        score = list(self.oww_model.prediction_buffer[mdl])[-1]
                        if score > 0.1:  # surface near-misses for tuning
                            print(f"\r[Oww] Score: {score:.3f} | Vol: {current_max}   ", end="", flush=True)

                        if score > WAKE_WORD_THRESHOLD:
                            print(f"\n[WAKE] Triggered on '{mdl}' with score: {score:.2f}", flush=True)
                            self.oww_model.reset()
                            return  # Success


    def record_voice_adaptive(self, filename="input.wav"):
        print("Recording (Adaptive)...", flush=True)
        time.sleep(0.5) 
        samplerate = choose_input_samplerate(INPUT_DEVICE_NAME, CURRENT_CONFIG.get("input_sample_rate"))

        silence_threshold = 0.006
        silence_duration = 2.5
        max_record_time = 30.0
        buffer = []
        silent_chunks = 0
        chunk_duration = 0.05 
        chunk_size = int(samplerate * chunk_duration)
        
        num_silent_chunks = int(silence_duration / chunk_duration)
        max_chunks = int(max_record_time / chunk_duration)
        recorded_chunks = 0
        silence_started = False

        def callback(indata, frames, time_info, status):
            nonlocal silent_chunks, recorded_chunks, silence_started
            volume_norm = np.linalg.norm(indata) / np.sqrt(len(indata))
            buffer.append(indata.copy())  
            recorded_chunks += 1
            if recorded_chunks < 5: return 
            if volume_norm < silence_threshold:
                silent_chunks += 1
                if silent_chunks >= num_silent_chunks: silence_started = True
            else: silent_chunks = 0

        try:
            # Explicitly close stream if it exists to free hardware
            sd.stop()
            time.sleep(0.2)
            
            with sd.InputStream(samplerate=samplerate, channels=1, callback=callback, 
                                device=INPUT_DEVICE_NAME, blocksize=chunk_size): 
                while not silence_started and recorded_chunks < max_chunks:
                    sd.sleep(int(chunk_duration * 1000))
        except Exception as e: 
            print(f"[AUDIO ERROR] Adaptive Recording Failed: {e}", flush=True)
            return None 
        
        return self.save_audio_buffer(buffer, filename, samplerate)

    def record_voice_ptt(self, filename="input.wav"):
        print("Recording (PTT)...", flush=True)
        time.sleep(0.5)
        samplerate = choose_input_samplerate(INPUT_DEVICE_NAME, CURRENT_CONFIG.get("input_sample_rate"))

        buffer = []
        def callback(indata, frames, time_info, status): buffer.append(indata.copy())
        
        try:
            # Explicitly close stream if it exists to free hardware
            # This is critical on Pi 5 where hardware contention causes freezes
            sd.stop() 
            time.sleep(0.2)
            
            with sd.InputStream(samplerate=samplerate, channels=1, callback=callback, device=INPUT_DEVICE_NAME):
                while self.recording_active.is_set(): 
                    sd.sleep(50)
        except Exception as e: 
            print(f"[AUDIO ERROR] PTT Recording Failed: {e}", flush=True)
            return None
            
        return self.save_audio_buffer(buffer, filename, samplerate)

    def save_audio_buffer(self, buffer, filename, samplerate=16000):
        if not buffer: return None
        audio_data = np.concatenate(buffer, axis=0).flatten()
        audio_data = np.nan_to_num(audio_data, nan=0.0, posinf=0.0, neginf=0.0)
        audio_data = (audio_data * 32767).astype(np.int16)
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(audio_data.tobytes())
        self.play_sound(self.get_random_sound(ack_sounds_dir))
        return filename

    def transcribe_audio(self, filename):
        print("Transcribing...", flush=True)
        try:
            result = subprocess.run(
                ["./whisper.cpp/build/bin/whisper-cli", "-m", "./whisper.cpp/models/ggml-small.en.bin", "-l", "en", "-t", "4", "-f", filename],
                capture_output=True, text=True
            )
            transcription_lines = result.stdout.strip().split('\n')
            if transcription_lines and transcription_lines[-1].strip():
                last_line = transcription_lines[-1].strip()
                if ']' in last_line: transcription = last_line.split("]")[1].strip()
                else: transcription = last_line
            else: transcription = ""
            print(f"Heard: '{transcription}'", flush=True)
            return transcription.strip()
        except Exception as e:
            print(f"Transcription Error: {e}")
            return ""

    def capture_image(self):
        self.set_state(BotStates.CAPTURING, "Watching...")
        try:
            subprocess.run(["rpicam-still", "-t", "500", "-n", "--width", "640", "--height", "480", "-o", BMO_IMAGE_FILE], check=True)
            rotation = CURRENT_CONFIG.get("camera_rotation", 0)
            if rotation != 0:
                img = Image.open(BMO_IMAGE_FILE)
                img = img.rotate(rotation, expand=True) 
                img.save(BMO_IMAGE_FILE)
            return BMO_IMAGE_FILE
        except Exception as e:
            print(f"Camera Error: {e}")
            return None

    def capture_visitor_photo(self, visit_id):
        """Snap a per-visit photo to <photo_dir>/<visit_id>.jpg.

        Unlike capture_image() this does NOT clobber the LLM's current_image.jpg.
        Returns the path, or None if disabled or the camera fails (a camera
        problem must never block a check-in).
        """
        log_cfg = CURRENT_CONFIG.get("visitor_log") or {}
        if not log_cfg.get("capture_photo", True):
            return None
        photo_dir = log_cfg.get("photo_dir", "visitor_photos")
        path = os.path.join(photo_dir, f"{visit_id}.jpg")
        try:
            os.makedirs(photo_dir, exist_ok=True)
            subprocess.run(
                ["rpicam-still", "-t", "500", "-n", "--width", "640", "--height", "480", "-o", path],
                check=True,
            )
            rotation = CURRENT_CONFIG.get("camera_rotation", 0)
            if rotation != 0:
                img = Image.open(path)
                img = img.rotate(rotation, expand=True)
                img.save(path)
            return path
        except Exception as e:
            print(f"[PHOTO] Visitor photo capture failed: {e}", flush=True)
            return None

    def _on_check_in(self, visitor_name, host):
        """Flow hook: open a visit record with a per-visit photo."""
        visit_id = uuid.uuid4().hex[:12]
        photo = self.capture_visitor_photo(visit_id)
        try:
            self.visitor_log.check_in(visitor_name, host, photo=photo, visit_id=visit_id)
        except Exception as e:
            print(f"[VISIT] check_in failed: {e}", flush=True)

    def _on_check_out(self, visit, visitor_name):
        """Flow hook: close the visit and tell the host their guest has left."""
        try:
            self.visitor_log.check_out(visit.get("visit_id"))
        except Exception as e:
            print(f"[VISIT] check_out failed: {e}", flush=True)
        host_name = visit.get("host")
        host_channel = visit.get("host_channel_id")
        if host_name and host_channel:
            msg = resolve_phrase(
                CURRENT_CONFIG.get("phrases") or {}, "exit_notice",
                visitor=visitor_name, host=host_name,
            )
            try:
                self.notifier(Employee(host_name, "", (), host_channel), msg)
            except Exception as e:
                print(f"[VISIT] exit notify failed: {e}", flush=True)

    # =========================================================================
    # 5. TTS PLUMBING
    # =========================================================================

    def wait_for_tts(self):
        while self.tts_queue or self.tts_active.is_set():
            if self.interrupted.is_set(): break
            time.sleep(0.1)

    def _tts_worker(self):
        while True:
            text = None
            with self.tts_queue_lock:
                if self.tts_queue: 
                    text = self.tts_queue.pop(0)
                    self.tts_active.set() 
            if text: 
                self.speak(text)
                self.tts_active.clear() 
            else: time.sleep(0.05)

    def speak(self, text):
        """Speak using Piper, then play the generated WAV via ALSA/aplay.

        Piper renders to a WAV which aplay plays directly through ALSA. This
        avoids Python sounddevice output issues with some USB DACs (e.g. the
        UACDemo speaker), where the streaming RawOutputStream path stayed
        silent on the Pi. The ALSA device is set by the "aplay_device" config
        key (e.g. "plughw:CARD=UACDemoV10,DEV=0"); when unset, aplay uses the
        system default.
        """
        clean = re.sub(r"[^\w\s,.!?:-]", "", text)
        if not clean.strip(): return

        print(f"[PIPER SPEAKING] '{clean}'", flush=True)
        voice_model = BRANDING.get("voice_model") or CURRENT_CONFIG.get("voice_model", "piper/en_GB-semaine-medium.onnx")

        # Prefer a piper on PATH (matches the deployed Pi); fall back to the
        # binary setup.sh installs into ./piper/ on a fresh flash.
        piper_bin = "piper" if shutil.which("piper") else "./piper/piper"
        aplay_device = CURRENT_CONFIG.get("aplay_device")
        wav_path = "/tmp/xebop_piper_speech.wav"

        try:
            result = subprocess.run(
                [piper_bin, "--model", voice_model, "--output_file", wav_path],
                input=clean, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if result.returncode != 0:
                print(f"[PIPER ERROR] Piper failed: {result.stderr}", flush=True)
                return

            aplay_cmd = ["aplay", "-q"]
            if aplay_device:
                aplay_cmd += ["-D", aplay_device]
            aplay_cmd.append(wav_path)
            subprocess.run(aplay_cmd, check=False)

        except Exception as e:
            print(f"[PIPER ERROR] speak failed: {e}", flush=True)

    def _run_thinking_sound_loop(self):
        time.sleep(0.5)
        while self.thinking_sound_active.is_set():
            sound = self.get_random_sound(thinking_sounds_dir)
            if sound: self.play_sound(sound)
            for _ in range(50):
                if not self.thinking_sound_active.is_set(): return
                time.sleep(0.1)

    def get_random_sound(self, directory):
        if os.path.exists(directory):
            files = [f for f in os.listdir(directory) if f.endswith(".wav")]
            return os.path.join(directory, random.choice(files)) if files else None
        return None

    def play_sound(self, file_path):
        if not file_path or not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'rb') as wf:
                file_sr = wf.getframerate()
                data = wf.readframes(wf.getnframes())
                audio = np.frombuffer(data, dtype=np.int16)

            try:
                device_info = sd.query_devices(OUTPUT_DEVICE_NAME, kind='output') if OUTPUT_DEVICE_NAME is not None else sd.query_devices(kind='output')
                native_rate = int(device_info['default_samplerate'])
            except:
                native_rate = 48000

            playback_rate = file_sr
            try:
                sd.check_output_settings(device=OUTPUT_DEVICE_NAME, samplerate=file_sr)
            except:
                playback_rate = native_rate
                num_samples = int(len(audio) * (native_rate / file_sr))
                audio = scipy.signal.resample(audio, num_samples).astype(np.int16)

            sd.play(audio, playback_rate, device=OUTPUT_DEVICE_NAME)
            sd.wait()
        except: pass

    def load_chat_history(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r") as f: return json.load(f)
            except: pass
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    def save_chat_history(self):
        full = self.permanent_memory + self.session_memory
        conv = full[1:]
        if len(conv) > 10: conv = conv[-10:]
        with open(MEMORY_FILE, "w") as f: 
            json.dump([full[0]] + conv, f, indent=4)

if __name__ == "__main__":
    print("--- SYSTEM STARTING ---", flush=True)
    root = tk.Tk()
    app = BotGUI(root)
    root.mainloop()
