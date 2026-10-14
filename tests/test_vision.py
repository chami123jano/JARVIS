"""Seeing the screen: the right picture, shrunk, kept in memory, sent only to the local
model (Day 15)."""
import base64
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vision


class UnderstandingTests(unittest.TestCase):
    def test_what_was_asked(self):
        for text, expected in [
                ('මේකේ මොකක්ද වැරැද්ද', ('error', 'window')),
                ("what's wrong with this", ('error', 'window')),
                ('මේ error එක මොකක්ද', ('error', 'window')),
                ('read this to me', ('read', 'window')),
                ("what's on my screen", ('describe', 'screen')),
                ('screen එකේ error එක මොකක්ද', ('error', 'screen')),
                ('මේ ෆොටෝ එක කියවන්න', ('document', 'photo')),
                ('මේ බිල බලන්න', ('document', 'photo'))]:
            with self.subTest(text=text):
                self.assertEqual(vision.understand(text), expected)


class PreparingTests(unittest.TestCase):
    def test_a_4k_capture_is_shrunk(self):
        encoded, size = vision.prepare(Image.new('RGB', (3840, 2160), 'white'))
        self.assertEqual(max(size), vision.MAX_SIDE)
        self.assertEqual(size, (1600, 900))
        decoded = Image.open(io.BytesIO(base64.b64decode(encoded)))
        self.assertEqual(decoded.size, (1600, 900))

    def test_a_small_window_is_left_alone(self):
        _, size = vision.prepare(Image.new('RGBA', (500, 300)))
        self.assertEqual(size, (500, 300))


class PrivacyTests(unittest.TestCase):
    def test_the_picture_goes_only_to_ollama_on_this_computer(self):
        response = MagicMock(ok=True)
        response.json.return_value = {'message': {'content': 'An error dialog.'}}
        with patch.object(vision.requests, 'post', return_value=response) as post:
            answer, _ = vision.ask('AAAA', 'error', 'si', 'window', 'මේකේ මොකක්ද වැරැද්ද')
        url = post.call_args.args[0]
        self.assertTrue(url.startswith('http://127.0.0.1:'), url)
        message = post.call_args.kwargs['json']['messages'][0]
        self.assertEqual(message['images'], ['AAAA'])
        self.assertIn('Sinhala', message['content'])
        self.assertIs(post.call_args.kwargs['json']['think'], False)
        self.assertEqual(answer, 'An error dialog.')

    def test_a_screenshot_is_never_written_to_disk(self):
        saved = []
        original = Image.Image.save

        def watch(image, target, *args, **kwargs):
            if not isinstance(target, io.BytesIO):
                saved.append(target)
            return original(image, target, *args, **kwargs)
        with patch.object(vision, 'capture', return_value=(Image.new('RGB', (800, 600)), 'x')), \
                patch.object(vision, 'ask', return_value=('fine', 1.0)), \
                patch.object(Image.Image, 'save', watch):
            result = vision.look("what's wrong with this", 'en')
        self.assertEqual(saved, [])
        self.assertEqual(result['answer'], 'fine')

    def test_silence_from_the_model_is_an_error_not_an_answer(self):
        response = MagicMock(ok=True)
        response.json.return_value = {'message': {'content': '  '}}
        with patch.object(vision.requests, 'post', return_value=response):
            with self.assertRaises(vision.VisionError):
                vision.ask('AAAA')


class PhotoTests(unittest.TestCase):
    def test_the_newest_image_is_the_photo(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            old, new, other = folder / 'old.jpg', folder / 'bill.png', folder / 'notes.pdf'
            for path in (old, new):
                Image.new('RGB', (10, 10)).save(path)
            other.write_bytes(b'%PDF')
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            os.utime(other, (time.time() + 60, time.time() + 60))
            self.assertEqual(vision.latest_photo([folder]), new)

    def test_no_photo_is_said(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(vision, 'latest_photo', return_value=None):
            with self.assertRaises(vision.VisionError):
                vision.look('මේ ෆොටෝ එක කියවන්න', 'si')


class CaptureTests(unittest.TestCase):
    def test_the_window_in_front_is_found_or_the_screen_is_used(self):
        window = vision.foreground_window()
        if window is not None:
            title, (left, top, right, bottom) = window
            self.assertGreater(right - left, 40)
        image, shows = vision.capture('window')
        self.assertGreater(image.width, 40)
        self.assertTrue(shows.startswith(('the window', 'the whole screen')))


if __name__ == '__main__':
    unittest.main()
