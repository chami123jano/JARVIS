"""Match a spoken command to an action, phonetically, before involving the model.

Whisper writes Sinhala speech in whatever script it is most confident about — Tamil,
Latin, Devanagari — so the transcript handed to a language model often looks like
gibberish even though the sounds are right. Sending that to the model wastes seconds and
usually fails. Matching it against a small vocabulary by sound instead is both faster and
more reliable, and anything unmatched still falls through to the model.

    python router.py "ammata message ekak yavanna"
    python router.py --list
"""
import datetime as dt
import re
import sys

from sinhala import normalize, similarity

# Spoken forms for each command, in Sinhala script, Singlish and English. More phrasings
# is strictly better: they are alternatives, and only the closest one counts.
COMMANDS = {
    'time': [
        'දැන් වෙලාව කීයද', 'වෙලාව කීයද', 'දැන් කීයද',
        'dan velava kiyada', 'velava kiyada', 'time eka kiyada',
        'what time is it', 'the time',
        # From the live log: how Whisper actually rendered this command in use, labelled
        # German and Vietnamese. Real transcripts make better phrasings than invented ones.
        'deng velave gehde', 'dang mela va kiyade',
    ],
    'weather': [
        'අද කාලගුණය කොහොමද', 'කාලගුණය කොහොමද', 'එළිය කොහොමද',
        'ada kalaguna kohomada', 'kalaguna kohomada', 'weather eka kohomada',
        'whats the weather', 'weather today',
    ],
    'system_status': [
        'සිස්ටම් එකේ තත්වය කියන්න', 'පරිගණකයේ තත්වය', 'පීසී එක කොහොමද',
        'system eke thathwaya kiyanna', 'system status', 'pc eka kohomada',
        'pariganakaye thathwaya', 'computer status',
    ],
    'battery': [
        'බැටරිය කීයද', 'බැටරි එක කීයද',
        'batteriya kiyada', 'battery eka kiyada', 'battery level',
    ],
    'send_message': [
        'මැසේජ් එකක් යවන්න', 'අම්මාට මැසේජ් එකක් යවන්න', 'මැසේජ් එකක් දාන්න',
        'message ekak yavanna', 'ammata message ekak yavanna', 'message yavanna',
        'send a message',
    ],
    'reminder': [
        'මතක් කරන්න', 'මිනිත්තු දහයකින් මතක් කරන්න', 'හෙට උදේ මතක් කරන්න',
        'mathak karanna', 'minitthu dahayakin mathak karanna', 'remind me',
        'heta ude mathak karanna', 'set a reminder',
    ],
    'play_music': [
        'සින්දුවක් දාන්න', 'සිංදු දාන්න', 'මියුසික් දාන්න',
        'sinduvak danna', 'sindu danna', 'music eka danna', 'play a song',
        'song ekak danna', 'play music',
    ],
    'volume_up': [
        'වොලියුම් එක වැඩි කරන්න', 'සද්දේ වැඩි කරන්න',
        'volume eka vadi karanna', 'volume vadi karanna', 'sound eka vadi karanna',
        'turn up the volume', 'volume up',
    ],
    'volume_down': [
        'වොලියුම් එක අඩු කරන්න', 'සද්දේ අඩු කරන්න',
        'volume eka adu karanna', 'volume adu karanna', 'turn down the volume',
        'volume down',
    ],
    'open_app': [
        'ඕපන් කරන්න', 'ක්‍රෝම් එක ඕපන් කරන්න', 'එක ඕපන් කරන්න',
        'open karanna', 'chrome eka open karanna', 'eka open karanna', 'open chrome',
    ],
    'dollar_rate': [
        'අද ඩොලර් රේට් කීයද', 'ඩොලර් එක කීයද', 'ඩොලර් රේට් එක',
        'ada dollar rate kiyada', 'dollar rate kiyada', 'dollar eka kiyada',
        'whats the dollar rate',
    ],
    'save_note': [
        'සටහනක් සේව් කරන්න', 'නෝට් එකක් ලියන්න', 'සටහනක් ලියන්න',
        # How it was actually said in use, which the invented phrasings missed.
        'සටහනක් තබන්න', 'සටහනක් දාන්න', 'නෝට් එකක් තියන්න',
        'satahanak save karanna', 'note ekak save karanna', 'satahanak liyanna',
        'satahanak thabanna', 'save a note', 'take a note',
    ],
    'list_notes': [
        'මගේ සටහන් පෙන්නන්න', 'සටහන් පෙන්නන්න', 'නෝට්ස් පෙන්නන්න',
        'mage satahan pennanna', 'satahan pennanna', 'notes pennanna',
        'show my notes', 'list notes',
    ],
    'news': [
        'අද පුවත් මොනවද', 'පුවත් මොනවද', 'නිව්ස් මොනවද',
        'ada puvath monavada', 'puvath monavada', 'news monavada', 'todays news',
    ],
    'email': [
        'ඊමේල් බලන්න', 'ඊමේල් එක බලන්න', 'මේල් බලන්න', 'මොනවද ඊමේල් ආවේ',
        'email balanna', 'email eka balanna', 'mail balanna', 'check my email',
        'mokakda email awe', 'any new email',
    ],
    # Day 8: a call through the iPhone (Phone Link), and finding the phone.
    'call': [
        'අම්මාට call එකක් ගන්න', 'call එකක් ගන්න', 'කෝල් එකක් ගන්න', 'call කරන්න',
        'ammata call ekak ganna', 'call ekak ganna', 'call karanna', 'call amma',
    ],
    'find_phone': [
        'මගේ phone එක කොහෙද', 'ෆෝන් එක කොහෙද', 'ෆෝන් එක හොයන්න',
        'mage phone eka koheda', 'phone eka hoyanna', 'where is my phone', 'find my phone',
    ],
    # Day 9: what is on the calendar, today or ("heta", "tomorrow") tomorrow.
    'agenda': [
        'අද මොනවද තියෙන්නේ', 'අද calendar එකේ මොනවද', 'හෙට මොනවද තියෙන්නේ',
        'ada mokada thiyenne', 'heta mokada thiyenne', 'calendar eka balanna',
        "what's on today", "what's on tomorrow", 'my calendar today',
    ],
    # Day 9: the email just mentioned, read out, or answered.
    'read_email': [
        'ඒ ඊමේල් එක කියවන්න', 'ඊමේල් එක කියවන්න', 'ඒකේ මොකක්ද තියෙන්නේ',
        'email eka kiyawanna', 'mail eka kiyawanna', 'read that email', 'read the email',
    ],
    'reply_email': [
        'ඒකට reply කරන්න', 'ඊමේල් එකට reply කරන්න', 'ඊමේල් එකට උත්තර දෙන්න',
        'ekata reply karanna', 'email ekata reply karanna', 'reply to that email',
        'reply saying',
    ],
    'screenshot': [
        'ස්ක්‍රීන්ෂොට් එකක් ගන්න', 'ස්ක්‍රීන් එක ගන්න',
        'screenshot ekak ganna', 'screenshot ganna', 'take a screenshot',
    ],
    'lock_pc': [
        'පරිගණකය ලොක් කරන්න', 'ලොක් කරන්න', 'පීසී එක ලොක් කරන්න',
        'pariganakaya lock karanna', 'lock karanna', 'pc eka lock karanna',
        'lock the computer',
    ],
    'calculate': [
        'ගණන් හදන්න', 'ගුණ කරන්න',
        'calculate karanna', 'ganan hadanna', 'guna karanna', 'calculate',
    ],
    'capabilities': [
        'ඔයාට මොනවද කරන්න පුළුවන්', 'ජාවිස් ඔයාට මොනවද කරන්න පුළුවන්',
        'oyata monava karanna puluvanda', 'monava karanna puluvanda',
        'what can you do',
    ],
    'read_messages': [
        'මොනවද ආවේ', 'මැසේජ් මොනවද ආවේ', 'කවුද මැසේජ් කළේ', 'මැසේජ් බලන්න',
        'monavada ave', 'message monavada ave', 'kavuda message kale',
        'what messages came', 'any messages', 'who messaged me',
    ],
    'briefing': [
        'අද විස්තර කියන්න', 'දවසේ විස්තර කියන්න', 'සුබ උදෑසනක්', 'බ්‍රීෆිං එක දෙන්න',
        'ada vistara kiyanna', 'davase vistara kiyanna', 'suba udesanak',
        'briefing eka denna', 'brief me', 'morning briefing', 'good morning',
    ],
    'pause_music': [
        'සින්දුව නවත්වන්න', 'සින්දුව pause කරන්න', 'sinduwa nawaththanna',
        'sinduwa pause karanna', 'pause the music', 'pause the song', 'pause music',
    ],
    'resume_music': [
        'සින්දුව දිගටම දාන්න', 'sinduwa digatama danna', 'resume the music',
        'continue the song', 'resume music', 'unpause',
    ],
    'next_song': [
        'ඊළඟ සින්දුව', 'ඊළඟ සින්දුව දාන්න', 'eelanga sinduwa', 'next song',
        'skip this song', 'play the next song',
    ],
    'previous_song': [
        'කලින් සින්දුව', 'කලින් සින්දුව දාන්න', 'kalin sinduwa', 'previous song',
        'play the previous song', 'go back a song',
    ],
    'look_screen': [
        'මේකේ මොකක්ද වැරැද්ද', 'මේ error එක මොකක්ද', 'screen එකේ මොකක්ද තියෙන්නේ',
        'මේක කියවන්න', 'මේ ෆොටෝ එක කියවන්න', 'මේ බිල බලන්න',
        'meke mokakda waraddha', 'me error eka mokakda', 'screen eke mokakda thiyenne',
        "what's wrong with this", 'explain this error', "what's on my screen",
        'read this to me', 'read this photo', 'look at my screen',
    ],
    'make_skill': [
        'අලුත් tool එකක් හදන්න', 'tool එකක් හදන්න', 'අලුත් skill එකක් හදන්න',
        'ඒක කරන්න ඉගෙන ගන්න', 'aluth tool ekak hadanna', 'tool ekak hadanna',
        'make a new tool', 'make a tool that', 'learn how to', 'write a skill',
    ],
    'task_status': [
        'වැඩේ කොහොමද', 'වැඩේ කොච්චර දුර ගියාද', 'ඉවරද', 'වැඩේ ඉවරද',
        'vade kohomada', 'vade ivarada', 'how is it going', 'are you done',
        'whats the progress',
    ],
    'stop': [
        'නවත්වන්න', 'නිශ්ශබ්ද වෙන්න',
        'navathvanna', 'nishshabda venna', 'stop talking', 'be quiet',
    ],
}

