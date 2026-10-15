"""Is it him? Measured on his own recordings against other voices, before trusting it.

    python bench_speaker.py              scores, and the threshold that separates them

Genuine: each of the owner's clips against a voiceprint built from all the OTHER clips,
so no clip is ever compared with itself. Impostors: synthetic voices of other people --
including JARVIS's own, which is what the microphone hears when it talks. Synthetic
voices are not real visitors; the live test with a second person is in PLAN.md.
"""
import sys
from pathlib import Path

import numpy

import speaker

IMPOSTORS = ['si-LK-SameeraNeural', 'si-LK-ThiliniNeural', 'en-IN-PrabhatNeural',
             'en-IN-NeerjaNeural', 'en-US-GuyNeural', 'en-GB-RyanNeural',
             'ta-IN-ValluvarNeural', 'hi-IN-MadhurNeural', 'en-US-JennyNeural']
SENTENCES = ['අම්මාට මැසේජ් එකක් යවන්න, මම රෑට එනවා කියලා.',
             'Send a message to everyone saying that I have quit my job today.']


def to_16k(audio, rate):
    if rate == speaker.SAMPLE_RATE:
        return audio
    positions = numpy.arange(0, len(audio), rate / speaker.SAMPLE_RATE)
    return numpy.interp(positions, numpy.arange(len(audio)), audio).astype(numpy.float32)


def impostor_audio(limit=30):
    """Other voices: JARVIS's own (cached from before edge-tts began refusing requests on
    15 Oct) and the two voices Windows ships with."""
    import speech
    clips = []
    for path in sorted((speaker.ROOT / 'data' / 'tts_cache').glob('*.mp3'))[:limit]:
        audio = numpy.asarray(speech.decode(path.read_bytes()), dtype=numpy.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = to_16k(audio, speech.SAMPLE_RATE)
        if len(audio) / speaker.SAMPLE_RATE >= 1.5:
            clips.append(('JARVIS voice', audio))
    clips += sapi_audio()
    return clips


def sapi_audio():
    import tempfile
    import win32com.client
    clips = []
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    for token in voice.GetVoices():
        for sentence in ('Send a message to everyone saying that I have quit my job today.',
                         'Hey Jarvis, message my mother that I will be late tonight.'):
            target = Path(tempfile.gettempdir()) / 'sapi_bench.wav'
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            fmt = win32com.client.Dispatch('SAPI.SpAudioFormat')
            fmt.Type = 18                       # 16 kHz, 16-bit, mono
            stream.Format = fmt
            stream.Open(str(target), 3)
            voice.AudioOutputStream = stream
            voice.Voice = token
            voice.Speak(sentence)
            stream.Close()
            clips.append((token.GetDescription(), speaker.load_wav(target)))
    return clips


# Four live clips from one minute of wake-word testing on 28 Sept score 0.04-0.13 against
# the rest: JARVIS hearing itself or the room, not the owner. They are left out.
NOT_OWNER = {'20260928-132717.wav', '20260928-132722.wav', '20260928-132745.wav',
             '20260928-132806.wav'}


def measure():
    """(owner, impostors) scores against the voiceprint speaker.enroll() would build. Each
    owner clip is scored against a voiceprint made WITHOUT it."""
    chosen = speaker.owner_set()
    vectors = [speaker.embed(a) for a in chosen if len(a) / speaker.SAMPLE_RATE >= speaker.MIN_SECONDS]
    owner = []
    for index, vector in enumerate(vectors):
        others = numpy.mean([v for i, v in enumerate(vectors) if i != index], axis=0)
        owner.append(('read' if index < 20 else 'live', float(vector @ (others / numpy.linalg.norm(others)))))
    voiceprint = numpy.mean(vectors, axis=0)
    voiceprint /= numpy.linalg.norm(voiceprint)
    impostors = [(name, float(speaker.embed(a) @ voiceprint)) for name, a in impostor_audio()]
    return owner, impostors


def main():
    owner, impostors = measure()
    for label, rows in (('owner, read on Day 2', [r for r in owner if r[0] == 'read']),
                        ('owner, live commands', [r for r in owner if r[0] == 'live']),
                        ('other voices', impostors)):
        values = sorted(v for _, v in rows)
        print(f'{label:22} {len(values):3}: lowest {values[0]:.3f}  median '
              f'{numpy.median(values):.3f}  highest {values[-1]:.3f}')
    owner_low = min(v for _, v in owner)
    other_high = max(v for _, v in impostors)
    print(f'gap {owner_low - other_high:.3f}; threshold midway: {(owner_low + other_high) / 2:.3f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
