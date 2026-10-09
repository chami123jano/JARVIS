"""Hearing about messages that arrive, without scraping anything.

Windows hands any program the notifications other applications post, through
UserNotificationListener. That is how JARVIS learns that amma messaged: it never touches
the WhatsApp window, so a WhatsApp redesign cannot break it, it costs no data at all, and
the same code covers Telegram, email and anything else once permitted.

This listener sees EVERYTHING, including banking codes and private messages from people
you did not ask about. So:

  - the allowlist starts empty; nothing is listened to until it is added
  - one-time codes are never spoken aloud, only shown
  - message bodies are not written to disk unless asked for
  - a contact can be muted without muting the application

    python notifications.py --check
    python notifications.py --watch
    python notifications.py --allow WhatsApp
"""
import argparse
import asyncio
import re
import sys
import time

POLL_SECONDS = 2.0
SEEN_LIMIT = 400

# A code you must not have read out in a room with other people in it. Matched before
# anything is spoken, and deliberately broad: a false positive shows the text on screen,
# a false negative reads your banking code aloud.
CODE_PATTERNS = [
    re.compile(r'\b\d{4,8}\b.{0,40}\b(otp|code|pin|password|verification|verify)\b', re.I),
    re.compile(r'\b(otp|code|pin|password|verification|verify)\b.{0,40}\b\d{4,8}\b', re.I),
    re.compile(r'\b\d{3}[\s-]\d{3}\b'),                       # 123 456
    re.compile(r'(එක්\s*වරක්|රහස්|කේතය|පාස්වර්ඩ්)'),            # one-time, secret, code
]

# Applications whose notifications are worth hearing about at all, once allowed.
KNOWN_MESSAGING = ('whatsapp', 'telegram', 'signal', 'messenger', 'slack', 'discord',
                   'mail', 'outlook', 'gmail')


class NotAvailable(Exception):
    """The notification listener could not be used."""


def looks_like_a_code(text):
    """Would reading this aloud hand someone a credential?"""
    return any(pattern.search(str(text)) for pattern in CODE_PATTERNS)


async def _listener():
    try:
        from winrt.windows.ui.notifications.management import (
            UserNotificationListener, UserNotificationListenerAccessStatus)
    except ImportError as error:
        raise NotAvailable(f'winrt is not installed: {error}') from error
    listener = UserNotificationListener.current
    status = await listener.request_access_async()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        raise NotAvailable(
            'Windows has not granted notification access. '
            'Settings > Privacy & security > Notifications')
    return listener


async def _read_all():
    from winrt.windows.ui.notifications import NotificationKinds
    listener = await _listener()
    items = await listener.get_notifications_async(NotificationKinds.TOAST)
    out = []
    for item in items:
        try:
            app = (item.app_info.display_info.display_name
                   if item.app_info and item.app_info.display_info else '')
            texts = []
            visual = item.notification.visual if item.notification else None
            binding = visual.get_binding('ToastGeneric') if visual else None
            if binding:
                texts = [e.text for e in binding.get_text_elements() if e.text]
            out.append({'id': item.id, 'app': app or '?',
                        'title': texts[0] if texts else '',
                        'body': ' '.join(texts[1:]) if len(texts) > 1 else '',
                        'texts': texts})
        except Exception:
            continue
    return out


def read_all():
    """Every notification currently on screen."""
    return asyncio.run(_read_all())


def available():
    try:
        asyncio.run(_listener())
        return True
    except Exception:
        return False


def allowed_apps(store):
    """Applications permitted to interrupt. Empty until something is added."""
    return [a.lower() for a in (store.config('notify_apps', []) or [])]


def allow(store, app):
    apps = store.config('notify_apps', []) or []
    if app.lower() not in [a.lower() for a in apps]:
        apps.append(app)
        store.set_config('notify_apps', apps)
    return apps


def disallow(store, app):
    apps = [a for a in (store.config('notify_apps', []) or [])
            if a.lower() != app.lower()]
    store.set_config('notify_apps', apps)
    return apps


def muted_senders(store):
    return [s.lower() for s in (store.config('notify_mute', []) or [])]


