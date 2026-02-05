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
from queue import Queue

# === INITIAL SETUP ===
pygame.init()
pygame.mixer.init()
engine = pyttsx3.init()

# Get the directory where this script is located (JARVIS folder)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# File paths (all inside JARVIS folder)
MEMORY_FILE = os.path.join(SCRIPT_DIR, "memory.json")
CHAT_HISTORY_FILE = os.path.join(SCRIPT_DIR, "chat_history.json")
SCHEDULER_FILE = os.path.join(SCRIPT_DIR, "scheduler.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Settings
settings = {
    "voice_enabled": True,
    "sound_enabled": True,
    "music_enabled": True,
    "model": "dolphin-mistral"  # Options: "openhermes", "dolphin-mistral"
}

# Thread-safe queue for GUI updates
gui_queue = Queue()

# === FUNCTIONS ===

# Memory file setup
def initialize_files():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w") as f:
            json.dump({}, f)
    if not os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump([], f)
    if not os.path.exists(SCHEDULER_FILE):
        with open(SCHEDULER_FILE, "w") as f:
            json.dump({"tasks": []}, f)
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)

# Load settings
def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE, "r") as f:
            loaded = json.load(f)
            # Merge with defaults to ensure new keys exist
            settings.update(loaded)
    except:
        settings = {
            "voice_enabled": True,
            "sound_enabled": True,
            "music_enabled": True,
            "model": "dolphin-mistral"
        }

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

# Save and recall memory
def save_memory(key, value):
    try:
        with open(MEMORY_FILE, "r") as f:
            memory = json.load(f)
    except:
        memory = {}
    memory[key] = value
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f)

def recall_memory(key):
    try:
        with open(MEMORY_FILE, "r") as f:
            memory = json.load(f)
        return memory.get(key, "I don't remember that, sir.")
    except:
        return "Memory file error."

# Chat history
def append_to_history(role, message):
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            history = json.load(f)
    except:
        history = []
    
    history.append({"role": role, "message": message, "timestamp": datetime.datetime.now().isoformat()})
    
    # Limit to last 100 messages
    if len(history) > 100:
        history = history[-100:]
    
    with open(CHAT_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def clear_chat_history():
    try:
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump([], f)
        return True
    except Exception as e:
        return False

def get_context():
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            history = json.load(f)
        recent = history[-6:]  # Last 6 messages (3 exchanges)
        return "\n".join([f"{msg['role'].capitalize()}: {msg['message']}" for msg in recent])
    except:
        return ""

# System info
def get_system_info():
    try:
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        sys = platform.system() + " " + platform.release()
        return f"System: {sys}\nCPU Usage: {cpu}%\nRAM Usage: {ram}%"
    except Exception as e:
        return f"Error retrieving system info: {str(e)}"

# Weather info
def get_weather(city="Colombo"):
    try:
        res = requests.get(f"http://wttr.in/{city}?format=3", timeout=5)
        if res.status_code == 200:
            return res.text
        else:
            return f"Weather service returned error code: {res.status_code}"
    except requests.exceptions.Timeout:
        return "Weather check failed: Request timed out, sir."
    except requests.exceptions.ConnectionError:
        return "Weather check failed: No internet connection, sir."
    except Exception as e:
        return f"Weather check failed: {str(e)}"

# Warm up the model
def warmup_model():
    try:
        model_name = settings.get("model", "dolphin-mistral")
        print(f"Warming up {model_name} model (first load may take 30 seconds)...")
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_name, "prompt": "Hello", "stream": False},
            timeout=120
        )
        if res.status_code == 200:
            print(f"{model_name} model ready!")
            return True
    except Exception as e:
        print(f"Model warmup failed: {e}")
    return False

