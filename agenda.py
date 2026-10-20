"""Your calendar (Day 9): what is on today or tomorrow, said aloud and in the briefing.

    python agenda.py --setup        store the calendar's secret address (asked for, hidden)
    python agenda.py                today and tomorrow

Read from Google Calendar's "secret address in iCal format" -- Settings, your calendar,
"Integrate calendar". It lets a program read that calendar and nothing more: no Google
Cloud project, no sign-in, and it cannot change anything. Anyone holding it can read the
calendar, so it is kept in Windows Credential Manager like the email password, and Google
can reset it ("Reset" beside it) at any time.

Recurring events -- a weekly lecture, a monthly bill -- are expanded properly. Only the
title, the time and the place are ever said; an event's description is someone else's
words and is never read out or acted on.
"""
import argparse
import datetime as dt
import getpass
import re
import sys
import threading
import time

CREDENTIAL = 'calendar'                   # "JARVIS/calendar" in Credential Manager
TIMEOUT = 20
KEEP_SECONDS = 600                        # a fetched calendar is good for ten minutes


class AgendaError(Exception):
    pass


def address():
    import vault
    try:
        return vault.read(CREDENTIAL)[1]
    except vault.Missing as error:
        raise AgendaError('the calendar is not set up yet (python agenda.py --setup)') from error


def is_set_up():
    try:
        address()
        return True
    except AgendaError:
        return False


_cache = {'text': None, 'at': 0.0}
_lock = threading.Lock()


def fetch(url=None, fresh=False):
    """The calendar as iCal text, at most ten minutes old."""
    import requests
    with _lock:
        if not fresh and url is None and _cache['text'] and \
                time.monotonic() - _cache['at'] < KEEP_SECONDS:
            return _cache['text']
    try:
        response = requests.get(url or address(), timeout=TIMEOUT)
    except requests.RequestException as error:
        raise AgendaError('the calendar could not be reached') from error
    if response.status_code != 200 or 'BEGIN:VCALENDAR' not in response.text[:2000]:
        raise AgendaError('that address did not give a calendar')
    if url is None:
        with _lock:
            _cache.update(text=response.text, at=time.monotonic())
    return response.text


def local(value):
    """An event's start or end as local time. An all-day event is a date: midnight."""
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)).astimezone()
    return dt.datetime.combine(value, dt.time()).astimezone()


def events_between(start, end, text=None):
    """[{'title', 'start', 'end', 'all_day', 'place'}], soonest first, all-day ones first."""
    import icalendar
    import recurring_ical_events
    calendar = icalendar.Calendar.from_ical(text if text is not None else fetch())
    found = []
    for event in recurring_ical_events.of(calendar).between(start, end):
        begins = event.get('DTSTART').dt
        finishes = event.get('DTEND').dt if event.get('DTEND') else begins
        all_day = not isinstance(begins, dt.datetime)
        title = re.sub(r'\s+', ' ', str(event.get('SUMMARY', '') or '(no title)')).strip()
        found.append({'title': title[:120], 'start': local(begins), 'end': local(finishes),
                      'all_day': all_day,
                      'place': re.sub(r'\s+', ' ', str(event.get('LOCATION', '') or ''))[:80]})
    return sorted(found, key=lambda e: (not e['all_day'], e['start']))


def day(offset=0, moment=None, text=None):
    """What is on today (0) or tomorrow (1). Today, only what has not finished yet."""
    moment = moment or dt.datetime.now().astimezone()
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=offset)
    events = events_between(midnight, midnight + dt.timedelta(days=1), text)
    if offset == 0:
        events = [e for e in events if e['all_day'] or e['end'] > moment]
    return events


def setup():
    print('JARVIS calendar setup. In Google Calendar: Settings -> your calendar -> '
          '"Integrate calendar" -> "Secret address in iCal format". Copy that address.')
    url = getpass.getpass('Secret address (hidden as you paste): ').strip()
    if not re.match(r'https://[^\s]+\.ics(\?.*)?$', url):
        print('That is not an iCal address (it should start with https:// and end in .ics). '
              'Nothing was saved.')
        return 1
    try:
        text = fetch(url)
        coming = events_between(dt.datetime.now().astimezone(),
                                dt.datetime.now().astimezone() + dt.timedelta(days=7), text)
    except AgendaError as error:
        print(f'{error}. Nothing was saved.')
        return 1
    import vault
    vault.save(CREDENTIAL, 'Google Calendar', url, 'Calendar secret iCal address for JARVIS')
    print(f'Done. {len(coming)} events in the next seven days. The address is in Windows '
          'Credential Manager under "JARVIS/calendar".')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--forget', action='store_true')
    args = parser.parse_args()
    if args.setup:
        return setup()
    if args.forget:
        import vault
        vault.forget(CREDENTIAL)
        print('Removed. To make the old address useless too, press Reset beside it in '
              'Google Calendar.')
        return 0
    try:
        for offset, name in ((0, 'Today'), (1, 'Tomorrow')):
            print(name)
            for event in day(offset):
                when = 'all day' if event['all_day'] else event['start'].strftime('%H:%M')
                print(f"  {when:>8}  {event['title']}" + (f"  ({event['place']})" if event['place'] else ''))
    except AgendaError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
