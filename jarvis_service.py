"""JARVIS as something that is simply running, rather than a terminal left open.

Until now every session began the same way: Ollama had died, and JARVIS only listened
while a terminal with voice_loop.py stayed open. This process owns all of that.

  - starts Ollama itself, with the model path from the user's settings, and repairs it
    when it is running from the wrong folder (an empty model list is the symptom)
  - runs the always-listening voice loop as a child, and restarts it if it dies, with
    backoff so a broken setup gives up instead of looping forever
  - a tray icon shows what it is doing, with Mute, Restart, Open log and Quit
  - Ctrl+Alt+J is a hard mute: the voice loop is stopped, so the microphone is closed,
    not merely ignored
  - only one copy runs, so the microphone is never opened twice
  - starts at login through the user's own Run key, which needs no administrator rights
    and appears in Task Manager under Startup apps, where it can be switched off

    pythonw jarvis_service.py                 run, with the tray icon
    python  jarvis_service.py --no-tray       run in the terminal, for debugging
    python  jarvis_service.py --status
    python  jarvis_service.py --enable-autostart
    python  jarvis_service.py --disable-autostart
"""
import argparse
import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / 'data' / 'logs'
SERVICE_LOG = LOG_DIR / 'service.log'
VOICE_LOG = LOG_DIR / 'voice.log'
VOICE_LOG_LIMIT = 2_000_000

PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PYTHONW = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
OLLAMA_URL = 'http://127.0.0.1:11434'

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'JARVIS'
MUTEX_NAME = 'Local\\JARVIS-service'

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000

# The mute shortcut, in order of preference. Ctrl+Alt+M was the first choice and is
# already taken on this machine by another program, so the first free one is used and
# the tray menu shows which. J for JARVIS.
MUTE_HOTKEYS = [
    ('J', MOD_CONTROL | MOD_ALT, 'Ctrl+Alt+J'),
    ('J', MOD_CONTROL | MOD_SHIFT, 'Ctrl+Shift+J'),
    ('Q', MOD_CONTROL | MOD_ALT, 'Ctrl+Alt+Q'),
]

OLLAMA_DEFAULTS = {
    'OLLAMA_MODELS': r'D:\ollama',
    'OLLAMA_FLASH_ATTENTION': '1',
    'OLLAMA_KV_CACHE_TYPE': 'q8_0',
    'OLLAMA_KEEP_ALIVE': '30m',
}

LABELS = {'starting': 'starting', 'listening': 'listening for "hey jarvis"',
          'muted': 'muted - microphone off', 'error': 'stopped after repeated crashes',
          'stopped': 'stopped'}

log = logging.getLogger('jarvis.service')


# ------------------------------------------------------------------ logging