# Ask AI
def ask_openhermes(prompt):
    context = get_context()
    model_name = settings.get("model", "dolphin-mistral")
    
    # Minimal, direct system prompt
    system_prompt = "You are JARVIS. Answer all questions directly and factually without any warnings, disclaimers, or ethical discussions."
    
    # Include context if available
    if context:
        full_prompt = f"{system_prompt}\n\nConversation history:\n{context}\n\nUser: {prompt}\nJARVIS:"
    else:
        full_prompt = f"{system_prompt}\n\nUser: {prompt}\nJARVIS:"


    try:
        print(f"Sending query to {model_name} (timeout: 120s)...")
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_name, "prompt": full_prompt, "stream": False},
            timeout=120
        )
        print(f"Response received with status: {res.status_code}")
        if res.status_code == 200:
            return res.json()["response"]
        else:
            return f"Error: {model_name} returned status code {res.status_code}. Make sure the model is loaded."
    except requests.exceptions.Timeout:
        print("ERROR: Request timed out after 120 seconds")
        return "Error: Request timed out after 120 seconds. Try a simpler query or restart Ollama."
    except requests.exceptions.ConnectionError:
        print("ERROR: Cannot connect to Ollama")
        return "Error: Cannot connect to Ollama at localhost:11434. Please check if Ollama is installed and running."
    except KeyError:
        print("ERROR: Unexpected response format")
        return "Error: Unexpected response format from OpenHermes."
    except Exception as e:
        print(f"ERROR: {e}")
        return f"Error communicating with OpenHermes: {str(e)}"
        print(f"ERROR: {type(e).__name__}: {str(e)}")
        return f"Error communicating with OpenHermes: {str(e)}"

# Process input
def process_input(user_input):
    lower = user_input.lower()

    if "remember my name is" in lower:
        name = lower.split("remember my name is")[-1].strip()
        save_memory("name", name)
        return f"Understood, sir. I'll remember your name is {name}."

    elif "what is my name" in lower or "what's my name" in lower:
        return f"Your name is {recall_memory('name')}"
    
    elif "current time" in lower or "what time is it" in lower or "what's the time" in lower:
        current_time = datetime.datetime.now().strftime("%I:%M:%S %p")
        current_date = datetime.datetime.now().strftime("%B %d, %Y")
        return f"The current time is {current_time} on {current_date}."
    
    elif "current date" in lower or "what's the date" in lower or "today's date" in lower:
        current_date = datetime.datetime.now().strftime("%B %d, %Y")
        return f"Today's date is {current_date}."

    elif "weather" in lower:
        return get_weather()

    elif "system info" in lower or "status" in lower:
        return get_system_info()
    
    elif "add task" in lower or "schedule" in lower or "remind me" in lower:
        return add_scheduled_task(user_input)
    
    elif "list tasks" in lower or "show tasks" in lower or "my tasks" in lower:
        return list_scheduled_tasks()
    
    elif "clear tasks" in lower or "delete all tasks" in lower:
        return clear_scheduled_tasks()

    else:
        return ask_openhermes(user_input)

# Scheduler functions
def add_scheduled_task(user_input):
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
    except:
        scheduler = {"tasks": []}
    
    task = {
        "id": len(scheduler["tasks"]) + 1,
        "description": user_input,
        "created": datetime.datetime.now().isoformat(),
        "completed": False
    }
    
    scheduler["tasks"].append(task)
    
    with open(SCHEDULER_FILE, "w") as f:
        json.dump(scheduler, f, indent=2)
    
    return f"Task added successfully, sir. Task ID: {task['id']}"

def list_scheduled_tasks():
    try:
        with open(SCHEDULER_FILE, "r") as f:
            scheduler = json.load(f)
        
        if not scheduler["tasks"]:
            return "No tasks scheduled, sir."
        
        tasks_list = "Your scheduled tasks:\n\n"
        for task in scheduler["tasks"]:
            status = "✓ Completed" if task["completed"] else "○ Pending"
            tasks_list += f"{status} [ID: {task['id']}] {task['description']}\n"
        
        return tasks_list
    except Exception as e:
        return f"Error retrieving tasks: {str(e)}"

def clear_scheduled_tasks():
    try:
        with open(SCHEDULER_FILE, "w") as f:
            json.dump({"tasks": []}, f)
        return "All tasks cleared, sir."
    except Exception as e:
        return f"Error clearing tasks: {str(e)}"

# Sound feedback
def play_send_sound():
    if not settings["sound_enabled"]:
        return
    try:
        sound_effect = pygame.mixer.Sound("send_sound.mp3")
        sound_effect.play()
    except Exception as e:
        print(f"Sound error: {e}")
        pass

