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
    if sinhala / len(letters) >= .3:
        return 'si'
    # Singlish is Sinhala in Latin letters, and wants a Sinhala answer. Measured on Day
    # 26: "mata hitha hodha sindu ekak daanna" was answered "Playing ...".
    from sinhala import singlish_to_script
    return 'si' if singlish_to_script(text) != text else 'en'


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


def say_time(_tools=None, _result=None, lang='si', moment=None):
    if lang == 'en':
        return f'It is {english_clock(moment)}.'
    return f'දැන් වෙලාව {clock(moment)}.'


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
                'time': 'When should I remind you? Say morning or night as well.',
                'reminder': 'What should I remind you of?',
                'skill': 'What should the new tool do?',
                'app': 'Which application?'}.get(what, 'Tell me a little more.')
    return {'message': 'කාටද මැසේජ් එක යවන්නේ? මොකක්ද කියන්න ඕනේ?',
            'note': 'මොකක්ද සටහන් කරන්නේ?',
            'time': 'කීයටද මතක් කරන්නේ? උදේද රෑද කියලත් කියන්න.',
            'reminder': 'මොකක්ද මතක් කරන්නේ?',
            'skill': 'අලුත් tool එක මොකක්ද කරන්න ඕනේ?',
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


def say_weather(data, lang='si'):
    """Spoken weather. Degrees and percentages become words, as everywhere else."""
    place = data.get('place', 'colombo')
    if lang == 'en':
        line = (f"In {place.title()} it is {english_number(data['temperature'])} degrees "
                f"and {data['condition_en']}.")
        if data.get('rain_chance', 0) >= 50:
            chance = english_number(data['rain_chance'])
            article = 'an' if chance.startswith(('eight', 'eleven')) else 'a'
            line += f' There is {article} {chance} percent chance of rain.'
        return line
    sinhala_place = SINHALA_PLACES.get(place, place)
    line = (f"{sinhala_place} {number_word(data['temperature'])} අංශකයි, "
            f"{data['condition_si']}.")
    if data.get('rain_chance', 0) >= 50:
        line += f" වැස්සක් එන්න සියයට {number_word(data['rain_chance'])}ක ඉඩක් තියෙනවා."
    return line


SINHALA_PLACES = {
    'colombo': 'කොළඹ', 'kandy': 'මහනුවර', 'galle': 'ගාල්ලේ', 'jaffna': 'යාපනයේ',
    'negombo': 'මීගමුවේ', 'kurunegala': 'කුරුණෑගල', 'matara': 'මාතර',
    'anuradhapura': 'අනුරාධපුරේ', 'nuwara eliya': 'නුවරඑළියේ',
}


def say_prices(cards, lang='si'):
    """Day 11: the closest product, and at most one other, from PricePlease.lk. Said by
    template: measured, the model turned Rs. 167,500 into "less than a lakh"."""
    def shops(card, english):
        count = card.get('shops') or 0
        if english:
            return f', at {count} shop{"s" if count != 1 else ""}' if count else ''
        return f', කඩ {count}ක' if count else ''
    # 'MacBook Air 15" M3': the inch mark is not speech.
    cards = [dict(c, name=re.sub(r'(\d+(?:\.\d+)?)\s*"', r'\1 inch', c['name']).strip(' &'))
             for c in cards]
    first = cards[0]
    if lang == 'en':
        said = f"{first['name']} starts at {money(first['rupees'], 'en')}{shops(first, True)}."
        if len(cards) > 1:
            said += f" The {cards[1]['name']} is from {money(cards[1]['rupees'], 'en')}."
        return said + ' That is from PricePlease.'
    said = f"{first['name']} {money(first['rupees'])} ඉඳන් තියෙනවා{shops(first, False)}."
    if len(cards) > 1:
        said += f" {cards[1]['name']} {money(cards[1]['rupees'])} ඉඳන්."
    return said + ' PricePlease එකේ හැටියට.'


def say_dollar(data, lang='si'):
    rate = data.get('rate', 0)
    if lang == 'en':
        return f'One US dollar is {rate:,.2f} rupees today.'
    # The rupee figure is left as digits: three hundred and thirty point six five is
    # harder to follow spoken than the number itself, and Sinhala TTS reads it correctly.
    return f'අද එක ඩොලරයක් රුපියල් {rate:,.2f}යි.'


def say_converted(data, lang='si'):
    if lang == 'en':
        return (f"{data['amount']:,.0f} {data['from']} is about "
                f"{data['result']:,.0f} {data['to']}.")
    return f"{data['amount']:,.0f} {data['from']} කියන්නේ රුපියල් {data['result']:,.0f}ක් විතර."


def say_news(headlines, lang='si'):
    if not headlines:
        return 'No news right now.' if lang == 'en' else 'දැනට පුවත් නැහැ.'
    listed = '. '.join(item['title'] for item in headlines[:3])
    if lang == 'en':
        return f'Today: {listed}.'
    return f'අද පුවත්. {listed}.'


def say_offline(lang='si'):
    return ('I cannot reach the internet just now.' if lang == 'en'
            else 'දැන් අන්තර්ජාලයට සම්බන්ධ වෙන්න බැහැ.')


def say_volume(data, lang='si'):
    level = data.get('volume')
    if data.get('muted'):
        return 'Muted.' if lang == 'en' else 'සද්දේ නිහඬ කළා.'
    if level is None:
        return 'Done.' if lang == 'en' else 'හරි.'
    if lang == 'en':
        return f'Volume is at {english_number(level)} percent.'
    return f'සද්දේ {percent(level)}යි.'


def say_opened(name, lang='si'):
    return f'Opening {name}.' if lang == 'en' else f'{name} ඕපන් කරනවා.'


def say_app_missing(spoken, lang='si'):
    if lang == 'en':
        return f'I could not find an application called {spoken}.'
    return f'{spoken} කියන ඇප් එකක් හම්බවුණේ නෑ.'


def say_screenshot(data, lang='si'):
    return 'Screenshot saved.' if lang == 'en' else 'ස්ක්‍රීන්ෂොට් එක සේව් කළා.'


def say_locking(lang='si'):
    return 'Locking the computer.' if lang == 'en' else 'පරිගණකය ලොක් කරනවා.'


def say_recent_messages(messages, lang='si'):
    """What arrived while you were away."""
    if not messages:
        return 'Nothing new.' if lang == 'en' else 'අලුතෙන් මොකුත් නෑ.'
    if lang == 'en':
        if len(messages) == 1:
            m = messages[0]
            return f'One message. {m["sender"]} says: {m.get("body") or ""}'.strip()
        senders = ', '.join(dict.fromkeys(m['sender'] for m in messages[:4]))
        return f'{english_number(len(messages))} messages, from {senders}.'
    if len(messages) == 1:
        m = messages[0]
        body = m.get('body') or ''
        return f'{m["sender"]}ගෙන් මැසේජ් එකක්. {body}'.strip()
    senders = ', '.join(dict.fromkeys(m['sender'] for m in messages[:4]))
    return f'මැසේජ් {number_word(len(messages))}ක් තියෙනවා. {senders}ගෙන්.'


def say_notifications_off(lang='si'):
    if lang == 'en':
        return ('No applications are allowed to notify me yet. '
                'Add one with notifications.py --allow WhatsApp.')
    return 'තාම මොන ඇප් එකකටවත් අවසර දීලා නෑ.'


# ---------------------------------------------------------------- speaking first (Day 19)

def clock_at(moment):
    """'රෑ දහයට' -- the time something happens at, rather than the time it is.

    clock() ends in 'යි', and adding the dative to that gives "දහයයිට", which is not
    Sinhala. The dative goes on the last number word instead.
    """
    hour = moment.hour % 12 or 12
    minute = moment.minute
    when, hour_word = period(moment.hour), number_word(hour)
    if minute == 0:
        return f'{when} {hour_word}ට'
    if minute == 30:
        return f'{when} {hour_word}හමාරට'
    return f'{when} {hour_word}යි {number_word(minute)}ට'


def english_at(moment):
    return f'at {english_clock(moment)}'


def say_greeting(moment=None, lang='si'):
    hour = (moment or dt.datetime.now()).hour
    if lang == 'en':
        return ('Good morning.' if hour < 12 else 'Good afternoon.' if hour < 18
                else 'Good evening.')
    return 'සුබ උදෑසනක්.' if hour < 12 else 'ආයුබෝවන්.'


def say_today(reminders, routines, lang='si'):
    """What is left to do today, from reminders and routines together."""
    items = list(dict.fromkeys(list(reminders) + list(routines)))
    if not items:
        return 'Nothing is scheduled for today.' if lang == 'en' else 'අද කරන්න දෙයක් දාලා නෑ.'
    listed = '. '.join(items[:3])
    if lang == 'en':
        if len(items) == 1:
            return f'One thing today. {listed}.'
        return f'{english_number(len(items)).capitalize()} things today. {listed}.'
    if len(items) == 1:
        return f'අද එක දෙයක් තියෙනවා. {listed}.'
    return f'අද කරන්න දේවල් {number_word(len(items))}ක් තියෙනවා. {listed}.'


def say_messages_waiting(count, lang='si'):
    if lang == 'en':
        return ('One message is waiting.' if count == 1 else
                f'{english_number(count).capitalize()} messages are waiting.')
    return ('මැසේජ් එකක් ඇවිත් තියෙනවා.' if count == 1 else
            f'මැසේජ් {number_word(count)}ක් ඇවිත් තියෙනවා.')


def say_reminder_due(title, late=False, lang='si'):
    if lang == 'en':
        return (f'You asked me to remind you earlier. {title}.' if late
                else f'Reminder. {title}.')
    return (f'මතක් කරන්න තිබුණු දෙයක්. {title}.' if late
            else f'මතක් කිරීමක්. {title}.')


def say_routine_set(title, at, lang='si'):
    moment = dt.datetime.combine(dt.date.today(), at)
    if lang == 'en':
        return f"Right. Every day {english_at(moment)} I'll remind you. {title}."
    return f'හරි. හැමදාම {clock_at(moment)} මතක් කරන්නම්. {title}.'


def say_reminder_at(title, moment, lang='si'):
    """A one-off reminder at a clock time, rather than after a delay."""
    tomorrow = moment.date() != dt.date.today()
    if lang == 'en':
        day = ' tomorrow' if tomorrow else ''
        return f"Right. I'll remind you{day} {english_at(moment)}." + (f' {title}.' if title else '')
    day = 'හෙට ' if tomorrow else ''
    return f'හරි. {day}{clock_at(moment)} මතක් කරන්නම්.' + (f' {title}.' if title else '')


def say_briefing_time_set(at, lang='si'):
    moment = dt.datetime.combine(dt.date.today(), at)
    if lang == 'en':
        return f"Right. I'll brief you every day {english_at(moment)}."
    return f'හරි. හැමදාම {clock_at(moment)} දවසේ විස්තර කියන්නම්.'


def say_battery_low(level, lang='si'):
    if lang == 'en':
        return f'The battery is down to {english_number(level)} percent. Plug in the charger.'
    return f'බැටරිය {percent(level)}යි. චාජර් එක ගහන්න.'


def say_disk_full(used, lang='si'):
    if lang == 'en':
        return f'The disk is {english_number(used)} percent full.'
    return f'තැටිය {percent(used)}ක් පිරිලා. ඉඩ හදන්න ඕනේ.'


def say_cpu_busy(lang='si'):
    if lang == 'en':
        return 'The processor has been at full load for five minutes. Something may be stuck.'
    return 'සීපීයූ එක මිනිත්තු පහක් තිස්සේ උපරිමෙන් වැඩ. මොකක් හරි හිරවෙලා වගේ.'


def say_missed(titles, lang='si'):
    """Reminders that came due while JARVIS was off for a day or more."""
    listed = '. '.join(titles[:2])
    if lang == 'en':
        if len(titles) == 1:
            return f'While I was off, a reminder came and went. {listed}.'
        return (f'While I was off, {english_number(len(titles))} reminders came and went. '
                f'{listed}.')
    if len(titles) == 1:
        return f'මම නැති වෙලාවේ මතක් කිරීමක් ගියා. {listed}.'
    return f'මම නැති වෙලාවේ මතක් කිරීම් {number_word(len(titles))}ක් ගියා. {listed}.'


# ---------------------------------------------------------------- multi-step tasks (Day 20)

ORDINALS = ['පළමුව', 'දෙවනුව', 'තෙවනුව', 'හතරවනුව', 'පස්වනුව', 'හයවනුව', 'හත්වනුව',
            'අටවනුව', 'නවවනුව', 'දහවනුව', 'එකොළොස්වනුව', 'දොළොස්වනුව']
ENGLISH_ORDINALS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh',
                    'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth']
