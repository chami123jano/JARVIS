"""JARVIS speaking first: reminders, the morning briefing, routines and watchers (Day 19).

Everything here runs on one clock thread inside the voice loop, so reminders fire whether
or not the browser console is open. Two rules keep an assistant that speaks first from
becoming one that gets muted:

  - What you asked for always happens: a reminder you set, a routine you made, the
    briefing at the time you chose. Neither quiet hours nor the budget can stop them.
  - What JARVIS decides to mention on its own -- a low battery, a full disk, a message --
    is held during quiet hours and limited to a few remarks an hour, most important first.

    python routines.py --briefing          say the briefing now
    python routines.py --status            settings, routines, pending reminders
    python routines.py --set-time 07:30    briefing time
    python routines.py --quiet 22:30 06:30 quiet hours
"""
import argparse
import collections
import datetime as dt
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent

DEFAULTS = {
    'briefing_enabled': True,
    'briefing_time': '07:30',
    'quiet_hours': ['22:30', '06:30'],
    'interruptions_per_hour': 4,
}
TICK_SECONDS = 15
# The computer was asleep at 07:30 and woke at 09:00: brief then. At 13:00 it is no
# longer a morning briefing, and today is skipped.
BRIEFING_GRACE = dt.timedelta(hours=3)
# A routine for 22:00 noticed at 22:40 still makes sense; noticed at 03:00 it does not.
ROUTINE_GRACE = dt.timedelta(hours=1)
# A reminder this late is said as late, so it does not sound as if it were on time.
LATE = dt.timedelta(minutes=10)
# Older than this, missed reminders are summed up in one sentence rather than read out.
STALE = dt.timedelta(days=1)

# Lower is more important. When several remarks are ready at once, the budget is spent
# on the important ones and the rest wait for the next hour.
PRIORITY = {'reminder': 0, 'routine': 0, 'briefing': 1, 'battery': 2, 'disk': 3,
            'cpu': 3, 'message': 4}
# Asked for, so always said. Everything else is JARVIS's own idea.
ASKED_FOR = {'reminder', 'routine', 'briefing'}

BATTERY_LOW, BATTERY_REARM = 20, 25
DISK_FULL = 90
CPU_BUSY, CPU_MINUTES = 90, 5


def now_local():
    return dt.datetime.now().astimezone()


def parse_hhmm(text):
    """'07:30' -> time(7, 30). Raises ValueError for anything else."""
    hour, minute = str(text).strip().split(':')
    return dt.time(int(hour), int(minute))


def settings(store):
    return {key: store.config(key, default) for key, default in DEFAULTS.items()}


def in_quiet_hours(moment, quiet):
    """Quiet hours usually cross midnight: 22:30 to 06:30 includes 23:00 and 03:00."""
    if not quiet:
        return False
    start, end = parse_hhmm(quiet[0]), parse_hhmm(quiet[1])
    current = moment.time().replace(second=0, microsecond=0)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


class Gate:
    """Decides whether a remark JARVIS thought of may be said now."""

    def __init__(self, store):
        self.store = store
        self.said = collections.deque()

    def allow(self, kind, moment):
        if kind in ASKED_FOR:
            return True, 'asked for'
        config = settings(self.store)
        if in_quiet_hours(moment, config['quiet_hours']):
            return False, 'quiet hours'
        while self.said and moment - self.said[0] > dt.timedelta(hours=1):
            self.said.popleft()
        if len(self.said) >= int(config['interruptions_per_hour']):
            return False, 'hourly limit reached'
        self.said.append(moment)
        return True, ''


# ---------------------------------------------------------------- the briefing

