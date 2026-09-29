"""People JARVIS is allowed to contact.

Seeded by hand and never inferred. A misheard command that picks the wrong recipient
sends a private message to the wrong person, which cannot be undone, so the only names
that exist are the ones deliberately entered.

Matching is phonetic, like commands: "අම්මා", "ammata" and "amma" are the same person,
and Whisper may render any of them in a different script.

    python contacts.py --list
    python contacts.py --add amma 94771234567 --nickname අම්මා
    python contacts.py --find "අම්මට"
    python contacts.py --remove amma
"""
import argparse
import re
import sys

from sinhala import normalize, similarity

# Dative and other case endings. "අම්මට" is "to amma", and the name is "amma"; without
# stripping these, a spoken recipient never matches the stored contact.
SUFFIXES = ('ටත්', 'ගෙන්', 'ට', 'ගේ', 'ව', 'ne', 'ta', 'ge')
MATCH_FLOOR = .72


def ensure_table(store):
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS contacts (
                        name TEXT PRIMARY KEY,
                        nickname TEXT NOT NULL DEFAULT '',
                        phone TEXT NOT NULL,
                        channel TEXT NOT NULL DEFAULT 'whatsapp',
                        created TEXT NOT NULL)''')


def clean_phone(phone):
    """Full international form, digits only: 94771234567.

    WhatsApp's deep link takes no '+', no spaces and no leading zero. A Sri Lankan
    number given as 0771234567 is rewritten rather than refused, since that is how
    everyone writes it here.
    """
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        raise ValueError('A contact needs a phone number')
    if digits.startswith('0'):
        digits = '94' + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValueError(f'{phone!r} does not look like a phone number')
    return digits


def add(store, name, phone, nickname='', channel='whatsapp'):
    import datetime as dt
    ensure_table(store)
    name = str(name).strip()
    if not name:
        raise ValueError('A contact needs a name')
    if channel not in ('whatsapp', 'telegram', 'sms'):
        raise ValueError('Channel must be whatsapp, telegram or sms')
    record = (name, str(nickname).strip(), clean_phone(phone), channel,
              dt.datetime.now().astimezone().isoformat())
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO contacts VALUES (?,?,?,?,?)', record)
    return {'name': name, 'nickname': nickname, 'phone': record[2], 'channel': channel}


def remove(store, name):
    ensure_table(store)
    with store.connect() as db:
        return {'removed': db.execute('DELETE FROM contacts WHERE name=?',
                                      (str(name).strip(),)).rowcount > 0}


def all_contacts(store):
    ensure_table(store)
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM contacts ORDER BY name')]


def strip_suffix(word):
    """'අම්මට' to 'අම්මා'. Longest ending first, so 'ගෙන්' wins over 'න්'."""
    for ending in sorted(SUFFIXES, key=len, reverse=True):
        if word.endswith(ending) and len(word) > len(ending) + 1:
            return word[:-len(ending)]
    return word


def find(store, spoken):
    """The contact named in an utterance, or None.

    Returns None rather than a best guess when nothing is close enough. Sending to the
    wrong person is worse than asking who you meant.
    """
    people = all_contacts(store)
    if not people or not spoken:
        return None
    words = [w for w in str(spoken).split() if w.strip()]
    candidates = {normalize(w) for w in words}
    candidates |= {normalize(strip_suffix(w)) for w in words}
    candidates |= {normalize(strip_suffix(normalize(w))) for w in words}
    candidates = {c for c in candidates if len(c) >= 3}

    # The word that matched is remembered, so only that word is removed from the message
    # later. Removing anything merely similar deletes real content: "මම" (I) scores 0.86
    # against "අම්මා" (amma), and stripping it corrupts the message being sent.
    best, best_score, best_word = None, 0.0, ''
    for person in people:
        targets = [person['name']] + ([person['nickname']] if person['nickname'] else [])
        for target in targets:
            goal = normalize(target).replace(' ', '')
            if not goal:
                continue
            for word in words:
                for candidate in {normalize(word), normalize(strip_suffix(word)),
                                  normalize(strip_suffix(normalize(word)))}:
                    if len(candidate) < 3:
                        continue
                    score = _ratio(candidate, goal)
                    if score > best_score:
                        best, best_score, best_word = person, score, word
    if best_score >= MATCH_FLOOR:
        return {**best, 'score': round(best_score, 3), 'matched_word': best_word}
    return None


def strip_name(text, person):
    """Remove the recipient from the message body.

    "අම්මට මැසේජ් එකක් යවන්න මම රාත්‍රී එනවා" carries the recipient inside it. Left in,
    the message sent to your mother begins by addressing her in the third person.
    """
    if not person:
        return text
    # Only the exact word that identified this contact, never anything that merely
    # resembles the name. "මම" (I) is 0.86 similar to "අම්මා" (amma), and removing it
    # would quietly delete a word from the message being sent.
    matched = person.get('matched_word')
    if not matched:
        return text
    kept = [word for word in str(text).split() if word != matched]
    # Removing the name can leave the word that introduced it: "to amma I am coming"
    # became "to I am coming", and that dangling word goes out in the message.
    leading = {'to', 'tell', 'for', 'at', 'ta'}
    while kept and kept[0].lower().strip(' ,.') in leading:
        kept.pop(0)
    return ' '.join(kept).strip(' .,!?')


def speakable(person, lang='si'):
    """What to call them out loud: the nickname in Sinhala, the name otherwise."""
    if lang == 'si' and person.get('nickname'):
        return person['nickname']
    return person['name']


def _ratio(left, right):
    """Direct comparison: contact names are short, and similarity() refuses short text."""
    from difflib import SequenceMatcher
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def main():
    from assistant_core import ROOT, Store
    parser = argparse.ArgumentParser(description='People JARVIS may contact')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--add', nargs=2, metavar=('NAME', 'PHONE'))
    parser.add_argument('--nickname', default='', help='what you call them out loud')
    parser.add_argument('--channel', default='whatsapp',
                        choices=['whatsapp', 'telegram', 'sms'])
    parser.add_argument('--remove', metavar='NAME')
    parser.add_argument('--find', metavar='SPOKEN', help='test what a phrase resolves to')
    args = parser.parse_args()

    store = Store(ROOT / 'data' / 'jarvis.db')
    if args.add:
        print(add(store, args.add[0], args.add[1], args.nickname, args.channel))
    elif args.remove:
        print(remove(store, args.remove))
    elif args.find:
        found = find(store, args.find)
        print(found if found else 'no contact matched, JARVIS would ask who you meant')
    people = all_contacts(store)
    if args.list or not any([args.add, args.remove, args.find]):
        if not people:
            print('No contacts yet. Add one:')
            print('  python contacts.py --add amma 0771234567 --nickname අම්මා')
        for person in people:
            nickname = f' ({person["nickname"]})' if person['nickname'] else ''
            print(f'  {person["name"]}{nickname:14} {person["phone"]}  {person["channel"]}')


if __name__ == '__main__':
    main()