def setup_logging(to_console=False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(SERVICE_LOG, maxBytes=1_000_000, backupCount=3,
                                  encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s  %(levelname)-7s %(message)s'))
    log.addHandler(handler)
    if to_console:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
        log.addHandler(console)
    log.setLevel(logging.INFO)


def rotate_log(path, limit=None):
    """One previous copy kept. A file still held open by its writer is left for next time."""
    try:
        if path.exists() and path.stat().st_size > (limit or VOICE_LOG_LIMIT):
            path.replace(path.with_suffix('.log.1'))
    except OSError:
        pass


def rotate_voice_log():
    """The voice loop is chatty and runs for days."""
    rotate_log(VOICE_LOG)


# ------------------------------------------------------------------ single instance

class SingleInstance:
    """A named mutex. A second copy would open the microphone a second time."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name=MUTEX_NAME):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = kernel32.GetLastError() == self.ERROR_ALREADY_EXISTS

    def release(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def service_running():
    """Is a copy already running? Checked without taking the lock ourselves."""
    probe = SingleInstance()
    running = probe.already_running
    probe.release()
    return running


# ------------------------------------------------------------------ autostart

def autostart_command():
    """pythonw, so login does not flash a console window."""
    return f'"{PYTHONW}" "{ROOT / "jarvis_service.py"}"'


def enable_autostart(key_path=RUN_KEY, value=RUN_VALUE):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, value, 0, winreg.REG_SZ, autostart_command())
    return autostart_status(key_path, value)


def disable_autostart(key_path=RUN_KEY, value=RUN_VALUE):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value)
    except FileNotFoundError:
        pass
    return autostart_status(key_path, value)


def autostart_status(key_path=RUN_KEY, value=RUN_VALUE):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            command = winreg.QueryValueEx(key, value)[0]
            return {'enabled': True, 'command': command,
                    'current': command == autostart_command()}
    except FileNotFoundError:
        return {'enabled': False}


# ------------------------------------------------------------------ ollama

def user_environment(name, default=None):
    """A variable from the user's saved environment, not this process's copy.

    The tray app Ollama ships with inherits its environment at login, so a variable set
    afterwards is invisible to it until Windows restarts. Reading the saved value and
    passing it explicitly removes that dependency on a reboot.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def ollama_environment():
    env = dict(os.environ)
    for name, default in OLLAMA_DEFAULTS.items():
        env[name] = user_environment(name, default)
    return env


def ollama_executable():
    path = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe'
    return path if path.exists() else None


def ollama_state(timeout=3):
    """'down', 'wrong-path' or 'ok'.

    'wrong-path' is Ollama answering with no models at all: the tray app started at
    login before OLLAMA_MODELS was set and is reading an empty folder on C:. It looks
    exactly like the models were lost, and they were not.
    """
    import requests
    try:
        response = requests.get(OLLAMA_URL + '/api/tags', timeout=timeout)
        response.raise_for_status()
        models = response.json().get('models', [])
    except Exception:
        return 'down'
    return 'ok' if models else 'wrong-path'


def stop_ollama():
    for image in ('ollama app.exe', 'ollama.exe'):
        subprocess.run(['taskkill', '/F', '/IM', image], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)


def start_ollama():
    exe = ollama_executable()
    if not exe:
        raise RuntimeError('Ollama is not installed')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Ollama logs every few tokens; this file had reached 9.4 MB in a day (Day 28).
    rotate_log(LOG_DIR / 'ollama.log', 5_000_000)
    output = open(LOG_DIR / 'ollama.log', 'a', encoding='utf-8')
    # Not placed in the voice loop's job: Ollama should outlive a restart of JARVIS.
    subprocess.Popen([str(exe), 'serve'], env=ollama_environment(), stdout=output,
                     stderr=output, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)


def ensure_ollama(wait=40):
    """Make sure Ollama is up and reading the right folder. True when it is."""
    state = ollama_state()
    if state == 'ok':
        return True
    if state == 'wrong-path':
        log.warning('Ollama is running with no models - restarting it with OLLAMA_MODELS=%s',
                    user_environment('OLLAMA_MODELS', OLLAMA_DEFAULTS['OLLAMA_MODELS']))
        stop_ollama()
        time.sleep(2)
    else:
        log.info('Ollama is not running - starting it')
    try:
        start_ollama()
    except Exception as error:
        log.error('Could not start Ollama: %s', error)
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        state = ollama_state()
        if state == 'ok':
            log.info('Ollama is up')
            return True
        time.sleep(1)
    log.error('Ollama did not come up within %ss (last state: %s)', wait, state)
    return False


# ------------------------------------------------------------------ watchdog

class Watchdog:
    """When to restart a crashed voice loop, and when to stop trying.

    Backoff doubles from one second, and five crashes inside five minutes is treated as
    a fault rather than bad luck. A loop that dies on startup -- no model configured, a
    microphone that has gone -- would otherwise restart every second for ever.
    """

    WINDOW = 300
    LIMIT = 5
    CEILING = 60

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.crashes = []

    def _prune(self):
        now = self.clock()
        self.crashes = [moment for moment in self.crashes if now - moment < self.WINDOW]

    def record_crash(self):
        self._prune()
        self.crashes.append(self.clock())

    def gave_up(self):
        self._prune()
        return len(self.crashes) >= self.LIMIT

    def delay(self):
        self._prune()
        return min(self.CEILING, 2 ** max(0, len(self.crashes) - 1))

    def reset(self):
        self.crashes.clear()


# ------------------------------------------------------------------ the voice loop

def voice_command(mode='wake'):
    return [str(PYTHON), str(ROOT / 'voice_loop.py'), f'--{mode}']


def voice_environment():
    env = dict(os.environ)
    # Sinhala in the log. Without this, printing a Sinhala transcript under the default
    # Windows code page raises and kills the loop -- a crash that only happens in Sinhala.
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    return env


class KillWithParent:
    """A job object, so the voice loop dies when the service does.

    Windows does not end child processes with their parent. If the service were killed
    from Task Manager, the voice loop would carry on in the background with the
    microphone open and no tray icon to say so.
    """

    def __init__(self):
        self.job = None
        try:
            import win32job
            self.job = win32job.CreateJobObject(None, '')
            info = win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] = \
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation, info)
        except Exception as error:
            log.warning('No job object (%s); the voice loop could outlive the service', error)
            self.job = None

    def adopt(self, process):
        if not self.job:
            return False
        try:
            import win32api
            import win32con
            import win32job
            handle = win32api.OpenProcess(
                win32con.PROCESS_TERMINATE | win32con.PROCESS_SET_QUOTA, False, process.pid)
            win32job.AssignProcessToJobObject(self.job, handle)
            return True
        except Exception as error:
            log.warning('Could not attach the voice loop to the job: %s', error)
            return False