PERIOD_WORDS = {
    'si': {'today': 'අද', 'yesterday': 'ඊයේ', 'this_week': 'මේ සතියේ',
           'last_week': 'පසුගිය සතියේ', 'this_month': 'මේ මාසයේ', 'last_month': 'පසුගිය මාසයේ',
           'this_year': 'මේ අවුරුද්දේ', 'last_year': 'පසුගිය අවුරුද්දේ', 'all': ''},
    'en': {'today': "today's", 'yesterday': "yesterday's", 'this_week': "this week's",
           'last_week': "last week's", 'this_month': "this month's",
           'last_month': "last month's", 'this_year': "this year's",
           'last_year': "last year's", 'all': 'all'},
}
SINHALA_WORDS = {'invoice': 'ඉන්වොයිස්', 'receipt': 'රිසිට්', 'bill': 'බිල්'}


def ordinal(number, lang='si'):
    table = ENGLISH_ORDINALS if lang == 'en' else ORDINALS
    return table[number - 1] if 0 < number <= len(table) else str(number)


def say_step(step, lang='si'):
    """One planned step, as it will be read out. From the step itself, not the model."""
    name, args = step['step'], step.get('args', {})
    if name == 'find_documents':
        kind = str(args.get('type', 'pdf') or '').upper().replace('ANY', '')
        word = str(args.get('contains', '') or '')
        when = PERIOD_WORDS[lang].get(args.get('period', 'all'), '')
        if lang == 'en':
            plural = word + 's' if word and not word.endswith('s') else word
            return ' '.join(p for p in ['find', when, kind, plural or 'documents'] if p)
        word = SINHALA_WORDS.get(word.lower(), word) or 'ලියකියවිලි'
        return ' '.join(p for p in [when, kind, word, 'හොයනවා'] if p)
    title = str(args.get('title', '') or '')
    if lang == 'en':
        return {'read_totals': 'read the total on each one', 'add_up': 'add them up',
                'write_summary': 'write a summary and save it',
                'email_me': 'put it in an email draft for you to send',
                'save_note': f'save a note, {title}', 'set_reminder': f'set a reminder, {title}',
                'weather': 'check the weather', 'dollar_rate': 'check the dollar rate',
                'news': 'check the news'}.get(name, name)
    return {'read_totals': 'හැම එකකම මුළු මුදල කියවනවා', 'add_up': 'ඒවා එකතු කරනවා',
            'write_summary': 'සාරාංශයක් ලියලා සේව් කරනවා',
            'email_me': 'ඊමේල් එකක් ලෑස්ති කරනවා, යවන්නේ ඔයා',
            'save_note': f'සටහනක් සේව් කරනවා, {title}',
            'set_reminder': f'මතක් කිරීමක් දානවා, {title}',
            'weather': 'කාලගුණය බලනවා', 'dollar_rate': 'ඩොලර් රේට් එක බලනවා',
            'news': 'පුවත් බලනවා'}.get(name, name)


