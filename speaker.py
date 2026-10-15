"""It knows who is speaking (Day 17).

    python speaker.py --enroll              learn the owner's voice from the saved clips
    python speaker.py --check FILE.wav      whose voice is this?
    python speaker.py --status

A voice becomes 256 numbers (wespeaker ResNet34, 26.5 MB, run by onnxruntime on this
computer). The owner's voiceprint is the average of those numbers over his own recordings;
any later utterance is compared with it. Close enough, and it is him.

What it guards: anything that leaves the computer in his name -- a WhatsApp message, and
the "yes" that sends it. Anyone may ask the time or play a song. The television, a
visitor or JARVIS's own voice coming back through the microphone may not send a message.

Short sounds carry little of a voice. Under MIN_SECONDS the answer is "unsure", which is
not the same as "him".
"""
import argparse
import json
import sys
import wave
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'data' / 'models' / 'wespeaker_en_voxceleb_resnet34_LM.onnx'
VOICEPRINT = ROOT / 'data' / 'voiceprint.json'
SAMPLE_RATE = 16000
MIN_SECONDS = 1.0
# Set by calibrate(), from the owner's own clips against other voices; this is only the
# fallback before that has run.
DEFAULT_THRESHOLD = 0.5

_session = None


# ---------------------------------------------------------------- features

