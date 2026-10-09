"""Control of this machine: sound, applications, windows, the screen, the clipboard.

The router has matched volume_up, open_app, screenshot and lock_pc since Day 5 and then
done nothing, because nothing stood behind them. This is what stands behind them.

Every capability is a named function. There is deliberately no "run this command" tool:
a misheard Sinhala sentence must not be able to reach a shell, and no amount of
confirmation logic makes that safe enough to be worth having.

    python pc_control.py --volume 40
    python pc_control.py --apps chrome
    python pc_control.py --open chrome
    python pc_control.py --screenshot
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

START_MENUS = [
    Path(os.environ.get('ProgramData', r'C:\ProgramData')) /
    'Microsoft/Windows/Start Menu/Programs',
    Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs',
]

# Virtual key codes for the media and volume keys. Using the keys the keyboard would
# send means the change applies wherever Windows would apply it.
VK = {'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD,
      'play_pause': 0xB3, 'next': 0xB0, 'previous': 0xB1, 'stop': 0xB2}

_apps_cache = {'at': 0, 'apps': {}}
APP_CACHE_SECONDS = 300

# What people say against what Windows calls it. "vs code" shares almost no letters with
# "Visual Studio Code", so no amount of fuzzy matching reaches it.
ALIASES = {
    'vs code': 'visual studio code', 'vscode': 'visual studio code',
    'code': 'visual studio code', 'vs': 'visual studio code',
    'chrome': 'google chrome', 'browser': 'google chrome',
    'word': 'word', 'excel': 'excel', 'ppt': 'powerpoint',
    'explorer': 'file explorer', 'files': 'file explorer',
    'cmd': 'command prompt', 'terminal': 'windows terminal',
    'calc': 'calculator', 'ගණකය': 'calculator',
    'settings': 'settings', 'සෙටින්ග්ස්': 'settings',
}


class ControlError(Exception):
    """The action did not happen, and the message says why."""


def tap(key, times=1):
    """Press a virtual key, as the keyboard would."""
    try:
        import win32api
        import win32con
    except ImportError as error:
        raise ControlError('pywin32 is required for key presses') from error
    code = VK.get(key, key) if isinstance(key, str) else key
    for _ in range(max(1, times)):
        win32api.keybd_event(code, 0, 0, 0)
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(.02)
    return True


# ------------------------------------------------------------------ sound

def mixer():
    """The system volume interface, or None if it cannot be reached.

    pycaw changed shape: GetSpeakers() now returns an AudioDevice wrapper exposing
    EndpointVolume, where it used to return a COM pointer needing Activate(). Both are
    tried, so this keeps working on either version.
    """
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, 'EndpointVolume'):
            return speakers.EndpointVolume
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return interface.QueryInterface(IAudioEndpointVolume)
    except Exception:
        return None


def volume(level=None, step=None, mute=None):
    """Read or set the volume. level is 0-100, step is a relative change.

    Falls back to the media keys when the mixer cannot be reached, because being able to
    say "louder" matters more than being able to say "set it to forty".
    """
    control = mixer()
    if control is None:
        if level is not None:
            raise ControlError('Cannot set an exact volume on this machine')
        if step:
            tap('volume_up' if step > 0 else 'volume_down', abs(int(step)) // 2 or 1)
            return {'changed': True, 'exact': False}
        if mute is not None:
            tap('mute')
            return {'muted': 'toggled', 'exact': False}
        raise ControlError('Cannot read the volume on this machine')

    current = round(control.GetMasterVolumeLevelScalar() * 100)
    if mute is not None:
        control.SetMute(bool(mute), None)
        return {'muted': bool(mute), 'volume': current}
    if level is None and step is None:
        return {'volume': current, 'muted': bool(control.GetMute())}
    target = max(0, min(100, int(level if level is not None else current + step)))
    control.SetMasterVolumeLevelScalar(target / 100, None)
    if target > 0 and control.GetMute():
        control.SetMute(False, None)
    return {'volume': target, 'was': current}


def media(action):
    """play_pause, next, previous or stop."""
    if action not in ('play_pause', 'next', 'previous', 'stop'):
        raise ControlError(f'Unknown media action: {action}')
    tap(action)
    return {'media': action}


# ------------------------------------------------------------------ applications

def start_apps():
    """Everything Windows itself lists in Start, via Get-StartApps.

    Scanning the Start Menu for .lnk files finds 158 programs and misses Notepad,
    WhatsApp and VS Code, because Store apps have no shortcut file. Get-StartApps
    returns all 223, Win32 and Store alike, each with the identifier Windows uses to
    launch it.
    """
    command = ['powershell', '-NoProfile', '-NonInteractive', '-Command',
               'Get-StartApps | ForEach-Object { "$($_.Name)|$($_.AppID)" }']
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=25,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return {}
    apps = {}
    for line in out.stdout.splitlines():
        name, _, app_id = line.partition('|')
        name, app_id = name.strip(), app_id.strip()
        if name and app_id:
            apps.setdefault(name.lower(), app_id)
    return apps


def installed_apps(refresh=False):
    """Every launchable application, by name."""
    now = time.monotonic()
    if not refresh and _apps_cache['apps'] and now - _apps_cache['at'] < APP_CACHE_SECONDS:
        return _apps_cache['apps']
    apps = start_apps()
    # Start Menu shortcuts as a fallback, in case PowerShell is unavailable.
    if not apps:
        for folder in START_MENUS:
            if not folder.is_dir():
                continue
            for link in folder.rglob('*.lnk'):
                apps.setdefault(link.stem.strip().lower(), str(link))
    _apps_cache.update(at=now, apps=apps)
    return apps


def find_app(spoken):
    """The best matching installed application, or None.

    Matching is phonetic, like everything else here, so "ක්‍රෝම්" and "chrome" both work.
    """
    from sinhala import normalize
    apps = installed_apps()
    if not apps or not spoken:
        return None
    from difflib import SequenceMatcher
    spoken = ALIASES.get(str(spoken).strip().lower(), spoken)
    wanted = normalize(spoken).replace(' ', '')
    # "zzzzqqq" collapses to "sk", which is inside "ekskel" (excel) and matched at 0.85.
    # Opening the wrong application on noise is worse than opening none.
    if len(wanted) < 3:
        return None
    best, best_score = None, 0.0
    for name, app_id in apps.items():
        target = normalize(name).replace(' ', '')
        if not target:
            continue
        score = SequenceMatcher(None, wanted, target).ratio()
        # Containment is a strong signal, but scaled by how much of the name it covers.
        # Flat scoring made "chrome" pick "Chrome Remote Desktop" over "Google Chrome",
        # because both contain it and the first one seen won.
        # Containment only counts for a substantial fragment, for the same reason.
        if len(wanted) >= 4 and wanted in target:
            score = max(score, .80 + .15 * (len(wanted) / len(target)))
        elif len(target) >= 4 and target in wanted:
            score = max(score, .80 + .15 * (len(target) / len(wanted)))
        if score > best_score:
            best, best_score = (name, app_id), score
    if best_score >= .7:
        return {'name': best[0], 'id': str(best[1]), 'score': round(best_score, 3)}
    return None


def open_app(spoken):
    app = find_app(spoken)
    if not app:
        raise ControlError(f'No installed application matches {spoken!r}')
    target = app['id']
    try:
        if os.path.exists(target):
            os.startfile(target)
        else:
            # A Start AppID, not a path. shell:AppsFolder launches Win32 and Store apps
            # alike; os.startfile cannot open an AppID directly.
            subprocess.Popen(['explorer.exe', 'shell:AppsFolder' + os.sep + target],
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as error:
        raise ControlError(f'Could not start {app["name"]}: {error}') from error
    return {'opened': app['name'], 'id': target}


# ------------------------------------------------------------------ windows

def windows():
    """Visible windows with a title."""
    import win32gui
    found = []

    def visit(handle, _):
        if win32gui.IsWindowVisible(handle):
            title = win32gui.GetWindowText(handle)
            if title.strip():
                found.append({'handle': handle, 'title': title})

    win32gui.EnumWindows(visit, None)
    return found


def focus_window(title):
    import win32con
    import win32gui
    from difflib import SequenceMatcher
    wanted = str(title).lower()
    best, best_score = None, 0.0
    for window in windows():
        score = SequenceMatcher(None, wanted, window['title'].lower()).ratio()
        if wanted in window['title'].lower():
            score = max(score, .9)
        if score > best_score:
            best, best_score = window, score
    if not best or best_score < .5:
        raise ControlError(f'No window matches {title!r}')
    win32gui.ShowWindow(best['handle'], win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(best['handle'])
    return {'focused': best['title']}


def close_window(title):
    import win32con
    import win32gui
    from difflib import SequenceMatcher
    wanted = str(title).lower()
    best, best_score = None, 0.0
    for window in windows():
        score = SequenceMatcher(None, wanted, window['title'].lower()).ratio()
        if wanted in window['title'].lower():
            score = max(score, .9)
        if score > best_score:
            best, best_score = window, score
    if not best or best_score < .6:
        raise ControlError(f'No window matches {title!r}')
    win32gui.PostMessage(best['handle'], win32con.WM_CLOSE, 0, 0)
    return {'closed': best['title']}


# ------------------------------------------------------------------ screen

def screenshot(path=None):
    """Capture the screen to a file."""
    try:
        from PIL import ImageGrab
    except ImportError as error:
        raise ControlError('Pillow is required for screenshots') from error
    folder = ROOT / 'data' / 'screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(path) if path else folder / f'{time.strftime("%Y%m%d-%H%M%S")}.png'
    image = ImageGrab.grab(all_screens=True)
    image.save(target)
    return {'saved': str(target), 'size': f'{image.width}x{image.height}'}


def lock():
    """Lock the session. Reversible, unlike the things kept at tier three."""
    if not ctypes.windll.user32.LockWorkStation():
        raise ControlError('Windows refused to lock the session')
    return {'locked': True}


# ------------------------------------------------------------------ clipboard

def clipboard(text=None):
    """Read the clipboard, or write to it."""
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        if text is None:
            try:
                return {'text': win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)}
            except TypeError:
                return {'text': ''}
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(text), win32clipboard.CF_UNICODETEXT)
        return {'copied': len(str(text))}
    finally:
        win32clipboard.CloseClipboard()


def main():
    parser = argparse.ArgumentParser(description='Control this machine')
    parser.add_argument('--volume', nargs='?', type=int, const=-1, metavar='LEVEL')
    parser.add_argument('--louder', action='store_true')
    parser.add_argument('--quieter', action='store_true')
    parser.add_argument('--mute', action='store_true')
    parser.add_argument('--apps', nargs='?', const='', metavar='FILTER')
    parser.add_argument('--open', metavar='NAME')
    parser.add_argument('--windows', action='store_true')
    parser.add_argument('--focus', metavar='TITLE')
    parser.add_argument('--screenshot', action='store_true')
    parser.add_argument('--clipboard', nargs='?', const='', metavar='TEXT')
    args = parser.parse_args()
    try:
        if args.volume is not None:
            print(volume() if args.volume < 0 else volume(level=args.volume))
        elif args.louder:
            print(volume(step=10))
        elif args.quieter:
            print(volume(step=-10))
        elif args.mute:
            print(volume(mute=True))
        elif args.apps is not None:
            apps = installed_apps()
            shown = [n for n in sorted(apps) if args.apps.lower() in n]
            print(f'{len(apps)} installed, {len(shown)} matching')
            for name in shown[:30]:
                print(f'  {name}')
        elif args.open:
            print(open_app(args.open))
        elif args.windows:
            for window in windows()[:25]:
                print(f'  {window["title"][:70]}')
        elif args.focus:
            print(focus_window(args.focus))
        elif args.screenshot:
            print(screenshot())
        elif args.clipboard is not None:
            print(clipboard(args.clipboard or None))
        else:
            parser.print_help()
    except ControlError as error:
        print(f'failed: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
