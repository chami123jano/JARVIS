"""Speaking first: what is said, when, and what is held back (Day 19).

An assistant that speaks unprompted fails in two directions. Too quiet, and the reminder
you set is never heard. Too loud, and it talks at three in the morning and gets muted for
good. These tests pin both edges with a fake clock and a throwaway database.
"""
import datetime as dt
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import responses
import router
import routines
from assistant_core import Store

TZ = dt.datetime.now().astimezone().tzinfo


def at(hour, minute=0, day=15):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=TZ)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / 'test.db')
        self.store.set_config('reply_language', 'si')
        # Off unless a test is about it: at 10:00 the 07:30 briefing is still in its
        # grace period, and would be said alongside whatever the test is watching.
        self.store.set_config('briefing_enabled', False)
        self.spoken = []
        self.busy = False
        self.machine = SimpleNamespace(battery=None, disk=50, cpu=5)
        self.clock = routines.Routines(
            self.store, self.spoken.append, busy=lambda: self.busy,
            battery=lambda: self.machine.battery, disk=lambda: self.machine.disk,
            cpu=lambda: self.machine.cpu)

    def tearDown(self):
        self.folder.cleanup()

    def remind(self, title, due):
        record_id = self.store.add('reminder', title, due=due.isoformat())['id']
        return record_id

    def done(self, record_id):
        return next(r for r in self.store.records() if r['id'] == record_id)['done']


class QuietHoursTests(unittest.TestCase):
    QUIET = ['22:30', '06:30']

    def test_quiet_hours_cross_midnight(self):
        for hour, minute, quiet in [(23, 0, True), (3, 0, True), (6, 29, True),
                                    (6, 30, False), (12, 0, False), (22, 29, False)]:
            with self.subTest(time=f'{hour}:{minute}'):
                self.assertEqual(routines.in_quiet_hours(at(hour, minute), self.QUIET), quiet)

    def test_quiet_hours_within_a_day(self):
        self.assertTrue(routines.in_quiet_hours(at(14), ['13:00', '15:00']))
        self.assertFalse(routines.in_quiet_hours(at(16), ['13:00', '15:00']))

    def test_no_quiet_hours(self):
        self.assertFalse(routines.in_quiet_hours(at(3), []))
        self.assertFalse(routines.in_quiet_hours(at(3), ['00:00', '00:00']))


class GateTests(StoreCase):
    def test_what_you_asked_for_ignores_quiet_hours(self):
        for kind in ('reminder', 'routine', 'briefing'):
            with self.subTest(kind=kind):
                self.assertTrue(self.clock.gate.allow(kind, at(3))[0])

    def test_jarvis_own_remarks_wait_for_morning(self):
        allowed, reason = self.clock.gate.allow('message', at(3))
        self.assertFalse(allowed)
        self.assertEqual(reason, 'quiet hours')

    def test_the_hourly_limit(self):
        limit = routines.DEFAULTS['interruptions_per_hour']
        results = [self.clock.gate.allow('message', at(10, i))[0] for i in range(limit + 2)]
        self.assertEqual(results, [True] * limit + [False, False])
        # An hour later the budget is back.
        self.assertTrue(self.clock.gate.allow('message', at(11, 30))[0])

    def test_reminders_do_not_spend_the_budget(self):
        self.store.set_config('interruptions_per_hour', 1)
        for _ in range(5):
            self.assertTrue(self.clock.gate.allow('reminder', at(10))[0])
        self.assertTrue(self.clock.gate.allow('message', at(10))[0])


