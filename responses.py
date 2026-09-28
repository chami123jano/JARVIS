"""Spoken Sinhala replies built directly from tool results.

The router already knows which command was spoken and the tools already return the
numbers, so asking a language model to phrase them adds a second of latency and a chance
of being wrong. Observed before this existed: asked for the battery level, the model
replied that the tool had not reported one — it had — and asked for the time it read out
the full date as "පස්වරු 14:32", mixing the afternoon marker with a 24-hour clock.

These replies are written for the ear, not the eye: one or two short sentences, no lists,
no markdown, no percent signs to be read aloud as symbols.
"""
import datetime as dt
import re

SINHALA_RANGE = ('඀', '෿')


def language_of(text):
    """'si' when the text is mostly Sinhala script, otherwise 'en'.

    Replies follow the language they were asked in. Answering an English question in
    Sinhala is technically correct and useless.
    """
    letters = [c for c in str(text) if c.isalpha()]
    if not letters:
        return 'si'
    sinhala = sum(1 for c in letters if SINHALA_RANGE[0] <= c <= SINHALA_RANGE[1])
    return 'si' if sinhala / len(letters) >= .3 else 'en'


def english_clock(moment=None):
    """'four fifty-seven in the afternoon'. Digits read as digits, so words again."""
    moment = moment or dt.datetime.now()
    hour = moment.hour % 12 or 12
    minute = moment.minute
    part = ('in the morning' if moment.hour < 12 else
            'in the afternoon' if moment.hour < 18 else 'in the evening')
    if minute == 0:
        return f"{ENGLISH_NUMBERS[hour]} o'clock {part}"
    if minute == 30:
        return f'half past {ENGLISH_NUMBERS[hour]} {part}'
    if minute == 15:
        return f'quarter past {ENGLISH_NUMBERS[hour]} {part}'
    if minute == 45:
        return f'quarter to {ENGLISH_NUMBERS[(hour % 12) + 1]} {part}'
    return f'{ENGLISH_NUMBERS[hour]} {english_number(minute)} {part}'


ENGLISH_NUMBERS = {
    0: 'zero', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six',
    7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten', 11: 'eleven', 12: 'twelve',
    13: 'thirteen', 14: 'fourteen', 15: 'fifteen', 16: 'sixteen', 17: 'seventeen',
    18: 'eighteen', 19: 'nineteen',
}
ENGLISH_TENS = {20: 'twenty', 30: 'thirty', 40: 'forty', 50: 'fifty',
                60: 'sixty', 70: 'seventy', 80: 'eighty', 90: 'ninety'}


def english_number(value):
    """Words for 0 to 100, because a voice reads numerals as numerals."""
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    if value in ENGLISH_NUMBERS:
        return ENGLISH_NUMBERS[value]
    if value == 100:
        return 'a hundred'
    ten, unit = divmod(value, 10)
    if ten * 10 in ENGLISH_TENS:
        return ENGLISH_TENS[ten * 10] + (f'-{ENGLISH_NUMBERS[unit]}' if unit else '')
    return str(value)

# Time of day, the way it is actually said.
def period(hour):
    if hour < 6:
        return 'අලුයම'      # small hours
    if hour < 12:
        return 'උදේ'        # morning
    if hour < 14:
        return 'දවල්'       # midday
    if hour < 18:
        return 'හවස'        # afternoon
    return 'රෑ'             # night


# Sinhala number words. Written out because a voice reads "2.34" as a decimal --
# "two point three four" -- not as a time. Times have to be words.
UNITS = {
    1: 'එක', 2: 'දෙක', 3: 'තුන', 4: 'හතර', 5: 'පහ', 6: 'හය', 7: 'හත', 8: 'අට',
    9: 'නමය', 10: 'දහය', 11: 'එකොළහ', 12: 'දොළහ', 13: 'දහතුන', 14: 'දාහතර',
    15: 'පහළොව', 16: 'දහසය', 17: 'දාහත', 18: 'දහඅට', 19: 'දහනමය',
}
TENS = {20: 'විස්ස', 30: 'තිහ', 40: 'හතළිහ', 50: 'පනහ',
        60: 'හැට', 70: 'හැත්තෑව', 80: 'අසූව', 90: 'අනූව'}
TENS_PREFIX = {20: 'විසි', 30: 'තිස්', 40: 'හතළිස්', 50: 'පනස්',
               60: 'හැට', 70: 'හැත්තෑ', 80: 'අසූ', 90: 'අනූ'}


