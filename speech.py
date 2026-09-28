"""Spoken output for JARVIS, in Sinhala and English.

Windows ships no Sinhala voice at all — this machine has only Microsoft David and Zira,
both en-US — so Sinhala speech has to come from Microsoft's free neural endpoint through
edge-tts. That needs the internet, which here is not always present, so every path has a
fallback that says something rather than going silent.

Audio is decoded with PyAV and played through sounddevice rather than handed to a media
player, because playback must be interruptible: Day 4's wake word has to cut JARVIS off
mid-sentence when you speak over it.

    python speech.py "ආයුබෝවන්"
    python speech.py --voices si
    python speech.py --check
"""
import argparse
import asyncio
import hashlib
import queue
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'data' / 'tts_cache'

# si-LK-SameeraNeural is the male Sinhala voice; Thilini is the female one. Confirmed
# present in Microsoft's live voice list.
SINHALA_VOICE = 'si-LK-SameeraNeural'
ENGLISH_VOICE = 'en-GB-RyanNeural'
SAMPLE_RATE = 24000          # what the endpoint returns

SINHALA_RANGE = ('඀', '෿')
MAX_CHARACTERS = 3000        # refuse to narrate an essay
CACHE_LIMIT = 500            # cached clips before the oldest are dropped


def is_sinhala(text):
    """True when the text is mostly Sinhala script."""
    letters = [c for c in str(text) if c.isalpha()]
    if not letters:
        return False
    sinhala = sum(1 for c in letters if SINHALA_RANGE[0] <= c <= SINHALA_RANGE[1])
    return sinhala / len(letters) >= .3


def strip_unspeakable(text):
    """Remove emoji and symbols before synthesis.

    The endpoint reads an emoji out by its Unicode name: a reply containing a smiling
    face was spoken as "smiling face with smiling eyes", the microphone heard that, and
    it came back as a command. Never send a picture to a voice.
    """
    import unicodedata
    out = []
    for character in str(text):
        category = unicodedata.category(character)
        # So, Sk and Cs are symbols, modifiers and surrogates: emoji and the like.
        if category in ('So', 'Sk', 'Cs', 'Co', 'Cn'):
            continue
        if character in '‍️︎':      # zero-width joiner, variation selectors
            continue
        out.append(character)
    return ' '.join(''.join(out).split())


def segments(text):
    """Split into sentences, each tagged with the voice that should read it.

    Replies are routinely mixed — a Sinhala sentence with an English filename in it — and
    reading Sinhala with an English voice is unintelligible, so the split is per sentence
    rather than per reply.
    """
    pieces = re.split(r'(?<=[.!?।])\s+|\n+', strip_unspeakable(text))
    out = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        voice = SINHALA_VOICE if is_sinhala(piece) else ENGLISH_VOICE
        if out and out[-1][0] == voice and len(out[-1][1]) + len(piece) < 400:
            out[-1] = (voice, out[-1][1] + ' ' + piece)
        else:
            out.append((voice, piece))
    return out


class Offline(Exception):
    """The speech endpoint could not be reached."""


def cache_path(text, voice):
    key = hashlib.sha256(f'{voice}\n{text}'.encode()).hexdigest()
    return CACHE / f'{key}.mp3'


def prune_cache():
    try:
        files = sorted(CACHE.glob('*.mp3'), key=lambda f: f.stat().st_mtime)
        for old in files[:-CACHE_LIMIT]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def synthesize(text, voice, rate='+0%', timeout=20):
    """Return mp3 bytes for one piece of text, from cache when possible."""
    text = str(text).strip()[:MAX_CHARACTERS]
    if not text:
        return b''
    path = cache_path(f'{text}|{rate}', voice)
    if path.exists():
        try:
            data = path.read_bytes()
            if data:
                path.touch()
                return data
        except OSError:
            pass

    import edge_tts

    async def fetch():
        audio = bytearray()
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                audio.extend(chunk['data'])
        return bytes(audio)

    try:
        data = asyncio.run(asyncio.wait_for(fetch(), timeout))
    except Exception as error:               # network, DNS, endpoint changes
        raise Offline(str(error)[:160]) from error
    if not data:
        raise Offline('The speech service returned no audio')
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        prune_cache()
    except OSError:
        pass
    return data


def decode(mp3_bytes):
    """mp3 bytes to mono float32 at SAMPLE_RATE, using PyAV."""
    import av
    import numpy
    import io

    with av.open(io.BytesIO(mp3_bytes)) as container:
        resampler = av.AudioResampler(format='flt', layout='mono', rate=SAMPLE_RATE)
        chunks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
        for resampled in resampler.resample(None):
            chunks.append(resampled.to_ndarray().reshape(-1))
    if not chunks:
        return numpy.zeros(0, dtype='float32')
    return numpy.concatenate(chunks).astype('float32')