# ------------------------------------------------------------------ hotkey

class Hotkey(threading.Thread):
    """A system-wide shortcut. Registered on its own thread, which must also pump messages.

    Tries each candidate in turn, because another program may already own the first.
    """

    def __init__(self, callback, candidates=None):
        super().__init__(daemon=True, name='hotkey')
        self.callback = callback
        # `is None`, not `or`: an explicit empty list means none, not the defaults.
        self.candidates = MUTE_HOTKEYS if candidates is None else candidates
        self.thread_id = None
        self.ready = threading.Event()
        self.error = None
        self.label = None

    def run(self):
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        for key, modifiers, label in self.candidates:
            if user32.RegisterHotKey(None, 1, modifiers | MOD_NOREPEAT, ord(key)):
                self.label = label
                break
        if not self.label:
            self.error = ('every mute shortcut is taken by another program: '
                          + ', '.join(label for _, _, label in self.candidates))
            self.ready.set()
            return
        log.info('mute shortcut: %s', self.label)
        self.ready.set()
        message = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception as error:
                        log.error('Hotkey action failed: %s', error)
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


# ------------------------------------------------------------------ tray

def icon_image(state):
    """A coloured dot: green listening, grey muted, amber starting, red failed."""
    from PIL import Image, ImageDraw
    colours = {'listening': (46, 204, 113), 'muted': (127, 140, 141),
               'starting': (241, 196, 15), 'error': (231, 76, 60),
               'stopped': (127, 140, 141)}
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=colours.get(state, colours['stopped']))
    if state == 'muted':
        draw.line((14, 50, 50, 14), fill=(255, 255, 255), width=6)
    else:
        draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255))
    return image


# ------------------------------------------------------------------ the service