def number_word(value):
    """Sinhala word for 0 to 100.

    Covers percentages and counts as well as clock times, because a voice reads every
    digit it is given: "සියයට 100" comes out as a numeral, not as සියයක්.
    """
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    if value == 0:
        return 'බින්දුව'
    if value == 100:
        return 'සියය'
    if value in UNITS:
        return UNITS[value]
    if value in TENS:
        return TENS[value]
    ten, unit = divmod(value, 10)
    if ten * 10 in TENS_PREFIX and unit:
        return TENS_PREFIX[ten * 10] + UNITS[unit]
    return str(value)


def percent(value):
    """'සියයට අසූදෙක', never 'සියයට 82'."""
    return f'සියයට {number_word(value)}'


def clock(moment=None):
    """Spoken Sinhala time: 'හවස දෙකයි තිස්හතරයි', never 'හවස 2.34'.

    A voice reads digits as digits. The quarter and half forms are used because that is
    how the time is actually said out loud.
    """
    moment = moment or dt.datetime.now()
    hour = moment.hour % 12 or 12
    minute = moment.minute
    when = period(moment.hour)
    hour_word = number_word(hour)
    if minute == 0:
        return f'{when} {hour_word}යි'
    if minute == 30:
        return f'{when} {hour_word}හමාරයි'
    if minute == 15:
        return f'{when} {hour_word}යි කාලයි'
    return f'{when} {hour_word}යි {number_word(minute)}යි'


def say_time(_tools=None, _result=None, lang='si'):
    if lang == 'en':
        return f'It is {english_clock()}.'
    return f'දැන් වෙලාව {clock()}.'


def say_battery(result, lang='si'):
    level = result.get('battery')
    if level is None:
        return ('This machine has no battery.' if lang == 'en'
                else 'මේ පරිගණකයේ බැටරියක් නැහැ.')
    if level >= 99:
        return 'The battery is full.' if lang == 'en' else 'බැටරිය ෆුල්.'
    if level <= 20:
        return (f'The battery is at {english_number(level)} percent. It needs charging.'
                if lang == 'en' else f'බැටරිය {percent(level)}යි. චාජ් කරන්න ඕනේ.')
    return (f'The battery is at {english_number(level)} percent.' if lang == 'en'
            else f'බැටරිය {percent(level)}යි.')


def say_system(result, lang='si'):
    if lang == 'en':
        line = (f"Everything is fine. The processor is at {english_number(result['cpu'])} "
                f"percent, memory at {english_number(result['memory'])} percent, "
                f"and the disk at {english_number(result['disk'])} percent.")
    else:
        parts = [f"සීපීයූ {percent(result['cpu'])}",
                 f"මතකය {percent(result['memory'])}",
                 f"තැටිය {percent(result['disk'])}"]
        line = 'සිස්ටම් එක හොඳින්. ' + ', '.join(parts) + 'යි.'
    if result.get('battery') is not None:
        # Through say_battery, so a full battery does not become the clumsy
        # "සියයට සියයයි" here while reading correctly everywhere else.
        line += ' ' + say_battery(result, lang)
    return line


def say_notes(records, lang='si'):
    notes = [r for r in records if r['kind'] == 'note' and not r['done']]
    if not notes:
        return 'You have no notes.' if lang == 'en' else 'ඔබට සටහන් නැහැ.'
    listed = '. '.join(note['title'] for note in notes[:5])
    if lang == 'en':
        if len(notes) == 1:
            return f'You have one note. {notes[0]["title"]}.'
        more = f' And {english_number(len(notes) - 5)} more.' if len(notes) > 5 else ''
        return f'You have {english_number(len(notes))} notes. {listed}.{more}'
    if len(notes) == 1:
        return f'ඔබට සටහනක් තියෙනවා. {notes[0]["title"]}.'
    more = f' තව {number_word(len(notes) - 5)}ක් තියෙනවා.' if len(notes) > 5 else ''
    return f'ඔබට සටහන් {number_word(len(notes))}ක් තියෙනවා. {listed}.{more}'


def say_reminders(records, lang='si'):
    pending = [r for r in records if r['kind'] == 'reminder' and not r['done']]
    if not pending:
        return 'You have no reminders.' if lang == 'en' else 'මතක් කිරීම් නැහැ.'
    listed = '. '.join(r['title'] for r in pending[:3])
    if lang == 'en':
        return f'You have {english_number(len(pending))} reminders. {listed}.'
    return f'මතක් කිරීම් {number_word(len(pending))}ක් තියෙනවා. {listed}.'