def _mel_banks(bins=80, fft=512, rate=SAMPLE_RATE, low=20.0, high=None):
    """Kaldi's triangular mel filters, on its own mel scale: 1127 ln(1 + f/700)."""
    high = high or rate / 2
    mel = lambda f: 1127.0 * numpy.log(1.0 + f / 700.0)
    edges = numpy.linspace(mel(low), mel(high), bins + 2)
    freqs = mel(numpy.arange(fft // 2) * rate / fft)
    banks = numpy.zeros((bins, fft // 2), dtype=numpy.float32)
    for b in range(bins):
        left, centre, right = edges[b], edges[b + 1], edges[b + 2]
        up = (freqs - left) / (centre - left)
        down = (right - freqs) / (right - centre)
        banks[b] = numpy.maximum(0.0, numpy.minimum(up, down))
    return banks


_BANKS = None


def fbank(audio, rate=SAMPLE_RATE):
    """80-band log-mel filterbank, as Kaldi computes it (25 ms frames, 10 ms apart,
    no dither), and mean-normalised over time, which is what the model was trained on."""
    global _BANKS
    if _BANKS is None:
        _BANKS = _mel_banks()
    signal = numpy.asarray(audio, dtype=numpy.float64) * 32768.0   # the model wants int16 scale
    length, shift = int(0.025 * rate), int(0.010 * rate)
    if len(signal) < length:
        return numpy.zeros((0, 80), dtype=numpy.float32)
    count = 1 + (len(signal) - length) // shift
    index = numpy.arange(length)[None, :] + shift * numpy.arange(count)[:, None]
    frames = signal[index]
    frames = frames - frames.mean(axis=1, keepdims=True)              # remove DC
    frames = numpy.concatenate([frames[:, :1] * (1 - 0.97),
                                frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    window = (0.5 - 0.5 * numpy.cos(2 * numpy.pi * numpy.arange(length) / (length - 1))) ** 0.85
    spectrum = numpy.abs(numpy.fft.rfft(frames * window, n=512)) ** 2
    energies = spectrum[:, :256] @ _BANKS.T
    feats = numpy.log(numpy.maximum(energies, numpy.finfo(numpy.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(numpy.float32)


# ---------------------------------------------------------------- voiceprints

def session():
    global _session
    if _session is None:
        if not MODEL.exists():
            raise FileNotFoundError(f'the voice model is missing: {MODEL}')
        import onnxruntime
        _session = onnxruntime.InferenceSession(str(MODEL), providers=['CPUExecutionProvider'])
    return _session


def embed(audio):
    """The voice in this audio as a unit-length 256-number vector."""
    feats = fbank(audio)
    if len(feats) < 50:
        raise ValueError('too short to tell whose voice it is')
    vector = session().run(None, {'feats': feats[None]})[0][0]
    return vector / (numpy.linalg.norm(vector) + 1e-9)


def load_wav(path):
    with wave.open(str(path)) as source:
        if source.getframerate() != SAMPLE_RATE or source.getsampwidth() != 2:
            raise ValueError(f'{path}: expected 16 kHz, 16-bit audio')
        data = numpy.frombuffer(source.readframes(source.getnframes()), dtype=numpy.int16)
        if source.getnchannels() > 1:
            data = data.reshape(-1, source.getnchannels()).mean(axis=1)
    return (data / 32768.0).astype(numpy.float32)


def owner_clips():
    """The owner's own recordings: the Day 2 session and the live command log."""
    voice = ROOT / 'data' / 'voice'
    return sorted((voice / 'clips').glob('*.wav')) + sorted((voice / 'live').glob('*.wav'))


def voiceprint_of(clips):
    vectors = []
    for clip in clips:
        audio = load_wav(clip) if isinstance(clip, (str, Path)) else clip
        if len(audio) / SAMPLE_RATE >= MIN_SECONDS:
            vectors.append(embed(audio))
    if not vectors:
        raise ValueError('no recording long enough to learn a voice from')
    mean = numpy.mean(vectors, axis=0)
    return mean / numpy.linalg.norm(mean), len(vectors)


LIVE_FLOOR = 0.4


def owner_set():
    """Recordings to learn the owner from: everything read on Day 2, plus the live
    commands that already sound like him.

    Measured: against the Day 2 voice alone, live commands through the wake-word
    microphone scored 0.26-0.57 -- room noise and a different gain -- while JARVIS's own
    voice reached 0.25. Learning from the live ones too teaches the voiceprint how he sounds
    through that microphone. Live clips under LIVE_FLOOR are left out: four of them, from
    one minute of wake-word testing, were JARVIS or the room (0.04-0.13).
    """
    recorded = [load_wav(c) for c in owner_clips() if c.parent.name == 'clips']
    base, _ = voiceprint_of(recorded)
    chosen = list(recorded)
    for clip in owner_clips():
        if clip.parent.name != 'live':
            continue
        audio = load_wav(clip)
        if len(audio) / SAMPLE_RATE >= MIN_SECONDS and float(embed(audio) @ base) >= LIVE_FLOOR:
            chosen.append(audio)
    return chosen


def enroll(clips=None, threshold=None, path=VOICEPRINT):
    clips = clips or owner_set()
    vector, count = voiceprint_of(clips)
    saved = {'vector': vector.round(6).tolist(), 'clips': count,
             'threshold': threshold or DEFAULT_THRESHOLD, 'model': MODEL.name}
    Path(path).write_text(json.dumps(saved), encoding='utf-8')
    return saved


def load_voiceprint(path=VOICEPRINT):
    path = Path(path)
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['vector'] = numpy.array(saved['vector'], dtype=numpy.float32)
    return saved


def score(audio, voiceprint):
    return float(embed(audio) @ voiceprint['vector'])


def verdict(audio, voiceprint=None):
    """'owner', 'other', 'unsure' (too short) or 'unknown' (no voiceprint yet), with the
    score, so that callers and logs can see how close it was."""
    voiceprint = voiceprint or load_voiceprint()
    if voiceprint is None:
        return 'unknown', None
    if audio is None or len(audio) / SAMPLE_RATE < MIN_SECONDS:
        return 'unsure', None
    value = score(audio, voiceprint)
    return ('owner' if value >= voiceprint['threshold'] else 'other'), round(value, 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--enroll', action='store_true')
    parser.add_argument('--threshold', type=float)
    parser.add_argument('--check', metavar='WAV')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.enroll:
        saved = enroll(threshold=args.threshold)
        print(f"learned from {saved['clips']} clips; threshold {saved['threshold']}")
    elif args.check:
        print(verdict(load_wav(args.check)))
    else:
        saved = load_voiceprint()
        print('no voiceprint yet: python speaker.py --enroll' if not saved else
              f"voiceprint from {saved['clips']} clips, threshold {saved['threshold']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