# Thresholds set from the 20 real recordings, not guessed. On that set:
#   correct matches      0.44 to 0.93
#   incorrect matches     up to 0.462   (only two, both routed to 'weather')
# 0.60 clears every wrong match with margin and still acts on most correct ones; the
# rest fall through to the model, which is slower but not wrong. A wrong action is
# unrecoverable, so the gap is deliberately biased towards asking.
ACCEPT = .50
CONFIDENT = .60
# Two commands this close together is an ambiguous match, not a decision.
MARGIN = .06

# Sinhala number words, for "minitthu dahayakin" and similar.
NUMBERS = {
    'eka': 1, 'deka': 2, 'thuna': 3, 'hathara': 4, 'paha': 5, 'haya': 6, 'hatha': 7,
    'ata': 8, 'navaya': 9, 'dahaya': 10, 'pahalova': 15, 'visi': 20, 'this': 30,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'ten': 10, 'fifteen': 15,
    'twenty': 20, 'thirty': 30, 'sixty': 60,
}
UNITS = {
    'minit': 60, 'minitthu': 60, 'minute': 60, 'minutes': 60, 'vinadi': 60,
    'pava': 3600, 'පැය': 3600, 'paya': 3600, 'hour': 3600, 'hours': 3600,
    'thappara': 1, 'second': 1, 'seconds': 1,
}


