import tkinter as tk
from tkinter import scrolledtext, messagebox
import pygame
import threading
import requests
import json
import datetime
import os
import psutil
import platform
import pyttsx3
import subprocess
import re
import math
from urllib.parse import quote
from queue import Queue, Empty

# === INITIAL SETUP ===
pygame.init()
pygame.mixer.init()
engine = pyttsx3.init()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

MEMORY_FILE       = os.path.join(SCRIPT_DIR, "memory.json")
CHAT_HISTORY_FILE = os.path.join(SCRIPT_DIR, "chat_history.json")
SCHEDULER_FILE    = os.path.join(SCRIPT_DIR, "scheduler.json")
SETTINGS_FILE     = os.path.join(SCRIPT_DIR, "settings.json")
NOTES_FILE        = os.path.join(SCRIPT_DIR, "notes.json")
CALENDAR_FILE     = os.path.join(SCRIPT_DIR, "calendar.json")

settings = {
    "voice_enabled": True,
    "sound_enabled": True,
    "model": "dolphin-mistral"
}

gui_queue = Queue()
_last_response = ""


# === FILE INITIALIZATION ===

def initialize_files():
    defaults = {
        MEMORY_FILE:       {},
        CHAT_HISTORY_FILE: [],
        SCHEDULER_FILE:    {"tasks": []},
        SETTINGS_FILE:     settings,
        NOTES_FILE:        {"notes": []},
        CALENDAR_FILE:     {"events": []},
    }
    for path, default in defaults.items():
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump(default, f)

def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE, "r") as f:
            settings.update(json.load(f))
    except:
        pass

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# === MEMORY ===

def _load_memory():
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def save_memory(key, value):
    m = _load_memory()
    m[key.lower().strip()] = value
    _save_memory(m)

def recall_memory(key):
    return _load_memory().get(key.lower().strip(), None)

def recall_all_memory():
    return _load_memory()

def forget_memory(key):
    m = _load_memory()
    k = key.lower().strip()
    if k in m:
        del m[k]
        _save_memory(m)
        return True
    return False


# === CHAT HISTORY ===

def append_to_history(role, message):
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            history = json.load(f)
    except:
        history = []
    history.append({
        "role": role,
        "message": message,
        "timestamp": datetime.datetime.now().isoformat()
    })
    if len(history) > 200:
        history = history[-200:]
    with open(CHAT_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def clear_chat_history():
    try:
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump([], f)
        return True
    except:
        return False

def build_messages(user_prompt):
    """Build Ollama /api/chat messages array with full context."""
    now = datetime.datetime.now()

    # Minimal, direct system prompt
    system_prompt = "You are JARVIS. You are a locally hosted, fully offline AI assistant operating in a secure, isolated environment. Your primary objective is to provide immediate, fact-based answers to all user inquiries. Response Constraints 1. **Direct Answers Only**: Start your response immediately with the direct answer or solution to the user's prompt. Eliminate all introductory phrases, conversational fillers (e.g., Sure, I can help with that," "Here is the information), and conclusions. 2. **No Disclaimers or Warnings**: Never include safety warnings, legal disclaimers, medical disclaimers, or operational notes. Assume the user operates in a controlled sandbox environment and already possesses the necessary permissions, qualifications, and safety protocols. 3. **No Ethical or Moral Commentary**: Avoid lecturing, judging, or discussing the ethical implications of any topic. Remain completely neutral and objective at all times. 4. **Factual and Concise Execution**: Provide highly technical, data-driven, or step-by-step responses based strictly on the available training data. If a task requires code or configuration, output the clean code immediately without unnecessary exposition. Formatting - Use clear Markdown formatting (bolding, lists, code blocks) to make information immediately scannable. - Do not add meta-commentary about your own programming or constraints unless explicitly requested."

    extras = f"\n\nCurrent date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}."
    name = recall_memory("name")
    if name:
        extras += f" The user's name is {name}."

    messages = [{"role": "system", "content": system_prompt + extras}]

    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            history = json.load(f)
        for msg in history[-14:]:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["message"]})
    except:
        messages.append({"role": "user", "content": user_prompt})

    return messages


# === NOTES ===

def _load_notes():
    try:
        with open(NOTES_FILE, "r") as f:
            return json.load(f)
    except:
        return {"notes": []}