class ReminderTests(StoreCase):
    def test_a_due_reminder_is_said_once_and_marked_done(self):
        record = self.remind('බෙහෙත් බොන්න', at(9, 59))
        said = self.clock.tick(at(10))
        self.assertEqual([kind for kind, _, ok in said if ok], ['reminder'])
        self.assertIn('බෙහෙත් බොන්න', self.spoken[0])
        self.assertTrue(self.done(record))
        self.assertEqual(self.clock.tick(at(10, 1)), [])

    def test_a_future_reminder_waits(self):
        record = self.remind('later', at(11))
        self.clock.tick(at(10))
        self.assertEqual(self.spoken, [])
        self.assertFalse(self.done(record))

    def test_a_late_reminder_says_it_is_late(self):
        """Fired after the computer woke up, it must not sound as if it were on time."""
        self.remind('call the bank', at(8))
        self.clock.tick(at(10))
        self.assertIn('තිබුණු', self.spoken[0])

    def test_days_old_reminders_are_summed_up_not_read_out(self):
        """The first live run read three leftover reminders out one after another."""
        old = [self.remind(f'old {n}', at(9, day=12)) for n in range(3)]
        recent = self.remind('recent', at(9, 50))
        self.clock.tick(at(10))
        self.assertEqual(len(self.spoken), 2)
        summary = next(text for text in self.spoken if 'old' in text)
        self.assertIn('තුනක්', summary)
        self.assertTrue(all(self.done(record) for record in old + [recent]))

    def test_a_reminder_set_for_three_in_the_morning_fires_at_three(self):
        self.remind('alarm', at(3))
        self.clock.tick(at(3, 0))
        self.assertEqual(len(self.spoken), 1)

    def test_nothing_is_said_over_the_user(self):
        """Mid-command, the reminder waits -- and is not marked done unheard."""
        record = self.remind('wait', at(9))
        self.busy = True
        self.clock.tick(at(10))
        self.assertEqual(self.spoken, [])
        self.assertFalse(self.done(record))
        self.busy = False
        self.clock.tick(at(10, 0))
        self.assertTrue(self.done(record))


class RoutineTests(StoreCase):
    def test_a_routine_fires_daily_at_its_time(self):
        routines.add_routine(self.store, dt.time(22), 'ෆෝන් එක චාජ් කරන්න', moment=at(12))
        self.clock.tick(at(21, 59))
        self.assertEqual(self.spoken, [])
        self.clock.tick(at(22, 0))
        self.clock.tick(at(22, 1))
        self.assertEqual(len(self.spoken), 1, 'once a day, not once a tick')
        self.clock.tick(at(22, 0, day=16))
        self.assertEqual(len(self.spoken), 2, 'and again the next day')

    def test_a_routine_made_after_its_time_starts_tomorrow(self):
        routines.add_routine(self.store, dt.time(22), 'charge', moment=at(22, 30))
        self.clock.tick(at(22, 31))
        self.assertEqual(self.spoken, [])

    def test_a_stale_routine_is_skipped(self):
        """Ten o'clock's routine is noise at three in the morning."""
        routines.add_routine(self.store, dt.time(22), 'charge', moment=at(12))
        self.clock.tick(at(23, 30))
        self.assertEqual(self.spoken, [])

    def test_removing_a_routine(self):
        routine = routines.add_routine(self.store, dt.time(7), 'x', moment=at(12))
        self.assertTrue(routines.remove_routine(self.store, routine['id']))
        self.assertEqual(routines.routines(self.store), [])


class BriefingTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.store.set_config('briefing_time', '07:30')
        self.store.set_config('briefing_enabled', True)
        self.text = patch.object(routines, 'briefing', return_value='BRIEFING')
        self.text.start()

    def tearDown(self):
        self.text.stop()
        super().tearDown()

    def test_the_briefing_comes_at_its_time_once(self):
        self.clock.tick(at(7, 29))
        self.assertEqual(self.spoken, [])
        self.clock.tick(at(7, 30))
        self.clock.tick(at(7, 45))
        self.assertEqual(self.spoken, ['BRIEFING'])
        self.clock.tick(at(7, 30, day=16))
        self.assertEqual(len(self.spoken), 2)

    def test_a_computer_that_woke_late_still_briefs(self):
        self.clock.tick(at(9, 15))
        self.assertEqual(self.spoken, ['BRIEFING'])

    def test_not_a_morning_briefing_in_the_afternoon(self):
        self.clock.tick(at(14))
        self.assertEqual(self.spoken, [])

    def test_it_can_be_turned_off(self):
        self.store.set_config('briefing_enabled', False)
        self.clock.tick(at(7, 30))
        self.assertEqual(self.spoken, [])

    def test_a_briefing_inside_quiet_hours_is_still_given(self):
        """You chose the time; quiet hours are for JARVIS's own ideas."""
        self.store.set_config('briefing_time', '06:00')
        self.clock.tick(at(6))
        self.assertEqual(self.spoken, ['BRIEFING'])

    def test_reminders_come_before_the_briefing(self):
        self.remind('first', at(7, 30))
        self.clock.tick(at(7, 30))
        self.assertIn('first', self.spoken[0])
        self.assertEqual(self.spoken[1], 'BRIEFING')


