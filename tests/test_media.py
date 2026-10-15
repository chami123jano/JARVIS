"""Music that actually plays (Day 26). Pages are faked and nothing is started here; the
live run, with the sound measured, is recorded in PLAN.md."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media
import responses
import router
from assistant_core import Store

PAGE = ('...{"videoRenderer":{"videoId":"abcDEF12345","thumbnail":{},"title":{"runs":'
        '[{"text":"Man Pathanawa \\u0026 friends | මන් පතනවා #hits"}]}}...'
        '{"videoRenderer":{"videoId":"zzzzzzzzzzz","title":{"runs":[{"text":"second"}]}}')


class UnderstandingTests(unittest.TestCase):
    def test_moods_and_names(self):
        cases = [
            ('mata hitha hodha sindu ekak daanna', 'feel good sinhala songs'),
            ('මට හිත හොඳ සින්දුවක් දාන්න', 'feel good sinhala songs'),
            ('dukha sinduwak danna', 'sad sinhala songs'),
            ('play some music', 'popular sinhala songs'),
            ('play Man Pathanawa by Ashan Fernando', 'man pathanawa by ashan fernando song'),
        ]
        for text, query in cases:
            with self.subTest(text=text):
                self.assertEqual(media.understand(text)['query'], query)

    def test_again(self):
        for text in ('ඊයේ ඇහුව සින්දුව ආයෙත් දාන්න', 'play the song I listened to yesterday'):
            with self.subTest(text=text):
                self.assertEqual(media.understand(text)['kind'], 'again')


class YouTubeTests(unittest.TestCase):
    def test_the_first_result_with_its_title(self):
        with patch.object(media.requests, 'get', return_value=MagicMock(text=PAGE)):
            found = media.youtube_first('man pathanawa')
        self.assertEqual(found['id'], 'abcDEF12345')
        self.assertEqual(found['url'], 'https://www.youtube.com/watch?v=abcDEF12345')
        self.assertEqual(found['title'], 'Man Pathanawa & friends | මන් පතනවා #hits')

    def test_offline_and_empty_are_said(self):
        with patch.object(media.requests, 'get', side_effect=media.requests.ConnectionError()):
            with self.assertRaises(media.MediaError):
                media.youtube_first('x')
        with patch.object(media.requests, 'get', return_value=MagicMock(text='<html></html>')):
            with self.assertRaises(media.MediaError):
                media.youtube_first('x')


class LibraryTests(unittest.TestCase):
    def test_a_local_file_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('Ashan Fernando - Man Pathanawa.mp3', 'Other_Song.m4a', 'notes.txt'):
                (Path(folder) / name).write_bytes(b'')
            found = media.find_local('man pathanawa song', [folder])
            self.assertEqual(found.name, 'Ashan Fernando - Man Pathanawa.mp3')
            self.assertIsNone(media.find_local('completely different', [folder]))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'm.db')

    def tearDown(self):
        self.temp.cleanup()

    def test_yesterdays_song_comes_back(self):
        yesterday = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec='seconds')
        self.store.set_config('media_history', [
            {'title': 'Yesterday song', 'url': 'u1', 'source': 'youtube', 'when': yesterday},
            {'title': 'Today song', 'url': 'u2', 'source': 'youtube',
             'when': dt.datetime.now().isoformat(timespec='seconds')}])
        item = media.choose('ඊයේ ඇහුව සින්දුව ආයෙත් දාන්න', self.store)
        self.assertEqual((item['title'], item['source']), ('Yesterday song', 'history'))
        self.assertIn('ආයෙත්', responses.say_playing(item, 'si'))

    def test_nothing_played_yet_is_said(self):
        with self.assertRaises(media.MediaError):
            media.choose('play the song I listened to yesterday', self.store)

    def test_history_is_kept_short(self):
        for number in range(media.HISTORY_KEPT + 10):
            media.remember_played(self.store, {'title': str(number), 'url': 'u'})
        self.assertEqual(len(media.history(self.store)), media.HISTORY_KEPT)


class ControlTests(unittest.TestCase):
    def test_controls_are_media_keys(self):
        import pc_control
        with patch.object(pc_control, 'media') as key:
            for action, sent in (('pause', 'play_pause'), ('resume', 'play_pause'),
                                 ('next', 'next'), ('previous', 'previous')):
                media.control(action)
                self.assertEqual(key.call_args.args[0], sent)

    def test_playing_reports_who_is_making_sound(self):
        session = MagicMock()
        session.Process.name.return_value = 'chrome.exe'
        session._ctl.QueryInterface.return_value.GetPeakValue.return_value = 0.4
        with patch('pycaw.pycaw.AudioUtilities.GetAllSessions', return_value=[session]):
            self.assertEqual(media.playing(), [('chrome.exe', 0.4)])


class RoutingTests(unittest.TestCase):
    def test_music_is_decided_by_its_words(self):
        """"dukha sinduwak danna" sounded like "previous song"; "mata ..." like a reminder."""
        for text, command in [('mata hitha hodha sindu ekak daanna', 'play_music'),
                              ('dukha sinduwak danna', 'play_music'),
                              ('ඊයේ ඇහුව සින්දුව ආයෙත් දාන්න', 'play_music'),
                              ('සින්දුව නවත්වන්න', 'pause_music'),
                              ('ඊළඟ සින්දුව', 'next_song'), ('කලින් සින්දුව', 'previous_song'),
                              ('resume the music', 'resume_music'), ('නවත්වන්න', 'stop')]:
            with self.subTest(text=text):
                self.assertEqual(router.route(text)['command'], command)

    def test_mata_is_not_mathak(self):
        self.assertFalse(router.has_keyword('mata hitha hodha', 'reminder'))
        self.assertTrue(router.has_keyword('mathak karanna', 'reminder'))

    def test_titles_are_said_cleanly(self):
        self.assertEqual(responses.short_title('Best Songs | Calm Music #slowed'), 'Best Songs')


if __name__ == '__main__':
    unittest.main()
