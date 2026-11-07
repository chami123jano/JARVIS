@echo off
rem Start everything in JARVIS with one double-click:
rem   - the service: Ollama, the always-listening voice loop ("Hey JARVIS"), the tray icon,
rem     reminders, email, notifications, the phone door (if switched on)
rem   - the console in the browser, at http://localhost:4190
rem Starting it twice is harmless: each part runs only one copy of itself.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo JARVIS is not installed here yet. Run install.ps1 first.
    pause
    exit /b 1
)
rem Full paths, so Stop JARVIS.bat can tell these apart from any other Python program.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0jarvis_service.py"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0server.py"
rem Give the console a moment to open its port, then show it.
ping -n 4 127.0.0.1 >nul
start "" http://localhost:4190