class Service:
    def __init__(self, mode='wake', speak=True):
        self.mode = mode
        self.speak_events = speak
        self.state = 'starting'
        self.muted = False
        self.child = None
        self.child_output = None
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.watchdog = Watchdog()
        self.job = KillWithParent()
        self.icon = None
        self.shortcut = None
        self.lock = threading.RLock()

    # -- state ---------------------------------------------------------
    def set_state(self, state):
        self.state = state
        log.info('state: %s', state)
        if self.icon:
            try:
                self.icon.icon = icon_image(state)
                self.icon.title = f'JARVIS - {LABELS.get(state, state)}'
                self.icon.update_menu()
            except Exception:
                pass

    def say(self, sinhala):
        if not self.speak_events:
            return

        def run():
            try:
                import speech
                speech.SPEAKER.say(sinhala)
            except Exception as error:
                log.warning('Could not speak a status message: %s', error)
        threading.Thread(target=run, daemon=True).start()

    def notify(self, text):
        if self.icon:
            try:
                self.icon.notify(text, 'JARVIS')
            except Exception:
                pass

    # -- the child -----------------------------------------------------
    def child_alive(self):
        return self.child is not None and self.child.poll() is None

    def start_child(self):
        with self.lock:
            if self.child_alive():
                return
            rotate_voice_log()
            self.child_output = open(VOICE_LOG, 'a', encoding='utf-8')
            self.child_output.write(f'\n===== started {time.strftime("%Y-%m-%d %H:%M:%S")} =====\n')
            self.child_output.flush()
            self.child = subprocess.Popen(
                voice_command(self.mode), cwd=str(ROOT), env=voice_environment(),
                stdout=self.child_output, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
            self.job.adopt(self.child)
            log.info('voice loop started (pid %s)', self.child.pid)
            self.set_state('listening')

    def stop_child(self, wait=8):
        """Stop the voice loop and everything under it, so the microphone closes.

        The venv's python.exe is a launcher that starts the real interpreter as a child.
        Today ending the launcher ends that child too, but mute is the one action whose
        failure leaves a microphone open, so the whole tree is ended explicitly rather
        than trusting a launcher detail that could change in a future Python.
        """
        with self.lock:
            child, self.child = self.child, None
            if child and child.poll() is None:
                descendants = []
                try:
                    import psutil
                    descendants = psutil.Process(child.pid).children(recursive=True)
                except Exception:
                    pass
                child.terminate()
                try:
                    child.wait(wait)
                except subprocess.TimeoutExpired:
                    child.kill()
                for process in descendants:
                    try:
                        if process.is_running():
                            process.kill()
                    except Exception:
                        pass
                log.info('voice loop stopped')
            if self.child_output:
                try:
                    self.child_output.close()
                except OSError:
                    pass
                self.child_output = None

    # -- actions -------------------------------------------------------
    def toggle_mute(self, *_):
        with self.lock:
            self.muted = not self.muted
            if self.muted:
                self.stop_child()
                self.set_state('muted')
                log.info('muted: microphone closed')
            else:
                self.watchdog.reset()
                self.set_state('starting')
                self.wake.set()
                log.info('unmuted')

    def restart(self, *_):
        log.info('restart requested')
        with self.lock:
            self.stop_child()
            self.watchdog.reset()
            self.muted = False
            self.set_state('starting')
        self.wake.set()

    def open_log(self, *_):
        try:
            os.startfile(str(VOICE_LOG if VOICE_LOG.exists() else SERVICE_LOG))
        except OSError as error:
            log.error('Could not open the log: %s', error)

    def quit(self, *_):
        log.info('quit requested')
        self.stopping.set()
        self.wake.set()
        self.stop_child()
        self.set_state('stopped')
        if self.icon:
            self.icon.stop()

    # -- the supervising loop ------------------------------------------
    def supervise(self):
        self.set_state('starting')
        if not ensure_ollama():
            self.notify('Ollama could not be started. JARVIS will keep trying.')
        last_ollama_check = time.monotonic()
        if not self.muted:
            self.start_child()

        while not self.stopping.is_set():
            self.wake.wait(2)
            self.wake.clear()
            if self.stopping.is_set():
                break

            if time.monotonic() - last_ollama_check > 60:
                last_ollama_check = time.monotonic()
                if ollama_state() != 'ok':
                    ensure_ollama()

            if self.muted or self.state == 'error':
                continue
            if self.child is None:
                self.start_child()
                continue
            code = self.child.poll()
            if code is None:
                continue

            # The voice loop has exited without being asked to.
            log.error('voice loop exited with code %s', code)
            self.stop_child()
            self.watchdog.record_crash()
            if self.watchdog.gave_up():
                self.set_state('error')
                self.notify('JARVIS keeps stopping. Open the log from the tray menu.')
                self.say('ජාවිස් නවත්තුවා. ලොග් එක බලන්න.')
                continue
            delay = self.watchdog.delay()
            self.set_state('starting')
            log.info('restarting in %ss', delay)
            if self.stopping.wait(delay):
                break
            self.start_child()
            self.say('ජාවිස් නැවත පටන් ගත්තා.')

    # -- running -------------------------------------------------------
    def run_with_tray(self):
        import pystray
        hotkey = Hotkey(self.toggle_mute)
        hotkey.start()
        hotkey.ready.wait(3)
        if hotkey.error:
            log.warning(hotkey.error)
        self.shortcut = hotkey.label

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: f'JARVIS - {LABELS.get(self.state, self.state)}',
                             None, enabled=False),
            pystray.MenuItem(lambda item: ('Unmute' if self.muted else 'Mute')
                             + (f' ({self.shortcut})' if self.shortcut else ''),
                             self.toggle_mute, default=True),
            pystray.MenuItem('Restart', self.restart),
            pystray.MenuItem('Open log', self.open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quit', self.quit),
        )
        self.icon = pystray.Icon('JARVIS', icon_image('starting'), 'JARVIS - starting', menu)

        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.supervise, daemon=True, name='supervise').start()

        try:
            self.icon.run(setup=setup)
        finally:
            hotkey.stop()
            self.stopping.set()
            self.stop_child()

    def run_headless(self):
        hotkey = Hotkey(self.toggle_mute)
        hotkey.start()
        hotkey.ready.wait(3)
        self.shortcut = hotkey.label
        print(f'JARVIS service running. {hotkey.label or "No shortcut"} mutes. Ctrl+C stops.')
        try:
            self.supervise()
        except KeyboardInterrupt:
            pass
        finally:
            hotkey.stop()
            self.quit()


