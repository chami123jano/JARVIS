"""Spoken Sinhala replies, built from tool data instead of asked for from a model.

Each case here is a failure that actually happened. Asked for the battery level, the
model said the tool had not reported one, when it had. Asked the time, it read back
"පස්වරු 14:32" -- the afternoon marker with a 24-hour clock. Asked for system status, it
produced a bulleted list, which is meaningless read aloud.
"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import responses
from assistant_core import Store, Tools

SINHALA = ('඀', '෿')


def sinhala_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if SINHALA[0] <= c <= SINHALA[1]) / len(letters)


class SpeakabilityTests(unittest.TestCase):
    """Everything here is read aloud, so page formatting is a defect."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'r.db')
        self.tools = Tools(self.store, root / 'workspace')

    def tearDown(self):
        self.temp.cleanup()

    def all_replies(self):
        replies = [responses.answer(name, self.tools, self.store)
                   for name in responses.DIRECT]
        replies += [responses.say_reminder_set('කිරි ගන්න', 600),
                    responses.say_note_saved('කිරි ගන්න'),
                    responses.say_not_understood(),
                    responses.say_need_detail('message')]
        return [r for r in replies if r]

    def test_no_markdown_or_list_formatting(self):
        for reply in self.all_replies():
            with self.subTest(reply=reply[:40]):
                for bad in ('*', '- ', '#', '`', '|', '\n-', '1.', 'http'):
                    self.assertNotIn(bad, reply, f'{bad!r} cannot be spoken')

    def test_replies_are_short(self):
        for reply in self.all_replies():
            with self.subTest(reply=reply[:40]):
                self.assertLess(len(reply), 220, 'too long to listen to')

    def test_no_digits_anywhere_in_a_spoken_reply(self):
        """A voice reads digits as digits.

        "හවස 2.34" was spoken as "two point three four", a decimal number rather than a
        time, and "සියයට 100" as a numeral. Every number a reply contains has to be a
        Sinhala word before it reaches the voice.
        """
        for reply in self.all_replies():
            with self.subTest(reply=reply[:40]):
                digits = [c for c in reply if c.isdigit()]
                self.assertEqual(digits, [], f'{digits} would be read as numerals')

    def test_replies_are_in_sinhala(self):
        for reply in self.all_replies():
            with self.subTest(reply=reply[:40]):
                self.assertGreater(sinhala_ratio(reply), .5, 'not mostly Sinhala')


class NumberWordTests(unittest.TestCase):
    def test_units_tens_and_compounds(self):
        cases = {1: 'එක', 9: 'නමය', 12: 'දොළහ', 15: 'පහළොව', 20: 'විස්ස',
                 25: 'විසිපහ', 30: 'තිහ', 34: 'තිස්හතර', 45: 'හතළිස්පහ',
                 62: 'හැටදෙක', 79: 'හැත්තෑනමය', 82: 'අසූදෙක', 100: 'සියය'}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(responses.number_word(value), expected)

    def test_floats_are_rounded_not_spelled_out(self):
        self.assertEqual(responses.number_word(45.4), 'හතළිස්පහ')
        self.assertEqual(responses.number_word(78.9), 'හැත්තෑනමය')

    def test_percent_has_no_digits(self):
        for value in (0, 7, 50, 82, 99, 100):
            with self.subTest(value=value):
                self.assertFalse(any(c.isdigit() for c in responses.percent(value)))


class TimeTests(unittest.TestCase):
    def test_time_is_words_not_digits(self):
        """Observed: "හවස 2.34" spoken as a decimal, and "පස්වරු 14:32"."""
        spoken = responses.clock(dt.datetime(2026, 9, 28, 14, 34))
        self.assertEqual(spoken, 'හවස දෙකයි තිස්හතරයි')
        self.assertFalse(any(c.isdigit() for c in spoken))

    def test_half_and_quarter_use_the_spoken_forms(self):
        self.assertEqual(responses.clock(dt.datetime(2026, 9, 28, 9, 30)), 'උදේ නමයහමාරයි')
        self.assertEqual(responses.clock(dt.datetime(2026, 9, 28, 14, 15)), 'හවස දෙකයි කාලයි')

    def test_on_the_hour_says_only_the_hour(self):
        self.assertEqual(responses.clock(dt.datetime(2026, 9, 28, 12, 0)), 'දවල් දොළහයි')

    def test_each_part_of_the_day(self):
        cases = [(3, 'අලුයම'), (9, 'උදේ'), (13, 'දවල්'), (16, 'හවස'), (21, 'රෑ')]
        for hour, expected in cases:
            with self.subTest(hour=hour):
                self.assertTrue(responses.clock(dt.datetime(2026, 9, 28, hour, 5)).startswith(expected))

    def test_midnight_and_noon_read_as_twelve(self):
        for hour in (0, 12):
            with self.subTest(hour=hour):
                self.assertIn('දොළහ', responses.clock(dt.datetime(2026, 9, 28, hour, 5)))


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'r.db')
        self.tools = Tools(self.store, root / 'workspace')

    def tearDown(self):
        self.temp.cleanup()

    def test_battery_reports_the_number_it_was_given(self):
        """The model claimed no battery reading existed when one did."""
        self.assertIn('අසූදෙක', responses.say_battery({'battery': 82}))
        self.assertIn('නැහැ', responses.say_battery({'battery': None}))

    def test_low_battery_says_so(self):
        self.assertIn('චාජ්', responses.say_battery({'battery': 12}))
        self.assertNotIn('චාජ්', responses.say_battery({'battery': 90}))

    def test_system_status_uses_the_real_figures(self):
        reply = responses.say_system({'cpu': 45.4, 'memory': 62.1, 'disk': 78.9,
                                      'battery': 82, 'memory_gb': 15.6})
        for word in ('හතළිස්පහ', 'හැටදෙක', 'හැත්තෑනමය', 'අසූදෙක'):
            self.assertIn(word, reply)

    def test_notes_counts_match(self):
        self.assertIn('නැහැ', responses.say_notes([]))
        one = [{'kind': 'note', 'title': 'කිරි ගන්න', 'done': 0}]
        self.assertIn('කිරි ගන්න', responses.say_notes(one))
        many = [{'kind': 'note', 'title': f'n{i}', 'done': 0} for i in range(7)]
        self.assertIn('හත', responses.say_notes(many))

    def test_completed_notes_are_not_read_out(self):
        records = [{'kind': 'note', 'title': 'done one', 'done': 1},
                   {'kind': 'note', 'title': 'කිරි ගන්න', 'done': 0}]
        reply = responses.say_notes(records)
        self.assertNotIn('done one', reply)

    def test_reminder_says_the_delay_in_words(self):
        self.assertIn('දහය', responses.say_reminder_set('', 600))
        self.assertIn('දෙක', responses.say_reminder_set('', 7200))
        self.assertFalse(any(c.isdigit() for c in responses.say_reminder_set('', 600)))

    def test_full_battery_avoids_the_clumsy_hundred_percent(self):
        """"සියයට සියයයි" is awkward aloud."""
        self.assertNotIn('සියයට', responses.say_battery({'battery': 100}))

    def test_unknown_command_is_not_answered_directly(self):
        self.assertIsNone(responses.answer('weather', self.tools, self.store))


if __name__ == '__main__':
    unittest.main()