def say_plan(steps, lang='si', ask=False):
    """The whole plan, read out in order, before anything is done."""
    parts = [f'{ordinal(n, lang)}, {say_step(step, lang)}.' for n, step in enumerate(steps, 1)]
    if lang == 'en':
        head = f'That is {english_number(len(steps))} steps.' if len(steps) > 1 else 'One step.'
        return ' '.join([head] + [p[0].upper() + p[1:] for p in parts]
                        + (['Shall I go ahead?'] if ask else []))
    head = f'මේක පියවර {number_word(len(steps))}කින් කරන්නම්.'
    return ' '.join([head] + parts + (['කරන්නද?'] if ask else []))


def say_amounts(totals, lang='si'):
    spoken = []
    for currency, amount in totals.items():
        if currency == 'LKR':
            spoken.append(money(amount, lang))
        elif lang == 'en':
            spoken.append(f'{amount:,.2f} {currency}')
        else:
            spoken.append({'USD': 'ඩොලර්', 'EUR': 'යුරෝ', 'GBP': 'පවුම්'}.get(currency, currency)
                          + f' {amount:,.2f}')
    return ', '.join(spoken)


def say_step_result(step, lang='si'):
    """What one finished step actually produced."""
    name, result = step['step'], step.get('result') or {}
    if name == 'find_documents':
        count = len(result.get('files', []))
        if lang == 'en':
            return f'Found {english_number(count)}.' if count else 'Found none.'
        return f'{number_word(count)}ක් හම්බවුණා.' if count else 'මොකුත් හම්බවුණේ නෑ.'
    if name == 'read_totals':
        missed = len(result.get('unreadable', []))
        if not missed:
            return ''
        return (f'{english_number(missed).capitalize()} could not be read.' if lang == 'en'
                else f'{number_word(missed)}ක මුදල කියවන්න බැරි වුණා.')
    if name == 'add_up':
        if not result.get('totals'):
            return 'There was nothing to add up.' if lang == 'en' else 'එකතු කරන්න දෙයක් නෑ.'
        amounts = say_amounts(result['totals'], lang)
        return f'The total is {amounts}.' if lang == 'en' else f'එකතුව {amounts}.'
    if name == 'write_summary':
        return ('The summary is saved in the summaries folder.' if lang == 'en'
                else 'සාරාංශය summaries ෆෝල්ඩර් එකේ සේව් කළා.')
    if name == 'email_me':
        if lang == 'en':
            return ('The email is ready in your mail app. Press Send.' if result.get('to')
                    else 'The email draft is open. Add your address and press Send.')
        return ('ඊමේල් එක ලෑස්තියි. Send ඔබන්න.' if result.get('to')
                else 'ඊමේල් draft එක open කළා. ඔයාගේ ලිපිනය දාලා Send ඔබන්න.')
    if name == 'save_note':
        return 'Note saved.' if lang == 'en' else 'සටහන සේව් කළා.'
    if name == 'set_reminder':
        return 'Reminder set.' if lang == 'en' else 'මතක් කිරීම දැම්මා.'
    if name == 'weather' and result.get('temperature') is not None:
        return say_weather(result, lang)
    if name == 'dollar_rate' and result.get('rate'):
        return say_dollar(result, lang)
    if name == 'news':
        return say_news(result.get('headlines', []), lang)
    return ''


