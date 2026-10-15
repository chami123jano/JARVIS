"""How understandable is the offline Sinhala voice? Measured, since nobody here can listen.

    python bench_offline_voice.py                         the installed offline voice
    python bench_offline_voice.py --model PATH.onnx       a checkpoint under training
    python bench_offline_voice.py --baseline              today's fallback, for comparison

Ten things JARVIS actually says are spoken by the voice and transcribed by Google's si-LK
recogniser -- the one that scored 0.94 on real Sinhala speech on Day 2. If Google can
write down what it said, a person can understand it. The score is the similarity of what
was meant and what was heard, by sound (sinhala.normalize), from 0 to 1.

Needs the internet, for the recogniser. The voice itself does not.
"""
import argparse
import sys
from difflib import SequenceMatcher
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sinhala import normalize

SENTENCES = [
    'ආයුබෝවන් චමින්දු.',
    'දැන් වෙලාව හවස පහයි.',
    'මතක් කිරීමක්. බෙහෙත් බොන්න.',
    'අද කාලගුණය හොඳයි.',
    'අම්මාට මැසේජ් එක යැව්වා.',
    'බැටරිය සියයට අසූවයි.',
    'සමාවෙන්න, මැසේජ් යවන්න පුළුවන් චමින්දුට විතරයි.',
    'හරි, සින්දුවක් දානවා.',
    'මම ඒක මතක තියාගන්නම්.',
    'අද කරන්න දේවල් තුනක් තියෙනවා.',
]


def to_16k(audio, rate):
    if rate == 16000:
        return audio.astype(numpy.float32)
    positions = numpy.arange(0, len(audio), rate / 16000)
    return numpy.interp(positions, numpy.arange(len(audio)), audio).astype(numpy.float32)


def piper_audio(model):
    from piper.voice import PiperVoice
    import offline_voice
    voice = PiperVoice.load(str(model), config_path=str(Path(model).with_suffix('.onnx.json')))
    original = voice.phonemes_to_ids
    voice.phonemes_to_ids = lambda p: original([offline_voice.SINHALA_NASALS.get(x, x) for x in p])

    def speak(text):
        audio = numpy.concatenate([c.audio_float_array for c in voice.synthesize(text)])
        return to_16k(audio, voice.config.sample_rate)
    return speak


def sapi_audio():
    """Today's fallback: the Windows English voice reading the Sinhala in Latin letters."""
    import tempfile
    import wave
    import win32com.client
    import speech

    def speak(text):
        target = Path(tempfile.gettempdir()) / 'bench_sapi.wav'
        voice = win32com.client.Dispatch('SAPI.SpVoice')
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        fmt = win32com.client.Dispatch('SAPI.SpAudioFormat')
        fmt.Type = 18                               # 16 kHz, 16-bit, mono
        stream.Format = fmt
        stream.Open(str(target), 3)
        voice.AudioOutputStream = stream
        voice.Speak(speech.speakable_roman(text))
        stream.Close()
        with wave.open(str(target)) as source:
            data = numpy.frombuffer(source.readframes(source.getnframes()), dtype=numpy.int16)
        return (data / 32768).astype(numpy.float32)
    return speak


def score(speak):
    import voice_loop
    ears = voice_loop.GoogleEars()
    results = []
    for sentence in SENTENCES:
        heard, _, _ = ears.transcribe(speak(sentence))
        value = SequenceMatcher(None, normalize(sentence).replace(' ', ''),
                                normalize(heard).replace(' ', '')).ratio()
        results.append((sentence, heard, value))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--model')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    if args.baseline:
        speak, label = sapi_audio(), 'Windows voice, romanised'
    else:
        import offline_voice
        model = args.model or str(offline_voice.MODEL)
        speak, label = piper_audio(model), Path(model).name
    results = score(speak)
    for sentence, heard, value in results:
        print(f'{value:.2f}  {sentence:45} heard: {heard}')
    mean = sum(v for *_, v in results) / len(results)
    usable = sum(v >= .7 for *_, v in results)
    print(f'\n{label}: mean {mean:.2f}, {usable}/{len(results)} understood (>= 0.7)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