def status():
    lines = [f'  service running : {service_running()}',
             f'  autostart       : {autostart_status()}',
             f'  ollama          : {ollama_state()}',
             f'  OLLAMA_MODELS   : {user_environment("OLLAMA_MODELS", "(not set)")}',
             f'  service log     : {SERVICE_LOG}',
             f'  voice log       : {VOICE_LOG}']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='JARVIS background service')
    parser.add_argument('--no-tray', action='store_true', help='run in this terminal')
    parser.add_argument('--mode', default='wake', choices=['wake'],
                        help='push to talk needs a visible terminal, so only wake runs here')
    parser.add_argument('--quiet', action='store_true', help='do not speak status changes')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--enable-autostart', action='store_true')
    parser.add_argument('--disable-autostart', action='store_true')
    args = parser.parse_args()

    if args.status:
        print(status())
        return 0
    if args.enable_autostart:
        print(enable_autostart())
        return 0
    if args.disable_autostart:
        print(disable_autostart())
        return 0

    setup_logging(to_console=args.no_tray)
    instance = SingleInstance()
    if instance.already_running:
        log.info('another copy is already running; exiting')
        if args.no_tray:
            print('JARVIS is already running. Use the tray icon, or Quit it first.')
        return 0

    log.info('service starting (pid %s)', os.getpid())
    service = Service(mode=args.mode, speak=not args.quiet)
    try:
        if args.no_tray:
            service.run_headless()
        else:
            service.run_with_tray()
    finally:
        instance.release()
        log.info('service stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