def say_task_question(task, lang='si'):
    """A step failed and was not fixed by a retry: what now?"""
    failed = next((s for s in task['steps'] if s['state'] == 'failed'), None)
    which = ordinal(failed['number'], lang) if failed else ''
    if lang == 'en':
        return (f'The {which} step failed: {task.get("question", "")}. '
                'Shall I retry, skip it, or stop?')
    return (f'{which} පියවර වැඩ කළේ නෑ: {task.get("question", "")}. '
            'ආයෙත් කරන්නද, මඟහරින්නද, නවත්වන්නද?')


def say_task_report(task, lang='si'):
    """How it ended, built only from the journal."""
    lines = [say_step_result(s, lang) for s in task['steps'] if s['state'] == 'done']
    for step in task['steps']:
        if step['state'] in ('failed', 'skipped'):
            which = ordinal(step['number'], lang)
            lines.append(f'The {which} step was skipped.' if lang == 'en'
                         else f'{which} පියවර කළේ නෑ.')
    done = sum(1 for s in task['steps'] if s['state'] == 'done')
    total = len(task['steps'])
    if task['state'] == 'stopped':
        lines.append(f'Stopped after {english_number(done)} of {english_number(total)} steps.'
                     if lang == 'en' else
                     f'නැවැත්තුවා. පියවර {number_word(total)}න් {number_word(done)}ක් කළා.')
    elif task['state'] == 'timed_out':
        lines.append(f'I ran out of time after {english_number(done)} steps.' if lang == 'en'
                     else f'වෙලාව ඉවරයි. පියවර {number_word(done)}ක් විතරයි කළේ.')
    elif task['state'] == 'failed':
        lines.append('Something went wrong, and I stopped.' if lang == 'en'
                     else 'මොකක් හරි වැරදුණා, ඒ නිසා නැවැත්තුවා.')
    elif task['state'] == 'done' and not any(lines):
        lines.append('Done.' if lang == 'en' else 'හරි, කළා.')
    return ' '.join(line for line in lines if line)