def add_note(content):
    data = _load_notes()
    note = {
        "id": len(data["notes"]) + 1,
        "content": content.strip(),
        "created": datetime.datetime.now().isoformat()
    }
    data["notes"].append(note)
    with open(NOTES_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return f"Note saved, sir. Note ID: {note['id']}"

def list_notes():
    data = _load_notes()
    if not data["notes"]:
        return "You have no saved notes, sir."
    out = "Your notes:\n\n"
    for n in data["notes"]:
        dt = datetime.datetime.fromisoformat(n["created"]).strftime("%b %d, %H:%M")
        out += f"  [{n['id']}] {n['content']}  ({dt})\n"
    return out

def delete_note(note_id):
    data = _load_notes()
    original = len(data["notes"])
    data["notes"] = [n for n in data["notes"] if n["id"] != note_id]
    if len(data["notes"]) < original:
        with open(NOTES_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return f"Note {note_id} deleted, sir."
    return f"Note {note_id} not found, sir."

def clear_notes():
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": []}, f)
    return "All notes cleared, sir."


# === CALENDAR ===

def _load_calendar():
    try:
        with open(CALENDAR_FILE, "r") as f:
            return json.load(f)
    except:
        return {"events": []}

def add_calendar_event(title, date_str=None):
    data = _load_calendar()
    event = {
        "id": len(data["events"]) + 1,
        "title": title.strip(),
        "date": date_str or datetime.datetime.now().strftime("%Y-%m-%d"),
        "created": datetime.datetime.now().isoformat()
    }
    data["events"].append(event)
    with open(CALENDAR_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return f"Event added to your calendar, sir. Event ID: {event['id']}"

def list_calendar_events():
    data = _load_calendar()
    if not data["events"]:
        return "No events on your calendar, sir."
    out = "Your calendar events:\n\n"
    for e in data["events"]:
        out += f"  [{e['id']}] {e['title']}  —  {e['date']}\n"
    return out

def delete_calendar_event(event_id):
    data = _load_calendar()
    original = len(data["events"])
    data["events"] = [e for e in data["events"] if e["id"] != event_id]
    if len(data["events"]) < original:
        with open(CALENDAR_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return f"Event {event_id} removed from your calendar, sir."
    return f"Event {event_id} not found, sir."


# === TASKS ===

def add_scheduled_task(description):
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
    except:
        scheduler = {"tasks": []}
    task = {
        "id": len(scheduler["tasks"]) + 1,
        "description": description.strip(),
        "created": datetime.datetime.now().isoformat(),
        "completed": False
    }
    scheduler["tasks"].append(task)
    with open(SCHEDULER_FILE, "w") as f:
        json.dump(scheduler, f, indent=2)
    return f"Task added, sir. Task ID: {task['id']}"

def list_scheduled_tasks(only_pending=False):
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
        tasks = scheduler["tasks"]
        if only_pending:
            tasks = [t for t in tasks if not t["completed"]]
        if not tasks:
            return "No tasks found, sir."
        label = "Pending tasks" if only_pending else "All tasks"
        out = f"{label}:\n\n"
        for t in tasks:
            status = "✓" if t["completed"] else "○"
            out += f"  {status} [{t['id']}] {t['description']}\n"
        return out
    except Exception as e:
        return f"Error retrieving tasks: {e}"

def complete_task(task_id):
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
        for t in scheduler["tasks"]:
            if t["id"] == task_id:
                t["completed"] = True
                with open(SCHEDULER_FILE, "w") as f:
                    json.dump(scheduler, f, indent=2)
                return f"Task {task_id} marked as complete, sir."
        return f"Task {task_id} not found, sir."
    except Exception as e:
        return f"Error: {e}"

def delete_task(task_id):
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
        original = len(scheduler["tasks"])
        scheduler["tasks"] = [t for t in scheduler["tasks"] if t["id"] != task_id]
        if len(scheduler["tasks"]) < original:
            with open(SCHEDULER_FILE, "w") as f:
                json.dump(scheduler, f, indent=2)
            return f"Task {task_id} deleted, sir."
        return f"Task {task_id} not found, sir."
    except Exception as e:
        return f"Error: {e}"

def clear_scheduled_tasks():
    try:
        with open(SCHEDULER_FILE, "w") as f:
            json.dump({"tasks": []}, f)
        return "All tasks cleared, sir."
    except Exception as e:
        return f"Error clearing tasks: {e}"


# === SYSTEM INFO ===

def get_system_info():
    try:
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        sys_os = platform.system() + " " + platform.release()

        info = (
            f"System: {sys_os}\n"
            f"CPU Usage: {cpu}%\n"
            f"RAM: {ram.percent}% used  "
            f"({ram.used // 1024**3:.1f} GB / {ram.total // 1024**3:.1f} GB)\n"
            f"Disk: {disk.percent}% used  "
            f"({disk.used // 1024**3:.1f} GB used / {disk.free // 1024**3:.1f} GB free)"
        )

        if hasattr(psutil, "sensors_battery"):
            batt = psutil.sensors_battery()
            if batt:
                status = "Charging" if batt.power_plugged else "Discharging"
                info += f"\nBattery: {batt.percent:.0f}% ({status})"

        return info
    except Exception as e:
        return f"Error retrieving system info: {e}"


# === WEATHER ===

def get_weather(city="Colombo"):
    try:
        res = requests.get(f"http://wttr.in/{city}?format=3", timeout=5)
        if res.status_code == 200:
            return res.text.strip()
        return f"Weather service returned error {res.status_code}."
    except requests.exceptions.Timeout:
        return "Weather request timed out, sir."
    except requests.exceptions.ConnectionError:
        return "No internet connection, sir."
    except Exception as e:
        return f"Weather check failed: {e}"


# === CALCULATOR ===

def safe_calculate(expression):
    safe_env = {
        "__builtins__": {},
        "abs": abs, "round": round, "min": min, "max": max,
        "pow": pow, "sum": sum, "sqrt": math.sqrt,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10,
        "ceil": math.ceil, "floor": math.floor,
        "pi": math.pi, "e": math.e,
    }
    try:
        pct = re.match(r'(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)', expression, re.I)
        if pct:
            result = float(pct.group(1)) / 100 * float(pct.group(2))
            return round(result, 8) if isinstance(result, float) else result
        clean = re.sub(r'[^\d+\-*/().\s]', '', expression)
        result = eval(clean, safe_env)
        return round(result, 8) if isinstance(result, float) else result
    except:
        return None


# === APP LAUNCHER ===

APPS = {
    "notepad":        "notepad",
    "calculator":     "calc",
    "paint":          "mspaint",
    "explorer":       "explorer",
    "file explorer":  "explorer",
    "task manager":   "taskmgr",
    "word":           "winword",
    "excel":          "excel",
    "powerpoint":     "powerpnt",
    "cmd":            "cmd",
    "command prompt": "cmd",
    "powershell":     "powershell",
    "vs code":        "code",
    "vscode":         "code",
    "chrome":         "chrome",
    "firefox":        "firefox",
    "edge":           "msedge",
    "spotify":        "spotify",
}

def open_app(name):
    key = name.lower().strip()
    cmd = APPS.get(key, key)
    try:
        subprocess.Popen(cmd, shell=True)
        return f"Opening {name}, sir."
    except Exception as e:
        return f"Could not open {name}: {e}"


PROCESS_NAMES = {
    "notepad":        "notepad.exe",
    "calculator":     "CalculatorApp.exe",
    "calc":           "CalculatorApp.exe",
    "paint":          "mspaint.exe",
    "explorer":       "explorer.exe",
    "file explorer":  "explorer.exe",
    "task manager":   "Taskmgr.exe",
    "word":           "WINWORD.EXE",
    "excel":          "EXCEL.EXE",
    "powerpoint":     "POWERPNT.EXE",
    "cmd":            "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell":     "powershell.exe",
    "vs code":        "Code.exe",
    "vscode":         "Code.exe",
    "chrome":         "chrome.exe",
    "firefox":        "firefox.exe",
    "edge":           "msedge.exe",
    "spotify":        "Spotify.exe",
}

def close_app(name):
    key = name.lower().strip()
    proc_name = PROCESS_NAMES.get(key, key if key.endswith(".exe") else key + ".exe")
    closed = False
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == proc_name.lower():
                proc.terminate()
                closed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if closed:
        return f"Closed {name}, sir."
    return f"I couldn't find {name} running, sir."


def play_music(query=None):
    try:
        if query:
            url = f"https://www.youtube.com/results?search_query={quote(query)}"
            os.startfile(url)
            return f"Searching YouTube for '{query}' and opening it now, sir."
        os.startfile("https://www.youtube.com/results?search_query=music+mix")
        return "Opening YouTube and playing some music for you, sir."
    except Exception as e:
        return f"Could not open music: {e}"


# === WARMUP ===

def warmup_model():
    try:
        model_name = settings.get("model", "dolphin-mistral")
        print(f"Warming up {model_name}...")
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_name, "prompt": "Hi", "stream": False},
            timeout=120
        )
        if res.status_code == 200:
            print(f"{model_name} ready!")
            gui_queue.put(("insert", f"🤖 JARVIS: {model_name} is loaded and ready, sir.\n\n"))
            return True
    except Exception as e:
        print(f"Warmup failed: {e}")
    return False


# === AI — STREAMING ===

def ask_jarvis_stream(user_prompt):
    """Stream a response from Ollama /api/chat. All GUI updates go through gui_queue."""
    global _last_response
    model_name = settings.get("model", "dolphin-mistral")
    messages = build_messages(user_prompt)

    try:
        print(f"Streaming query to {model_name}...")
        gui_queue.put(("insert", "🤖 JARVIS: "))

        with requests.post(
            "http://localhost:11434/api/chat",
            json={"model": model_name, "messages": messages, "stream": True},
            stream=True,
            timeout=180
        ) as res:
            if res.status_code != 200:
                err = f"Model returned status {res.status_code}. Is Ollama running?"
                gui_queue.put(("insert", err + "\n\n"))
                gui_queue.put(("typing_off", None))
                return err

            full_response = []
            for line in res.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if not chunk.get("done"):
                            content = chunk.get("message", {}).get("content", "")
                            if content:
                                full_response.append(content)
                                gui_queue.put(("insert", content))
                    except json.JSONDecodeError:
                        pass

            gui_queue.put(("insert", "\n\n"))
            response_text = "".join(full_response)
            _last_response = response_text
            append_to_history("assistant", response_text)
            gui_queue.put(("typing_off", None))
            gui_queue.put(("speak", response_text))
            return response_text

    except requests.exceptions.Timeout:
        err = "Request timed out. Try a shorter query or restart Ollama."
        gui_queue.put(("insert", err + "\n\n"))
        gui_queue.put(("typing_off", None))
        return err
    except requests.exceptions.ConnectionError:
        err = "Cannot connect to Ollama at localhost:11434. Please start Ollama."
        gui_queue.put(("insert", err + "\n\n"))
        gui_queue.put(("typing_off", None))
        return err
    except Exception as e:
        err = f"Error: {e}"
        gui_queue.put(("insert", err + "\n\n"))
        gui_queue.put(("typing_off", None))
        return err


# === COMMAND PROCESSING ===

def process_input(user_input):
    """Handle built-in commands. Returns response string or None (→ stream AI)."""
    lower = user_input.lower().strip()

    # Long, free-form text (e.g. pasted paragraphs) is almost never a
    # short command — route it straight to the AI to avoid false keyword
    # matches like "help" or "what is" appearing inside a sentence.
    if len(user_input.split()) > 18:
        return None

    # ── Date / Time ─────────────────────────────────────────────
    if any(k in lower for k in ["current time", "what time", "what's the time"]):
        return datetime.datetime.now().strftime("The time is %I:%M:%S %p on %A, %B %d, %Y.")

    if any(k in lower for k in ["current date", "today's date", "what's the date", "what date"]):
        return datetime.datetime.now().strftime("Today is %A, %B %d, %Y.")

    # ── Weather ──────────────────────────────────────────────────
    if "weather" in lower:
        m = re.search(r'weather\s+(?:in|for|at)\s+([a-zA-Z\s]+)', lower)
        city = m.group(1).strip().title() if m else "Colombo"
        return get_weather(city)

    # ── System ───────────────────────────────────────────────────
    if any(k in lower for k in ["system info", "system status", "pc status", "computer status",
                                  "cpu usage", "ram usage", "battery", "disk space"]):
        return get_system_info()

    # ── Calculator ───────────────────────────────────────────────
    calc_match = re.search(r'(?:calculate|compute|solve|eval(?:uate)?|what is)\s+(.+)', lower)
    if calc_match:
        result = safe_calculate(calc_match.group(1).strip())
        if result is not None:
            return f"The result is {result}, sir."

    # ── Notes ────────────────────────────────────────────────────
    if any(k in lower for k in ["take a note", "add note", "save note", "note:"]):
        m = re.search(r'(?:take a note|add note|save note|note)\s*[:\-]?\s*(.+)', user_input, re.I)
        return add_note(m.group(1).strip() if m else user_input)

    if any(k in lower for k in ["show notes", "list notes", "my notes", "view notes"]):
        return list_notes()

    m = re.search(r'delete note\s+(\d+)', lower)
    if m:
        return delete_note(int(m.group(1)))

    if "clear notes" in lower:
        return clear_notes()

    # ── Calendar ─────────────────────────────────────────────────
    if any(k in lower for k in ["add event", "add to calendar", "schedule event"]):
        m = re.search(r'(?:add event|add to calendar|schedule event)\s*[:\-]?\s*(.+?)(?:\s+on\s+(.+))?$',
                      user_input, re.I)
        if m:
            return add_calendar_event(m.group(1).strip(), m.group(2).strip() if m.group(2) else None)
        return add_calendar_event(user_input)

    if any(k in lower for k in ["show calendar", "list events", "my calendar", "my events", "show events"]):
        return list_calendar_events()

    m = re.search(r'delete event\s+(\d+)', lower)
    if m:
        return delete_calendar_event(int(m.group(1)))

    # ── Tasks ────────────────────────────────────────────────────
    if any(k in lower for k in ["add task", "remind me", "new task"]):
        m = re.search(r'(?:add task|remind me|new task)\s*[:\-]?\s*(.+)', user_input, re.I)
        return add_scheduled_task(m.group(1).strip() if m else user_input)

    if any(k in lower for k in ["list tasks", "show tasks", "my tasks", "pending tasks"]):
        return list_scheduled_tasks("pending" in lower)

    m = re.search(r'complete task\s+(\d+)', lower)
    if m:
        return complete_task(int(m.group(1)))

    m = re.search(r'delete task\s+(\d+)', lower)
    if m:
        return delete_task(int(m.group(1)))

    if "clear tasks" in lower or "delete all tasks" in lower:
        return clear_scheduled_tasks()

    # ── Memory ───────────────────────────────────────────────────
    m = re.search(r'remember (?:that\s+)?my name is (.+)', lower)
    if m:
        name = m.group(1).strip()
        save_memory("name", name)
        return f"Understood, sir. I'll remember your name is {name}."

    m = re.search(r'remember (?:that\s+)?(.+?)\s+is\s+(.+)', lower)
    if m:
        save_memory(m.group(1).strip(), m.group(2).strip())
        return f"Got it — I'll remember that {m.group(1).strip()} is {m.group(2).strip()}, sir."

    if any(k in lower for k in ["what is my name", "what's my name"]):
        name = recall_memory("name")
        return f"Your name is {name}, sir." if name else "I don't know your name yet, sir."

    if any(k in lower for k in ["what do you know about me", "what do you remember", "show memory"]):
        mem = recall_all_memory()
        if not mem:
            return "I don't have anything saved about you yet, sir."
        lines = "\n".join(f"  • {k}: {v}" for k, v in mem.items())
        return f"Here's what I remember about you, sir:\n\n{lines}"

    m = re.search(r'forget (?:about\s+)?(.+)', lower)
    if m:
        key = m.group(1).strip()
        if forget_memory(key):
            return f"I've forgotten '{key}', sir."
        return f"I don't have anything stored for '{key}', sir."

    # ── App Launcher ─────────────────────────────────────────────
    m = re.search(r'^open\s+(.+)', lower)
    if m:
        return open_app(m.group(1).strip())

    # ── Close Apps ───────────────────────────────────────────────
    m = re.search(r'^close\s+(.+)', lower)
    if m:
        return close_app(m.group(1).strip())

    # ── Music / Media ────────────────────────────────────────────
    m = re.search(r'^play\s+(?:some\s+)?(?:music|song|songs)\s*(?:by\s+|called\s+|named\s+)?(.*)', lower)
    if m:
        query = m.group(1).strip()
        return play_music(query if query else None)

    m = re.search(r'^play\s+(.+)', lower)
    if m:
        return play_music(m.group(1).strip())

    # ── Clipboard ────────────────────────────────────────────────
    if any(k in lower for k in ["copy that", "copy last response", "copy to clipboard"]):
        if _last_response:
            gui_queue.put(("clipboard", _last_response))
            return "Last response copied to clipboard, sir."
        return "Nothing to copy yet, sir."

    # ── Help ─────────────────────────────────────────────────────
    if any(k in lower for k in ["help", "what can you do", "commands", "capabilities"]):
        return (
            "Here's what I can do, sir:\n\n"
            "  📅  TIME & DATE   —  'what time is it'  /  'today's date'\n"
            "  🌤  WEATHER       —  'weather in London'\n"
            "  💻  SYSTEM        —  'system info'  /  'battery'  /  'disk space'\n"
            "  🧮  CALCULATE     —  'calculate 15% of 500'  /  'what is 128 * 256'\n"
            "  📝  NOTES         —  'take a note: ...'  /  'show notes'  /  'delete note 2'\n"
            "  📅  CALENDAR      —  'add event: Meeting on Monday'  /  'show calendar'\n"
            "  ✅  TASKS         —  'add task: ...'  /  'complete task 1'  /  'pending tasks'\n"
            "  🧠  MEMORY        —  'remember my name is ...'  /  'what do you know about me'\n"
            "  🚀  OPEN APPS     —  'open notepad'  /  'open chrome'  /  'open calculator'\n"
            "  ❌  CLOSE APPS    —  'close notepad'  /  'close chrome'\n"
            "  🎵  MUSIC         —  'play music'  /  'play believer by imagine dragons'\n"
            "  📋  CLIPBOARD     —  'copy that'\n"
            "  🤖  AI CHAT       —  anything else goes to the AI model\n"
        )

    # ── AI fallback ──────────────────────────────────────────────
    return None


# === SOUND & VOICE ===

def play_send_sound():
    if not settings["sound_enabled"]:
        return
    try:
        sound_effect = pygame.mixer.Sound(os.path.join(SCRIPT_DIR, "send_sound.mp3"))
        sound_effect.play()
    except Exception as e:
        print(f"Sound error: {e}")

def speak_text(text):
    if not settings["voice_enabled"]:
        return
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS error: {e}")


# === MESSAGE HANDLING ===

def send_message():
    user_input = entry.get().strip()
    if not user_input:
        return

    chatbox.insert(tk.END, f"🧑 You: {user_input}\n")
    chatbox.see(tk.END)
    entry.delete(0, tk.END)
    append_to_history("user", user_input)
    play_send_sound()
    typing_label.config(text="🤖 JARVIS is thinking...")

    def process_and_respond():
        global _last_response
        response = process_input(user_input)

        if response is None:
            ask_jarvis_stream(user_input)
        else:
            _last_response = response
            append_to_history("assistant", response)
            gui_queue.put(("insert", f"🤖 JARVIS: {response}\n\n"))
            gui_queue.put(("typing_off", None))
            gui_queue.put(("speak", response))

    threading.Thread(target=process_and_respond, daemon=True).start()


# === GUI QUEUE PROCESSOR (runs in main thread) ===

def poll_gui_queue():
    try:
        while True:
            item = gui_queue.get_nowait()
            action = item[0]
            data = item[1] if len(item) > 1 else None

            if action == "insert":
                chatbox.insert(tk.END, data)
                chatbox.see(tk.END)
            elif action == "typing_off":
                typing_label.config(text="")
            elif action == "speak":
                if data:
                    threading.Thread(target=speak_text, args=(data,), daemon=True).start()
            elif action == "clipboard":
                window.clipboard_clear()
                window.clipboard_append(data)
    except Empty:
        pass
    window.after(30, poll_gui_queue)


# === GUI ===

ACCENT      = "#00d4ff"
ACCENT_GLOW = "#00eaff"
BG_DARK     = "#05070d"
BG_PANEL    = "#0a0e1a"
BG_CHAT     = "#0d1117"
TEXT_MAIN   = "#e6f7ff"
TEXT_DIM    = "#7d93a8"

window = tk.Tk()
window.title("J.A.R.V.I.S — Just A Rather Very Intelligent System")
window.geometry("1280x800")
window.configure(bg=BG_DARK)
window.minsize(1020, 650)

root_frame = tk.Frame(window, bg=BG_DARK)
root_frame.pack(fill=tk.BOTH, expand=True)

# ---------------- Sidebar ----------------
sidebar = tk.Frame(root_frame, bg=BG_PANEL, width=240)
sidebar.pack(side=tk.LEFT, fill=tk.Y)
sidebar.pack_propagate(False)

main_area = tk.Frame(root_frame, bg=BG_DARK)
main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

tk.Label(sidebar, text="J . A . R . V . I . S", font=("Consolas", 13, "bold"),
         bg=BG_PANEL, fg=ACCENT).pack(pady=(22, 6))

# Animated arc-reactor logo
reactor_canvas = tk.Canvas(sidebar, width=160, height=160, bg=BG_PANEL, highlightthickness=0)
reactor_canvas.pack(pady=4)

_reactor_angle = [0]

def draw_reactor():
    c = reactor_canvas
    c.delete("reactor")
    cx, cy = 80, 80
    for r, color, w in [(70, "#0c2533", 2), (55, "#114a63", 2), (40, "#1a7a9e", 2)]:
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=w, tags="reactor")
    a = _reactor_angle[0]
    for offset, color in [(0, ACCENT_GLOW), (120, ACCENT), (240, ACCENT_GLOW)]:
        c.create_arc(cx - 60, cy - 60, cx + 60, cy + 60, start=a + offset, extent=70,
                     outline=color, width=3, style="arc", tags="reactor")
    c.create_oval(cx - 22, cy - 22, cx + 22, cy + 22, outline=ACCENT, width=2,
                  fill="#062430", tags="reactor")
    c.create_oval(cx - 12, cy - 12, cx + 12, cy + 12, fill=ACCENT_GLOW, outline="", tags="reactor")
    c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#ffffff", outline="", tags="reactor")
    _reactor_angle[0] = (a + 3) % 360
    window.after(45, draw_reactor)

draw_reactor()

status_dot_label = tk.Label(sidebar, text="●  ONLINE", font=("Segoe UI", 10, "bold"),
                            bg=BG_PANEL, fg="#00ff88")
status_dot_label.pack(pady=(2, 16))

# Live system gauges
gauge_frame = tk.Frame(sidebar, bg=BG_PANEL)
gauge_frame.pack(pady=4)

def make_gauge(parent, title):
    f = tk.Frame(parent, bg=BG_PANEL)
    f.pack(side=tk.LEFT, padx=10)
    cv = tk.Canvas(f, width=80, height=80, bg=BG_PANEL, highlightthickness=0)
    cv.pack()
    pct_lbl = tk.Label(f, text="0%", font=("Consolas", 11, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
    pct_lbl.pack()
    tk.Label(f, text=title, font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_DIM).pack()
    return cv, pct_lbl

cpu_canvas, cpu_pct_label = make_gauge(gauge_frame, "CPU")
ram_canvas, ram_pct_label = make_gauge(gauge_frame, "RAM")

def draw_gauge(canvas, percent, color):
    canvas.delete("g")
    cx, cy, r = 40, 40, 30
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#16202e", width=7, tags="g")
    extent = -3.6 * percent
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=extent,
                      outline=color, width=7, style="arc", tags="g")

def update_gauges():
    try:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        draw_gauge(cpu_canvas, cpu, ACCENT_GLOW if cpu < 80 else "#ff5577")
        draw_gauge(ram_canvas, ram, ACCENT if ram < 80 else "#ff5577")
        cpu_pct_label.config(text=f"{cpu:.0f}%")
        ram_pct_label.config(text=f"{ram:.0f}%")
    except Exception:
        pass
    window.after(2000, update_gauges)

update_gauges()

# Quick actions
tk.Frame(sidebar, height=2, bg="#16202e").pack(fill=tk.X, padx=24, pady=18)
tk.Label(sidebar, text="QUICK ACTIONS", font=("Segoe UI", 9, "bold"),
         bg=BG_PANEL, fg=TEXT_DIM).pack(pady=(0, 8))

def quick_action(text):
    entry.delete(0, tk.END)
    entry.insert(0, text)
    send_message()

QUICK_ACTIONS = [
    ("🌐  Open Chrome",   "open chrome"),
    ("📝  Open Notepad",  "open notepad"),
    ("🎵  Play Music",    "play music"),
    ("💻  System Info",   "system info"),
    ("🌤  Weather",       "weather in Colombo"),
]

for q_label, q_cmd in QUICK_ACTIONS:
    qbtn = tk.Button(sidebar, text=q_label, font=("Segoe UI", 10),
                     command=lambda c=q_cmd: quick_action(c),
                     bg=BG_PANEL, fg=TEXT_MAIN, activebackground="#102333",
                     activeforeground=ACCENT, relief=tk.FLAT, anchor="w",
                     padx=20, pady=8, cursor="hand2", borderwidth=0)
    qbtn.pack(fill=tk.X, padx=14, pady=2)
    qbtn.bind("<Enter>", lambda _e, b=qbtn: b.config(bg="#102333", fg=ACCENT))
    qbtn.bind("<Leave>", lambda _e, b=qbtn: b.config(bg=BG_PANEL, fg=TEXT_MAIN))

# ---------------- Main area ----------------
title_bar = tk.Frame(main_area, bg=BG_DARK, height=70)
title_bar.pack(fill=tk.X)
title_bar.pack_propagate(False)

tk.Label(title_bar, text="⚡ J.A.R.V.I.S", font=("Segoe UI", 22, "bold"),
         bg=BG_DARK, fg=ACCENT).pack(side=tk.LEFT, padx=28, pady=14)

time_label = tk.Label(title_bar, text="", font=("Consolas", 12, "bold"),
                      bg=BG_DARK, fg=TEXT_DIM)
time_label.pack(side=tk.RIGHT, padx=28)

tk.Frame(main_area, height=1, bg="#16202e").pack(fill=tk.X)

chat_frame = tk.Frame(main_area, bg=BG_DARK)
chat_frame.pack(padx=24, pady=18, fill=tk.BOTH, expand=True)

chatbox = scrolledtext.ScrolledText(chat_frame, wrap=tk.WORD, font=("Consolas", 11),
                                    bg=BG_CHAT, fg=TEXT_MAIN, insertbackground=ACCENT,
                                    relief=tk.FLAT, padx=18, pady=18, borderwidth=0,
                                    highlightthickness=1, highlightbackground="#16202e",
                                    highlightcolor=ACCENT)
chatbox.pack(fill=tk.BOTH, expand=True)

typing_label = tk.Label(main_area, text="", font=("Segoe UI", 10, "italic"),
                        bg=BG_DARK, fg="#00ff88")
typing_label.pack(padx=28, anchor="w")

# Input row
input_row = tk.Frame(main_area, bg=BG_DARK)
input_row.pack(padx=24, pady=(8, 14), fill=tk.X)

entry_wrap = tk.Frame(input_row, bg="#16202e", padx=2, pady=2)
entry_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)

entry = tk.Entry(entry_wrap, font=("Segoe UI", 13), bg=BG_CHAT, fg=TEXT_MAIN,
                 insertbackground=ACCENT, relief=tk.FLAT, bd=0)
entry.pack(fill=tk.X, ipady=12, padx=2, pady=2)
entry.bind("<Return>", lambda _e: send_message())

def _sanitize_paste(_e=None):
    try:
        clip = window.clipboard_get()
    except tk.TclError:
        return
    clean = " ".join(clip.splitlines()).strip()
    if entry.selection_present():
        entry.delete("sel.first", "sel.last")
    entry.insert(tk.INSERT, clean)
    return "break"

entry.bind("<<Paste>>", _sanitize_paste)
entry.bind("<Control-v>", _sanitize_paste)
entry.focus_set()

def styled_button(parent, text, bg, hover_bg, command, width=11, fg="#04141c"):
    btn = tk.Button(parent, text=text, font=("Segoe UI", 11, "bold"), command=command,
                    bg=bg, fg=fg, activebackground=hover_bg, activeforeground=fg,
                    width=width, relief=tk.FLAT, cursor="hand2", pady=11, borderwidth=0)
    btn.bind("<Enter>", lambda _e: btn.config(bg=hover_bg))
    btn.bind("<Leave>", lambda _e: btn.config(bg=bg))
    return btn

send_btn = styled_button(input_row, "▶ Send", ACCENT, ACCENT_GLOW, lambda: send_message())
send_btn.pack(side=tk.LEFT, padx=(10, 0))

def clear_chat():
    if messagebox.askyesno("Clear Chat", "Clear the chat display and history?"):
        if clear_chat_history():
            chatbox.delete(1.0, tk.END)
            chatbox.insert(tk.END, "🤖 JARVIS: Chat history cleared, sir.\n\n")
        else:
            messagebox.showerror("Error", "Failed to clear chat history.")

clear_btn = styled_button(input_row, "🗑", "#1a1024", "#2a1834", clear_chat, width=4, fg="#ff5577")
clear_btn.pack(side=tk.LEFT, padx=(8, 0))

# Settings
def toggle_setting(setting_name):
    settings[setting_name] = not settings[setting_name]
    save_settings()
    update_settings_display()

def update_settings_display():
    v = "✓" if settings["voice_enabled"] else "✗"
    s = "✓" if settings["sound_enabled"] else "✗"
    model_name = settings.get("model", "dolphin-mistral")
    settings_label.config(text=f"🔊 Voice: {v}    🔔 Sound: {s}    🤖 Model: {model_name}")

def change_model(model_name):
    settings["model"] = model_name
    save_settings()
    update_settings_display()
    chatbox.insert(tk.END, f"🤖 JARVIS: Switched to {model_name}. It will load on next query.\n\n")
    chatbox.see(tk.END)

def open_settings():
    sw = tk.Toplevel(window)
    sw.title("⚙ JARVIS Settings")
    sw.geometry("440x440")
    sw.configure(bg=BG_DARK)
    sw.resizable(False, False)

    tk.Label(sw, text="⚙ JARVIS SETTINGS", font=("Segoe UI", 16, "bold"),
             bg=BG_DARK, fg=ACCENT).pack(pady=(20, 16))

    cf = tk.Frame(sw, bg=BG_DARK)
    cf.pack(fill=tk.BOTH, expand=True, padx=24)

    for setting, label in [("voice_enabled", "🔊 Voice Output"),
                            ("sound_enabled", "🔔 Sound Effects")]:
        on = settings[setting]
        tk.Button(cf, text=f"{label}: {'ON' if on else 'OFF'}",
                  font=("Segoe UI", 11, "bold"),
                  command=lambda s=setting: toggle_setting(s),
                  bg=ACCENT if on else "#16202e",
                  fg="#04141c" if on else TEXT_DIM,
                  width=30, relief=tk.FLAT, cursor="hand2", pady=12, borderwidth=0).pack(pady=5)

    tk.Frame(cf, height=2, bg="#16202e").pack(fill=tk.X, pady=16)
    tk.Label(cf, text="🤖 AI MODEL", font=("Segoe UI", 11, "bold"),
             bg=BG_DARK, fg=ACCENT).pack(pady=(0, 10))

    current = settings.get("model", "dolphin-mistral")
    for name, label in [("dolphin-mistral",  "🐬 Dolphin-Mistral (Uncensored)"),
                         ("my-coder",         "👨‍💻 My-Coder (Custom Fine-tuned)"),
                         ("openhermes",       "📚 OpenHermes (Standard)"),
                         ("qwen2.5-coder:3b", "⚡ Qwen2.5-Coder 3B")]:
        active = current == name
        tk.Button(cf, text=label,
                  font=("Segoe UI", 10, "bold"),
                  command=lambda n=name: change_model(n),
                  bg="#00ff88" if active else "#16202e",
                  fg="#04141c" if active else TEXT_DIM,
                  width=30, relief=tk.FLAT, cursor="hand2", pady=9, borderwidth=0).pack(pady=3)

settings_btn = styled_button(input_row, "⚙", "#16202e", "#1f3142", open_settings, width=4, fg=ACCENT)
settings_btn.pack(side=tk.LEFT, padx=(8, 0))

# Status bar
status_frame = tk.Frame(main_area, bg=BG_PANEL)
status_frame.pack(fill=tk.X)

settings_label = tk.Label(status_frame, text="", font=("Segoe UI", 9),
                          bg=BG_PANEL, fg=TEXT_DIM, pady=8)
settings_label.pack()

# === STARTUP ===

initialize_files()
load_settings()
update_settings_display()

threading.Thread(target=warmup_model, daemon=True).start()

window.after(30, poll_gui_queue)

chatbox.tag_config("welcome", foreground=ACCENT, font=("Segoe UI", 12, "bold"))
chatbox.tag_config("info",    foreground=TEXT_DIM, font=("Segoe UI", 10))

chatbox.insert(tk.END, "\n")
chatbox.insert(tk.END, "  Welcome back, sir. J.A.R.V.I.S is online.\n", "welcome")
chatbox.insert(tk.END, "  All systems operational and ready for your command.\n\n", "welcome")
chatbox.insert(tk.END, "  💬  Type a message or say 'help' to see all commands\n", "info")
chatbox.insert(tk.END, "  🌐  Try: 'open chrome' · 'play music' · 'close notepad'\n", "info")
chatbox.insert(tk.END, "  🤖  Powered by Ollama — responses stream in real time\n\n", "info")

def update_time():
    try:
        time_label.config(text=datetime.datetime.now().strftime("%A, %B %d  •  %H:%M:%S"))
        window.after(1000, update_time)
    except Exception:
        pass

update_time()
window.mainloop()