def briefing(store, lang='si', moment=None, waiting_messages=0, lookups=None):
    """The morning briefing as one spoken text.

    Each online part is fetched in parallel and fails on its own: no internet costs the
    weather and the news, never the time and your reminders.
    """
    import knowledge
    import responses
    moment = moment or now_local()
    if lookups is None:
        lookups = {
            'weather': lambda: responses.say_weather(knowledge.weather(), lang),
            'dollar': lambda: responses.say_dollar(knowledge.dollar_rate(), lang),
            'news': lambda: responses.say_news(knowledge.news(), lang),
        }
        import agenda
        if agenda.is_set_up():
            # Day 9: what is on the calendar today; nothing said if it is empty.
            lookups['calendar'] = lambda: responses.say_agenda(agenda.day(0, moment), False,
                                                               lang, brief=True)
        import mail
        if mail.is_set_up():
            # Day 9: new mail in the Primary tab, said briefly; nothing if there is none.
            lookups['email'] = lambda: responses.say_new_mail(
                mail.WATCHER.fresh() or mail.WATCHER.refresh(), lang, brief=True)
    with ThreadPoolExecutor(max_workers=len(lookups)) as pool:
        futures = {name: pool.submit(build) for name, build in lookups.items()}
    online = {}
    for name, future in futures.items():
        try:
            online[name] = future.result()
        except Exception as error:
            print(f'  briefing: {name} unavailable ({error})', file=sys.stderr)

    parts = [responses.say_greeting(moment, lang), responses.say_time(lang=lang, moment=moment)]
    if 'weather' in online:
        parts.append(online['weather'])
    parts.append(responses.say_today(todays_reminders(store, moment),
                                     todays_routines(store, moment), lang))
    if online.get('calendar'):
        parts.append(online['calendar'])
    if waiting_messages:
        parts.append(responses.say_messages_waiting(waiting_messages, lang))
    if online.get('email'):
        parts.append(online['email'])
    if 'dollar' in online:
        parts.append(online['dollar'])
    if 'news' in online:
        parts.append(online['news'])
    if not any(name in online for name in ('weather', 'dollar', 'news')):
        # Not "if not online": mail the watcher saw minutes ago is no sign of a connection.
        parts.append(responses.say_offline(lang))
    return ' '.join(part for part in parts if part)


def todays_reminders(store, moment):
    """Reminders still to come today, soonest first."""
    end = moment.replace(hour=23, minute=59, second=59)
    due = []
    for record in store.records():
        if record['kind'] != 'reminder' or record['done'] or not record['due']:
            continue
        when = dt.datetime.fromisoformat(record['due'])
        if moment <= when <= end:
            due.append((when, record['title']))
    return [title for _, title in sorted(due)]


def todays_routines(store, moment):
    return [r['title'] for r in sorted(routines(store), key=lambda r: r['time'])
            if parse_hhmm(r['time']) > moment.time()]


# ---------------------------------------------------------------- daily routines

BACKUPS_KEPT = 7


def backup(database, day=None, keep=BACKUPS_KEPT):
    """A consistent copy of the database in data/backups, through SQLite's own backup --
    safe while JARVIS is writing to it, unlike copying the file. The oldest beyond
    `keep` are deleted. Returns the new file's path."""
    import sqlite3
    database = Path(database)
    folder = database.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{database.stem}-{(day or dt.date.today()).isoformat()}.db'
    source, copy = sqlite3.connect(database), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    for old in sorted(folder.glob(f'{database.stem}-*.db'))[:-keep]:
        old.unlink()
    return target


def routines(store):
    return list(store.config('routines', []))


def add_routine(store, at, title, moment=None):
    """A daily reminder. Made after today's time has passed, it starts tomorrow."""
    moment = moment or now_local()
    if not str(title).strip():
        raise ValueError('A routine needs something to say.')
    at = dt.time(at.hour, at.minute)
    routine = {'id': uuid.uuid4().hex[:8], 'time': at.strftime('%H:%M'),
               'title': str(title).strip()[:200],
               'last': moment.date().isoformat() if at <= moment.time() else ''}
    store.set_config('routines', routines(store) + [routine])
    return routine


def remove_routine(store, routine_id):
    before = routines(store)
    kept = [r for r in before if r['id'] != routine_id]
    store.set_config('routines', kept)
    return len(kept) != len(before)


# ---------------------------------------------------------------- the clock

