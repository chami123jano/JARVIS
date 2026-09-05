# J.A.R.V.I.S - AI Assistant

## New command console

Run `.\start.ps1` in PowerShell, then open **http://localhost:4190**.
The new console includes local AI tool execution, an animated interface, SQLite memory,
timed reminders, document tools, voice controls, and an activity journal.
See [CONSOLE.md](CONSOLE.md) for setup, capabilities, limitations, and tests.
The original Tkinter application described below remains available as `jarvis_gui.py`.

**Just A Rather Very Intelligent System**

A professional desktop AI assistant powered by Ollama with a beautiful GUI interface and uncensored conversational capabilities.

![JARVIS](https://img.shields.io/badge/Python-3.13+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Status](https://img.shields.io/badge/Status-Active-success.svg)

---

## 🌟 Features

- ✨ **Professional Modern GUI** - Beautiful cyan/black themed interface with live clock
- 💬 **Uncensored AI** - Uses dolphin-mistral model for unrestricted conversations
- 🗣️ **Text-to-Speech** - Voice responses powered by pyttsx3
- 📝 **Chat History** - Saves and maintains conversation context (last 100 messages)
- 🧠 **Memory System** - Remembers user information across sessions
- 📅 **Task Scheduler** - Add, list, and manage tasks
- 🌤️ **Weather Info** - Real-time weather updates
- 💻 **System Monitoring** - CPU and RAM usage display
- ⚙️ **Customizable Settings** - Toggle voice, sounds, and switch AI models
- 🔄 **Context Awareness** - Maintains conversation flow with recent context

---

## 📦 Prerequisites

### Required Software

**1. Python 3.13 or higher**
- Download from: https://www.python.org/downloads/
- Make sure to check "Add Python to PATH" during installation

**2. Ollama**
- Download from: https://ollama.com/download
- Install and it will run automatically in the background

**3. Python Packages**

Install all required dependencies:
```bash
pip install pygame pyttsx3 requests psutil pillow
```

**4. AI Model**

Pull the required AI model (this will download ~4GB):
```bash
ollama pull dolphin-mistral
```

**Optional models:**
```bash
ollama pull openhermes    # Alternative standard model
```

---

## 🚀 Quick Start

### Step 1: Install Everything
```bash
# Install Python packages
pip install pygame pyttsx3 requests psutil pillow

# Pull the AI model
ollama pull dolphin-mistral
```

### Step 2: Run JARVIS
```bash
cd JARVIS
python jarvis_gui.py
```

### Step 3: Start Chatting!
- Type your message in the input box
- Press Enter or click the Send button
- JARVIS will respond with text and voice

---

## 🎯 How to Use

### Basic Commands

**Time & Date:**
```
"what time is it"
"current date"
"what's today's date"
```

**Memory Functions:**
```
"remember my name is John"
"what is my name"
```

**Weather:**
```
"weather"  (defaults to Colombo)
```

**System Information:**
```
"system info"
"status"
```

**Task Management:**
```
"add task buy groceries"
"remind me to call mom"
"list tasks"
"show my tasks"
"clear tasks"
```

### GUI Controls

**Buttons:**
- **▶ Send** - Send your message (or press Enter)
- **🗑 Clear** - Clear chat history (with confirmation)
- **⚙ Settings** - Open settings panel

**Settings Panel:**
- **🔊 Voice Output** - Toggle text-to-speech ON/OFF
- **🔔 Sound Effects** - Toggle UI sounds ON/OFF
- **🎵 Background Music** - Toggle background music ON/OFF
- **🤖 Model Selection** - Switch between dolphin-mistral and openhermes

---

## 📁 Project Structure

```
JARVIS/
├── jarvis_gui.py          # Main application file
├── README.md              # This documentation
├── .gitignore            # Git exclusion rules
├── background.mp3        # Background music (optional)
├── send_sound.mp3        # Send button sound (optional)
├── memory.json           # User memory storage (not tracked)
├── chat_history.json     # Conversation history (not tracked)
├── scheduler.json        # Task list (not tracked)
└── settings.json         # App preferences (not tracked)
```

---

## ⚙️ Configuration

### AI Models

**Default Model:** dolphin-mistral
- Uncensored, no content filtering
- Answers all questions directly
- No ethical warnings or refusals

**Alternative Model:** openhermes
- More standard responses
- Some content filtering

**How to Switch Models:**
1. Click the **⚙ Settings** button
2. Click on your preferred model
3. The model will load on your next query

### Audio Settings

**Voice Output:**
- Uses system TTS (Text-to-Speech)
- Can be toggled in Settings
- Reads JARVIS responses aloud

**Sound Effects:**
- Requires `send_sound.mp3` file
- Optional enhancement
- Can be disabled in Settings

**Background Music:**
- Requires `background_music.mp3` file
- Optional ambient sound
- Can be disabled in Settings

---

## 🔒 Privacy & Security

### What Stays Local (NOT uploaded to GitHub):

- ❌ **memory.json** - Your personal information
- ❌ **chat_history.json** - All your conversations
- ❌ **scheduler.json** - Your task list
- ❌ **settings.json** - Your preferences
- ❌ **blobs/** - Large Ollama model files
- ❌ **manifests/** - Model manifests
- ❌ ***.exe, *.msi** - Installer files

### Privacy Features:

- ✅ **100% Offline** - All AI processing happens locally
- ✅ **No Cloud Connection** - No data sent to external servers
- ✅ **Complete Control** - All your data stays on your machine
- ✅ **Open Source** - Inspect the code anytime

---

## 🛠️ Troubleshooting

### "Cannot connect to Ollama at localhost:11434"

**Solution:**
1. Make sure Ollama is installed
2. Check if Ollama is running:
   ```bash
   ollama serve
   ```
3. Verify it's accessible: open http://localhost:11434 in browser

### "Model not found" or "Model returned status code 404"

**Solution:**
```bash
# Check installed models
ollama list

# Pull the required model
ollama pull dolphin-mistral
```

### No Voice Output

**Solution:**
1. Open Settings and check if "Voice Output" is ON
2. Verify pyttsx3 is installed:
   ```bash
   pip install pyttsx3
   ```
3. Check your system's TTS settings

### Slow Responses

**Common causes:**
- First query after startup (model loading) - this is normal
- Large context/long conversations - clear chat history
- System resources low - close other applications
- Complex queries - try simpler questions

### Application Won't Start

**Solution:**
```bash
# Reinstall dependencies
pip install --upgrade pygame pyttsx3 requests psutil pillow

# Check Python version (should be 3.13+)
python --version

# Run from correct directory
cd JARVIS
python jarvis_gui.py
```

---

## 🎨 Customization

### Color Scheme

Edit colors in `jarvis_gui.py`:
```python
# Primary colors
"#00d4ff"  # Cyan (main theme color)
"#00a8cc"  # Darker cyan (status bar)
"#0d1117"  # Dark background (chat area)
"#000000"  # Pure black (window background)
```

### Fonts

Default fonts used:
- **Title**: Arial 28pt bold
- **Chat Text**: Consolas 11pt
- **Buttons**: Segoe UI 12pt bold
- **Status**: Segoe UI 9pt

### Window Size

Default: 1000x750 pixels
```python
window.geometry("1000x750")  # Change to your preferred size
```

---

## 💻 System Requirements

**Minimum:**
- OS: Windows 10/11, macOS 10.15+, Linux
- RAM: 8GB (Ollama uses ~4GB)
- Storage: 10GB free space (for AI models)
- CPU: Multi-core processor recommended

**Recommended:**
- RAM: 16GB or more
- GPU: Not required but can speed up responses
- SSD: For faster model loading

---

## 🧪 Tech Stack

- **Python 3.13+** - Core programming language
- **tkinter** - GUI framework (built-in with Python)
- **Ollama** - Local AI model runtime
- **dolphin-mistral** - Uncensored AI language model
- **pygame** - Audio playback system
- **pyttsx3** - Text-to-speech engine
- **requests** - HTTP communication with Ollama
- **psutil** - System monitoring
- **Pillow** - Image processing (reserved for future features)

---

## 🤝 Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest new features
- Submit pull requests
- Improve documentation

---

## 📝 License

MIT License - Feel free to use and modify for personal or commercial projects.

---

## 🙏 Acknowledgments

- **Ollama Team** - For making local AI accessible
- **Eric Hartford** - Creator of dolphin-mistral uncensored model
- **Python Community** - For excellent libraries and tools
- **Open Source Contributors** - For making this possible

---

## 📧 Support

Having issues? 
- Check the Troubleshooting section above
- Open an issue on GitHub
- Review Ollama documentation: https://ollama.com/docs

---

## 🎯 Future Features (Roadmap)

- [ ] Image upload and analysis (with llava model)
- [ ] Multiple language support
- [ ] Custom wake word hotkey
- [ ] Plugin system
- [ ] Dark/Light theme toggle
- [ ] Export conversations
- [ ] Voice input (speech-to-text)
- [ ] Multi-model conversations

---

**Made with ❤️ for productivity and privacy**

*Your personal AI assistant that respects your privacy and freedom*
