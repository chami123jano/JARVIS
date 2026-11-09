"""English said aloud is heard (9 Nov).

Every spoken command that evening -- "play a music", "play music", "play a song", "how are
you doing" -- came back from Google's si-LK recogniser empty, or as "අවවායෙනි", and was lost.
en-US heard all of them. Both are now asked at once and the better one kept; the Sinhala
pairs below are what the two recognisers really returned for the Day 1 recordings.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live  # noqa: E402
import voice_loop  # noqa: E402

choose = voice_loop.choose_transcript


class ChoiceTests(unittest.TestCase):
    def test_english_said_in_english(self):
        self.assertEqual(choose('', 'play a music'), ('play a music', 'en-US'))
        self.assertEqual(choose('අවවායෙනි', 'how are you doing'), ('how are you doing', 'en-US'))

    def test_sinhala_said_in_sinhala(self):
        for sinhala, english in [
                ('අද ඩොලර් රේට් එක කීයද', 'at the dollar rate take'),
                ('සින්දුවක් ප්ලේ කරන්න', 'tenduaa play Kara'),
                ('අද පුවත් මොනවද', 'other what'),
                ('දැන් වෙලාව කීයද', 'Dan Velu Kiyada'),
                ('බැටරිය කීයද', 'battery a key other')]:
            self.assertEqual(choose(sinhala, english), (sinhala, 'si-LK'), sinhala)
        self.assertEqual(choose('දැන් වෙලාව කීයද', ''), ('දැන් වෙලාව කීයද', 'si-LK'))

    def test_nothing_heard(self):
        self.assertEqual(choose('', ''), ('', 'si-LK'))


class EarsTests(unittest.TestCase):
    def ears(self, heard):
        ears = voice_loop.GoogleEars('si-LK')

        def recognise(data, language):
            answer = heard[language]
            if isinstance(answer, Exception):
                raise answer
            return answer
        ears.recognise = recognise
        return ears

    def test_both_languages_are_asked(self):
        import numpy
        text, _, info = self.ears({'si-LK': '', 'en-US': 'play a song'}).transcribe(
            numpy.zeros(16000, dtype='float32'))
        self.assertEqual((text, info.language), ('play a song', 'en'))

    def test_one_language_down_is_not_both(self):
        import numpy
        text, _, info = self.ears({'si-LK': 'බැටරිය කීයද', 'en-US': OSError('timeout')}) \
            .transcribe(numpy.zeros(16000, dtype='float32'))
        self.assertEqual((text, info.language), ('බැටරිය කීයද', 'si'))

    def test_both_down_is_a_failure_for_whisper_to_take(self):
        import numpy
        with self.assertRaises(OSError):
            self.ears({'si-LK': OSError('offline'), 'en-US': OSError('offline')}) \
                .transcribe(numpy.zeros(16000, dtype='float32'))


class LogTests(unittest.TestCase):
    def test_a_console_that_stopped_waiting_is_not_an_error(self):
        server = live.ExclusiveServer.__new__(live.ExclusiveServer)
        with patch('socketserver.BaseServer.handle_error') as reported:
            try:
                raise ConnectionAbortedError(10053, 'aborted')
            except ConnectionAbortedError:
                server.handle_error(None, ('127.0.0.1', 1))
            self.assertFalse(reported.called)
            try:
                raise ValueError('a real error')
            except ValueError:
                server.handle_error(None, ('127.0.0.1', 1))
            self.assertTrue(reported.called)


if __name__ == '__main__':
    unittest.main()