class Routines:
    """Checks every few seconds for something worth saying, and says it.

    say(text) speaks; busy() is true while the user is giving a command, so JARVIS never
    talks over them -- whatever is due waits for the next tick. waiting_messages()
    counts messages heard but not yet read out, for the briefing.
    """

    def __init__(self, store, say, busy=lambda: False, waiting_messages=lambda: 0,
                 battery=psutil.sensors_battery, disk=None, cpu=None):
        self.store, self.say, self.busy = store, say, busy
        self.waiting_messages = waiting_messages
        self.gate = Gate(store)
        self.battery = battery
        # The drive JARVIS lives on, which is where its logs and models fill up.
        self.disk = disk or (lambda: psutil.disk_usage(ROOT.anchor).percent)
        self.cpu = cpu or (lambda: psutil.cpu_percent(interval=None))
        self.battery_warned = False
        self.cpu_busy_since = None
        self.cpu_warned_at = None
        self.held = []          # what the gate refused, for --status and the log
        self.stopped = threading.Event()
        self.briefing_lock = threading.Lock()

    def lang(self):
        return self.store.config('reply_language', 'si')

    # Each check returns remarks: (kind, text, on_said). on_said runs only once the
    # remark is actually spoken, so a reminder is never marked done unheard.

    def check_reminders(self, moment):
        import responses
        remarks, stale = [], []
        for record in self.store.records():
            if record['kind'] != 'reminder' or record['done'] or not record['due']:
                continue
            due = dt.datetime.fromisoformat(record['due'])
            if due > moment:
                continue
            if moment - due > STALE:
                stale.append(record)
                continue
            text = responses.say_reminder_due(record['title'], moment - due > LATE, self.lang())
            remarks.append(('reminder', text,
                            lambda rid=record['id']: self.store.update(rid, 'complete')))
        if stale:
            # Seen on the first live run: three reminders left over from days earlier,
            # read out one after another. After a long time off, one sentence will do.
            def clear(ids=[r['id'] for r in stale]):
                for record_id in ids:
                    self.store.update(record_id, 'complete')
            remarks.append(('reminder', responses.say_missed([r['title'] for r in stale],
                                                             self.lang()), clear))
        return remarks

    def check_routines(self, moment):
        import responses
        remarks, today = [], moment.date().isoformat()
        for routine in routines(self.store):
            at = dt.datetime.combine(moment.date(), parse_hhmm(routine['time']),
                                     moment.tzinfo)
            if routine.get('last') == today or not (at <= moment < at + ROUTINE_GRACE):
                continue
            remarks.append(('routine', responses.say_reminder_due(routine['title'], False,
                                                                  self.lang()),
                            lambda rid=routine['id']: self.mark_routine(rid, today)))
        return remarks

    def mark_routine(self, routine_id, today):
        updated = [dict(r, last=today) if r['id'] == routine_id else r
                   for r in routines(self.store)]
        self.store.set_config('routines', updated)

    def check_briefing(self, moment):
        config = settings(self.store)
        if not config['briefing_enabled']:
            return []
        today = moment.date().isoformat()
        if self.store.config('briefing_last', '') == today:
            return []
        at = dt.datetime.combine(moment.date(), parse_hhmm(config['briefing_time']),
                                 moment.tzinfo)
        if not at <= moment < at + BRIEFING_GRACE or not self.claim_briefing(moment):
            return []
        return [('briefing', briefing(self.store, self.lang(), moment,
                                      self.waiting_messages()), None)]

    def claim_briefing(self, moment=None):
        """Today's briefing, once. Both the clock and an arrival (Day 16) may give it; the
        first to claim it does, under a lock, so it is never given twice."""
        today = (moment or now_local()).date().isoformat()
        with self.briefing_lock:
            if self.store.config('briefing_last', '') == today:
                return False
            self.store.set_config('briefing_last', today)
            return True

    def briefing_on_arrival(self, moment=None):
        """The briefing text if this arrival should get it, else None."""
        moment = moment or now_local()
        if not settings(self.store)['briefing_enabled'] or not self.claim_briefing(moment):
            return None
        return briefing(self.store, self.lang(), moment, self.waiting_messages())

    def check_machine(self, moment):
        """Battery, disk and processor. Each counts as warned only once it is said.

        Seen on the first live run: a full disk found at one in the morning, held for
        quiet hours, and marked as warned anyway -- so at seven it was never said. During
        quiet hours these checks do not run at all, and pick up again in the morning.
        """
        import responses
        if in_quiet_hours(moment, settings(self.store)['quiet_hours']):
            return []
        remarks, lang = [], self.lang()
        battery = self.battery()
        if battery is not None:
            level = round(battery.percent)
            if battery.power_plugged or level >= BATTERY_REARM:
                self.battery_warned = False
            elif level < BATTERY_LOW and not self.battery_warned:
                remarks.append(('battery', responses.say_battery_low(level, lang),
                                lambda: setattr(self, 'battery_warned', True)))

        today = moment.date().isoformat()
        if self.store.config('disk_warned', '') != today:
            used = round(self.disk())
            if used >= DISK_FULL:
                remarks.append(('disk', responses.say_disk_full(used, lang),
                                lambda: self.store.set_config('disk_warned', today)))

        # Windows does not report CPU temperature without administrator rights, so the
        # watcher looks at the cause instead: the processor held at full load.
        if self.cpu() >= CPU_BUSY:
            self.cpu_busy_since = self.cpu_busy_since or moment
            recently = self.cpu_warned_at and moment - self.cpu_warned_at < dt.timedelta(hours=1)
            if moment - self.cpu_busy_since >= dt.timedelta(minutes=CPU_MINUTES) and not recently:
                remarks.append(('cpu', responses.say_cpu_busy(lang),
                                lambda: setattr(self, 'cpu_warned_at', moment)))
        else:
            self.cpu_busy_since = None
        return remarks

    def check_backup(self, moment):
        """Once a day, a copy of the database; seven kept. Silent: nothing to say."""
        today = moment.date().isoformat()
        if self.store.config('backup_last', '') == today:
            return None
        made = backup(self.store.path, moment.date())
        self.store.set_config('backup_last', today)
        return made

    def tick(self, moment=None):
        """One look at the clock. Returns [(kind, text, said)] for the log and tests."""
        moment = moment or now_local()
        if self.busy():
            return []
        remarks = []
        for check in (self.check_reminders, self.check_routines, self.check_briefing,
                      self.check_machine):
            try:
                remarks += check(moment)
            except Exception as error:
                print(f'  routines: {check.__name__} failed ({error})', file=sys.stderr)
        try:
            self.check_backup(moment)
        except Exception as error:
            print(f'  routines: backup failed ({error})', file=sys.stderr)
        remarks.sort(key=lambda remark: PRIORITY.get(remark[0], 9))
        outcome = []
        for kind, text, on_said in remarks:
            allowed, reason = self.gate.allow(kind, moment)
            if not allowed:
                # Logged once, not every fifteen seconds while it waits.
                if not self.held or self.held[-1][1:] != (kind, text, reason):
                    print(f'  (held {kind}: {reason})', flush=True)
                self.held.append((moment, kind, text, reason))
                del self.held[:-50]
                outcome.append((kind, text, False))
                continue
            print(f'\n  JARVIS ({kind}): {text[:160]}', flush=True)
            self.say(text)
            if on_said:
                on_said()
            outcome.append((kind, text, True))
        return outcome

    def remark(self, kind, text):
        """Something noticed elsewhere -- an arriving message -- through the same gate."""
        allowed, reason = self.gate.allow(kind, now_local())
        if not allowed:
            print(f'  (held {kind}: {reason})', flush=True)
            return False
        self.say(text)
        return True

    def run(self):
        print(f'Routines on: briefing at {settings(self.store)["briefing_time"]}, '
              f'quiet {"-".join(settings(self.store)["quiet_hours"])}', flush=True)
        while not self.stopped.wait(TICK_SECONDS):
            self.tick()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True, name='routines')
        thread.start()
        return thread

    def stop(self):
        self.stopped.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--briefing', action='store_true', help='say the briefing now')
    parser.add_argument('--silent', action='store_true', help='print instead of speaking')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--set-time', metavar='HH:MM')
    parser.add_argument('--quiet', nargs=2, metavar=('FROM', 'TO'))
    parser.add_argument('--off', action='store_true', help='turn the briefing off')
    parser.add_argument('--on', action='store_true', help='turn the briefing on')
    args = parser.parse_args()

    from assistant_core import ROOT, Store
    store = Store(ROOT / 'data' / 'jarvis.db')
    if args.set_time:
        store.set_config('briefing_time', parse_hhmm(args.set_time).strftime('%H:%M'))
    if args.quiet:
        store.set_config('quiet_hours', [parse_hhmm(t).strftime('%H:%M') for t in args.quiet])
    if args.off or args.on:
        store.set_config('briefing_enabled', bool(args.on))
    if args.briefing:
        text = briefing(store, store.config('reply_language', 'si'))
        print(text)
        if not args.silent:
            import speech
            speech.SPEAKER.say(text)
        return 0
    config = settings(store)
    print(f'briefing   {"on" if config["briefing_enabled"] else "off"} at {config["briefing_time"]}'
          f' (last given {store.config("briefing_last", "never")})')
    print(f'quiet      {config["quiet_hours"][0]} to {config["quiet_hours"][1]}')
    print(f'limit      {config["interruptions_per_hour"]} unprompted remarks an hour')
    for routine in routines(store):
        print(f'routine    {routine["time"]}  {routine["title"]}  [{routine["id"]}]')
    for title in todays_reminders(store, now_local()):
        print(f'today      {title}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
