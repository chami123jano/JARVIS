"""The face page (web/face.html): its mouth follows the loudness of what is really playing."""
import json
import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy  # noqa: E402

import live  # noqa: E402
import speech  # noqa: E402


class EnvelopeTests(unittest.TestCase):
    def test_loud_and_quiet_parts_are_told_apart(self):
        rate = speech.SAMPLE_RATE
        quiet = numpy.zeros(rate // 2, dtype='float32')
        loud = (0.3 * numpy.sin(numpy.arange(rate // 2) / 5)).astype('float32')
        env = speech.envelope(numpy.concatenate([quiet, loud]), rate)
        self.assertEqual(len(env), speech.ENVELOPE_RATE)          # one second, fifty steps
        self.assertLess(max(env[:20]), 0.05)
        self.assertGreater(min(env[30:]), 0.8)

    def test_a_quiet_voice_still_opens_the_mouth(self):
        """Scaled to the clip's own loud parts, not to full volume."""
        audio = (0.02 * numpy.sin(numpy.arange(speech.SAMPLE_RATE) / 5)).astype('float32')
        self.assertGreater(max(speech.envelope(audio)), 0.9)

    def test_nothing_to_measure(self):
        self.assertEqual(speech.envelope([]), [])
        self.assertEqual(speech.envelope(None), [])


class VoiceNowTests(unittest.TestCase):
    def test_only_while_playing(self):
        speaker = speech.Speaker()
        self.assertIsNone(speaker.voice_now())
        speaker.playing = {'id': 5, 'envelope': [0.1, 0.9], 'start': 0}
        now = speaker.voice_now()
        self.assertEqual(now['envelope'], [0.1, 0.9])
        self.assertEqual(now['rate'], speech.ENVELOPE_RATE)
        self.assertGreater(now['elapsed'], 0)

    def test_the_live_picture_carries_it(self):
        picture = live.Live().snapshot(True, {'id': 1, 'envelope': [1]})
        self.assertEqual(picture['voice'], {'id': 1, 'envelope': [1]})
        self.assertIsNone(live.Live().snapshot()['voice'])

    def test_served_and_a_broken_voice_does_not_break_the_picture(self):
        def broken():
            raise RuntimeError('no audio device')
        for voice, expected in ((lambda: {'id': 2, 'envelope': [0.5]}, 2), (broken, None)):
            import socket
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            server = live.serve(live.Live(), port=port, voice=voice)
            try:
                request = urllib.request.Request(f'http://127.0.0.1:{port}/live',
                                                 headers={'Host': f'127.0.0.1:{port}'})
                with urllib.request.urlopen(request, timeout=5) as reply:
                    picture = json.loads(reply.read())
                self.assertEqual((picture['voice'] or {}).get('id'), expected)
            finally:
                server.shutdown()


class PageTests(unittest.TestCase):
    def test_the_page_and_its_way_in(self):
        page = (ROOT / 'web' / 'face.html').read_text(encoding='utf-8')
        self.assertIn('face.js', page)
        self.assertIn('href="face.html"', (ROOT / 'web' / 'index.html').read_text(encoding='utf-8'))
        script = (ROOT / 'web' / 'face.js').read_text(encoding='utf-8')
        self.assertIn('/api/live', script)
        self.assertIn('speak: true', script)       # typed requests are answered aloud


if __name__ == '__main__':
    unittest.main()