def say_reminder_set(title, seconds, lang='si'):
    minutes = round(seconds / 60)
    if lang == 'en':
        when = (f'in {english_number(seconds)} seconds' if seconds < 60 else
                f'in {english_number(minutes)} minutes' if seconds < 3600 else
                f'in {english_number(round(seconds / 3600))} hours')
        return f"Right. I'll remind you {when}." + (f' {title}.' if title else '')
    # The clock time is deliberately left out: appending the dative 'ට' to a time ending
    # in 'යි' gives "පනස්හතරයිට", which is not Sinhala. The delay alone is how it is said.
    when = (f'තත්පර {number_word(seconds)}කින්' if seconds < 60 else
            f'මිනිත්තු {number_word(minutes)}කින්' if seconds < 3600 else
            f'පැය {number_word(round(seconds / 3600))}කින්')
    return f'හරි. {when} මතක් කරන්නම්.' + (f' {title}.' if title else '')


def say_note_saved(title, lang='si'):
    if lang == 'en':
        return f'Saved. {title}.' if title else 'Note saved.'
    return f'සටහන සේව් කළා. {title}.' if title else 'සටහන සේව් කළා.'


def say_capabilities(lang='si'):
    if lang == 'en':
        return ('I can tell you the time, save notes, set reminders, check the system, '
                'play music and send messages. What do you need?')
    return ('මට වෙලාව කියන්න, සටහන් තියන්න, මතක් කිරීම් දාන්න, සිස්ටම් එක බලන්න, '
            'සින්දු දාන්න, මැසේජ් යවන්න පුළුවන්. මොකක් ද ඕනේ?')


def say_stopped(lang='si'):
    return 'Right.' if lang == 'en' else 'හරි.'


def say_not_understood(lang='si'):
    return ('I did not catch that. Say it again.' if lang == 'en'
            else 'මට තේරුණේ නැහැ. ආයෙත් කියන්න.')


def say_need_detail(what, lang='si'):
    if lang == 'en':
        return {'message': 'Who should I send it to, and what should it say?',
                'note': 'What should the note say?',
                'time': 'When should I remind you?',
                'app': 'Which application?'}.get(what, 'Tell me a little more.')
    return {'message': 'කාටද මැසේජ් එක යවන්නේ? මොකක්ද කියන්න ඕනේ?',
            'note': 'මොකක්ද සටහන් කරන්නේ?',
            'time': 'කීයටද මතක් කරන්නේ?',
            'app': 'මොන ඇප් එකද ඕපන් කරන්නේ?'}.get(what, 'ටිකක් විස්තර කියන්න.')


def say_failed(what, lang='si'):
    if lang == 'en':
        return {'note': 'I could not save that note.',
                'reminder': 'I could not set that reminder.'}.get(what, 'That did not work.')
    return {'note': 'සටහන සේව් කරන්න බැරි වුණා.',
            'reminder': 'මතක් කිරීම දාන්න බැරි වුණා.'}.get(what, 'ඒක කරන්න බැරි වුණා.')


def money(amount, lang='si'):
    """Rupees, spoken. 'Rs. 2500' is a symbol and a numeral, neither of which is speech."""
    try:
        amount = int(round(float(amount)))
    except (TypeError, ValueError):
        return str(amount)
    if lang == 'en':
        return f'{english_number(amount)} rupees' if amount <= 100 else f'{amount:,} rupees'
    return f'රුපියල් {number_word(amount)}' if amount <= 100 else f'රුපියල් {amount:,}'


# Commands answerable from a tool alone. Each entry names the tool and the formatter, so
# the model is never involved and the reply cannot contradict the data.
DIRECT = {
    'time': ('system_status', lambda result, store, lang: say_time(lang=lang)),
    'battery': ('system_status', lambda result, store, lang: say_battery(result, lang)),
    'system_status': ('system_status', lambda result, store, lang: say_system(result, lang)),
    'list_notes': ('list_records', lambda result, store, lang: say_notes(result, lang)),
    'capabilities': (None, lambda result, store, lang: say_capabilities(lang)),
    'stop': (None, lambda result, store, lang: say_stopped(lang)),
}


def answer(command, tools, store, lang='si'):
    """Run a direct command and return a spoken reply, or None if it is not direct."""
    if command not in DIRECT:
        return None
    tool_name, formatter = DIRECT[command]
    result = tools.execute(tool_name, {}) if tool_name else None
    return formatter(result, store, lang)
