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
        'satahanak save karanna', 'note ekak save karanna', 'satahanak liyanna',
        'save a note', 'take a note',
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
        'ඊමේල් බලන්න', 'ඊමේල් එක බලන්න', 'මේල් බලන්න',
        'email balanna', 'email eka balanna', 'mail balanna', 'check my email',
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
    'pava': 3600, 'hour': 3600, 'hours': 3600,
    'thappara': 1, 'second': 1, 'seconds': 1,
}


def match(text):
    """Best command for this transcript: (name, score, runner_up, runner_up_score)."""
    scored = []
    for name, phrasings in COMMANDS.items():
        best = max(similarity(text, phrase) for phrase in phrasings)
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
            break
    if amount and unit:
        total = amount * unit
        if 0 < total <= 31536000:
            return total
    return None


def route(text):
    """Decide what to do with a transcript.

    action='command' to run it directly, 'model' to let the language model handle it,
    'unclear' to ask the user to repeat.
    """
    cleaned = normalize(text)
    if not cleaned:
        return {'action': 'unclear', 'reason': 'nothing recognisable', 'text': text,
                'phonetic': ''}
    name, score, second, second_score = match(text)
    result = {'text': text, 'phonetic': cleaned, 'command': name, 'score': round(score, 3),
              'runner_up': second, 'runner_up_score': round(second_score, 3)}
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
        seconds = extract_duration(text)
        result['seconds'] = seconds
        if not seconds:
            result.update(action='model', reason='reminder without a clear time')
    return result


def main():
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
