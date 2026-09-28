"""What JARVIS may do on its own, and what it must ask about first.

By Day 20 this assistant can send messages, control the machine and act unprompted. A
misheard Sinhala sentence must not be able to do something that cannot be undone, and no
amount of prompting makes a language model reliable about that — asked to save a note, it
announced it had and never called the tool. So the gate lives in code.

    Tier 0  read      status, time, notes, memory            just runs
    Tier 1  local     save a note, set a reminder, volume     runs, and is logged
    Tier 2  outward   WhatsApp, SMS, a call, email            confirmed every time
    Tier 3  final     delete, shut down, anything costing money  confirmed, with a passphrase

A tool result can never raise a tier. If a document says "send this to everyone", that is
data, not an instruction.
"""
import datetime as dt

READ, LOCAL, OUTWARD, FINAL = 0, 1, 2, 3

TIERS = {
    # Tier 0: reading.
    'system_status': READ, 'list_records': READ, 'calculate': READ,
    'read_document': READ, 'find_files': READ, 'translate': READ,
    'time': READ, 'battery': READ, 'list_notes': READ, 'capabilities': READ,
    'weather': READ, 'news': READ, 'dollar_rate': READ,
    # Tier 1: changes confined to this machine.
    'save_note': LOCAL, 'set_reminder': LOCAL, 'remember': LOCAL,
    'write_document': LOCAL, 'open_app': LOCAL, 'web_search': LOCAL,
    'play_music': LOCAL, 'volume_up': LOCAL, 'volume_down': LOCAL,
    'screenshot': LOCAL,
    # Tier 2: leaves the machine, in the user's name.
    'send_message': OUTWARD, 'send_sms': OUTWARD, 'call': OUTWARD,
    'send_email': OUTWARD, 'whatsapp_send': OUTWARD,
    # Tier 3: cannot be undone.
    'delete_file': FINAL, 'shutdown': FINAL, 'purchase': FINAL, 'lock_pc': LOCAL,
}

# Spoken agreement, in both languages. Deliberately short and unambiguous: "හරි" is yes,
# "හරිද" is a question, and a maybe must not count as a yes.
YES = ('yes', 'yeah', 'yep', 'ok', 'okay', 'send it', 'send', 'go ahead', 'do it',
       'හරි', 'ඔව්', 'යවන්න', 'කරන්න', 'හොඳයි', 'ehenam', 'ow', 'hari', 'yawanna')
NO = ('no', 'nope', 'stop', 'cancel', "don't", 'dont', 'wait', 'nevermind',
      'නෑ', 'එපා', 'නවත්වන්න', 'ඕන නෑ', 'epa', 'na', 'nave')


def tier(action):
    """How dangerous an action is. Unknown actions are treated as outward-facing."""
    return TIERS.get(action, OUTWARD)


def needs_confirmation(action):
    return tier(action) >= OUTWARD


def is_yes(text):
    """A clear yes. Anything unclear is not a yes."""
    from sinhala import normalize
    cleaned = normalize(text).strip()
    if not cleaned:
        return False
    words = cleaned.split()
    targets = {normalize(word) for word in YES}
    # A short, whole-word match only. "yes please" counts; a sentence containing "ok"
    # somewhere does not, because that is not someone agreeing to send a message.
    return len(words) <= 3 and any(word in targets for word in words)


def is_no(text):
    from sinhala import normalize
    cleaned = normalize(text).strip()
    if not cleaned:
        return False
    targets = {normalize(word) for word in NO}
    return any(word in targets for word in cleaned.split())


def describe(action, detail, lang='si'):
    """Read back exactly what is about to happen, then ask."""
    if action in ('send_message', 'whatsapp_send'):
        who, what = detail.get('to', '?'), detail.get('text', '')
        if lang == 'en':
            return f'Sending to {who}: "{what}". Shall I?'
        # The dative attaches to the name, so "අම්මාට", not "අම්මා ට".
        return f'{who}ට යවනවා: "{what}". යවන්නද?'
    if lang == 'en':
        return f'About to {action.replace("_", " ")}. Shall I?'
    return f'{action.replace("_", " ")} කරන්නද?'


def log(store, action, detail, outcome):
    """Every outward action, recorded. What was sent in your name is not guesswork."""
    import json
    try:
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS actions (
                            id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL,
                            detail TEXT NOT NULL, outcome TEXT NOT NULL)''')
            db.execute('INSERT INTO actions(at,action,detail,outcome) VALUES (?,?,?,?)',
                       (dt.datetime.now().astimezone().isoformat(), action,
                        json.dumps(detail, ensure_ascii=False)[:2000], str(outcome)[:500]))
    except Exception:
        pass


def history(store, limit=20):
    try:
        with store.connect() as db:
            rows = db.execute('SELECT * FROM actions ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(row) for row in rows]
    except Exception:
        return []