def describe(item, store, lang='si'):
    """What to say about a notification, or None if it should stay silent.

    Returns (spoken, shown). spoken may be None while shown still has the text, which is
    how a one-time code reaches the user without being read out loud.
    """
    import contacts

    app = (item.get('app') or '').lower()
    if not any(allowed in app for allowed in allowed_apps(store)):
        return None

    sender = (item.get('title') or '').strip()
    body = (item.get('body') or '').strip()
    if not sender and not body:
        return None
    if sender.lower() in muted_senders(store):
        return None

    # The sender as you would say it: the contact's nickname when known.
    person = contacts.find(store, sender)
    called = contacts.speakable(person, lang) if person else sender

    if looks_like_a_code(f'{sender} {body}'):
        spoken = (f'{called} sent you a code. It is on the screen.' if lang == 'en'
                  else f'{called}ගෙන් කේතයක් ආවා. ස්ක්‍රීන් එකේ තියෙනවා.')
        return {'spoken': spoken, 'shown': f'{sender}: {body}', 'code': True,
                'sender': sender, 'app': item.get('app')}

    if not store.config('read_message_body', True):
        spoken = (f'{called} messaged you.' if lang == 'en'
                  else f'{called}ගෙන් මැසේජ් එකක් ආවා.')
        return {'spoken': spoken, 'shown': f'{sender}: {body}', 'code': False,
                'sender': sender, 'app': item.get('app'), 'body': body}

    if lang == 'en':
        spoken = f'{called} says: {body}' if body else f'{called} messaged you.'
    else:
        spoken = (f'{called}ගෙන් මැසේජ් එකක් ආවා. {body}' if body
                  else f'{called}ගෙන් මැසේජ් එකක් ආවා.')
    return {'spoken': spoken, 'shown': f'{sender}: {body}', 'code': False,
            'sender': sender, 'app': item.get('app'), 'body': body}


class Watcher:
    """Polls for new notifications and hands each one to a callback.

    Polling rather than subscribing: the event callback needs a running WinRT message
    loop, and two seconds of latency on a message is not worth that complexity.
    """

    def __init__(self, store):
        self.store = store
        self.seen = set()
        self.running = False

    def prime(self):
        """Treat everything already on screen as old, so starting up says nothing."""
        try:
            self.seen = {item['id'] for item in read_all()}
        except Exception:
            self.seen = set()
        return len(self.seen)

    def poll(self):
        """New notifications since the last call."""
        try:
            items = read_all()
        except Exception:
            return []
        fresh = [item for item in items if item['id'] not in self.seen]
        self.seen.update(item['id'] for item in items)
        if len(self.seen) > SEEN_LIMIT:
            self.seen = set(list(self.seen)[-SEEN_LIMIT:])
        return fresh

    def watch(self, on_message, lang='si', interval=POLL_SECONDS):
        self.prime()
        self.running = True
        while self.running:
            for item in self.poll():
                described = describe(item, self.store, lang)
                if described:
                    try:
                        on_message(described)
                    except Exception as error:
                        print(f'  notification handler failed: {error}', file=sys.stderr)
            time.sleep(interval)

    def stop(self):
        self.running = False


def main():
    from assistant_core import ROOT, Store
    parser = argparse.ArgumentParser(description='Incoming message notifications')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--allow', metavar='APP')
    parser.add_argument('--disallow', metavar='APP')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--lang', default='si')
    args = parser.parse_args()
    store = Store(ROOT / 'data' / 'jarvis.db')

    if args.allow:
        print('allowed:', allow(store, args.allow))
        return 0
    if args.disallow:
        print('allowed:', disallow(store, args.disallow))
        return 0
    if args.list:
        print('allowed applications:', allowed_apps(store) or '(none - nothing is heard)')
        print('muted senders:', muted_senders(store) or '(none)')
        print('reads the message body:', store.config('read_message_body', True))
        return 0

    if args.check:
        print(f'  listener available: {available()}')
        try:
            items = read_all()
        except NotAvailable as error:
            print(f'  {error}')
            return 1
        apps = sorted({item['app'] for item in items})
        print(f'  {len(items)} notifications on screen, from: {", ".join(apps) or "none"}')
        print(f'  allowed: {allowed_apps(store) or "(none yet)"}')
        messaging = [a for a in apps if any(k in a.lower() for k in KNOWN_MESSAGING)]
        if messaging:
            print(f'  messaging apps seen: {", ".join(messaging)}')
        print('\n  code detection:')
        for sample in ['Your OTP is 384521', 'ඔබගේ කේතය 4821', 'මම ගෙදර එනවා',
                       'Meeting at 3', 'verification code: 992 110']:
            print(f'    {"BLOCKED" if looks_like_a_code(sample) else "spoken "}  {sample}')
        return 0

    if args.watch:
        import speech
        watcher = Watcher(store)
        print(f'Watching. Allowed: {allowed_apps(store) or "(nothing - use --allow WhatsApp)"}')
        print('Ctrl+C to stop.\n')

        def announce(message):
            print(f'  {message["shown"][:100]}')
            print(f'  -> {message["spoken"][:100]}')
            speech.SPEAKER.say(message['spoken'])

        try:
            watcher.watch(announce, args.lang)
        except KeyboardInterrupt:
            print('\nStopped.')
        return 0

    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
