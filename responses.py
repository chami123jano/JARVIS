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


def clock(moment=None):
    moment = moment or dt.datetime.now()
    hour = moment.hour % 12 or 12
    return f'{period(moment.hour)} {hour}.{moment.minute:02d}'


def say_time(_tools=None, _result=None):
    return f'දැන් වෙලාව {clock()}.'


def say_battery(result):
    level = result.get('battery')
    if level is None:
        return 'මේ පරිගණකයේ බැටරියක් නැහැ.'
    if level <= 20:
        return f'බැටරිය සියයට {level}යි. චාජ් කරන්න ඕනේ.'
    return f'බැටරිය සියයට {level}යි.'


def say_system(result):
    parts = [f"සීපීයූ සියයට {round(result['cpu'])}",
             f"මතකය සියයට {round(result['memory'])}",
             f"තැටිය සියයට {round(result['disk'])}"]
    line = 'සිස්ටම් එක හොඳින්. ' + ', '.join(parts) + 'යි.'
    if result.get('battery') is not None:
        line += f" බැටරිය සියයට {result['battery']}යි."
    return line


def say_notes(records):
    notes = [r for r in records if r['kind'] == 'note' and not r['done']]
    if not notes:
        return 'ඔබට සටහන් නැහැ.'
    if len(notes) == 1:
        return f'ඔබට සටහනක් තියෙනවා. {notes[0]["title"]}.'
    listed = '. '.join(note['title'] for note in notes[:5])
    more = f' තව {len(notes) - 5}ක් තියෙනවා.' if len(notes) > 5 else ''
    return f'ඔබට සටහන් {len(notes)}ක් තියෙනවා. {listed}.{more}'


def say_reminders(records):
    pending = [r for r in records if r['kind'] == 'reminder' and not r['done']]
    if not pending:
        return 'මතක් කිරීම් නැහැ.'
    return f'මතක් කිරීම් {len(pending)}ක් තියෙනවා. ' + \
           '. '.join(r['title'] for r in pending[:3]) + '.'


def say_reminder_set(title, seconds):
    minutes = round(seconds / 60)
    when = (f'තත්පර {seconds}කින්' if seconds < 60 else
            f'මිනිත්තු {minutes}කින්' if seconds < 3600 else
            f'පැය {round(seconds / 3600)}කින්')
    at = (dt.datetime.now() + dt.timedelta(seconds=seconds))
    return f'හරි. {when}, {clock(at)}ට මතක් කරන්නම්.' + (f' {title}.' if title else '')


def say_note_saved(title):
    return f'සටහන සේව් කළා. {title}.' if title else 'සටහන සේව් කළා.'


def say_capabilities():
    return ('මට වෙලාව කියන්න, සටහන් තියන්න, මතක් කිරීම් දාන්න, සිස්ටම් එක බලන්න, '
            'සින්දු දාන්න, මැසේජ් යවන්න පුළුවන්. මොකක් ද ඕනේ?')


def say_stopped():
    return 'හරි.'


def say_not_understood():
    return 'මට තේරුණේ නැහැ. ආයෙත් කියන්න.'


def say_need_detail(what):
    return {'message': 'කාටද මැසේජ් එක යවන්නේ? මොකක්ද කියන්න ඕනේ?',
            'note': 'මොකක්ද සටහන් කරන්නේ?',
            'time': 'කීයටද මතක් කරන්නේ?',
            'app': 'මොන ඇප් එකද ඕපන් කරන්නේ?'}.get(what, 'ටිකක් විස්තර කියන්න.')


# Commands answerable from a tool alone. Each entry names the tool and the formatter, so
# the model is never involved and the reply cannot contradict the data.
DIRECT = {
    'time': ('system_status', lambda result, store: say_time()),
    'battery': ('system_status', lambda result, store: say_battery(result)),
    'system_status': ('system_status', lambda result, store: say_system(result)),
    'list_notes': ('list_records', lambda result, store: say_notes(result)),
    'capabilities': (None, lambda result, store: say_capabilities()),
    'stop': (None, lambda result, store: say_stopped()),
}


def answer(command, tools, store):
    """Run a direct command and return spoken Sinhala, or None if it is not direct."""
    if command not in DIRECT:
        return None
    tool_name, formatter = DIRECT[command]
    result = tools.execute(tool_name, {}) if tool_name else None
    return formatter(result, store)