class BriefingTextTests(StoreCase):
    def lookups(self, fail=False):
        def broken():
            raise OSError('offline')
        return {name: (broken if fail else (lambda n=name: f'<{n}>'))
                for name in ('weather', 'dollar', 'news')}

    def test_the_parts_in_order(self):
        self.remind('බැංකුවට යන්න', at(15))
        text = routines.briefing(self.store, 'si', at(7, 30), waiting_messages=2,
                                 lookups=self.lookups())
        order = ['සුබ උදෑසනක්', 'වෙලාව', '<weather>', 'බැංකුවට යන්න', 'මැසේජ් දෙකක්',
                 '<dollar>', '<news>']
        positions = [text.find(part) for part in order]
        self.assertNotIn(-1, positions, text)
        self.assertEqual(positions, sorted(positions))

    def test_offline_still_gives_the_time_and_your_day(self):
        self.remind('බැංකුවට යන්න', at(15))
        text = routines.briefing(self.store, 'si', at(7, 30), lookups=self.lookups(fail=True))
        self.assertIn('බැංකුවට යන්න', text)
        self.assertIn(responses.say_offline('si'), text)

    def test_english(self):
        text = routines.briefing(self.store, 'en', at(7, 30), lookups=self.lookups())
        self.assertTrue(text.startswith('Good morning.'))
        self.assertIn('Nothing is scheduled', text)

    def test_only_todays_reminders_are_listed(self):
        self.remind('today', at(15))
        self.remind('tomorrow', at(15, day=16))
        self.assertEqual(routines.todays_reminders(self.store, at(7)), ['today'])


class MachineWatcherTests(StoreCase):
    def battery(self, percent, plugged=False):
        self.machine.battery = SimpleNamespace(percent=percent, power_plugged=plugged)

    def test_a_low_battery_is_mentioned_once(self):
        self.battery(19)
        self.clock.tick(at(10))
        self.clock.tick(at(10, 1))
        self.assertEqual(len(self.spoken), 1)
        self.assertIn('චාජර්', self.spoken[0])

    def test_charging_re_arms_the_warning(self):
        self.battery(19)
        self.clock.tick(at(10))
        self.battery(19, plugged=True)
        self.clock.tick(at(10, 1))
        self.battery(18)
        self.clock.tick(at(10, 2))
        self.assertEqual(len(self.spoken), 2)

    def test_a_plugged_in_low_battery_is_not_news(self):
        self.battery(10, plugged=True)
        self.clock.tick(at(10))
        self.assertEqual(self.spoken, [])

    def test_a_full_disk_once_a_day(self):
        self.machine.disk = 95
        self.clock.tick(at(10))
        self.clock.tick(at(11))
        self.assertEqual(len(self.spoken), 1)
        self.clock.tick(at(10, day=16))
        self.assertEqual(len(self.spoken), 2)

    def test_a_full_disk_found_at_night_is_said_in_the_morning(self):
        """Seen live: held at 1 a.m., marked as warned anyway, and never said at all."""
        self.machine.disk = 95
        for minute in range(0, 60, 15):
            self.clock.tick(at(1, minute))
        self.assertEqual(self.spoken, [])
        self.clock.tick(at(7))
        self.assertEqual(len(self.spoken), 1)
        self.assertIn('තැටිය', self.spoken[0])

    def test_a_low_battery_held_by_the_limit_is_said_later(self):
        self.store.set_config('interruptions_per_hour', 0)
        self.battery(15)
        self.clock.tick(at(10))
        self.assertEqual(self.spoken, [])
        self.store.set_config('interruptions_per_hour', 4)
        self.clock.tick(at(10, 1))
        self.assertEqual(len(self.spoken), 1)

    def test_waiting_is_logged_once(self):
        self.store.set_config('interruptions_per_hour', 0)
        self.battery(15)
        with patch('builtins.print') as printed:
            for minute in range(4):
                self.clock.tick(at(10, minute))
        held = [call for call in printed.call_args_list if 'held' in str(call)]
        self.assertEqual(len(held), 1)

    def test_a_busy_processor_after_five_minutes(self):
        self.machine.cpu = 100
        for minute in range(5):
            self.clock.tick(at(10, minute))
        self.assertEqual(self.spoken, [])
        self.clock.tick(at(10, 5))
        self.assertEqual(len(self.spoken), 1)
        self.clock.tick(at(10, 10))
        self.assertEqual(len(self.spoken), 1, 'not again within the hour')

    def test_a_short_spike_is_ignored(self):
        self.machine.cpu = 100
        self.clock.tick(at(10))
        self.machine.cpu = 10
        self.clock.tick(at(10, 3))
        self.machine.cpu = 100
        self.clock.tick(at(10, 6))
        self.assertEqual(self.spoken, [])

    def test_the_most_important_remark_goes_first(self):
        self.store.set_config('interruptions_per_hour', 1)
        self.battery(15)
        self.machine.disk = 95
        said = [kind for kind, _, ok in self.clock.tick(at(10)) if ok]
        self.assertEqual(said, ['battery'])

    def test_a_broken_sensor_does_not_stop_reminders(self):
        def broken():
            raise OSError('no sensor')
        self.clock.battery = broken
        self.remind('still said', at(9))
        self.clock.tick(at(10))
        self.assertTrue(any('still said' in text for text in self.spoken))


