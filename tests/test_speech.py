"""Spoken output: language routing, caching, and behaviour with no internet.

The offline path is the one that matters most here. Sri Lankan connectivity drops, and
Windows has no Sinhala voice at all (this machine offers only Microsoft David and Zira,
both en-US), so Sinhala simply cannot be spoken without the network. What must never
happen is silence or an exception — the reply still has to reach the user somehow.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech
from speech import ENGLISH_VOICE, SINHALA_VOICE, Offline, Speaker, cache_path, is_sinhala, segments


class LanguageRoutingTests(unittest.TestCase):
    def test_detects_sinhala(self):
        self.assertTrue(is_sinhala('ආයුබෝවන් චමින්දු'))
        self.assertTrue(is_sinhala('CPU is at 40% ලෙස තිබේ'))   # mixed, mostly Sinhala
        self.assertFalse(is_sinhala('hello there'))
        self.assertFalse(is_sinhala('CPU 40% RAM 60%'))
        self.assertFalse(is_sinhala(''))

    def test_mixed_reply_splits_by_sentence(self):
        """Sinhala read by an English voice is unintelligible, so the split is per sentence."""
        result = segments('CPU is at 40 percent. මතකය හොඳින් තිබේ.')
        self.assertEqual([voice for voice, _ in result], [ENGLISH_VOICE, SINHALA_VOICE])

    def test_adjacent_sentences_in_one_language_are_merged(self):
        result = segments('One. Two. Three.')
        self.assertEqual(len(result), 1, 'Should not make three separate requests')

    def test_empty_text_produces_nothing(self):
        self.assertEqual(segments('   '), [])
        self.assertEqual(Speaker().say('')['reason'], 'empty')


class CacheTests(unittest.TestCase):
    def test_cache_key_is_stable_and_specific(self):
        first = cache_path('ආයුබෝවන්', SINHALA_VOICE)
        self.assertEqual(first, cache_path('ආයුබෝවන්', SINHALA_VOICE))
        self.assertNotEqual(first, cache_path('ආයුබෝවන්', ENGLISH_VOICE))
        self.assertNotEqual(first, cache_path('වෙනත්', SINHALA_VOICE))

    def test_cached_audio_is_returned_without_a_request(self):
        with patch('speech.cache_path') as path:
            fake = Path(speech.CACHE) / 'test-cached.mp3'
            fake.parent.mkdir(parents=True, exist_ok=True)
            fake.write_bytes(b'cached-audio')
            path.return_value = fake
            try:
                # edge_tts must never be reached when the file already exists.
                with patch('edge_tts.Communicate', side_effect=AssertionError('network used')):
                    self.assertEqual(speech.synthesize('anything', SINHALA_VOICE), b'cached-audio')
            finally:
                fake.unlink(missing_ok=True)


class OfflineTests(unittest.TestCase):
    """No internet: say something rather than nothing, and never raise."""

    def test_offline_falls_back_and_reports_why(self):
        speaker = Speaker()
        with patch('speech.synthesize', side_effect=Offline('no route to host')), \
             patch.object(speaker, 'offline_fallback', return_value={'spoken': True, 'fallback': 'sapi'}) as fallback:
            result = speaker.say('ආයුබෝවන්. Systems ready.')
        self.assertEqual(result['reason'], 'offline')
        self.assertIn('no route to host', result['detail'])
        fallback.assert_called_once()

    def test_offline_never_raises_even_when_sapi_fails(self):
        speaker = Speaker()
        with patch('speech.synthesize', side_effect=Offline('dns failure')), \
             patch('win32com.client.Dispatch', side_effect=OSError('no COM')):
            result = speaker.say('hello')
        self.assertFalse(result['spoken'])
        self.assertEqual(result['reason'], 'offline')
        self.assertIn('text', result)          # caller can still display it

    def test_sapi_is_given_the_english_part_only(self):
        """Windows has no Sinhala voice; feeding it Sinhala produces noise."""
        speaker = Speaker()
        with patch('win32com.client.Dispatch') as dispatch:
            speaker.offline_fallback('Battery is at 80 percent. බැටරිය හොඳයි.')
        spoken = dispatch.return_value.Speak.call_args.args[0]
        self.assertIn('Battery', spoken)
        self.assertNotIn('බැටරිය', spoken)


class InterruptionTests(unittest.TestCase):
    """Day 4's wake word has to cut JARVIS off mid-sentence."""

    def test_stop_during_synthesis_prevents_playback(self):
        """You say the wake word while JARVIS is still fetching the audio.

        With a plain cancel flag this failed, because say() cleared the flag on entry and
        spoke anyway. The request counter makes a stop apply to the request in flight.
        """
        speaker = Speaker()

        def synthesize_then_interrupt(*_args, **_kwargs):
            speaker.stop()          # the wake word fires mid-fetch
            return b'audio'

        with patch('speech.synthesize', side_effect=synthesize_then_interrupt), \
             patch('speech.decode', return_value=[0.0] * 100), \
             patch.object(speaker, 'play', side_effect=AssertionError('should not play')):
            result = speaker.say('ආයුබෝවන්. Second sentence here.')
        self.assertEqual(result['reason'], 'interrupted')

    def test_stop_during_playback_ends_it(self):
        speaker = Speaker()
        with patch('speech.synthesize', return_value=b'x'), \
             patch('speech.decode', return_value=[0.0] * 100), \
             patch.object(speaker, 'play', side_effect=lambda audio, number: False):
            result = speaker.say('ආයුබෝවන්')
        self.assertEqual(result['reason'], 'interrupted')

    def test_a_stop_does_not_mute_the_next_request(self):
        """Cancelling one reply must not silence everything afterwards."""
        speaker = Speaker()
        speaker.stop()
        played = []
        with patch('speech.synthesize', return_value=b'x'), \
             patch('speech.decode', return_value=[0.0] * 10), \
             patch.object(speaker, 'play', side_effect=lambda audio, number: played.append(number) or True):
            result = speaker.say('hello there')
        self.assertEqual(result['reason'], 'ok')
        self.assertEqual(len(played), 1)

    def test_stop_is_safe_when_nothing_is_playing(self):
        Speaker().stop()       # must not raise


if __name__ == '__main__':
    unittest.main()


class EmojiTests(unittest.TestCase):
    """A reply containing an emoji must not be read out as its Unicode name.

    Observed live: JARVIS answered with a smiling face, the endpoint spoke it as "smiling
    face with smiling eyes", the microphone heard that, and it came back as a command.
    """

    def test_emoji_are_removed(self):
        from speech import strip_unspeakable
        self.assertEqual(strip_unspeakable('Systems ready 😊'), 'Systems ready')
        self.assertEqual(strip_unspeakable('Battery at 80% 🔋 all good'),
                         'Battery at 80% all good')
        self.assertEqual(strip_unspeakable('👍'), '')

    def test_sinhala_and_punctuation_survive(self):
        from speech import strip_unspeakable
        self.assertEqual(strip_unspeakable('මතකය හොඳින් 👍'), 'මතකය හොඳින්')
        self.assertEqual(strip_unspeakable('CPU is at 40%. All fine!'), 'CPU is at 40%. All fine!')

    def test_segments_never_contain_emoji(self):
        from speech import segments
        pieces = [piece for _, piece in segments('Done 🎉. හරි 😊.')]
        self.assertTrue(pieces, 'everything was stripped')
        for piece in pieces:
            self.assertNotIn('🎉', piece)
            self.assertNotIn('😊', piece)
