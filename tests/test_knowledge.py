"""Live facts: weather, the dollar rate, the news.

Nothing here touches the network. The live sources are exercised by verify.py; these
tests cover what happens around them, which is where the failures that matter live --
a hang when the connection drops, a number altered on the way to being spoken, a cache
that serves yesterday's rate.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge
import responses

WEATHER = {'place': 'colombo', 'temperature': 28, 'humidity': 81,
           'condition_en': 'cloudy', 'condition_si': 'වළාකුළු',
           'high': 30, 'low': 26, 'rain_chance': 88, 'source': 'open-meteo.com'}
RATE = {'rate': 330.65, 'date': '2026-09-29', 'source': 'exchangerate-api.com'}
NEWS = [{'title': 'Warm weather advisory issued', 'source': 'Ada Derana'},
        {'title': 'Fuel prices revised', 'source': 'Daily Mirror'}]


class PlaceTests(unittest.TestCase):
    def test_towns_are_recognised_in_either_script(self):
        self.assertEqual(knowledge.find_place('මහනුවර කාලගුණය කොහොමද'), 'kandy')
        self.assertEqual(knowledge.find_place('whats the weather in galle'), 'galle')
        self.assertEqual(knowledge.find_place('කොළඹ කාලගුණය'), 'colombo')

    def test_an_unnamed_place_is_colombo(self):
        self.assertEqual(knowledge.find_place('අද කාලගුණය කොහොමද'), 'colombo')
        self.assertEqual(knowledge.find_place(''), 'colombo')


class OfflineTests(unittest.TestCase):
    """The connection here drops. Every path must say so rather than hang."""

    def test_a_network_failure_becomes_offline(self):
        import requests
        with patch('requests.get', side_effect=requests.ConnectionError('no route')):
            with self.assertRaises(knowledge.Offline):
                knowledge.fetch('https://example.invalid')

    def test_nonsense_json_becomes_offline_not_a_crash(self):
        class Bad:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): raise ValueError('not json')
        with patch('requests.get', return_value=Bad()):
            with self.assertRaises(knowledge.Offline):
                knowledge.fetch('https://example.invalid')

    def test_requests_are_bounded(self):
        """A slow site must not freeze JARVIS."""
        captured = {}

        def record(url, params=None, timeout=None, headers=None):
            captured['timeout'] = timeout
            raise __import__('requests').ConnectionError('stop')
        with patch('requests.get', side_effect=record):
            with self.assertRaises(knowledge.Offline):
                knowledge.fetch('https://example.invalid')
        self.assertIsNotNone(captured['timeout'])
        self.assertLessEqual(captured['timeout'], 15)

    def test_a_non_web_address_is_refused(self):
        for bad in ('file:///C:/Windows/System32/config', 'ftp://x', 'not a url'):
            with self.subTest(bad=bad):
                with self.assertRaises(knowledge.Offline):
                    knowledge.read_page(bad)


class CacheTests(unittest.TestCase):
    def setUp(self):
        knowledge._cache.clear()

    def test_a_repeat_question_does_not_refetch(self):
        calls = []

        def build():
            calls.append(1)
            return {'rate': 330.0}
        for _ in range(3):
            knowledge.cached('rate', 'usd-lkr', build)
        self.assertEqual(len(calls), 1)

    def test_the_cache_expires(self):
        calls = []

        def build():
            calls.append(1)
            return {'rate': 330.0}
        knowledge.cached('rate', 'k', build)
        key = ('rate', 'k')
        stamp, value = knowledge._cache[key]
        knowledge._cache[key] = (stamp - knowledge.CACHE_SECONDS['rate'] - 1, value)
        knowledge.cached('rate', 'k', build)
        self.assertEqual(len(calls), 2)


class SpokenTests(unittest.TestCase):
    """The number said aloud must be the number that came back."""

    def test_the_temperature_is_not_altered(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                spoken = responses.say_weather(WEATHER, lang)
                self.assertIn('twenty-eight' if lang == 'en' else 'විසිඅට', spoken)

    def test_a_likely_downpour_is_mentioned(self):
        self.assertIn('88' if False else 'අසූඅට', responses.say_weather(WEATHER, 'si'))
        dry = {**WEATHER, 'rain_chance': 10}
        self.assertNotIn('ඉඩක්', responses.say_weather(dry, 'si'))

    def test_english_article_agrees(self):
        """"a eighty-eight percent chance" is wrong."""
        spoken = responses.say_weather(WEATHER, 'en')
        self.assertIn('an eighty-eight', spoken)
        self.assertNotIn('a eighty', spoken)

    def test_the_rate_is_reported_exactly(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                self.assertIn('330.65', responses.say_dollar(RATE, lang))

    def test_weather_and_news_carry_no_page_formatting(self):
        for spoken in (responses.say_weather(WEATHER, 'si'),
                       responses.say_news(NEWS, 'si'),
                       responses.say_dollar(RATE, 'si')):
            with self.subTest(spoken=spoken[:30]):
                for bad in ('*', '- ', '#', 'http', '|'):
                    self.assertNotIn(bad, spoken)

    def test_no_news_says_so(self):
        self.assertIn('නැහැ', responses.say_news([], 'si'))
        self.assertIn('No news', responses.say_news([], 'en'))

    def test_offline_is_stated_in_both_languages(self):
        self.assertIn('අන්තර්ජාල', responses.say_offline('si'))
        self.assertIn('internet', responses.say_offline('en'))


if __name__ == '__main__':
    unittest.main()