class ClockParsingTests(unittest.TestCase):
    def clock(self, text):
        found = router.extract_clock(text)
        return found[0].strftime('%H:%M') if found else None

    def test_sinhala_times(self):
        for text, expected in [('රෑ දහයට', '22:00'), ('උදේ හතට', '07:00'),
                               ('හවස පහට', '17:00'), ('දවල් දොළහට', '12:00'),
                               ('දවල් එකට', '13:00'), ('උදේ හතහමාරට', '07:30'),
                               ('රෑ දොළහට', '00:00'), ('රෑ එකට', '01:00'),
                               ('ude atata', '08:00'), ('ra dahayata', '22:00')]:
            with self.subTest(text=text):
                self.assertEqual(self.clock(text), expected)

    def test_figures(self):
        for text, expected in [('22:00ට', '22:00'), ('at 7 am', '07:00'),
                               ('at 10 pm', '22:00'), ('7:30 am', '07:30'),
                               ('රෑ 10ට', '22:00'), ('at ten tonight', '22:00')]:
            with self.subTest(text=text):
                self.assertEqual(self.clock(text), expected)

    def test_no_part_of_the_day_is_not_guessed(self):
        """Ten in the morning or ten at night: a guess is twelve hours wrong."""
        self.assertIsNone(self.clock('දහයට'))
        self.assertIsNone(self.clock('at 10'))

    def test_one_is_not_a_time(self):
        """'එක' is also 'the': 'ෆෝන් එක' must not become one o'clock."""
        self.assertIsNone(self.clock('ෆෝන් එක චාජ් කරන්න'))


