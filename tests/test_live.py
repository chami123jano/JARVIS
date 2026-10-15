"""The console's live picture of the voice loop (Day 24)."""
import json
import socket
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class PictureTests(unittest.TestCase):
    def test_levels_are_kept_at_twenty_a_second_not_every_block(self):
        picture = live.Live()
        for _ in range(500):                      # 500 audio blocks in an instant
            picture.level(0.05)
        self.assertEqual(sum(1 for v in picture.levels if v), 1)
        self.assertEqual(len(picture.levels), live.LEVELS)

    def test_levels_are_a_number_not_audio(self):
        picture = live.Live()
        picture.level(0.5, every=0)
        self.assertEqual(picture.levels[-1], 1.0, 'clipped to 0..1')
        self.assertTrue(all(isinstance(v, float) for v in picture.levels))

    def test_subtitles_keep_the_last_three_exchanges(self):
        picture = live.Live()
        for number in range(10):
            picture.heard(f'question {number}')
            picture.said(f'answer {number}')
        texts = [line['text'] for line in picture.snapshot()['lines']]
        self.assertEqual(texts[0], 'question 7')
        self.assertEqual(len(texts), live.LINES)

    def test_speaking_shows_while_it_talks(self):
        picture = live.Live()
        picture.set_state('idle')
        self.assertEqual(picture.snapshot(speaking=True)['state'], 'speaking')
        picture.set_state('listening')
        self.assertEqual(picture.snapshot(speaking=True)['state'], 'listening')


class ServingTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.picture = live.Live()
        self.picture.heard('අද කාලගුණය කොහොමද')
        self.server = live.serve(self.picture, port=self.port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_served_as_json_on_this_computer(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/live', timeout=2) as reply:
            data = json.loads(reply.read())
        self.assertTrue(data['running'])
        self.assertEqual(data['lines'][0]['text'], 'අද කාලගුණය කොහොමද')

    def test_only_the_live_path_and_only_by_its_own_name(self):
        for url, host in ((f'http://127.0.0.1:{self.port}/other', None),
                          (f'http://127.0.0.1:{self.port}/live', 'evil.example')):
            with self.subTest(url=url, host=host):
                request = urllib.request.Request(url, headers={'Host': host} if host else {})
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(request, timeout=2)

    def test_a_second_copy_does_not_crash(self):
        self.assertIsNone(live.serve(self.picture, port=self.port))


class ConsoleTests(unittest.TestCase):
    def test_the_console_says_when_the_voice_loop_is_off(self):
        import tempfile
        import threading
        from server import create_server
        with tempfile.TemporaryDirectory() as folder:
            original = live.PORT
            live.PORT = free_port()                 # nothing listening there
            server = create_server(0, Path(folder))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/api/live'
                with urllib.request.urlopen(url, timeout=3) as reply:
                    data = json.loads(reply.read())
            finally:
                live.PORT = original
                server.shutdown()
                server.server_close()
        self.assertFalse(data['running'])
        self.assertEqual(data['state'], 'off')


if __name__ == '__main__':
    unittest.main()
