"""JARVIS's own Sinhala voice, with no internet at all (Day 29).

    python offline_voice.py "ආයුබෝවන් චමිදූ"     say it with the offline voice
    python offline_voice.py --status

A Piper voice trained on this computer from Google's openly licensed Sinhala recordings
(OpenSLR 30, CC BY-SA 4.0, speaker sin_6314), fine-tuned from Piper's English "lessac"
voice -- which was only the starting weights; it speaks Sinhala. It runs on the CPU in
onnxruntime, in a fraction of a second.

It is the fallback, not the main voice: Sameera (Microsoft, online) is used whenever the
service answers. When it does not -- no internet, or the service refusing requests as it
did from 15 October 2026 -- this voice says the Sinhala instead of the Windows English
voice saying "this is on screen only".

The OpenSLR 30 speakers are all women, so this voice is female, unlike Sameera.
"""
import argparse
import sys
import threading
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'data' / 'models' / 'si_LK-offline-medium.onnx'

# espeak-ng writes the prenasalised consonants (ඳ ඟ ඹ ඬ) with superscript nasals that the
# voice has no symbol for. Training mapped them like this; speaking must do the same.
SINHALA_NASALS = {'ⁿ': 'n', 'ᵑ': 'ŋ', 'ᵐ': 'm'}

_voice = None
_lock = threading.Lock()


def available():
    return MODEL.exists() and MODEL.with_suffix('.onnx.json').exists()


def voice():
    global _voice
    with _lock:
        if _voice is None:
            from piper.voice import PiperVoice
            loaded = PiperVoice.load(str(MODEL), config_path=str(MODEL.with_suffix('.onnx.json')))
            original = loaded.phonemes_to_ids
            loaded.phonemes_to_ids = lambda phonemes: original(
                [SINHALA_NASALS.get(p, p) for p in phonemes])
            _voice = loaded
        return _voice


def synthesize(text, rate=None):
    """Float32 audio of the text, resampled to `rate` if given. Raises if no model."""
    if not available():
        raise FileNotFoundError(f'no offline Sinhala voice at {MODEL}')
    spoken = voice()
    pieces = [chunk.audio_float_array for chunk in spoken.synthesize(str(text))]
    audio = numpy.concatenate(pieces) if pieces else numpy.zeros(0, dtype=numpy.float32)
    native = spoken.config.sample_rate
    if rate and rate != native and len(audio):
        positions = numpy.arange(0, len(audio), native / rate)
        audio = numpy.interp(positions, numpy.arange(len(audio)), audio)
    return audio.astype(numpy.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('text', nargs='*')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status or not args.text:
        print(f'offline Sinhala voice: {"ready" if available() else "not installed"} ({MODEL.name})')
        return 0
    import sounddevice
    audio = synthesize(' '.join(args.text))
    sounddevice.play(audio, voice().config.sample_rate)
    sounddevice.wait()
    return 0


if __name__ == '__main__':
    sys.exit(main())
