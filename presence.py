"""It knows when you are there (Day 16).

    python presence.py              here or away, and why
    python presence.py --watch      print every change

Presence comes from Windows, not a camera: how long since the last key press or mouse
movement, and whether the screen is locked. The plan's webcam is optional and off by
default -- "a camera that might be on is worse than no camera" -- so the default had to
work without one. Nothing is recorded; only the current state is kept.

  here   input in the last AWAY_AFTER seconds, screen unlocked
  away   locked, or no input for AWAY_AFTER seconds

On arrival after a real absence it greets you -- or, the first time in the morning, gives
the briefing. While you are away it keeps quiet: reminders and remarks wait for you, and
music that was playing is paused, then resumed when you are back.
"""
import argparse
import ctypes
import datetime as dt
import sys
import threading
import time
from ctypes import wintypes

AWAY_AFTER = 5 * 60          # seconds without input
GREET_AFTER = 30 * 60        # an absence shorter than this is a coffee, not a return
POLL_SECONDS = 2.0
MORNING_FROM = 4             # an arrival after 04:00 can be the first of the day


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]


def idle_seconds():
    """Seconds since the last keyboard or mouse input, from Windows."""
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def locked():
    """True while the lock screen is up: the input desktop cannot be opened then."""
    user32 = ctypes.windll.user32
    desktop = user32.OpenInputDesktop(0, False, 0x0100)      # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False


def sense(away_after=AWAY_AFTER):
    """('here' | 'away', why)."""
    if locked():
        return 'away', 'the screen is locked'
    idle = idle_seconds()
    if idle >= away_after:
        return 'away', f'no input for {int(idle // 60)} min'
    return 'here', f'input {int(idle)} s ago'


class Presence:
    """Watches for arrivals and departures and calls back on each.

    on_arrive(absence_seconds, first_of_day) and on_leave() run on the watching thread.
    sense and clock are replaceable, so the decisions can be tested without anyone leaving
    the room.
    """

    def __init__(self, on_arrive=lambda seconds, first: None, on_leave=lambda: None,
                 sense=sense, clock=time.monotonic, today=dt.date.today, now=dt.datetime.now,
                 idle=idle_seconds):
        self.on_arrive, self.on_leave = on_arrive, on_leave
        self.sense, self.clock, self.today, self.now = sense, clock, today, now
        self.idle = idle
        self.state = 'here'
        self.since = clock()
        self.last_arrival_day = today()     # starting up counts as being here today
        self.stopped = threading.Event()

    @property
    def away(self):
        return self.state == 'away'

    def check(self):
        """One look. Returns 'arrived', 'left' or None."""
        state, _ = self.sense()
        if state == self.state:
            return None
        absence = self.clock() - self.since
        self.state, self.since = state, self.clock()
        if state == 'away':
            # The absence began at the last input, not when it was noticed. Measured: idle
            # for 170 s, then back, and it counted 8 s -- so no greeting.
            self.since -= self.idle()
            self.on_leave()
            return 'left'
        first = self.today() != self.last_arrival_day and self.now().hour >= MORNING_FROM
        self.last_arrival_day = self.today()
        self.on_arrive(absence, first)
        return 'arrived'

    def run(self):
        while not self.stopped.wait(POLL_SECONDS):
            try:
                self.check()
            except Exception as error:
                print(f'  presence: {error}', file=sys.stderr)

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True, name='presence')
        thread.start()
        return thread


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--away-after', type=float, default=AWAY_AFTER)
    args = parser.parse_args()
    if not args.watch:
        state, why = sense(args.away_after)
        print(f'{state}: {why}')
        return 0
    watcher = Presence(lambda seconds, first: print(f'arrived after {seconds:.0f}s'
                                                    + (' (first today)' if first else '')),
                       lambda: print('left'), sense=lambda: sense(args.away_after))
    print('watching; Ctrl+C to stop')
    try:
        watcher.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