def say_task_progress(task, lang='si'):
    """'How is it going?' -- which step, of how many."""
    if not task:
        return 'Nothing is running.' if lang == 'en' else 'දැන් කරන වැඩක් නෑ.'
    total = len(task['steps'])
    current = next((s for s in task['steps'] if s['state'] in ('running', 'failed')), None)
    if task['state'] not in ('running', 'needs_you') or not current:
        return say_task_report(task, lang)
    if lang == 'en':
        return (f'On step {english_number(current["number"])} of {english_number(total)}: '
                f'{say_step(current, lang)}.')
    return (f'පියවර {number_word(total)}න් {ordinal(current["number"])}: '
            f'{say_step(current, lang)}.')


# ---------------------------------------------------------------- skills it writes (Day 21)

def say_skill_started(lang='si'):
    if lang == 'en':
        return ("I'll write that tool with the deep brain. It takes a few minutes. "
                'You will need to read it and approve it in the console before I use it.')
    return ('හරි, ඒ tool එක ලියන්නම්. විනාඩි කීපයක් යනවා. '
            'පාවිච්චි කරන්න කලින් ඔයා console එකේ කියවලා approve කරන්න ඕනේ.')


def say_skill_busy(lang='si'):
    return ('I am already writing one. Ask again when it is done.' if lang == 'en'
            else 'දැනටමත් එකක් ලියනවා. ඒක ඉවර වුණාම ආයෙත් කියන්න.')