def phrase_score(text, phrase):
    """How well a phrasing fits, allowing for content after the command.

    "සටහනක් තබන්න අද රෑට කෑම තියන්න එපා" is a save-note order with a long note attached.
    Compared whole, the note drowns the command and it scored 0.366 -- far too low to
    act on, so JARVIS handed it to the model, which said it had saved a note and did
    not. Comparing the head of the utterance as well keeps the command visible.
    """
    whole = similarity(text, phrase)
    target = normalize(phrase).replace(' ', '')
    heard = normalize(text).replace(' ', '')
    if len(heard) <= len(target) + 3:
        return whole
    # Commands lead and content follows, so the opening should look like the phrasing.
    head = heard[:len(target) + 2]
    return max(whole, similarity(head, target))


# A word distinctive enough to name the command on its own. "open whatsapp" shares
# little with any phrasing, because the application name is most of the sentence and
# could be anything; the verb is the part that carries the intent.
KEYWORDS = {
    'open_app': ['open', 'ඕපන්', 'විවෘත', 'launch', 'oapan'],
    'play_music': ['sinduwak', 'සින්දුවක්', 'සිංදු'],
    'briefing': ['බ්‍රීෆිං', 'briefing'],
    # A routine opens with its time -- "හැමදාම රෑ දහයට ... මතක් කරන්න" -- so the head of
    # the sentence looks like no phrasing, and the order is only in the verb.
    'reminder': ['මතක්', 'mathak', 'remind'],
    # A reply carries a whole dictated message, so it sounds like no phrasing at all.
    'reply_email': ['reply', 'රිප්ලයි'],
    'call': ['call', 'කෝල්', 'kol'],
    'email': ['email', 'emails', 'ඊමේල්', 'mail'],
}
KEYWORD_FLOOR = .78


def has_keyword(text, command):
    from difflib import SequenceMatcher
    words = normalize(text).split()
    for keyword in KEYWORDS.get(command, []):
        target = normalize(keyword).replace(' ', '')
        if not target:
            continue
        # Short keywords must be near-exact. At .85, "mata" (to me) matched "matak"
        # (remind), so "mata hitha hodha sindu ekak daanna" was routed as a reminder.
        needed = .95 if len(target) <= 5 else .85
        for word in words:
            if SequenceMatcher(None, word, target).ratio() >= needed:
                return True
    return False


# Music is decided by its words, not by sound: "dukha sinduwak danna" (a sad song)
# sounded closest to "kalin sinduwa" (previous song).
SONG_WORDS = re.compile(r'sindu|සින්දු|සිංදු|\bsongs?\b|\bmusic\b|මියුසික්', re.I)
MUSIC_ACTIONS = [
    ('pause_music', re.compile(r'pause|නවත්ව|නවත්ත|nawath|navath|stop the', re.I)),
    ('next_song', re.compile(r'\bnext\b|\bskip\b|ඊළඟ|eelanga|ilanga', re.I)),
    ('resume_music', re.compile(r'resume|unpause|continue|දිගටම|digatama', re.I)),
    ('previous_song', re.compile(r'previous|go back|කලින් සින්දුව|kalin sinduwa', re.I)),
]
HEARD_BEFORE = re.compile(r'ahapu|ඇහුව|listened|yesterday|ඊයේ|iye', re.I)


def music_intent(text):
    """The music command this sentence asks for, or None if it is not about music."""
    text = str(text)
    if not SONG_WORDS.search(text) and not re.match(r'\s*play\s+\w', text, re.I):
        return None
    for command, words in MUSIC_ACTIONS:
        if words.search(text) and not (command == 'previous_song' and HEARD_BEFORE.search(text)):
            return command
    return 'play_music'


