"""Replies follow the language they were asked in.

Answering an English question in Sinhala is technically correct and useless. Before this,
every templated reply was Sinhala regardless: "what time is it" was answered
"දැන් වෙලාව හවස පහයි".
"""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import responses
from assistant_core import Store, Tools
from router import route


class LanguageDetectionTests(unittest.TestCase):
    def test_script_decides(self):
        self.assertEqual(responses.language_of('දැන් වෙලාව කීයද'), 'si')
        self.assertEqual(responses.language_of('what time is it'), 'en')

    def test_code_mixing_counts_as_sinhala(self):
        """"volume එක වැඩි කරන්න" is Sinhala with an English noun in it."""
        self.assertEqual(responses.language_of('volume එක වැඩි කරන්න'), 'si')
        self.assertEqual(responses.language_of('system එකේ තත්ත්වය කියන්න'), 'si')

    def test_empty_defaults_to_sinhala(self):
        self.assertEqual(responses.language_of(''), 'si')
        self.assertEqual(responses.language_of('12345'), 'si')

    def test_the_router_reports_the_language(self):
        self.assertEqual(route('what time is it')['language'], 'en')
        self.assertEqual(route('දැන් වෙලාව කීයද')['language'], 'si')


class BilingualReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'b.db')
        self.tools = Tools(self.store, root / 'workspace')

    def tearDown(self):
        self.temp.cleanup()

    def sinhala_ratio(self, text):
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return 0.0
        return sum(1 for c in letters if '඀' <= c <= '෿') / len(letters)

    def test_english_asks_get_english_answers(self):
        for command in responses.DIRECT:
            with self.subTest(command=command):
                reply = responses.answer(command, self.tools, self.store, 'en')
                self.assertLess(self.sinhala_ratio(reply), .2,
                                f'{command} answered in Sinhala: {reply[:50]}')

    def test_sinhala_asks_get_sinhala_answers(self):
        for command in responses.DIRECT:
            with self.subTest(command=command):
                reply = responses.answer(command, self.tools, self.store, 'si')
                self.assertGreater(self.sinhala_ratio(reply), .5,
                                   f'{command} answered in English: {reply[:50]}')

    def test_neither_language_speaks_digits(self):
        for lang in ('si', 'en'):
            replies = [responses.answer(c, self.tools, self.store, lang)
                       for c in responses.DIRECT]
            replies.append(responses.say_reminder_set('milk', 600, lang))
            for reply in replies:
                with self.subTest(lang=lang, reply=reply[:36]):
                    self.assertFalse(any(c.isdigit() for c in reply))

    def test_english_clock_is_words(self):
        spoken = responses.english_clock(dt.datetime(2026, 9, 28, 16, 57))
        self.assertEqual(spoken, 'four fifty-seven in the afternoon')
        self.assertFalse(any(c.isdigit() for c in spoken))

    def test_english_half_and_quarter(self):
        self.assertIn('half past nine',
                      responses.english_clock(dt.datetime(2026, 9, 28, 9, 30)))
        self.assertIn('quarter to nine',
                      responses.english_clock(dt.datetime(2026, 9, 28, 8, 45)))

    def test_a_full_battery_is_never_a_hundred_percent(self):
        """It read "සියයට සියයයි" inside system status while reading correctly alone."""
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                reply = responses.say_system({'cpu': 10, 'memory': 20, 'disk': 30,
                                              'battery': 100}, lang)
                self.assertNotIn('සියයට සියය', reply)
                self.assertNotIn('a hundred percent', reply)


class MoneyTests(unittest.TestCase):
    def test_rupees_are_spoken_not_abbreviated(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                self.assertNotIn('Rs', responses.money(2500, lang))
        self.assertIn('රුපියල්', responses.money(80, 'si'))
        self.assertIn('rupees', responses.money(80, 'en'))

    def test_small_amounts_are_words(self):
        self.assertEqual(responses.money(80, 'en'), 'eighty rupees')
        self.assertFalse(any(c.isdigit() for c in responses.money(80, 'si')))


class TranslateToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'b.db')
        self.tools = Tools(self.store, root / 'workspace')

    def tearDown(self):
        self.temp.cleanup()

    def test_it_is_offered_as_a_tool(self):
        from assistant_core import schemas
        self.assertIn('translate', [s['function']['name'] for s in schemas()])

    def test_direction_must_be_sinhala_or_english(self):
        self.tools.execute('translate', {'text': 'hello', 'to': 'si'})
        for bad in ('fr', 'tamil', ''):
            with self.subTest(to=bad):
                with self.assertRaises(ValueError):
                    self.tools.execute('translate', {'text': 'hello', 'to': bad})

    def test_empty_text_is_refused(self):
        with self.assertRaises(ValueError):
            self.tools.execute('translate', {'text': '   ', 'to': 'en'})

    def test_names_are_to_be_left_alone(self):
        result = self.tools.execute('translate', {'text': 'අම්මා', 'to': 'en'})
        self.assertIn('names', result['note'].lower())


if __name__ == '__main__':
    unittest.main()