def say_skill_written(result, lang='si'):
    """Said when the writing finishes. It is never said to be ready to use: it is not."""
    if result.get('error'):
        return (f'I could not write that tool. {result["error"]}' if lang == 'en'
                else 'ඒ tool එක ලියන්න බැරි වුණා.')
    if result.get('problems'):
        return ('I wrote a tool, but it broke the safety rules, so it was refused. '
                'The console says why.' if lang == 'en' else
                'tool එකක් ලිව්වා, ඒත් ආරක්ෂක නීති කැඩුව නිසා ප්‍රතික්ෂේප වුණා. හේතුව console එකේ තියෙනවා.')
    return ('The new tool is written. Read it in the console, and approve it if it looks '
            'right.' if lang == 'en' else
            'අලුත් tool එක ලිව්වා. console එකේ කියවලා, හරි නම් approve කරන්න.')


def say_skill_failed(skill, error, lang='si'):
    title = skill.get('title_en' if lang == 'en' else 'title_si') or skill.get('name', '')
    return (f'The {title} tool did not work. {error}' if lang == 'en'
            else f'{title} tool එක වැඩ කළේ නෑ.')


# ---------------------------------------------------------------- deep think (Day 22)

def say_thinking_deeply(lang='si'):
    """Said at once, so the wait is expected rather than mistaken for a hang."""
    if lang == 'en':
        return ("I'll think about that properly and tell you in a few minutes. "
                'Loading the deep brain unloads the quick one, so I will be slower until then.')
    return ('හරි, ඒ ගැන හොඳට හිතලා විනාඩි කීපයකින් කියන්නම්. '
            'ඒ වෙනකම් මම ටිකක් හෙමින් වැඩ කරන්නේ.')