# Words that say nothing about which command is meant.
FILLER_WORDS = {'what', 'whats', "what's", 'the', 'is', 'it', 'are', 'a', 'an', 'to', 'of',
                'my', 'me', 'you', 'your', 'how', 'why', 'who', 'when', 'where', 'can',
                'could', 'do', 'does', 'this', 'that', 'please', 'jarvis', 'and', 'for', 'in',
                'on', 'with', 'now', 'today', 'any', 'some', 'there', 'tell', 'give', 'get',
                'i', 'am', 'be', 'up', 'down'}


def content_words(text):
    return {w for w in re.findall(r"[a-z']+", str(text).lower())
            if len(w) >= 3 and w not in FILLER_WORDS}


def shares_a_word(text, command):
    """Does an English (or Singlish) sentence contain a real word of this command?

    Sound-alike matching is right for Sinhala, which speech recognition spells many ways,
    and wrong for English sentences. Measured on Day 28: "what is the capital of Japan"
    came out as weather-or-time, and "why is the sky blue" as a confident request to write
    a new tool. Neither shares a single word with those commands.
    """
    from difflib import SequenceMatcher
    heard = content_words(text)
    if not heard:
        return True         # "what can you do": nothing to compare, so sound decides
    vocabulary = set()
    for phrase in COMMANDS.get(command, []) + KEYWORDS.get(command, []):
        vocabulary |= content_words(phrase)
    return any(SequenceMatcher(None, a, b).ratio() >= .85 for a in heard for b in vocabulary)


def match(text):
    """Best command for this transcript: (name, score, runner_up, runner_up_score)."""
    scored = []
    for name, phrasings in COMMANDS.items():
        best = max(phrase_score(text, phrase) for phrase in phrasings)
        if name in KEYWORDS and has_keyword(text, name):
            best = max(best, KEYWORD_FLOOR)
        scored.append((best, name))
    scored.sort(reverse=True)
    (top_score, top), (second_score, second) = scored[0], scored[1]
    return top, top_score, second, second_score


def lexicon_match(word, table, floor=.82):
    """Closest entry in a small word table, ignoring the length guard.

    similarity() refuses anything under five characters, because short noise like "hana"
    otherwise scores 0.47 against a real command. That guard is right for matching whole
    commands and wrong for a lexicon of short words: "ten", "paha" and "hour" are all
    under it, and "hour" normalises to "hur" at three characters. Compare directly here.

    Sinhala also inflects: "dahaya" (ten) becomes "dahayakin" (in ten), which Whisper
    writes "dahayaking". Matching the start of the word catches that.
    """
    if not word:
        return None
    from difflib import SequenceMatcher
    best_value, best_score = None, 0.0
    for name, value in table.items():
        target = normalize(name).replace(' ', '') or name
        head = word[:len(target)]
        score = max(SequenceMatcher(None, word, target).ratio(),
                    SequenceMatcher(None, head, target).ratio())
        if score > best_score:
            best_value, best_score = value, score
    return best_value if best_score >= floor else None


def number_in(word):
    """A number hiding inside a word, or a bare digit."""
    if word and word.isdigit():
        return int(word)
    return lexicon_match(word, NUMBERS)


def extract_duration(text):
    """Seconds from phrases like 'minitthu dahayakin' or 'in ten minutes'."""
    cleaned = normalize(text)
    words = cleaned.split()
    digits = re.findall(r'\d+', cleaned)
    amount = int(digits[0]) if digits else None
    unit = None
    for index, word in enumerate(words):
        unit = lexicon_match(word, UNITS, floor=.8)
        if unit:
            if amount is None:
                nearby = words[index + 1:index + 3] + words[max(0, index - 2):index]
                found = [number_in(candidate) for candidate in nearby]
                found = [value for value in found if value]
                # Sinhala compounds numbers: "visi paha" is twenty-five, not twenty.
                # Taking the first would set a reminder for the wrong time and say
                # nothing about it, so hand compounds to the model instead.
                if len(found) > 1:
                    return None
                amount = found[0] if found else None
                # "පැයකින්", in an hour: the one is in the ending, not a separate word.
                if amount is None and word.endswith('akin'):
                    amount = 1
            break
    if amount and unit:
        total = amount * unit
        if 0 < total <= 31536000:
            return total
    return None


# Clock times, for "රෑ දහයට" (at ten at night). The hour carries the dative 'ට', which
# is what separates "at ten" from "ten" -- and from "එක" (one), which is also "the".
HOURS_AT = {
    'එකට': 1, 'දෙකට': 2, 'තුනට': 3, 'හතරට': 4, 'පහට': 5, 'හයට': 6, 'හතට': 7,
    'අටට': 8, 'නමයට': 9, 'නවයට': 9, 'දහයට': 10, 'එකොළහට': 11, 'දොළහට': 12,
    'ekata': 1, 'dekata': 2, 'thunata': 3, 'hatharata': 4, 'pahata': 5, 'hayata': 6,
    'hathata': 7, 'atata': 8, 'namayata': 9, 'navayata': 9, 'dahayata': 10,
    'ekolahata': 11, 'dolahata': 12,
}
ENGLISH_HOURS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12}
# Which part of the day, which decides am or pm.
PERIODS = {
    'උදේ': 'morning', 'උදේට': 'morning', 'උදේම': 'morning', 'උදෑසන': 'morning',
    'පාන්දර': 'morning', 'අලුයම': 'morning',
    'දවල්': 'midday', 'දවල්ට': 'midday', 'දහවල්': 'midday',
    'හවස': 'evening', 'හවසට': 'evening', 'හවසම': 'evening', 'සවස': 'evening',
    'රෑ': 'night', 'රෑට': 'night', 'රෑම': 'night', 'රාත්‍රී': 'night',
    'ude': 'morning', 'udeta': 'morning', 'daval': 'midday', 'havasa': 'evening',
    'rae': 'night', 'raa': 'night',
    'am': 'morning', 'morning': 'morning', 'pm': 'evening', 'afternoon': 'evening',
    'evening': 'evening', 'night': 'night', 'tonight': 'night',
}
# "Every day". Its presence is what makes a reminder a routine.
DAILY = ['හැමදාම', 'හැමදාමත්', 'හැම', 'දිනපතා', 'hamadama', 'hama', 'every', 'daily']
HALF = 'hamara'


