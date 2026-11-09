"""Stopping songs and closing their tabs (8 Nov conversation).

"stop all songs" played a Tamil nonstop, "i said stop all song curently palying" played
"Nothing's Gonna Stop Us Now", and "stop all musics" and "close the all youtube tabs" went to
the model, which said it had no tool for either. Each "play" had opened a new tab, so two
songs played at once and the pause key only reached one of them.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import media  # noqa: E402
import responses  # noqa: E402
import router  # noqa: E402


class FakeStore:
    def __init__(self, titles=()):
        self.values = {'media_history': [{'title': t, 'source': 'youtube'} for t in titles]}

    def config(self, key, default=None):
        return self.values.get(key, default)

    def set_config(self, key, value):
        self.values[key] = value


class FakeTab(media.Tab):
    def __init__(self, name):
        super().__init__(None, name)
        self.closed = False

    def close(self):
        self.closed = True
        return True


class RoutingTests(unittest.TestCase):
    def command(self, text):
        decision = router.route(text)
        self.assertEqual(decision['action'], 'command', text)
        return decision['command']

    def test_what_was_said_on_8_november(self):
        for text in ('stop the api aye hamu nowena song', 'stop all songs',
                     'i said stop all song curently palying', 'stop all musics',
                     'pause the music', 'සින්දුව නවත්වන්න', 'සින්දු ඔක්කොම නවත්වන්න',
                     'stop youtube'):
            self.assertEqual(self.command(text), 'pause_music', text)
        for text in ('close the all youtube tabs', 'youtube tabs වහන්න', 'close the tabs'):
            self.assertEqual(self.command(text), 'close_tabs', text)

    def test_playing_still_plays(self):
        for text in ('play a music', 'play another song', 'play another sinhala song',
                     "play nothing's gonna stop us now"):
            self.assertEqual(self.command(text), 'play_music', text)

    def test_neither_takes_what_is_not_theirs(self):
        self.assertEqual(self.command('open youtube'), 'open_app')
        self.assertEqual(self.command('stop talking'), 'stop')
        self.assertEqual(self.command('next song'), 'next_song')
        self.assertEqual(self.command('resume the music'), 'resume_music')


class TabTests(unittest.TestCase):
    STORE = FakeStore(['Api Aye Hamunowena - අපි ආයේ හමු නොවෙනා'])

    def test_a_song_jarvis_played_is_known_by_its_tab(self):
        mine = FakeTab('(63) Api Aye Hamunowena - අපි ආයේ හමු නොවෙනා | Aditya - YouTube - '
                       'Memory usage - 307 MB')
        yours = FakeTab('(63) Python Full Course for Beginners - YouTube')
        console = FakeTab('JARVIS / Personal Intelligence - Memory usage - 220 MB')
        self.assertTrue(media.jarvis_played(mine, self.STORE))
        self.assertFalse(media.jarvis_played(yours, self.STORE))
        self.assertFalse(media.jarvis_played(console, self.STORE))
        self.assertTrue(media.is_youtube(yours))
        self.assertFalse(media.is_youtube(console))

    def test_closing_takes_only_what_was_asked(self):
        def tabs():
            return [FakeTab('Api Aye Hamunowena - අපි ආයේ හමු නොවෙනා - YouTube'),
                    FakeTab('Python Full Course - YouTube'),
                    FakeTab('JARVIS / Personal Intelligence')]
        with patch.object(media, 'browser_tabs', side_effect=tabs):
            self.assertEqual(len(media.close_tabs(self.STORE)), 1)                 # songs only
            self.assertEqual(len(media.close_tabs(self.STORE, True)), 2)           # all YouTube

    def test_a_short_or_local_title_matches_nothing(self):
        store = FakeStore(['song'])
        self.assertFalse(media.jarvis_played(FakeTab('My song - YouTube'), store))


class StopTests(unittest.TestCase):
    def test_paused_and_quiet(self):
        with patch.object(media, 'pause_everything', return_value=2), \
             patch.object(media, 'browser_sounding', return_value=False), \
             patch.object(media, 'close_tabs', side_effect=AssertionError('closed unasked')):
            done = media.stop_everything(FakeStore(), settle=0)
        self.assertEqual(done['paused'], 2)
        self.assertTrue(done['quiet'])
        self.assertEqual(responses.say_music_stopped(done, 'en'), 'Stopped.')

    def test_a_second_song_still_sounding_has_its_tab_closed(self):
        sounds = iter([True, False])
        with patch.object(media, 'pause_everything', return_value=1), \
             patch.object(media, 'browser_sounding', side_effect=lambda: next(sounds)), \
             patch.object(media, 'close_tabs', return_value=['song - youtube']):
            done = media.stop_everything(FakeStore(), settle=0)
        self.assertEqual(done['closed'], ['song - youtube'])
        self.assertTrue(done['quiet'])

    def test_never_claims_silence_it_did_not_get(self):
        with patch.object(media, 'pause_everything', return_value=0), \
             patch.object(media, 'browser_sounding', return_value=True), \
             patch.object(media, 'close_tabs', return_value=[]):
            done = media.stop_everything(FakeStore(), settle=0)
        self.assertFalse(done['quiet'])
        self.assertIn('still playing', responses.say_music_stopped(done, 'en'))

    def test_nothing_playing_is_said_so(self):
        done = {'paused': 0, 'closed': [], 'muted': [], 'quiet': True}
        self.assertEqual(responses.say_music_stopped(done, 'en'), 'Nothing is playing.')
        self.assertIn('වැහුවා', responses.say_tabs_closed(2, True, 'si'))
        self.assertIn('no YouTube tabs', responses.say_tabs_closed(0, True, 'en'))

    def test_a_new_song_makes_room_first(self):
        calls = []
        with patch.object(media, 'pause_everything', side_effect=lambda: calls.append('pause')), \
             patch.object(media, 'close_tabs', side_effect=lambda store: calls.append('close') or []):
            media.make_room(FakeStore())
        self.assertEqual(calls, ['pause', 'close'])


if __name__ == '__main__':
    unittest.main()