# ---------------------------------------------------------------- email (Day 9)

def sender_name(message):
    """"Kasun Perera" -> "Kasun Perera"; "noreply@daraz.lk" -> "daraz"."""
    name = str(message.get('from', '')).strip().strip('"')
    if '@' in name or not name:
        domain = str(message.get('address', name)).split('@')[-1]
        name = domain.split('.')[0] if domain else 'someone'
    return name[:40]


def say_new_mail(found, lang='si', brief=False):
    """What is new: how many, then who and what about, newest first (at most three)."""
    count, messages = found.get('count', 0), found.get('messages', [])[:3]
    if not count:
        if brief:
            return ''
        return 'No new email.' if lang == 'en' else 'අලුත් ඊමේල් නෑ.'
    each = [f"{sender_name(m)}: {short_title(m['subject'], 70)}" for m in messages]
    if lang == 'en':
        head = 'One new email.' if count == 1 else f'{count} new emails.'
        return ' '.join([head] + [f'{line}.' for line in each])
    head = 'අලුත් ඊමේල් එකක් තියෙනවා.' if count == 1 else f'අලුත් ඊමේල් {count}ක් තියෙනවා.'
    return ' '.join([head] + [f'{line}.' for line in each])


def say_mail_from(who, messages, lang='si'):
    if not messages:
        return (f'No recent email from {who}.' if lang == 'en'
                else f'{who} ගෙන් ළඟදී ඊමේල් ආවේ නෑ.')
    first = messages[0]
    when = str(first.get('when', ''))[:10]
    if lang == 'en':
        return f"The latest from {sender_name(first)}, on {when}: {short_title(first['subject'], 70)}."
    return f"{sender_name(first)} ගෙන් අන්තිමට ආවේ {when}: {short_title(first['subject'], 70)}."


def say_mail_not_set_up(lang='si'):
    return ('Email is not set up yet. Run mail.py --setup.' if lang == 'en'
            else 'ඊමේල් තාම set up කරලා නෑ.')


def say_which_mail(lang='si'):
    return ('Which email? Ask me to check your email first.' if lang == 'en'
            else 'මොන ඊමේල් එකද? මුලින් "ඊමේල් බලන්න" කියන්න.')


def say_mail_sent(to, lang='si'):
    return f'Sent to {to}.' if lang == 'en' else f'{to}ට ඊමේල් එක යැව්වා.'


def say_mail_failed(reason, lang='si'):
    return (f'I could not do that: {reason}.' if lang == 'en'
            else 'ඊමේල් එක කරන්න බැරි වුණා.')


def say_no_answer(lang='si'):
    """The model failed or timed out. Measured 16 Oct: "HTTPConnectionPool(host=
    '127.0.0.1', port=11434): Read timed out." was read aloud as the answer."""
    return ('Sorry, my thinking got stuck on that one. Please ask again.' if lang == 'en'
            else 'සමාවෙන්න, ඒකට උත්තරයක් හදාගන්න බැරි වුණා. ආයෙත් අහන්න.')


def say_still_thinking(lang='si'):
    return ('I am still thinking about the last one.' if lang == 'en'
            else 'කලින් එක ගැන තාම හිතනවා.')


