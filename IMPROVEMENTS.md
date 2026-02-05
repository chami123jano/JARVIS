# J.A.R.V.I.S - AI Assistant

**Just A Rather Very Intelligent System**

A professional desktop AI assistant powered by Ollama with a beautiful GUI interface, voice synthesis, and uncensored conversational capabilities.

---

## 🚀 Features

- ✨ **Beautiful Modern GUI** - Professional cyan/black themed interface
- ⏰ **Live Clock** - Real-time date and time display
- 💬 **Uncensored AI** - Uses dolphin-mistral model for unrestricted conversations
- 🗣️ **Text-to-Speech** - Voice responses powered by pyttsx3
- 📝 **Chat History** - Saves and maintains conversation context
- 🧠 **Memory System** - Remembers user information
- 📅 **Task Scheduler** - Add, list, and manage tasks
- 🌤️ **Weather Info** - Real-time weather updates
- 💻 **System Info** - CPU and RAM monitoring
- ⚙️ **Settings Panel** - Customize voice, sound effects, and AI model
- 🔄 **Context Awareness** - Maintains conversation flow

---

## 📋 Prerequisites

Before running JARVIS, you need to install the following:

### 1. **Python 3.13+**
Download from: https://www.python.org/downloads/

### 2. **Ollama**
Download and install from: https://ollama.com/download

After installing Ollama, pull the required AI model:
```bash
ollama pull dolphin-mistral
```

**Optional models:**
```bash
ollama pull openhermes    # Alternative text model
```

### 3. **Python Dependencies**
Install required packages:
```bash
pip install pygame pyttsx3 requests psutil pillow
```

---

## 🎯 How to Run

1. **Start Ollama** (must be running in the background)
   - Ollama runs automatically after installation
   - Or run: `ollama serve`

2. **Run JARVIS**
   ```bash
   cd JARVIS
   python jarvis_gui.py
   ```

3. **Start chatting!**
   - Type your messages in the input box
   - Press Enter or click Send
   - JARVIS will respond with text and voice

---

## 🎮 Usage Guide

### Basic Commands

**Time & Date:**
- "what time is it"
- "current date"

**Memory:**
- "remember my name is John"
- "what is my name"

**Weather:**
- "weather" (defaults to Colombo)

**System:**
- "system info" or "status"

**Tasks:**
- "add task buy groceries"
- "list tasks"
- "clear tasks"

### Settings Panel

Click the **⚙ Settings** button to:
- Toggle voice output ON/OFF
- Toggle sound effects ON/OFF
- Toggle background music ON/OFF
- Switch between AI models (dolphin-mistral/openhermes)

---

## 📁 Project Structure

```
JARVIS/
├── jarvis_gui.py          # Main application
├── IMPROVEMENTS.md        # This file
├── .gitignore            # Git ignore rules
├── memory.json           # User memory (not tracked)
├── chat_history.json     # Conversation history (not tracked)
├── scheduler.json        # Tasks (not tracked)
└── settings.json         # App settings (not tracked)
```

---

## ⚙️ Configuration

### AI Models

**Current Model:** dolphin-mistral (uncensored, no refusals)

**How to switch models:**
1. Open Settings
2. Click on the model name
3. Model loads on next query

### Voice Settings

- **Voice Output:** Text-to-speech responses
- **Sound Effects:** UI interaction sounds (requires send_sound.mp3)
- **Background Music:** Ambient music (requires background_music.mp3)

---

## 🔒 Privacy & Security

**What's NOT uploaded to GitHub:**
- ❌ Personal chat history
- ❌ Your memory data
- ❌ Task schedules
- ❌ Settings preferences
- ❌ Large Ollama model files

**All sensitive data stays on your computer!**

---

## 🛠️ Troubleshooting

### "Cannot connect to Ollama"
- Make sure Ollama is installed and running
- Check if `ollama serve` is active
- Verify at: http://localhost:11434

### "Model not found"
- Pull the model: `ollama pull dolphin-mistral`
- Check available models: `ollama list`

### No voice output
- Check Settings panel - ensure Voice Output is ON
- Verify pyttsx3 is installed: `pip install pyttsx3`

### Slow responses
- First query is always slower (model loading)
- Check your system resources
- Try simpler queries

---

## 🎨 Customization

### Colors
Edit the color scheme in `jarvis_gui.py`:
- `#00d4ff` - Primary cyan color
- `#0d1117` - Dark background
- `#000000` - Pure black

### Fonts
Default fonts:
- Title: Arial 28pt bold
- Chat: Consolas 11pt
- Buttons: Segoe UI 12pt bold

---

## 📝 License

This project is open source and available for personal use.

---

## 🙏 Credits

- **Ollama** - Local AI model hosting
- **dolphin-mistral** - Uncensored AI model
- **pygame** - Audio system
- **pyttsx3** - Text-to-speech
- **tkinter** - GUI framework

---

## 📧 Support

For issues or questions, please open an issue on GitHub.

---

**Made with ❤️ - Your Personal AI Assistant**

## 📝 How to Use New Features

### Scheduler Commands:
```
"add task meeting at 3pm"
"remind me to buy milk"
"schedule call with John"
"list my tasks"
"show tasks"
"clear all tasks"
```

### Settings:
- Click "Settings" button
- Click any option to toggle ON/OFF
- Settings automatically save
- Music stops/starts immediately when toggled

### Clear Chat:
- Click "Clear Chat" button
- Confirm in dialog box
- Both UI and history file are cleared

## 🎨 UI Changes

- Window size: 850x600 → 900x700
- Three buttons: Send (cyan), Clear Chat (red), Settings (blue)
- Status bar shows Voice/Sound/Music status
- Typing indicator below chatbox
- Better visual feedback

## 📊 File Structure

```
JARVIS/
├── jarvis_gui.py (improved)
├── chat_history.json (auto-limited to 100)
├── scheduler.json (now functional)
├── memory.json (unchanged)
├── settings.json (new - saves preferences)
├── send_sound.mp3 (optional)
├── background_music.mp3 (optional)
└── IMPROVEMENTS.md (this file)
```

## 🚀 Running the Application

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install any missing packages (if needed)
pip install pygame pyttsx3 SpeechRecognition psutil requests

# Run JARVIS
python jarvis_gui.py
```

## 📌 Notes

- System prompt remains unchanged as requested
- All improvements are backward compatible
- Existing memory and chat history files work fine
- Settings default to all features enabled