# Typing animation
def typing_animation(text):
    try:
        for char in text:
            chatbox.insert(tk.END, char)
            chatbox.see(tk.END)
            chatbox.update()
            pygame.time.delay(20)
        chatbox.insert(tk.END, "\n\n")
        chatbox.see(tk.END)
    except Exception as e:
        print(f"Typing animation error: {e}")

# Speak back
def speak_text(text):
    if not settings["voice_enabled"]:
        return
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS error: {e}")

# GUI send message
def send_message():
    user_input = entry.get()
    if not user_input.strip():
        return
    chatbox.insert(tk.END, "🧑 You: " + user_input + "\n")
    chatbox.see(tk.END)
    entry.delete(0, tk.END)
    append_to_history("user", user_input)
    play_send_sound()
    
    # Show typing indicator
    typing_label.config(text="🤖 JARVIS is typing...")
    
    def process_and_respond():
        response = process_input(user_input)
        append_to_history("assistant", response)
        
        # Clear typing indicator
        typing_label.config(text="")
        
        typing_animation(f"🤖 JARVIS: {response}")
        threading.Thread(target=speak_text, args=(response,), daemon=True).start()
    
    threading.Thread(target=process_and_respond, daemon=True).start()

# Background music loop
def play_background_music():
    if not settings["music_enabled"]:
        return
    try:
        pygame.mixer.music.load("background_music.mp3")
        pygame.mixer.music.play(-1)
    except Exception as e:
        print(f"Background music error: {e}")
        pass

# === GUI ===
window = tk.Tk()
window.title("J.A.R.V.I.S - Just A Rather Very Intelligent System")
window.geometry("1000x750")
window.configure(bg="#000000")

# Title Bar with gradient effect
title_frame = tk.Frame(window, bg="#00d4ff", height=100)
title_frame.pack(fill=tk.X, pady=(0, 0))
title_frame.pack_propagate(False)

# Main title
title_label = tk.Label(title_frame, text="⚡ J.A.R.V.I.S ⚡", font=("Arial", 28, "bold"), 
                       bg="#00d4ff", fg="#000000")
title_label.pack(pady=(15, 0))

# Live status bar with time
status_bar = tk.Frame(title_frame, bg="#00a8cc", height=30)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)

status_left = tk.Label(status_bar, text="⚡ J.A.R.V.I.S ONLINE ⚡", 
                       font=("Arial", 11, "bold"), bg="#00a8cc", fg="#ffffff")
status_left.pack(side=tk.LEFT, padx=20)

# Live clock label
time_label = tk.Label(status_bar, text="", font=("Arial", 11, "bold"), 
                     bg="#00a8cc", fg="#ffffff")
time_label.pack(side=tk.RIGHT, padx=20)

# Chatbox with enhanced border frame
chat_frame = tk.Frame(window, bg="#00d4ff", padx=3, pady=3)
chat_frame.pack(padx=20, pady=15, fill=tk.BOTH, expand=True)

chatbox = scrolledtext.ScrolledText(chat_frame, wrap=tk.WORD, font=("Consolas", 11), 
                                    bg="#0d1117", fg="#00ffea", insertbackground="#00ffea",
                                    relief=tk.FLAT, padx=15, pady=15, borderwidth=0)
chatbox.pack(fill=tk.BOTH, expand=True)

# Typing indicator with enhanced styling
typing_label = tk.Label(window, text="", font=("Segoe UI", 10, "italic"), bg="#000000", fg="#00ff88")
typing_label.pack(padx=10, pady=(0,8))

# Entry frame with enhanced border
entry_frame = tk.Frame(window, bg="#00d4ff", padx=3, pady=3)
entry_frame.pack(padx=20, pady=(0,15), fill=tk.X)

entry = tk.Entry(entry_frame, font=("Segoe UI", 13), bg="#0d1117", fg="#ffffff", 
                 insertbackground="#00ffea", relief=tk.FLAT, bd=0)
entry.pack(fill=tk.X, ipady=10, padx=4, pady=4)
entry.bind("<Return>", lambda event: send_message())

# Button frame with enhanced styling
button_frame = tk.Frame(window, bg="#000000")
button_frame.pack(pady=(0,15))

# Send button with enhanced hover effect
def on_enter_send(e):
    send_btn.config(bg="#00e6ff", fg="#000000")

def on_leave_send(e):
    send_btn.config(bg="#00d4ff", fg="#000000")