def say_deep_answer(job, lang='si'):
    """The answer only. The reasoning is in the console, never read out."""
    if job.get('state') == 'done' and str(job.get('answer', '')).strip():
        lead = 'I have thought it through. ' if lang == 'en' else 'හිතලා බැලුවා. '
        return lead + job['answer'].strip()
    if job.get('state') == 'cancelled':
        return 'I stopped thinking about it.' if lang == 'en' else 'ඒ ගැන හිතන එක නැවැත්තුවා.'
    return ('I could not finish thinking that through. The console says why.' if lang == 'en'
            else 'ඒක හිතලා ඉවර කරන්න බැරි වුණා. හේතුව console එකේ තියෙනවා.')


# ---------------------------------------------------------------- seeing (Day 15)

def say_cannot_see(reason, lang='si'):
    if lang == 'en':
        return f'I could not look at that. {reason}.'
    return 'ඒක බලන්න බැරි වුණා.'


# ---------------------------------------------------------------- memory (Day 14)

def say_learned(facts, lang=None):
    """Said every time something is remembered. Secret memory loses trust."""
    spoken = '. '.join(f['spoken'].rstrip('.') for f in facts)
    lang = lang or language_of(spoken)
    if lang == 'en':
        return f"I'll remember that. {spoken}."
    return f'මම ඒක මතක තියාගන්නම්. {spoken}.'


# ---------------------------------------------------------------- media (Day 26)

def short_title(title, limit=60):
    """A video title as it can be said: no hashtags, no '| channel', no emoji, not too long.

    Measured 16 Oct: "ලොව ආකර්ෂණීයම සිංදු😱Top 10 Most Attitude Song In The World🥰#ytshorts"
    was read out whole, the emoji spelled into the speech."""
    title = re.sub(r'\[[^\]]*\]', ' ', str(title))           # [JUKEBOX], [Official Video]
    title = re.split(r'#|\s\|\s?|\|', title)[0]
    # Letters, Sinhala (with its joiner), digits and plain punctuation; emoji become a gap.
    title = re.sub(r'[^\w\s඀-෿‍.,\'&()+-]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -|,.')
    return title[:limit].rsplit(' ', 1)[0] if len(title) > limit else title


def say_playing(item, lang='si'):
    title = short_title(item.get('title', ''))
    again = item.get('source') == 'history'
    if lang == 'en':
        return (f'Playing it again. {title}.' if again else f'Playing {title}.')
    return (f'හරි, ආයෙත් දානවා. {title}.' if again else f'හරි, දානවා. {title}.')


def say_media_done(action, lang='si'):
    if lang == 'en':
        return {'pause': 'Paused.', 'resume': 'Carrying on.', 'next': 'Next one.',
                'previous': 'The one before.'}.get(action, 'Done.')
    return {'pause': 'නැවැත්තුවා.', 'resume': 'හරි.', 'next': 'ඊළඟ එක.',
            'previous': 'කලින් එක.'}.get(action, 'හරි.')


def say_no_music(reason, lang='si'):
    if lang == 'en':
        return f'I could not play that. {reason}.'
    if 'nothing has been played' in reason:
        return 'තාම මොකුත් සින්දු දාලා නෑ.'
    return 'ඒ සින්දුව දාන්න බැරි වුණා.'


# ---------------------------------------------------------------- who is speaking (Day 17)

def say_not_owner(lang='si'):
    """Polite, and final: a voice that is not the owner's does not send in his name."""
    if lang == 'en':
        return "Sorry, I can only send messages for Chamindu, and that didn't sound like him."
    return 'සමාවෙන්න, මැසේජ් යවන්න පුළුවන් චමිදූට විතරයි.'


# ---------------------------------------------------------------- presence (Day 16)

def say_welcome_back(name='චමිදූ', waiting=0, lang='si'):
    """Said on a return after a real absence. The greeting alone is a fixed phrase, so it
    is cached and still spoken in Sinhala when the voice service is down."""
    if lang == 'en':
        line = f'Welcome back, {name if name.isascii() else "Chamindu"}.'
        return line + (f' {say_messages_waiting(waiting, "en")}' if waiting else '')
    return f'ආයුබෝවන් {name}' + (f'. {say_messages_waiting(waiting, "si")}' if waiting else '')