def _table(table):
    """A word table keyed by its romanized form, so every script meets in one place."""
    return {normalize(key).replace(' ', ''): value for key, value in table.items()}


def to_24_hour(hour, minute, part):
    if part == 'morning':
        return hour % 12            # 12 am is midnight
    if part == 'midday':
        return hour + 12 if hour < 6 else hour
    if part == 'evening':
        return hour + 12 if hour < 12 else hour
    if part == 'night':
        if hour == 12:
            return 0
        return hour if hour < 5 else (hour + 12 if hour < 12 else hour)
    return hour


def extract_clock(text):
    """A time of day in the sentence: (datetime.time, words it used), or None.

    Without a part of the day, "දහයට" could be ten in the morning or at night. Guessing
    sets a reminder twelve hours wrong without a word, so only a 24-hour figure such as
    "22:00" stands alone; otherwise the caller asks.
    """
    hours, periods = _table(HOURS_AT), _table(PERIODS)
    tokens = str(text).split()
    hour = minute = part = None
    used = set()
    for index, token in enumerate(tokens):
        word = normalize(token).replace(' ', '')
        lower = token.lower().strip('.,!?')
        if word in periods:
            # "at ten pm tonight" names the part twice; both are timing, not content.
            part = part or periods[word]
            used.add(index)
            continue
        if hour is not None:
            continue
        figure = re.fullmatch(r'(\d{1,2})(?:[:.](\d{2}))?\s*(ට|ta)?(am|pm|a\.m\.|p\.m\.)?',
                              lower)
        if figure:
            hour, minute = int(figure.group(1)), int(figure.group(2) or 0)
            if figure.group(4):
                part = 'morning' if figure.group(4).startswith('a') else 'evening'
            used.add(index)
            continue
        half = word.endswith(HALF + 'ta')
        stem = word[:-len(HALF + 'ta')] + 'ta' if half else word
        if stem in hours:
            hour, minute = hours[stem], 30 if half else 0
            used.add(index)
            continue
        # English: "at ten", "ten o'clock".
        if lower in ENGLISH_HOURS and index and tokens[index - 1].lower() == 'at':
            hour, minute = ENGLISH_HOURS[lower], 0
            used.update({index, index - 1})
    if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    for index, token in enumerate(tokens):
        if token.lower() in ('at', "o'clock") and index not in used:
            used.add(index)
    if part is None:
        if hour <= 12:
            return None
        return dt.time(hour, minute), used
    return dt.time(to_24_hour(hour, minute, part) % 24, minute), used


def is_daily(text):
    daily = {normalize(word).replace(' ', '') for word in DAILY}
    return any(normalize(token).replace(' ', '') in daily for token in str(text).split())


TOMORROW = ['හෙට', 'heta', 'tomorrow']


def is_tomorrow(text):
    tomorrow = {normalize(word).replace(' ', '') for word in TOMORROW}
    return any(normalize(token).replace(' ', '') in tomorrow for token in str(text).split())


def without_words(text, indices):
    return ' '.join(token for index, token in enumerate(str(text).split())
                    if index not in indices)


def next_occurrence(at, moment=None):
    """The next time the clock shows `at`: today if still ahead, otherwise tomorrow."""
    moment = moment or dt.datetime.now().astimezone()
    when = moment.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return when if when > moment else when + dt.timedelta(days=1)


# Words that are part of giving the order, not part of what was asked for. Stripped so a
# note reads "අද රෑට කෑම තියන්න එපා" rather than repeating the instruction back.
FILLER = {
    'save_note': ['සටහනක්', 'සටහන', 'තබන්න', 'සේව්', 'ලියන්න', 'දාන්න', 'කියලා',
                  'satahanak', 'satahan', 'note', 'save', 'ekak', 'liyanna', 'thabanna'],
    # No 'කරන්න' here: it is also the verb of the thing to be done. "ෆෝන් එක චාජ් කරන්න
    # මතක් කරන්න" must keep the first one. strip_reminder removes the second.
    'reminder': ['කියලා', 'mathak', 'remind', 'me', 'to',
                 'minitthu', 'minute', 'minutes', 'hour', 'hours', 'dahayakin', 'pahakin'],
    'send_message': ['මැසේජ්', 'එකක්', 'යවන්න', 'කියලා', 'message', 'ekak', 'yavanna',
                     'send', 'a', 'to'],
    'make_skill': ['අලුත්', 'tool', 'එකක්', 'හදන්න', 'skill', 'ඉගෙන', 'ගන්න', 'aluth',
                   'ekak', 'hadanna', 'make', 'a', 'new', 'that', 'learn', 'how', 'write',
                   'teach', 'yourself'],
    'open_app': ['ඕපන්', 'කරන්න', 'එක', 'විවෘත', 'open', 'karanna', 'eka', 'launch',
                 'start', 'up'],
    # What is left is whose mail you mean: "Daraz eken email awada" -> "Daraz".
    'email': ['ඊමේල්', 'මේල්', 'බලන්න', 'එක', 'එකක්', 'ආවද', 'ආවා', 'ආවේ', 'මොනවද', 'එකෙන්',
              'ගෙන්', 'ගෙන්ද', 'තියෙනවද', 'email', 'emails', 'mail', 'balanna', 'eka', 'ekak',
              'awada', 'aawada', 'awa', 'awe', 'eken', 'gen', 'genda', 'thiyenawada', 'check',
              'my', 'any', 'new', 'from', 'did', 'i', 'get', 'have', 'mokakda', 'monawada',
              'is', 'there', 'the', 'what'],
}

