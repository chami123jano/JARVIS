"""It sees your screen (Day 15).

    python vision.py                        what is in the window in front
    python vision.py --screen               the whole screen
    python vision.py --mode error --lang si explain the error in front, in Sinhala
    python vision.py --file photo.jpg       read a photographed document

The picture goes to qwen3.5:9b, which reads images itself -- no second model, no OCR
engine. It is captured into memory, shrunk, sent to Ollama on this computer and dropped.
**A screenshot is never written to disk and never leaves the machine.**

"This" means the window in front: when you ask "what's wrong with this?" you are looking
at one window, not at the taskbar and three monitors. Say "screen" for everything.
"""
import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

import requests

ENDPOINT = 'http://127.0.0.1:11434'
# Long side, in pixels. A 4K capture costs thousands of image tokens and minutes; at this
# size an error dialog and ordinary window text are still legible.
MAX_SIDE = 1600
IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')


class VisionError(Exception):
    pass


# ---------------------------------------------------------------- capture

def foreground_window():
    """(title, (left, top, right, bottom)) of the window in front, or None."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    rect = wintypes.RECT()
    # The visible frame, without the invisible resize border Windows 10+ adds.
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect),
                                                   ctypes.sizeof(rect))
    except OSError:
        user32.GetWindowRect(handle, ctypes.byref(rect))
    length = user32.GetWindowTextLengthW(handle)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, title, length + 1)
    box = (rect.left, rect.top, rect.right, rect.bottom)
    if box[2] - box[0] < 40 or box[3] - box[1] < 40:
        return None
    return title.value, box


def capture(region='window'):
    """(PIL image, what it shows). Held in memory only."""
    from PIL import ImageGrab
    if region == 'window':
        window = foreground_window()
        if window:
            title, box = window
            return ImageGrab.grab(bbox=box, all_screens=True), f'the window "{title}"'
    return ImageGrab.grab(all_screens=True), 'the whole screen'


def prepare(image, max_side=MAX_SIDE):
    """Shrink to at most max_side on the long edge, and encode for Ollama."""
    image = image.convert('RGB')
    scale = max_side / max(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return base64.b64encode(buffer.getvalue()).decode('ascii'), image.size


def latest_photo(folders=None):
    """The newest image in Downloads or Pictures: the photo you just took or were sent."""
    home = Path.home()
    folders = folders or [home / 'Downloads', home / 'Pictures', home / 'OneDrive' / 'Pictures']
    newest = None
    for folder in folders:
        if not Path(folder).is_dir():
            continue
        for path in Path(folder).iterdir():
            if path.suffix.lower() in IMAGE_TYPES and path.is_file():
                if not newest or path.stat().st_mtime > newest.stat().st_mtime:
                    newest = path
    return newest


# ---------------------------------------------------------------- asking

MODES = {
    'describe': ('Say briefly what is on this {what}: the application and what the person '
                 'is doing in it. Two or three sentences.'),
    'error': ('There is a problem showing in this {what}. Find the error message, say in '
              'plain words what it means, and say what to try first to fix it. If there is no '
              'error, say so.'),
    'read': ('Read out the main text in this {what}, as it would be read aloud to someone. '
             'Skip menus, buttons and toolbars.'),
    'document': ('This is a photo of a document, such as a bill or a letter. Say what it '
                 'is, who it is from, and the important figures and dates, then what the '
                 'person needs to do about it, if anything.'),
}
LANGUAGE = {
    'si': ('Answer in everyday spoken Sinhala, in Sinhala script, the way a person talks -- '
           'not formal written Sinhala. Keep names, numbers and technical words as they '
           'appear.'),
    'en': 'Answer in plain English.',
}
SPOKEN = ('Your answer is read aloud: no markdown, no lists, no symbols, at most five '
          'sentences.')


def ask(image_b64, mode='describe', lang='si', what='screen', question='', store=None,
        timeout=180):
    """What the local model makes of the picture. Returns (answer, seconds)."""
    model = (store.config('model', '') if store else '') or 'qwen3.5:9b'
    prompt = ' '.join([MODES[mode].format(what=what),
                       f'The person asked: "{question}".' if question else '',
                       LANGUAGE.get(lang, LANGUAGE['si']), SPOKEN])
    started = time.monotonic()
    try:
        response = requests.post(ENDPOINT + '/api/chat', json={
            'model': model, 'stream': False, 'think': False, 'keep_alive': '30m',
            'messages': [{'role': 'user', 'content': prompt, 'images': [image_b64]}],
            'options': {'temperature': 0.2, 'num_ctx': 8192, 'num_predict': 700}},
            timeout=(5, timeout))
    except requests.RequestException as error:
        raise VisionError(f'the local model could not be reached: {error}')
    if not response.ok:
        raise VisionError(response.json().get('error', f'Ollama HTTP {response.status_code}'))
    answer = (response.json().get('message') or {}).get('content', '').strip()
    if not answer:
        raise VisionError('the model said nothing about the picture')
    return answer, time.monotonic() - started


# What was asked, from the words. Errors first: "what's wrong with this" is the point.
ERROR_WORDS = ('error', 'wrong', 'problem', 'වැරැද්ද', 'වැරදි', 'ප්‍රශ්නේ', 'ප්‍රශ්නය',
               'අවුල', 'එරර්', 'ඇරර්', 'වැඩ කරන්නේ නැත්තේ')
READ_WORDS = ('read', 'කියවන්න', 'කියවලා')
PHOTO_WORDS = ('photo', 'picture', 'ෆොටෝ', 'පින්තූර', 'ඡායාරූප', 'බිල', 'bill', 'document',
               'ලියුම')
SCREEN_WORDS = ('screen', 'ස්ක්‍රීන්', 'තිරය', 'everything')


def understand(text):
    """(mode, region) from what was said."""
    lowered = str(text).lower()
    if any(w in lowered for w in PHOTO_WORDS) and not any(w in lowered for w in SCREEN_WORDS):
        return 'document', 'photo'
    region = 'screen' if any(w in lowered for w in SCREEN_WORDS) else 'window'
    if any(w in lowered for w in ERROR_WORDS):
        return 'error', region
    if any(w in lowered for w in READ_WORDS):
        return 'read', region
    return 'describe', region


def look(text, lang='si', store=None):
    """Answer a spoken question about the screen or the latest photo. Returns a dict."""
    mode, region = understand(text)
    if region == 'photo':
        path = latest_photo()
        if not path:
            raise VisionError('there is no photo in Downloads or Pictures')
        from PIL import Image
        with Image.open(path) as image:
            encoded, size = prepare(image)
        what = 'photo'
        shows = f'the photo {path.name}'
    else:
        image, shows = capture(region)
        encoded, size = prepare(image)
        what = 'window' if region == 'window' else 'screen'
    answer, seconds = ask(encoded, mode, lang, what, text, store)
    return {'answer': answer, 'mode': mode, 'shows': shows, 'size': f'{size[0]}x{size[1]}',
            'seconds': round(seconds, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--screen', action='store_true')
    parser.add_argument('--mode', default='describe', choices=list(MODES))
    parser.add_argument('--lang', default='en', choices=['si', 'en'])
    parser.add_argument('--file')
    args = parser.parse_args()
    if args.file:
        from PIL import Image
        with Image.open(args.file) as image:
            encoded, size = prepare(image)
        what = 'photo'
    else:
        image, shows = capture('screen' if args.screen else 'window')
        print(f'looking at {shows}')
        encoded, size = prepare(image)
        what = 'screen' if args.screen else 'window'
    answer, seconds = ask(encoded, args.mode, args.lang, what)
    print(f'[{size[0]}x{size[1]}, {seconds:.1f}s]\n{answer}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
