"""Presence (Day 16): arrivals and departures, decided without anyone leaving the room."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import presence
import responses
import routines
from assistant_core import Store


class World:
    """A fake clock, calendar and room."""

    def __init__(self):
        self.seconds = 0.0
        self.day = dt.date(2026, 10, 15)
        self.hour = 10
        self.state = 'here'
        self.idle = 0.0

    def sense(self):
        return self.state, 'fake'

    def clock(self):
        return self.seconds

    def today(self):
        return self.day

    def now(self):
        return dt.datetime.combine(self.day, dt.time(self.hour))


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.events = []
        self.watcher = presence.Presence(
            lambda seconds, first: self.events.append(('arrive', seconds, first)),
            lambda: self.events.append(('leave',)),
            sense=self.world.sense, clock=self.world.clock, today=self.world.today,
            now=self.world.now, idle=lambda: self.world.idle)

    def test_leaving_and_coming_back(self):
        self.assertIsNone(self.watcher.check())
        self.world.state = 'away'
        self.assertEqual(self.watcher.check(), 'left')
        self.assertTrue(self.watcher.away)
        self.world.seconds += 40 * 60
        self.world.state = 'here'
        self.assertEqual(self.watcher.check(), 'arrived')
        self.assertEqual(self.events, [('leave',), ('arrive', 2400.0, False)])

    def test_the_first_arrival_of_a_new_day(self):
        self.world.state = 'away'
        self.watcher.check()
        self.world.day += dt.timedelta(days=1)
        self.world.hour = 7
        self.world.state = 'here'
        self.watcher.check()
        self.assertTrue(self.events[-1][2], 'first time today')
        self.world.state = 'away'
        self.watcher.check()
        self.world.state = 'here'
        self.watcher.check()
        self.assertFalse(self.events[-1][2], 'only once a day')

    def test_the_absence_starts_at_the_last_input(self):
        """Measured: idle 170 s before it was noticed, then back -- and it counted 8 s."""
        self.world.idle = 170
        self.world.state = 'away'
        self.watcher.check()
        self.world.seconds += 8
        self.world.state = 'here'
        self.watcher.check()
        self.assertEqual(self.events[-1][1], 178)

    def test_a_night_visit_is_not_the_morning(self):
        """Up at 2 a.m. for water is not the start of the day."""
        self.world.state = 'away'
        self.watcher.check()
        self.world.day += dt.timedelta(days=1)
        self.world.hour = 2
        self.world.state = 'here'
        self.watcher.check()
        self.assertFalse(self.events[-1][2])


class BriefingOnceTests(unittest.TestCase):
    def test_the_clock_and_an_arrival_never_both_brief(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'p.db')
            clock = routines.Routines(store, lambda text: None, battery=lambda: None,
                                      disk=lambda: 0, cpu=lambda: 0)
            moment = dt.datetime(2026, 10, 15, 8, 0).astimezone()
            self.assertTrue(clock.claim_briefing(moment))
            self.assertFalse(clock.claim_briefing(moment))
            self.assertIsNone(clock.briefing_on_arrival(moment))
            self.assertEqual(clock.check_briefing(moment), [])


class RealSignalTests(unittest.TestCase):
    def test_windows_answers(self):
        self.assertGreaterEqual(presence.idle_seconds(), 0)
        self.assertIn(presence.locked(), (True, False))
        self.assertIn(presence.sense()[0], ('here', 'away'))


class WordingTests(unittest.TestCase):
    def test_welcome_back(self):
        self.assertEqual(responses.say_welcome_back(), 'ආයුබෝවන් චමින්දු')
        self.assertIn('දෙකක්', responses.say_welcome_back('චමින්දු', 2))
        self.assertEqual(responses.say_welcome_back('චමින්දු', 0, 'en'), 'Welcome back, Chamindu.')

    def test_messages_wait_while_away(self):
        import ast
        source = (Path(__file__).resolve().parents[1] / 'voice_loop.py').read_text(encoding='utf-8')
        announce = next(n for n in ast.walk(ast.parse(source))
                        if isinstance(n, ast.FunctionDef) and n.name == 'announce')
        self.assertIn('watcher.away', ast.unparse(announce))


if __name__ == '__main__':
    unittest.main()