# A reply's own words come off its two ends only, never from inside the message: matched
# by sound, "දෙන්න" took the "enna" out of "mata heta enna be" (I can't come tomorrow).
REPLY_ENDS = re.compile(
    r'^(?:\s*(?:ඒකට|ඊමේල්|එකට|reply|රිප්ලයි|කරන්න|උත්තරයක්|උත්තර|දෙන්න|ekata|email|eka|'
    r'karanna|to|that|this|saying|and|say|jarvis)\b[\s,:]*)+|'
    r'(?:[\s,]*\b(?:ඒකට|ඊමේල්|එකට|reply|රිප්ලයි|කරන්න|උත්තරයක්|උත්තර|දෙන්න|ekata|email|'
    r'karanna)\b)+[\s.!]*$', re.I)


def reply_words_off(text):
    """"ekata reply karanna mata heta enna be kiyala" -> "mata heta enna be kiyala"."""
    return re.sub(r'\s+', ' ', REPLY_ENDS.sub('', str(text))).strip(' ,.')


def strip_command(text, command, use_phrasing=True):
    """What is left after removing the words that gave the order.

    Two passes: the phrasing that matched, and a per-command filler list. The threshold
    is deliberately high, because "තබන්න" (put/save, the order) and "තියන්න" (keep, which
    may be the content) sound close, and eating the content is worse than leaving a
    stray word in it.
    """
    from difflib import SequenceMatcher
    # For open_app the phrasings name an example application ("chrome eka open
    # karanna"), so stripping by phrasing removes the very word being asked for. Those
    # commands strip by filler list only.
    targets = []
    if use_phrasing:
        phrasings = COMMANDS.get(command, [])
        best = max(phrasings, key=lambda p: similarity(text, p)) if phrasings else ''
        targets += [normalize(word) for word in best.split()]
    targets += [normalize(word) for word in FILLER.get(command, [])]
    targets = [t for t in targets if t]

    kept = []
    for word in str(text).split():
        cleaned = normalize(word)
        if not cleaned:
            continue
        if any(SequenceMatcher(None, cleaned, target).ratio() >= .85 for target in targets):
            continue
        kept.append(word)
    return ' '.join(kept).strip(' .,!?।')


def strip_reminder(text):
    """What to be reminded of: the sentence without "මතක් කරන්න" and the timing words.

    Only the 'කරන්න' that follows 'මතක්' is the order. Any earlier one belongs to the
    task -- "චාජ් කරන්න", charge it -- and removing it leaves half a sentence.
    """
    from difflib import SequenceMatcher
    tokens = str(text).split()
    order = normalize('මතක්').replace(' ', '')
    kept, skip_next = [], False
    for token in tokens:
        word = normalize(token).replace(' ', '')
        if skip_next and word.startswith('kara'):
            skip_next = False
            continue
        skip_next = False
        if word and SequenceMatcher(None, word, order).ratio() >= .85:
            skip_next = True
            continue
        kept.append(token)
    return strip_command(' '.join(without_delay(kept)), 'reminder', use_phrasing=False)


def without_delay(tokens):
    """Remove "මිනිත්තු එකකින්" and "in ten minutes": a unit and the number beside it.

    Only a number next to a unit goes. "එක" is also "the" -- "ෆෝන් එක" -- and removing
    every number word would take it.
    """
    words = [normalize(token).replace(' ', '') for token in tokens]
    drop = set()
    for index, word in enumerate(words):
        if not word or not lexicon_match(word, UNITS, floor=.8):
            continue
        drop.add(index)
        for near in (index - 1, index + 1):
            if 0 <= near < len(words) and (tokens[near].isdigit() or number_in(words[near])):
                drop.add(near)
                if near == index - 1 and near and tokens[near - 1].lower() == 'in':
                    drop.add(near - 1)
    return [token for index, token in enumerate(tokens) if index not in drop]