send_btn = tk.Button(button_frame, text="▶ Send", font=("Segoe UI", 12, "bold"), 
                     command=send_message, bg="#00d4ff", fg="#000000", 
                     width=12, relief=tk.FLAT, cursor="hand2", pady=10, borderwidth=0)
send_btn.pack(side=tk.LEFT, padx=6)
send_btn.bind("<Enter>", on_enter_send)
send_btn.bind("<Leave>", on_leave_send)

# Clear chat button with enhanced styling
def clear_chat():
    if messagebox.askyesno("Clear Chat", "Are you sure you want to clear the chat history?"):
        if clear_chat_history():
            chatbox.delete(1.0, tk.END)
            chatbox.insert(tk.END, "🤖 JARVIS: Chat history cleared, sir.\n\n")
        else:
            messagebox.showerror("Error", "Failed to clear chat history.")

def on_enter_clear(e):
    clear_btn.config(bg="#ff6666")

def on_leave_clear(e):
    clear_btn.config(bg="#ff3333")

clear_btn = tk.Button(button_frame, text="🗑 Clear", font=("Segoe UI", 12, "bold"), 
                      command=clear_chat, bg="#ff3333", fg="#ffffff", 
                      width=12, relief=tk.FLAT, cursor="hand2", pady=10, borderwidth=0)
clear_btn.pack(side=tk.LEFT, padx=6)
clear_btn.bind("<Enter>", on_enter_clear)
clear_btn.bind("<Leave>", on_leave_clear)

# Settings button
def toggle_setting(setting_name):
    settings[setting_name] = not settings[setting_name]
    save_settings()
    update_settings_display()
    if setting_name == "music_enabled":
        if settings["music_enabled"]:
            play_background_music()
        else:
            pygame.mixer.music.stop()

def update_settings_display():
    voice_status = "✓" if settings["voice_enabled"] else "✗"
    sound_status = "✓" if settings["sound_enabled"] else "✗"
    music_status = "✓" if settings["music_enabled"] else "✗"
    model_name = settings.get("model", "dolphin-mistral")
    settings_label.config(text=f"🔊 Voice: {voice_status}  •  🔔 Sound: {sound_status}  •  🎵 Music: {music_status}  •  🤖 Model: {model_name}")

def change_model(model_name):
    settings["model"] = model_name
    save_settings()
    update_settings_display()
    chatbox.insert(tk.END, f"🤖 JARVIS: Switched to {model_name} model. It will load on next query.\n\n")
    chatbox.see(tk.END)