class Speaker:
    """Plays speech, one utterance at a time, interruptibly."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = threading.Lock()
        # A counter rather than a flag. With a flag, say() has to clear it on entry, which
        # silently discards a stop issued while the request was still being prepared --
        # precisely the case the wake word creates when you talk over JARVIS. Each request
        # takes a number; a stop cancels every number issued so far and no later one.
        self.issued = 0
        self.cancelled_through = 0
        self.speaking = False
        self.last_error = ''

    def take_number(self):
        with self.state:
            self.issued += 1
            return self.issued

    def is_cancelled(self, number):
        with self.state:
            return self.cancelled_through >= number

    def stop(self):
        """Cut off whatever is being said, and anything already queued behind it."""
        with self.state:
            self.cancelled_through = self.issued
        try:
            import sounddevice
            sounddevice.stop()
        except Exception:
            pass

    def play(self, audio, number):
        """Play float32 audio, returning False if it was interrupted."""
        import sounddevice
        if audio is None or not len(audio):
            return True
        sounddevice.play(audio, SAMPLE_RATE)
        finish = time.monotonic() + len(audio) / SAMPLE_RATE + 1
        while time.monotonic() < finish:
            if self.is_cancelled(number):
                sounddevice.stop()
                return False
            stream = sounddevice.get_stream()
            if stream is None or not stream.active:
                break
            time.sleep(.03)
        return True

    def say(self, text, rate='+0%', fallback=True):
        """Speak text aloud. Returns a dict describing what actually happened."""
        text = str(text or '').strip()
        if not text:
            return {'spoken': False, 'reason': 'empty'}
        number = self.take_number()
        with self.lock:
            self.speaking = True
            spoken, offline, said = [], False, 0
            try:
                for voice, piece in segments(text):
                    if self.is_cancelled(number):
                        return {'spoken': said > 0, 'reason': 'interrupted', 'pieces': said}
                    try:
                        audio = decode(synthesize(piece, voice, rate))
                    except Offline as error:
                        offline = True
                        self.last_error = str(error)
                        break
                    # Fetching takes over a second on a cold cache. Re-check before
                    # playing, or a stop issued during the fetch is ignored and JARVIS
                    # talks over you anyway.
                    if self.is_cancelled(number):
                        return {'spoken': said > 0, 'reason': 'interrupted', 'pieces': said}
                    if not self.play(audio, number):
                        return {'spoken': True, 'reason': 'interrupted', 'pieces': said}
                    said += 1
                    spoken.append(voice)
                if offline:
                    result = self.offline_fallback(text) if fallback else {'spoken': False}
                    result.update(reason='offline', detail=self.last_error, pieces=said)
                    return result
                return {'spoken': True, 'reason': 'ok', 'pieces': said,
                        'voices': sorted(set(spoken))}
            finally:
                self.speaking = False

    def offline_fallback(self, text):
        """No internet: read the English with a Windows voice, show the Sinhala.

        Windows has no Sinhala voice, so Sinhala cannot be spoken offline at all. Saying
        so plainly is better than silence, and the caller still displays the text.
        """
        english = ' '.join(piece for voice, piece in segments(text) if voice == ENGLISH_VOICE)
        note = 'I cannot reach the speech service, so this is on screen only.'
        try:
            import win32com.client
            sapi = win32com.client.Dispatch('SAPI.SpVoice')
            sapi.Speak(english or note, 1)      # 1 = async, so stop() still works
            return {'spoken': True, 'fallback': 'sapi', 'text': text,
                    'sinhala_shown_only': bool(english) is False or is_sinhala(text)}
        except Exception as error:
            return {'spoken': False, 'fallback': 'none', 'text': text,
                    'detail': str(error)[:120]}


SPEAKER = Speaker()


def say(text, rate='+0%'):
    return SPEAKER.say(text, rate)


def stop():
    SPEAKER.stop()


def main():
    parser = argparse.ArgumentParser(description='JARVIS speech output')
    parser.add_argument('text', nargs='*', help='text to speak')
    parser.add_argument('--rate', default='+0%', help="speed, e.g. '+20%%' or '-10%%'")
    parser.add_argument('--voices', nargs='?', const='', metavar='PREFIX',
                        help='list available voices, optionally filtered by locale')
    parser.add_argument('--check', action='store_true', help='verify the whole path')
    parser.add_argument('--no-cache', action='store_true')
    args = parser.parse_args()

    if args.voices is not None:
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
        for voice in sorted(voices, key=lambda v: v['ShortName']):
            if voice['ShortName'].lower().startswith(args.voices.lower()):
                print(f"  {voice['ShortName']:32} {voice['Gender']:7} {voice['Locale']}")
        return

    if args.check:
        print('Sinhala detection:')
        for sample in ['ආයුබෝවන්', 'hello there', 'CPU is at 40% ලෙස තිබේ']:
            print(f'   {sample[:28]:30} sinhala={is_sinhala(sample)}')
        print('\nSegmentation of a mixed reply:')
        for voice, piece in segments('CPU is at 40 percent. මතකය හොඳින් තිබේ.'):
            print(f'   {voice:22} {piece}')
        print('\nSynthesising...')
        started = time.monotonic()
        try:
            data = synthesize('ආයුබෝවන්', SINHALA_VOICE)
            print(f'   sinhala  {len(data)/1024:.1f} KB in {time.monotonic()-started:.2f}s')
            audio = decode(data)
            print(f'   decoded  {len(audio)/SAMPLE_RATE:.2f}s of audio')
            started = time.monotonic()
            synthesize('ආයුබෝවන්', SINHALA_VOICE)
            print(f'   cached   second call in {time.monotonic()-started:.3f}s')
        except Offline as error:
            print(f'   OFFLINE: {error}')
            print('   falling back...')
            print('  ', SPEAKER.offline_fallback('Speech service unreachable'))
            return
        print('\nSpeaking aloud...')
        print('  ', say('ආයුබෝවන් චමින්දු. Systems are ready.'))
        return

    text = ' '.join(args.text) or 'ආයුබෝවන් චමින්දු. මම ජාවිස්.'
    if args.no_cache:
        path = cache_path(f'{text}|{args.rate}', SINHALA_VOICE if is_sinhala(text) else ENGLISH_VOICE)
        path.unlink(missing_ok=True)
    print(say(text, args.rate))


if __name__ == '__main__':
    main()
