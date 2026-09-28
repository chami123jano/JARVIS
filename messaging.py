"""Sending messages through the WhatsApp desktop application.

Uses WhatsApp's own deep link, whatsapp://send?phone=...&text=..., which opens the right
chat with the message already typed. That is the same mechanism as clicking a wa.me link,
so there is no browser to automate, no QR code to rescan and no page layout to break.

Nothing here is done by the language model. Asked to save a note, the model announced it
had saved one and never called the tool; the same failure applied to a message would tell
you your mother had been contacted when she had not. The application either opened with
the text in it or it did not, and that is what gets reported.

    python messaging.py --check
    python messaging.py --preview amma "mama rathri enawa"
"""
import argparse
import subprocess
import sys
import time
import urllib.parse

WHATSAPP_WINDOW_HINTS = ('whatsapp',)
LAUNCH_TIMEOUT = 20          # seconds to wait for the window to appear
MAX_MESSAGE = 4000


class SendError(Exception):
    """The message was not sent, and the reason is the message."""


def build_link(phone, text):
    """The deep link for a message.

    Sinhala is multi-byte, so the text is percent-encoded as UTF-8. Encoding it carelessly
    delivers rubbish, which is worse than failing because it still looks like it worked.
    """
    digits = ''.join(c for c in str(phone) if c.isdigit())
    if not digits:
        raise SendError('That contact has no usable phone number')
    body = str(text)[:MAX_MESSAGE]
    return f'whatsapp://send?phone={digits}&text={urllib.parse.quote(body, safe="")}'


def whatsapp_windows():
    """Titles and handles of visible WhatsApp windows."""
    try:
        import win32gui
    except ImportError:
        return []
    found = []

    def visit(handle, _):
        if not win32gui.IsWindowVisible(handle):
            return
        title = win32gui.GetWindowText(handle) or ''
        if any(hint in title.lower() for hint in WHATSAPP_WINDOW_HINTS):
            found.append((handle, title))

    win32gui.EnumWindows(visit, None)
    return found


def foreground_is_whatsapp():
    try:
        import win32gui
        handle = win32gui.GetForegroundWindow()
        title = (win32gui.GetWindowText(handle) or '').lower()
        return any(hint in title for hint in WHATSAPP_WINDOW_HINTS)
    except Exception:
        return False


def installed():
    """Is WhatsApp Desktop present and its protocol registered?"""
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                winreg.OpenKey(root, r'SOFTWARE\Classes\whatsapp').Close()
                return True
            except OSError:
                continue
    except ImportError:
        pass
    return False


def open_chat(phone, text, wait=LAUNCH_TIMEOUT):
    """Open the chat with the message typed in. Does not send it."""
    if not installed():
        raise SendError('WhatsApp Desktop is not installed on this machine')
    link = build_link(phone, text)
    before = {handle for handle, _ in whatsapp_windows()}
    try:
        subprocess.Popen(['cmd', '/c', 'start', '', link], shell=False,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as error:
        raise SendError(f'Could not open WhatsApp: {error}') from error

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        windows = whatsapp_windows()
        if windows and (set(h for h, _ in windows) - before or foreground_is_whatsapp()):
            return {'opened': True, 'window': windows[0][1], 'link': link}
        time.sleep(.25)
    if whatsapp_windows():
        return {'opened': True, 'window': whatsapp_windows()[0][1], 'link': link,
                'note': 'window was already open; the chat may need a moment'}
    raise SendError('WhatsApp did not open within %d seconds' % wait)


def press_enter():
    """Send Return to whatever has focus, only once WhatsApp actually has it.

    A blind keystroke lands wherever focus happens to be. If the user alt-tabbed while the
    application was starting, that is a message typed into something else.
    """
    if not foreground_is_whatsapp():
        raise SendError('WhatsApp is not the active window; nothing was sent')
    try:
        import win32api
        import win32con
    except ImportError as error:
        raise SendError('pywin32 is required to press Enter') from error
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(.05)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
    return True


def send(phone, text, auto_send=False, settle=1.2):
    """Open the chat, and press Enter only when explicitly told to.

    auto_send is False by default. With it off, the message sits visibly in your own
    WhatsApp window and you press Enter, so nothing leaves the machine without a human
    keystroke. That is a weaker promise to make than 'the confirmation logic is correct',
    and a much easier one to keep.
    """
    result = open_chat(phone, text)
    result['sent'] = False
    if not auto_send:
        result['note'] = 'Message is typed in WhatsApp. Press Enter there to send it.'
        return result
    time.sleep(settle)          # let the text finish arriving in the box
    press_enter()
    result['sent'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description='WhatsApp messaging')
    parser.add_argument('--check', action='store_true', help='report what is available')
    parser.add_argument('--preview', nargs=2, metavar=('CONTACT', 'TEXT'),
                        help='show the link that would be opened, without opening it')
    parser.add_argument('--open', nargs=2, metavar=('CONTACT', 'TEXT'),
                        help='open the chat with the text typed in, without sending')
    args = parser.parse_args()

    if args.check:
        print(f'  WhatsApp Desktop installed : {installed()}')
        windows = whatsapp_windows()
        print(f'  windows open               : {[t for _, t in windows] or "none"}')
        print(f'  WhatsApp has focus         : {foreground_is_whatsapp()}')
        sample = build_link('94771234567', 'ආයුබෝවන් අම්මා')
        print(f'  sample link                : {sample[:96]}')
        return

    if args.preview or args.open:
        from assistant_core import ROOT, Store
        import contacts
        name, text = args.preview or args.open
        store = Store(ROOT / 'data' / 'jarvis.db')
        person = contacts.find(store, name)
        if not person:
            print(f'No contact matches {name!r}. Add one with contacts.py --add')
            return 1
        print(f'  to   : {person["name"]}  {person["phone"]}')
        print(f'  text : {text}')
        print(f'  link : {build_link(person["phone"], text)[:110]}')
        if args.open:
            print(' ', open_chat(person['phone'], text))
        return 0

    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