# Commands asked with "how much" (කීයද, kiyada) need their own word as well.
OWN_WORDS = {
    'time': re.compile(r'වෙලාව|වේලාව|දැන්|පැය|velav|vela|wela|welaw|time|dan\b|dang|deng|'
                       r'clock|hour', re.I),
    'battery': re.compile(r'බැටරි|batter|batari|bateri|baitari|charge|චාජ්|power', re.I),
    # "කොහෙද" (where) sounds like "කොහොමද" (how): "යාපනය කොහෙද?" was given Jaffna's weather.
    'weather': re.compile(r'කාලගුණ|weather|kalagun|එළිය|eliya|වැස්ස|වහින|wassa|wahin|rain|'
                          r'උෂ්ණ|temperature|අව්ව|awwa|forecast|සීතල|රස්නෙ|hot|cold', re.I),
}


def route(text):
    """Decide what to do with a transcript.

    action='command' to run it directly, 'model' to let the language model handle it,
    'unclear' to ask the user to repeat.
    """
    from responses import language_of
    cleaned = normalize(text)
    language = language_of(text)
    if not cleaned:
        return {'action': 'unclear', 'reason': 'nothing recognisable', 'text': text,
                'phonetic': '', 'language': language}
    name, score, second, second_score = match(text)
    music = music_intent(text)
    if music:
        name, score, second, second_score = music, 1.0, name, 0.0
    elif {name, second} & {'call', 'reply_email', 'email'} and \
            {name, second} <= {'call', 'reply_email', 'email', 'reminder'} and \
            has_keyword(text, 'reminder'):
        # "පැය දෙකකින් අම්මාට කෝල් කරන්න මතක් කරන්න" asks to be reminded to call, not to
        # call now. Measured 21 Oct: the new call command tied with the reminder.
        other = second if name == 'reminder' else name
        name, score, second, second_score = 'reminder', max(score, KEYWORD_FLOOR), other, 0.0
    result = {'text': text, 'phonetic': cleaned, 'command': name, 'score': round(score, 3),
              'runner_up': second, 'runner_up_score': round(second_score, 3),
              # Replies follow the language they were asked in.
              'language': language}
    if name in OWN_WORDS and not OWN_WORDS[name].search(text):
        # "කීයද" is "how much" as well as "what time" and "how full": measured 16 Oct,
        # "iPhone 16 එක කීයද?" matched the time at 0.69 and was told it was two o'clock,
        # and "galaxy s25 eka kiyada" was the battery or a new skill.
        result.update(action='model', reason=f'no word of {name} in it')
        return result
    if language == 'en' and score >= ACCEPT and not shares_a_word(text, name):
        result.update(action='model', reason='no word of the command in an English sentence')
        return result
    if score < ACCEPT:
        result.update(action='model', reason='no command matched')
        return result
    if score < CONFIDENT:
        result.update(action='model', reason='match too weak to act on directly')
        return result
    # An ambiguous match is worse than none: acting on the wrong one is unrecoverable.
    if score - second_score < MARGIN:
        result.update(action='unclear', reason=f'ambiguous between {name} and {second}')
        return result
    result.update(action='command')
    if name == 'reminder':
        clock = extract_clock(text)
        used = set(clock[1]) if clock else set()
        daily = is_daily(text)
        if daily:
            used |= {i for i, token in enumerate(text.split()) if is_daily(token)}
        used |= {i for i, token in enumerate(text.split()) if is_tomorrow(token)}
        content = strip_reminder(without_words(text, used))
        result['content'] = content
        if daily:
            # "Every night at ten, remind me to charge the phone": a routine.
            result['command'] = 'routine'
            if not clock:
                result.update(action='ask', detail='time')
            elif not content:
                result.update(action='ask', detail='reminder')
            else:
                result['at'] = clock[0]
            return result
        seconds = extract_duration(text)
        if not seconds and clock:
            # "Remind me at ten tonight": a clock time rather than a delay.
            due = next_occurrence(clock[0])
            today = dt.datetime.now().astimezone()
            # At seven, "tomorrow at eight" is not the next eight o'clock. At one in the
            # morning it is: you have not slept yet, and "tomorrow" means after you do.
            if is_tomorrow(text) and today.hour >= 4:
                due = today.replace(hour=clock[0].hour, minute=clock[0].minute, second=0,
                                    microsecond=0) + dt.timedelta(days=1)
            seconds = round((due - dt.datetime.now().astimezone()).total_seconds())
            result['at'] = clock[0]
        else:
            result['content'] = strip_reminder(text)
        result['seconds'] = seconds
        if not seconds:
            result.update(action='model', reason='reminder without a clear time')
    elif name == 'make_skill':
        # What the new tool should do; the words that asked for it are not part of it.
        content = strip_command(text, name, use_phrasing=False)
        # "learn how to work out ...": a leading "to" belongs to the order. Not every "to"
        # does -- "kilometres to miles" needs it.
        result['content'] = re.sub(r'^(?:to|that)\s+', '', content, flags=re.I)
        if len(result['content'].split()) < 2:
            result.update(action='ask', detail='skill')
    elif name == 'briefing':
        # "Brief me at half past six every day" sets the time; otherwise brief now.
        clock = extract_clock(text)
        if clock:
            result['at'] = clock[0]
    elif name == 'email':
        # Empty: what is new. A name left over: that sender's mail.
        result['content'] = strip_command(text, name, use_phrasing=False)
    elif name == 'reply_email':
        result['content'] = reply_words_off(text)
        if not result['content']:
            result.update(action='ask', detail='reply')
    elif name in ('save_note', 'send_message', 'open_app'):
        result['content'] = strip_command(text, name, use_phrasing=(name != 'open_app'))
        if not result['content']:
            # An order with nothing attached. Ask, rather than saving an empty note,
            # messaging nobody, or opening whatever the sentence happens to resemble.
            result.update(action='ask',
                          detail={'save_note': 'note', 'send_message': 'message',
                                  'open_app': 'app'}[name])
    return result


