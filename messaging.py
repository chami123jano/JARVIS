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


BOX_PREFIX = 'type a message to '


def message_box():
    """WhatsApp's message box and the window it is in, or (None, None).

    WhatsApp Desktop 2.26 is a web page inside a window; its box is an edit control named
    "Type a message to Amma", whose value is what is typed in it ("\\n" when empty).
    """
    try:
        import uiautomation as auto
    except ImportError:
        return None, None
    for window in auto.GetRootControl().GetChildren():
        if not any(hint in (window.Name or '').lower() for hint in WHATSAPP_WINDOW_HINTS):
            continue
        box = window.EditControl(searchDepth=40, RegexName='(?i)^type a message')
        try:
            if box.Exists(0, 0):
                return window, box
        except Exception:
            continue
    return None, None


def chat_name(box):
    """Who the box writes to: "Amma" from "Type a message to Amma"."""
    name = str(getattr(box, 'Name', '') or '')
    return name[len(BOX_PREFIX):].strip() if name.lower().startswith(BOX_PREFIX) else name


def box_text(box):
    try:
        return str(box.GetValuePattern().Value or '').strip()
    except Exception:
        return ''


def is_their_chat(name, before, names=(), phone=''):
    """Is the open chat the one the link asked for?

    The link opens the chat a moment later, and pasting into the chat that was open before
    sends your words to someone else. So it counts only once the chat changed, or its name
    is the contact's (WhatsApp's own name for them may differ: then only a change counts).
    """
    if not name:
        return False
    if before is not None and name != before:
        return True
    plain = name.lower()
    for known in names:
        known = str(known or '').lower().strip()
        if known and (known == plain or known in plain.split() or plain in known.split()):
            return True
    digits = ''.join(c for c in name if c.isdigit())
    wanted = ''.join(c for c in str(phone) if c.isdigit())
    return bool(digits) and len(digits) >= 9 and wanted.endswith(digits[-9:])


def wait_for_chat(before, names=(), phone='', wait=8.0):
    """The message box once the right chat is open, or None."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        window, box = message_box()
        if box is not None and is_their_chat(chat_name(box), before, names, phone):
            return window, box
        time.sleep(.25)
    return None, None


def same_text(typed, wanted):
    squash = lambda s: ' '.join(str(s).split())
    return squash(typed) == squash(wanted)


def _clipboard_text():
    import win32clipboard as clipboard
    clipboard.OpenClipboard()
    try:
        if clipboard.IsClipboardFormatAvailable(clipboard.CF_UNICODETEXT):
            return clipboard.GetClipboardData(clipboard.CF_UNICODETEXT)
        return None
    finally:
        clipboard.CloseClipboard()


def _set_clipboard(text):
    import win32clipboard as clipboard
    clipboard.OpenClipboard()
    try:
        clipboard.EmptyClipboard()
        clipboard.SetClipboardData(clipboard.CF_UNICODETEXT, text)
    finally:
        clipboard.CloseClipboard()


def click(box):
    """A real click in the box, the pointer put back after. Measured 7 Nov: once JARVIS
    has started another program (the link), focusing the box through UI Automation
    reports focus but Ctrl+V lands in nothing; a click gives the page the keyboard."""
    import ctypes
    import win32api
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    where = win32api.GetCursorPos()
    try:
        box.Click(simulateMove=False, waitTime=.3)
    finally:
        try:
            win32api.SetCursorPos(where)
        except Exception:
            pass


def type_into(window, box, text):
    """Paste the text into the box and read it back. Measured 7 Nov: WhatsApp 2.2642
    opens the chat from the link but drops its text, so the box stayed empty while
    JARVIS said it was typed."""
    import uiautomation as auto
    if same_text(box_text(box), text):
        return True                     # an older WhatsApp that keeps the link's text
    if box_text(box):
        raise SendError('there is already something typed in that chat')
    try:
        saved = _clipboard_text()
    except Exception:
        saved = None
    try:
        _set_clipboard(text)
        # Twice at most: a paste made while the chat is still drawing lands in nothing.
        for _ in range(2):
            window.SetActive()
            window.SetFocus()
            click(box)
            if not (foreground_is_whatsapp() and box.HasKeyboardFocus):
                raise SendError('WhatsApp is not the active window')
            if not box_text(box):
                auto.SendKeys('{Ctrl}v', waitTime=.3)
            for _ in range(6):
                time.sleep(.25)
                if box_text(box):
                    break
            if box_text(box):
                break
            window, fresh = message_box()
            box = fresh if fresh is not None else box
    finally:
        try:
            if saved is not None:
                _set_clipboard(saved)
        except Exception:
            pass
    return same_text(box_text(box), text)


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


def send(phone, text, auto_send=False, names=(), settle=.3):
    """Open the chat, type the message, and press Enter only when explicitly told to.

    auto_send is False by default. With it off, the message sits visibly in your own
    WhatsApp window and you press Enter, so nothing leaves the machine without a human
    keystroke. That is a weaker promise to make than 'the confirmation logic is correct',
    and a much easier one to keep.

    'typed' is True only when the box was read back with the message in it.
    """
    try:
        import uiautomation as auto
        in_thread = auto.UIAutomationInitializerInThread()
    except ImportError as error:
        raise SendError('uiautomation is required to type the message') from error
    with in_thread:
        return _send(phone, text, auto_send, names, settle)


def _send(phone, text, auto_send, names, settle):
    _, box = message_box()
    before = chat_name(box) if box is not None else None
    result = open_chat(phone, text)
    result['sent'] = False
    window, box = wait_for_chat(before, names, phone)
    if box is None:
        raise SendError('WhatsApp opened, but not on that chat, so nothing was typed')
    time.sleep(1.0)                     # the chat finishes drawing; its box is replaced
    window, fresh = message_box()
    if fresh is not None and chat_name(fresh) == chat_name(box):
        box = fresh
    result['chat'] = chat_name(box)
    result['typed'] = type_into(window, box, text)
    if not result['typed']:
        raise SendError('WhatsApp opened the chat, but the message did not appear in it')
    if not auto_send:
        result['note'] = 'Message is typed in WhatsApp. Press Enter there to send it.'
        return result
    time.sleep(settle)
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