class RoutingTests(unittest.TestCase):
    def test_every_night_at_ten(self):
        outcome = router.route('හැමදාම රෑ දහයට ෆෝන් එක චාජ් කරන්න මතක් කරන්න')
        self.assertEqual((outcome['action'], outcome['command']), ('command', 'routine'))
        self.assertEqual(outcome['at'], dt.time(22))
        # The task keeps its own verb; only the order is removed.
        self.assertEqual(outcome['content'], 'ෆෝන් එක චාජ් කරන්න')

    def test_english_routine(self):
        outcome = router.route('every night at 10 pm remind me to charge the phone')
        self.assertEqual(outcome['command'], 'routine')
        self.assertEqual(outcome['content'], 'charge the phone')

    def test_a_routine_without_a_part_of_the_day_asks(self):
        outcome = router.route('හැමදාම දහයට ෆෝන් එක චාජ් කරන්න මතක් කරන්න')
        self.assertEqual((outcome['action'], outcome['detail']), ('ask', 'time'))

    def test_a_one_off_reminder_at_a_clock_time(self):
        outcome = router.route('හවස පහට ගෙදර යන්න මතක් කරන්න')
        self.assertEqual((outcome['action'], outcome['command']), ('command', 'reminder'))
        self.assertEqual(outcome['content'], 'ගෙදර යන්න')
        due = dt.datetime.now() + dt.timedelta(seconds=outcome['seconds'] + 5)
        self.assertEqual((due.hour, due.minute), (17, 0))

    def test_a_delay_still_works(self):
        outcome = router.route('මිනිත්තු දහයකින් කෑම ගන්න මතක් කරන්න')
        self.assertEqual(outcome['seconds'], 600)
        self.assertEqual(outcome['content'], 'කෑම ගන්න')

    def test_the_delay_is_not_part_of_the_reminder(self):
        """Seen live: "එකකින් වතුර බොන්න" -- the "in one" left in the reminder."""
        for text, seconds, content in [
                ('මිනිත්තු එකකින් වතුර බොන්න මතක් කරන්න', 60, 'වතුර බොන්න'),
                ('පැය දෙකකින් අම්මාට කෝල් කරන්න මතක් කරන්න', 7200, 'අම්මාට කෝල් කරන්න'),
                ('පැයකින් රෙදි ගන්න මතක් කරන්න', 3600, 'රෙදි ගන්න'),
                ('remind me in ten minutes to check the oven', 600, 'check the oven'),
                # "එක" here is "the", not "one", and stays.
                ('මිනිත්තු දහයකින් ෆෝන් එක චාජ් කරන්න මතක් කරන්න', 600,
                 'ෆෝන් එක චාජ් කරන්න')]:
            with self.subTest(text=text):
                outcome = router.route(text)
                self.assertEqual((outcome['seconds'], outcome['content']), (seconds, content))

    def test_briefing_now_and_briefing_time(self):
        for text in ('අද විස්තර කියන්න', 'සුබ උදෑසනක්', 'good morning'):
            with self.subTest(text=text):
                outcome = router.route(text)
                self.assertEqual((outcome['action'], outcome['command']),
                                 ('command', 'briefing'))
                self.assertIsNone(outcome.get('at'))
        outcome = router.route('උදේ හයහමාරට බ්‍රීෆිං එක දෙන්න')
        self.assertEqual(outcome['command'], 'briefing')
        self.assertEqual(outcome['at'], dt.time(6, 30))


class SpokenTests(unittest.TestCase):
    """Every new template, in both languages, without a digit to be misread."""

    def test_no_digits(self):
        moment = dt.datetime(2026, 10, 15, 22, 0)
        texts = [
            responses.clock_at(moment), responses.say_routine_set('x', dt.time(7, 30)),
            responses.say_routine_set('x', dt.time(22), 'en'),
            responses.say_reminder_at('x', moment), responses.say_reminder_at('x', moment, 'en'),
            responses.say_briefing_time_set(dt.time(6, 30)),
            responses.say_battery_low(17), responses.say_battery_low(17, 'en'),
            responses.say_disk_full(93), responses.say_disk_full(93, 'en'),
            responses.say_messages_waiting(3), responses.say_messages_waiting(3, 'en'),
            responses.say_today(['a', 'b'], ['c']), responses.say_today([], [], 'en'),
        ]
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(re.search(r'\d', text))

    def test_the_time_something_happens_at(self):
        self.assertEqual(responses.clock_at(dt.datetime(2026, 1, 1, 22)), 'රෑ දහයට')
        self.assertEqual(responses.clock_at(dt.datetime(2026, 1, 1, 7, 30)), 'උදේ හතහමාරට')
        self.assertNotIn('යිට', responses.clock_at(dt.datetime(2026, 1, 1, 7, 45)))


if __name__ == '__main__':
    unittest.main()


class BackupTests(unittest.TestCase):
    """Day 28: a copy a day, seven kept, taken safely while the database is in use."""

    def test_seven_days_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'jarvis.db')
            store.add('note', 'keep me')
            for day in range(1, 11):
                routines.backup(store.path, dt.date(2026, 10, day))
            kept = sorted(p.name for p in (Path(folder) / 'backups').glob('*.db'))
            self.assertEqual(len(kept), 7)
            self.assertEqual(kept[0], 'jarvis-2026-10-04.db')
            copy = Store(Path(folder) / 'backups' / kept[-1])
            self.assertEqual(copy.records()[0]['title'], 'keep me')

    def test_once_a_day_from_the_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'jarvis.db')
            store.set_config('briefing_enabled', False)
            clock = routines.Routines(store, lambda text: None, battery=lambda: None,
                                      disk=lambda: 0, cpu=lambda: 0)
            clock.tick(at(10))
            clock.tick(at(11))
            clock.tick(at(10, day=16))
            self.assertEqual(len(list((Path(folder) / 'backups').glob('*.db'))), 2)