CARD_TITLES = {
    'time': 'The time', 'weather': 'Weather', 'system_status': 'How the PC is doing',
    'battery': 'Battery', 'send_message': 'WhatsApp someone', 'reminder': 'Remind me',
    'play_music': 'Play music', 'volume_up': 'Louder', 'volume_down': 'Quieter',
    'open_app': 'Open an app', 'dollar_rate': 'Dollar rate', 'save_note': 'Save a note',
    'list_notes': 'Read my notes', 'news': 'News', 'email': 'Email',
    'read_email': 'Read that email', 'reply_email': 'Reply to it',
    'agenda': "What's on today / tomorrow",
    'call': 'Call someone (iPhone)', 'find_phone': "Where's my phone",
    'screenshot': 'Screenshot', 'lock_pc': 'Lock the PC', 'calculate': 'Calculate',
    'capabilities': 'What can you do', 'read_messages': 'What messages came',
    'briefing': 'Briefing now, or set its time', 'look_screen': "What's on screen / this error",
    'make_skill': 'Write a new tool', 'task_status': 'How is the task going',
    'pause_music': 'Pause the music', 'resume_music': 'Carry on playing',
    'next_song': 'Next song', 'previous_song': 'Previous song',
    'stop': 'Stop / be quiet',
}


def card():
    """VOICE-COMMANDS.md: what to say, generated from the phrasings themselves, so the
    card cannot drift from what the router actually understands."""
    sinhala = re.compile('[඀-෿]')
    rows = ['# What to say to JARVIS', '',
            'Say "Hey JARVIS" first. Generated by `python router.py --card` from the router '
            'itself -- regenerate after adding a phrasing.', '',
            '| | Sinhala | Singlish / English |', '|---|---|---|']
    for name, phrasings in COMMANDS.items():
        si = [p for p in phrasings if sinhala.search(p)][:2]
        en = [p for p in phrasings if not sinhala.search(p)][:2]
        rows.append(f"| **{CARD_TITLES.get(name, name)}** | {' / '.join(si)} | {' / '.join(en)} |")
    rows += ['', '## Also', '',
             '| | Say |', '|---|---|',
             '| Every day at a time | හැමදාම රෑ දහයට ෆෝන් එක චාජ් කරන්න මතක් කරන්න |',
             '| At a clock time | හවස පහට ගෙදර යන්න මතක් කරන්න · heta ude atata ... |',
             '| Several steps in one go | ... හොයලා, එකතු කරලා, සේව් කරලා ... / "then" |',
             '| Think hard | හොඳට හිතලා බලන්න ... / think hard about ... |',
             '| About your files | mage CV eke mona skills tikada thiyenne |',
             '| A price in Sri Lanka | iPhone 16 එක කීයද · galaxy s25 eka kiyada · how '
             'much is a macbook air |',
             '| A fact (Wikipedia) | සීගිරිය හැදුවේ කවුද · යාපනය කොහෙද · who painted the '
             'mona lisa |',
             '| A web page (typed) | read https://... and tell me ... |',
             '| Fill a form (typed) | fill the form at https://... with name ... -- you '
             'press submit |',
             '| Email from someone | Daraz eken email awada · any email from Kasun |',
             '| Call a number | 0771234567ට call කරන්න -- read back, dialled on ඔව් |',
             '| Tomorrow, not today | හෙට මොනවද තියෙන්නේ · heta mokada thiyenne |',
             '| A reply, in your words | ඒකට reply කරන්න, මම හෙට එන්නම් කියලා -- read '
             'back in English, sent only on ඔව් |',
             '| Answer a question JARVIS asked | ඔව් / හරි / yes -- නෑ / එපා / no |',
             '| A task that failed | ආයෙත් (retry) · මඟහරින්න (skip) · නවත්වන්න (stop) |',
             '| Mute the microphone | Ctrl+Alt+J |', '']
    return '\n'.join(rows)


def main():
    if '--card' in sys.argv:
        print(card())
        return
    if '--list' in sys.argv:
        print(f'{len(COMMANDS)} commands, '
              f'{sum(len(v) for v in COMMANDS.values())} phrasings\n')
        for name, phrasings in COMMANDS.items():
            print(f'  {name:16} {len(phrasings):2} phrasings   e.g. {phrasings[0]}')
        return
    text = ' '.join(sys.argv[1:])
    if not text:
        print(__doc__)
        return
    outcome = route(text)
    print(f'  heard     {outcome["text"]}')
    print(f'  phonetic  {outcome.get("phonetic", "")}')
    print(f'  action    {outcome["action"].upper()}')
    print(f'  command   {outcome.get("command")}  score {outcome.get("score")}')
    print(f'  runner up {outcome.get("runner_up")}  score {outcome.get("runner_up_score")}')
    if outcome.get('reason'):
        print(f'  reason    {outcome["reason"]}')
    if outcome.get('seconds'):
        print(f'  in        {outcome["seconds"]}s '
              f'({dt.timedelta(seconds=outcome["seconds"])})')


if __name__ == '__main__':
    main()
