"""No internet (Day 28): every feature that needs it says so, quickly, and nothing else
breaks. The plug is pulled in software -- see offline.py -- so this runs anywhere.

The full voice brain was also driven through twelve features unplugged, against the real
models; that run is recorded in PLAN.md, Day 28.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge
import responses
import routines
import speech
import tasks
from assistant_core import Store, Tools
from offline import unplugged


class UnpluggedCase(unittest.TestCase):
    def setUp(self):
        knowledge._cache.clear()
        self.started = time.monotonic()

    def quick(self, seconds=5):
        self.assertLess(time.monotonic() - self.started, seconds, 'failing must be fast')


class LiveFactsTests(UnpluggedCase):
    def test_each_lookup_says_it_is_offline(self):
        with unplugged() as attempts:
            for lookup in (knowledge.weather, knowledge.dollar_rate, knowledge.news):
                with self.subTest(lookup=lookup.__name__):
                    with self.assertRaises(knowledge.Offline):
                        lookup()
        self.assertTrue(attempts, 'the block was actually exercised')
        self.quick()

    def test_the_spoken_answer_is_plain(self):
        self.assertEqual(responses.say_offline('si'), 'දැන් අන්තර්ජාලයට සම්බන්ධ වෙන්න බැහැ.')


class BriefingTests(UnpluggedCase):
    def test_the_briefing_keeps_what_is_local(self):
        with tempfile.TemporaryDirectory() as folder, unplugged():
            store = Store(Path(folder) / 'b.db')
            # A fixed morning, not the clock: run after 23:30 this once failed, the
            # reminder having already passed.
            import datetime as dt
            morning = dt.datetime.now().astimezone().replace(hour=8, minute=0)
            store.add('reminder', 'බැංකුවට යන්න', due=morning.replace(hour=15).isoformat())
            text = routines.briefing(store, 'si', moment=morning)
        self.assertIn('වෙලාව', text)
        self.assertIn('බැංකුවට යන්න', text)
        self.assertIn(responses.say_offline('si'), text)
        self.quick(15)


class SpeechTests(UnpluggedCase):
    def test_the_sinhala_voice_reports_offline(self):
        with unplugged(), patch.object(speech, 'cache_path',
                                       return_value=Path(tempfile.gettempdir()) / 'never.mp3'):
            with self.assertRaises(speech.Offline):
                speech.synthesize('පරීක්ෂණයක්', 'si-LK-SameeraNeural', timeout=8)
        self.quick(10)

    def test_the_speaker_falls_back_rather_than_failing(self):
        speaker = speech.Speaker()
        with patch.object(speech, 'synthesize', side_effect=speech.Offline('no network')), \
                patch.object(speaker, 'offline_fallback', return_value={'spoken': True}) as fallback:
            result = speaker.say('හලෝ')
        fallback.assert_called_once()
        self.assertEqual(result['reason'], 'offline')

    def test_hearing_falls_back_to_whisper(self):
        import voice_loop
        hearing = voice_loop.Hearing.__new__(voice_loop.Hearing)
        hearing.engine, hearing.failures, hearing.language = 'auto', 0, 'si-LK'
        hearing.google = MagicMock()
        hearing.google.transcribe.side_effect = OSError('no network')
        hearing.whisper = MagicMock()
        hearing.whisper.transcribe.return_value = ('හලෝ', 1.0, None)
        hearing.load_whisper = lambda: hearing.whisper
        import numpy
        text, *_ = hearing.transcribe(numpy.zeros(32000, dtype='float32'), 'si')
        self.assertEqual(text, 'හලෝ')


class TaskTests(UnpluggedCase):
    def test_a_task_step_says_no_internet_in_words(self):
        """Measured: the question read out the raw HTTPSConnectionPool error."""
        with tempfile.TemporaryDirectory() as folder, unplugged():
            store = Store(Path(folder) / 't.db')
            steps = tasks.Steps(store, Tools(store, Path(folder) / 'w'))
            with self.assertRaises(tasks.StepError) as caught:
                steps.weather()
        self.assertEqual(str(caught.exception), tasks.NO_INTERNET)
        self.assertTrue(caught.exception.retryable)


class LocalStillWorksTests(UnpluggedCase):
    def test_local_answers_do_not_need_the_internet(self):
        with tempfile.TemporaryDirectory() as folder, unplugged() as attempts:
            store = Store(Path(folder) / 'l.db')
            tools = Tools(store, Path(folder) / 'w')
            responses.answer('time', tools, store, 'si')
            responses.answer('battery', tools, store, 'si')
            tools.execute('save_note', {'title': 'offline note', 'content': ''})
        self.assertEqual(attempts, [], 'nothing local reached for the network')


if __name__ == '__main__':
    unittest.main()


class OfflineSinhalaVoiceTests(UnpluggedCase):
    """Day 29: unplugged, Sinhala is still spoken -- by JARVIS's own offline voice."""

    def test_sinhala_goes_to_the_offline_voice_and_english_to_windows(self):
        import numpy
        import offline_voice
        speaker = speech.Speaker()
        played, said = [], []
        sapi = MagicMock()
        sapi.Speak.side_effect = lambda text, flags: said.append(text)
        with patch.object(speech, 'synthesize', side_effect=speech.Offline('down')), \
                patch.object(offline_voice, 'available', return_value=True), \
                patch.object(offline_voice, 'synthesize',
                             side_effect=lambda text, rate: played.append(text) or numpy.zeros(10)), \
                patch.object(speaker, 'play', return_value=True), \
                patch('win32com.client.Dispatch', return_value=sapi):
            result = speaker.say('හරි, දානවා. Best Sinhala Songs.')
        self.assertEqual(result['fallback'], 'offline')
        self.assertTrue(played and all(speech.is_sinhala(t) for t in played))
        self.assertTrue(said and not any(speech.is_sinhala(t) for t in said))

    def test_without_the_model_the_old_fallback_still_works(self):
        import offline_voice
        speaker = speech.Speaker()
        with patch.object(speech, 'synthesize', side_effect=speech.Offline('down')), \
                patch.object(offline_voice, 'available', return_value=False), \
                patch('win32com.client.Dispatch', return_value=MagicMock()):
            self.assertEqual(speaker.say('හරි')['fallback'], 'sapi')

    def test_the_real_voice_speaks_with_no_network(self):
        """Aeroplane mode: the network is cut, and Sinhala audio still comes out."""
        import offline_voice
        if not offline_voice.available():
            self.skipTest('offline voice not installed yet')
        with unplugged() as attempts:
            audio = offline_voice.synthesize('ආයුබෝවන් චමිදූ.', speech.SAMPLE_RATE)
        self.assertGreater(len(audio) / speech.SAMPLE_RATE, 0.5)
        self.assertGreater(float(abs(audio).max()), 0.05, 'not silence')
        self.assertEqual(attempts, [])
