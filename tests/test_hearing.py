"""Choosing a speech recogniser, and surviving the loss of the better one.

Google's si-LK model is far better at Sinhala than Whisper -- measured on the same 20
recordings, 0.940 against 0.758, and it returns Sinhala script rather than Tamil or
German transliteration. It needs the internet, which here is not dependable, so the
fallback has to be automatic and silent.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy
import voice_loop


def hearing(engine='auto', google=None, whisper=None):
    """Build a Hearing without touching the network or loading a model."""
    ears = voice_loop.Hearing.__new__(voice_loop.Hearing)
    ears.engine = engine
    ears.model_name = voice_loop.WHISPER_MODEL
    ears.whisper_device = 'auto'
    ears.language = 'si-LK'
    ears.google = google
    ears.whisper = whisper
    ears.device = engine
    ears.failures = 0
    return ears


def transcriber(text, seconds=1.0):
    stub = MagicMock()
    info = type('Info', (), {'language': 'si', 'language_probability': 1.0})()
    stub.transcribe.return_value = (text, seconds, info)
    return stub


AUDIO = numpy.zeros(voice_loop.SAMPLE_RATE * 3, dtype='float32')


class EngineChoiceTests(unittest.TestCase):
    def test_google_is_used_when_it_answers(self):
        google, whisper = transcriber('දැන් වෙලාව කීයද'), transcriber('wrong')
        ears = hearing(google=google, whisper=whisper)
        text, _, _ = ears.transcribe(AUDIO)
        self.assertEqual(text, 'දැන් වෙලාව කීයද')
        whisper.transcribe.assert_not_called()

    def test_whisper_takes_over_when_google_fails(self):
        google = MagicMock()
        google.transcribe.side_effect = OSError('no internet')
        whisper = transcriber('fallback text')
        ears = hearing(google=google, whisper=whisper)
        text, _, _ = ears.transcribe(AUDIO)
        self.assertEqual(text, 'fallback text')

    def test_repeated_failures_stop_retrying_google(self):
        """Three round trips to a dead endpoint per command is three wasted waits."""
        google = MagicMock()
        google.transcribe.side_effect = OSError('no internet')
        ears = hearing(google=google, whisper=transcriber('local'))
        for _ in range(3):
            ears.transcribe(AUDIO)
        self.assertEqual(ears.engine, 'whisper')
        calls_before = google.transcribe.call_count
        ears.transcribe(AUDIO)
        self.assertEqual(google.transcribe.call_count, calls_before,
                         'google was retried after being given up on')

    def test_empty_google_result_falls_through_for_real_audio(self):
        """Google returning nothing for three seconds of audio means it failed, not silence."""
        ears = hearing(google=transcriber(''), whisper=transcriber('whisper heard this'))
        text, _, _ = ears.transcribe(AUDIO)
        self.assertEqual(text, 'whisper heard this')

    def test_short_silence_is_accepted_as_empty(self):
        short = numpy.zeros(int(voice_loop.SAMPLE_RATE * .5), dtype='float32')
        whisper = transcriber('should not run')
        ears = hearing(google=transcriber(''), whisper=whisper)
        text, _, _ = ears.transcribe(short)
        self.assertEqual(text, '')
        whisper.transcribe.assert_not_called()

    def test_forcing_whisper_skips_google_entirely(self):
        google = transcriber('google text')
        ears = hearing(engine='whisper', google=google, whisper=transcriber('whisper text'))
        text, _, _ = ears.transcribe(AUDIO)
        self.assertEqual(text, 'whisper text')
        google.transcribe.assert_not_called()


class LazyLoadingTests(unittest.TestCase):
    def test_whisper_is_not_loaded_while_google_works(self):
        """Loading Whisper costs twelve seconds and 1.5 GB of VRAM for nothing."""
        ears = hearing(google=transcriber('fine'))
        ears.transcribe(AUDIO)
        self.assertIsNone(ears.whisper)


if __name__ == '__main__':
    unittest.main()