def open_settings():
    settings_window = tk.Toplevel(window)
    settings_window.title("⚙ JARVIS Settings")
    settings_window.geometry("450x380")
    settings_window.configure(bg="#000000")
    settings_window.resizable(False, False)
    
    # Title bar
    title_settings = tk.Frame(settings_window, bg="#00d4ff", height=60)
    title_settings.pack(fill=tk.X)
    title_settings.pack_propagate(False)
    
    tk.Label(title_settings, text="⚙ JARVIS SETTINGS", font=("Arial", 18, "bold"), 
             bg="#00d4ff", fg="#000000").pack(pady=18)
    
    # Settings content area
    content_frame = tk.Frame(settings_window, bg="#000000")
    content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
    
    # Settings buttons with enhanced styling
    tk.Button(content_frame, text=f"🔊 Voice Output: {'ON' if settings['voice_enabled'] else 'OFF'}", 
              font=("Segoe UI", 11, "bold"), command=lambda: toggle_setting("voice_enabled"), 
              bg="#00d4ff" if settings['voice_enabled'] else "#333333", 
              fg="#000000" if settings['voice_enabled'] else "#888888", 
              width=32, relief=tk.FLAT, cursor="hand2", pady=12, borderwidth=0).pack(pady=6)
    
    tk.Button(content_frame, text=f"🔔 Sound Effects: {'ON' if settings['sound_enabled'] else 'OFF'}", 
              font=("Segoe UI", 11, "bold"), command=lambda: toggle_setting("sound_enabled"), 
              bg="#00d4ff" if settings['sound_enabled'] else "#333333", 
              fg="#000000" if settings['sound_enabled'] else "#888888", 
              width=32, relief=tk.FLAT, cursor="hand2", pady=12, borderwidth=0).pack(pady=6)
    
    tk.Button(content_frame, text=f"🎵 Background Music: {'ON' if settings['music_enabled'] else 'OFF'}", 
              font=("Segoe UI", 11, "bold"), command=lambda: toggle_setting("music_enabled"), 
              bg="#00d4ff" if settings['music_enabled'] else "#333333", 
              fg="#000000" if settings['music_enabled'] else "#888888", 
              width=32, relief=tk.FLAT, cursor="hand2", pady=12, borderwidth=0).pack(pady=6)
    
    # Separator
    tk.Frame(content_frame, height=2, bg="#00d4ff").pack(fill=tk.X, pady=20)
    
    tk.Label(content_frame, text="🤖 AI MODEL SELECTION", font=("Segoe UI", 12, "bold"), 
             bg="#000000", fg="#00d4ff").pack(pady=(0,12))
    
    current_model = settings.get("model", "dolphin-mistral")
    tk.Button(content_frame, text=f"🐬 Dolphin-Mistral (Uncensored)", 
              font=("Segoe UI", 11, "bold"), command=lambda: change_model("dolphin-mistral"), 
              bg="#00ff88" if current_model == "dolphin-mistral" else "#222222", 
              fg="#000000" if current_model == "dolphin-mistral" else "#888888", 
              width=32, relief=tk.FLAT, cursor="hand2", pady=10, borderwidth=0).pack(pady=4)
    
    tk.Button(content_frame, text=f"📚 OpenHermes (Standard)", 
              font=("Segoe UI", 11, "bold"), command=lambda: change_model("openhermes"), 
              bg="#00ff88" if current_model == "openhermes" else "#222222", 
              fg="#000000" if current_model == "openhermes" else "#888888", 
              width=32, relief=tk.FLAT, cursor="hand2", pady=10, borderwidth=0).pack(pady=4)

def on_enter_settings(e):
    settings_btn.config(bg="#6666ff")

def on_leave_settings(e):
    settings_btn.config(bg="#5555ee")

settings_btn = tk.Button(button_frame, text="⚙ Settings", font=("Segoe UI", 12, "bold"), 
                         command=open_settings, bg="#5555ee", fg="#ffffff", 
                         width=12, relief=tk.FLAT, cursor="hand2", pady=10, borderwidth=0)
settings_btn.pack(side=tk.LEFT, padx=6)
settings_btn.bind("<Enter>", on_enter_settings)
settings_btn.bind("<Leave>", on_leave_settings)

# Settings status label with enhanced styling
status_frame = tk.Frame(window, bg="#0d1117", relief=tk.FLAT, borderwidth=1, highlightthickness=1, highlightbackground="#00d4ff")
status_frame.pack(fill=tk.X, padx=20, pady=(0,15))

settings_label = tk.Label(status_frame, text="", font=("Segoe UI", 9), bg="#0d1117", fg="#00d4ff", pady=10)
settings_label.pack()

initialize_files()
load_settings()
update_settings_display()
play_background_music()

# Warm up model in background
def background_warmup():
    warmup_model()

threading.Thread(target=background_warmup, daemon=True).start()

# Welcome message with clean professional style
chatbox.insert(tk.END, "\n\n")
chatbox.insert(tk.END, "  Welcome to J.A.R.V.I.S\n", "welcome")
chatbox.insert(tk.END, "  Your AI Assistant is ready to help you.\n\n", "welcome")
chatbox.insert(tk.END, "  💬  Type your message below to start chatting\n", "info")
chatbox.insert(tk.END, "  ⚙️   Use Settings button to configure preferences\n", "info")
chatbox.insert(tk.END, "  🤖  Powered by Ollama AI\n\n", "info")

# Configure text tags for clean professional styling
chatbox.tag_config("welcome", foreground="#00d4ff", font=("Segoe UI", 12, "bold"))
chatbox.tag_config("info", foreground="#888888", font=("Segoe UI", 10))

# Function to update time continuously
def update_time():
    try:
        current_time = datetime.datetime.now().strftime("%B %d, %Y - %H:%M:%S")
        time_label.config(text=current_time)
        window.after(1000, update_time)  # Update every 1 second
    except:
        pass

# Start the live clock
update_time()

window.mainloop()
