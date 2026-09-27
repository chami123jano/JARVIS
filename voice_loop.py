"""Microphone capture, speech detection and Sinhala transcription for JARVIS.

Runs outside the browser on purpose: Windows has no Sinhala speech voice and the
browser's recogniser is fixed to one language, so both halves of voice had to move
into Python. This module owns the listening half.

    python voice_loop.py --once          record one utterance and print it
    python voice_loop.py --once --lang en
    python voice_loop.py --devices       list microphones
    python voice_loop.py --check         verify the stack without recording

Speech detection uses Silero VAD as a plain ONNX model rather than the pip package,
which would drag in PyTorch (~2.5 GB) for a 2 MB model.
"""
import argparse
import os
import queue
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def register_cuda_libraries():
    """Let CTranslate2 find cuBLAS and cuDNN when pip supplied them.

    The nvidia-* wheels drop their DLLs inside site-packages rather than on PATH,
    so on Windows CTranslate2 fails at the first encode with
    "Library cublas64_12.dll is not found". Must run before the first inference.

    Both mechanisms are needed. os.add_dll_directory only covers DLLs that Python
    itself loads; CTranslate2's native library calls LoadLibrary directly, and that
    uses the classic search order, which reads PATH. Registering only the former
    looks correct and still fails.
    """
    added = []
    for parent in list(sys.path):
        nvidia = Path(parent) / 'nvidia'
        if not nvidia.is_dir():
            continue
        for folder in sorted(nvidia.glob('*/bin')):
            if not any(folder.glob('*.dll')):
                continue
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(str(folder))
                except OSError:
                    pass
            if str(folder) not in os.environ.get('PATH', ''):
                os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
            added.append(str(folder))
    return added


register_cuda_libraries()
MODELS = ROOT / 'data' / 'voice'
VAD_PATH = MODELS / 'silero_vad.onnx'
VAD_URL = 'https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx'

SAMPLE_RATE = 16000
FRAME = 512                     # Silero expects exactly 512 samples at 16 kHz
SPEECH_ON = .55                 # probability to treat a frame as speech
SPEECH_OFF = .35                # hysteresis, so a brief dip does not end the turn
SILENCE_END = .8                # seconds of quiet that means "they stopped talking"
MIN_SPEECH = .3                 # ignore a cough or a door
MAX_UTTERANCE = 30              # hard ceiling, seconds

WHISPER_MODEL = 'large-v3-turbo'

# Whisper invents stock phrases when handed near-silence: a clean one-second silence
# transcribes as "Thank you." Anything this short that matches is discarded rather than
# acted on, because a hallucinated command is worse than a missed one.
HALLUCINATIONS = {
    'thank you', 'thanks for watching', 'thank you for watching', 'thanks for watching!',
    'thank you.', 'you', 'bye', 'okay', 'ok', 'uh', 'um', 'hmm', 'so', 'yeah',
    'subtitles by the amara.org community', 'please subscribe', 'the end',
    'ස්තුතියි', 'ඔව්',
}
HALLUCINATION_MAX_SECONDS = 2.0

# Base Whisper has seen little Sinhala, so it often hears the words correctly but
# spells them in Latin, Devanagari, Gujarati or Malayalam instead of Sinhala. Feeding
# it a Sinhala sentence first biases the decoder toward Sinhala script.
SINHALA_PROMPT = 'දැන් වෙලාව කීයද? අද කාලගුණය කොහොමද? අම්මාට මැසේජ් එකක් යවන්න. මිනිත්තු දහයකින් මතක් කරන්න.'


def ensure_vad():
    """Fetch the 2 MB VAD model once."""
    if VAD_PATH.exists() and VAD_PATH.stat().st_size > 1_000_000:
        return VAD_PATH
    MODELS.mkdir(parents=True, exist_ok=True)
    print(f'Downloading Silero VAD to {VAD_PATH} ...', flush=True)
    temporary = VAD_PATH.with_suffix('.part')
    with urllib.request.urlopen(VAD_URL, timeout=60) as response, temporary.open('wb') as output:
        output.write(response.read())
    temporary.replace(VAD_PATH)
    print(f'VAD ready ({VAD_PATH.stat().st_size / 1024:.0f} KB)', flush=True)
    return VAD_PATH


class Vad:
    """Silero voice-activity detection over onnxruntime, no PyTorch."""

    def __init__(self):
        import numpy
        import onnxruntime
        self.numpy = numpy
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(
            str(ensure_vad()), sess_options=options, providers=['CPUExecutionProvider'])
        self.inputs = {value.name for value in self.session.get_inputs()}
        self.reset()

    def reset(self):
        self.state = self.numpy.zeros((2, 1, 128), dtype=self.numpy.float32)

    def probability(self, frame):
        """Speech probability for exactly one 512-sample frame."""
        feed = {'input': frame.reshape(1, -1).astype(self.numpy.float32)}
        if 'sr' in self.inputs:
            feed['sr'] = self.numpy.array(SAMPLE_RATE, dtype=self.numpy.int64)
        if 'state' in self.inputs:
            feed['state'] = self.state
        outputs = self.session.run(None, feed)
        if len(outputs) > 1 and getattr(outputs[1], 'shape', None) == self.state.shape:
            self.state = outputs[1]
        return float(self.numpy.asarray(outputs[0]).reshape(-1)[0])


class Ears:
    """Loads Whisper once, then transcribes short utterances."""

    def __init__(self, model=WHISPER_MODEL, device='auto'):
        from faster_whisper import WhisperModel
        self.device, self.compute = self.pick(device)
        started = time.monotonic()
        try:
            self.model = WhisperModel(model, device=self.device, compute_type=self.compute)
            self.warmup()
        except Exception as error:
            if self.device != 'cuda':
                raise
            # Constructing the model succeeds even when cuBLAS/cuDNN are missing: it only
            # fails at the first encode. The warmup above forces that to happen here,
            # where falling back to the CPU is still possible.
            print(f'GPU unusable ({str(error)[:140]})\nFalling back to the CPU.', flush=True)
            self.device, self.compute = 'cpu', 'int8'
            self.model = WhisperModel(model, device='cpu', compute_type='int8')
            self.warmup()
        self.load_seconds = time.monotonic() - started
        print(f'Whisper {model} ready on {self.device}/{self.compute} in {self.load_seconds:.1f}s', flush=True)

    def warmup(self):
        """Force one real inference so a broken GPU path fails now, not mid-command."""
        import numpy
        segments, _ = self.model.transcribe(numpy.zeros(SAMPLE_RATE, dtype=numpy.float32),
                                            language='en', beam_size=1)
        list(segments)

    @staticmethod
    def pick(device):
        if device == 'cpu':
            return 'cpu', 'int8'
        if device == 'cuda':
            return 'cuda', 'int8_float16'
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return 'cuda', 'int8_float16'
        except Exception:
            pass
        return 'cpu', 'int8'

    def transcribe(self, audio, language='si', prompt=None):
        """Return (text, seconds, info). language=None lets Whisper decide."""
        started = time.monotonic()
        if prompt is None and language == 'si':
            prompt = SINHALA_PROMPT
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=5, vad_filter=False,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            # Sinhala commands are short; this keeps it from inventing filler.
            no_speech_threshold=.5, log_prob_threshold=-1.0)
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        seconds = len(audio) / SAMPLE_RATE if hasattr(audio, '__len__') else 0
        if seconds <= HALLUCINATION_MAX_SECONDS and text.lower().strip(' .!?') in HALLUCINATIONS:
            text = ''
        return text, time.monotonic() - started, info


def record_utterance(vad, device=None, timeout=20):
    """Capture from the microphone until the speaker stops. Returns float32 audio."""
    import numpy
    import sounddevice

    blocks = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            print(f'audio: {status}', file=sys.stderr)
        blocks.put(indata[:, 0].copy())

    vad.reset()
    collected, speech, quiet, speaking = [], 0.0, 0.0, False
    frame_seconds = FRAME / SAMPLE_RATE
    deadline = time.monotonic() + timeout

    with sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                                 blocksize=FRAME, device=device, callback=callback):
        print('Listening...', flush=True)
        while True:
            if time.monotonic() > deadline and not speaking:
                return None
            try:
                frame = blocks.get(timeout=1)
            except queue.Empty:
                continue
            probability = vad.probability(frame)
            if probability >= SPEECH_ON:
                if not speaking:
                    speaking = True
                    print('Speech detected', flush=True)
                speech += frame_seconds
                quiet = 0.0
                collected.append(frame)
            elif speaking:
                collected.append(frame)
                if probability < SPEECH_OFF:
                    quiet += frame_seconds
                    if quiet >= SILENCE_END:
                        break
                else:
                    quiet = 0.0
            if speaking and speech >= MAX_UTTERANCE:
                break

    if speech < MIN_SPEECH:
        return None
    return numpy.concatenate(collected)


def save_wav(path, audio):
    """Write 16-bit PCM so recordings can be re-tested against other models later.

    Keeping the audio is the difference between trying a new model in a minute and
    asking the user to record everything again.
    """
    import wave
    import numpy
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = (numpy.clip(audio, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    return path


def load_wav(path):
    import wave
    import numpy
    with wave.open(str(path), 'rb') as source:
        frames = source.readframes(source.getnframes())
    return numpy.frombuffer(frames, dtype='<i2').astype('float32') / 32768.0


def record_push(device=None, max_seconds=60):
    """Record from Enter to Enter, with no speech detection involved.

    Used for the transcript session and as the fallback whenever VAD tuning is not
    cooperating: capture is the part that must never be in doubt.
    """
    import numpy
    import sounddevice

    blocks = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            print(f'  audio status: {status}', file=sys.stderr)
        blocks.put(indata[:, 0].copy())

    collected = []
    with sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                                 blocksize=FRAME, device=device, callback=callback):
        input('     RECORDING - speak now, then press Enter to stop... ')
        while True:
            try:
                collected.append(blocks.get_nowait())
            except queue.Empty:
                break

    if not collected:
        return None
    audio = numpy.concatenate(collected)
    if len(audio) < SAMPLE_RATE * .2:
        return None
    return audio[:SAMPLE_RATE * max_seconds]


def monitor(vad, device=None, seconds=15):
    """Live meter: is audio arriving, and does the VAD think it is speech?

    The decisive diagnostic when nothing gets recorded. A flat peak near 0.000 means
    the microphone is not reaching us; a healthy peak with a low probability means the
    VAD threshold is wrong for this microphone.
    """
    import numpy
    import sounddevice

    blocks = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            print(f'  audio status: {status}', file=sys.stderr)
        blocks.put(indata[:, 0].copy())

    name = sounddevice.query_devices(device if device is not None else
                                    sounddevice.default.device[0])['name']
    print(f'Device: {name}')
    print(f'Speak normally for {seconds} seconds.\n')
    print('  peak    rms      vad    speech?')

    vad.reset()
    peaks, probabilities = [], []
    frames_per_line = int(.25 * SAMPLE_RATE / FRAME)
    batch = []
    with sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                                 blocksize=FRAME, device=device, callback=callback):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                frame = blocks.get(timeout=1)
            except queue.Empty:
                print('  (no audio blocks arriving at all)')
                continue
            probability = vad.probability(frame)
            batch.append((float(numpy.abs(frame).max()), float(numpy.sqrt((frame ** 2).mean())), probability))
            if len(batch) >= frames_per_line:
                peak = max(item[0] for item in batch)
                rms = max(item[1] for item in batch)
                best = max(item[2] for item in batch)
                bar = '#' * min(40, int(peak * 80))
                print(f'  {peak:.3f}   {rms:.3f}    {best:.2f}   {"SPEECH" if best >= SPEECH_ON else "      "}  {bar}')
                peaks.append(peak)
                probabilities.append(best)
                batch = []

    if not peaks:
        print('\nNo audio arrived. The microphone is not reachable from Python.')
        return
    print(f'\nloudest peak     {max(peaks):.3f}   (speech normally peaks above 0.05)')
    print(f'highest vad      {max(probabilities):.2f}   (threshold is {SPEECH_ON})')
    print(f'frames over {SPEECH_ON}  {sum(1 for p in probabilities if p >= SPEECH_ON)} of {len(probabilities)}')
    if max(peaks) < .01:
        print('\nDIAGNOSIS: the microphone is delivering near-silence. Wrong input device,')
        print('or Windows microphone access is blocked. Try --devices and pass --device N.')
    elif max(probabilities) < SPEECH_ON:
        print(f'\nDIAGNOSIS: audio is arriving but the VAD never crossed {SPEECH_ON}.')
        print('Lower it with --speech-threshold, or the microphone level is very low.')
    else:
        print('\nDIAGNOSIS: audio and speech detection both look healthy.')


def session(ears, vad, device, language, count, out_path, push=False):
    """Record a numbered batch of spoken commands and log exactly what Whisper heard.

    The log is the raw material for the Sinhala alias table: the transcripts Whisper
    actually produces matter more than the words that were said, mistakes included.
    """
    import json

    entries = []
    if out_path.exists():
        try:
            entries = json.loads(out_path.read_text(encoding='utf-8'))
        except ValueError:
            entries = []
    start_at = len(entries) + 1

    print(f'\nRecording {count} commands, starting at number {start_at}.')
    if push:
        print('Press Enter to start recording, speak, then press Enter again to stop.')
    else:
        print('Speak naturally, then pause. Press Ctrl+C to stop early.')
    print(f'Saving to {out_path}\n')

    try:
        for index in range(start_at, start_at + count):
            if push:
                input(f'[{index}] Press Enter to start... ')
                audio = record_push(device)
            else:
                input(f'[{index}] Press Enter, then speak... ')
                audio = record_utterance(vad, device, timeout=25)
            if audio is None:
                print('     nothing heard - repeating this one\n')
                continue
            seconds = len(audio) / SAMPLE_RATE
            wav = save_wav(out_path.parent / 'clips' / f'{index:03d}.wav', audio)
            text, elapsed, info = ears.transcribe(audio, language)
            detected = getattr(info, 'language', language) or 'unknown'
            probability = round(float(getattr(info, 'language_probability', 0) or 0), 2)
            print(f'     heard ({seconds:.1f}s audio, {elapsed:.2f}s, {detected} p={probability}):')
            print(f'     >>> {text or "(nothing recognised)"}\n')
            entries.append({'number': index, 'transcript': text, 'language': detected,
                            'language_probability': probability,
                            'audio_seconds': round(seconds, 2),
                            'transcribe_seconds': round(elapsed, 2),
                            'clip': str(wav.relative_to(ROOT)).replace('\\', '/')})
            out_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    except KeyboardInterrupt:
        print('\nStopped early.')

    print(f'\n{len(entries)} transcripts saved to {out_path}')
    recognised = sum(1 for entry in entries if entry['transcript'])
    print(f'{recognised} of {len(entries)} produced text.')
    if entries:
        average = sum(e['transcribe_seconds'] for e in entries) / len(entries)
        print(f'Average transcription time: {average:.2f}s')


def main():
    global SPEECH_ON, SPEECH_OFF
    parser = argparse.ArgumentParser(description='JARVIS microphone and transcription loop')
    parser.add_argument('--once', action='store_true', help='record a single utterance and print it')
    parser.add_argument('--session', type=int, metavar='N',
                        help='record N numbered commands and log every transcript')
    parser.add_argument('--out', default='data/voice/transcripts.json', help='session log path')
    parser.add_argument('--push', action='store_true',
                        help='push to talk: Enter to start, Enter to stop, no speech detection')
    parser.add_argument('--devices', action='store_true', help='list input devices')
    parser.add_argument('--check', action='store_true', help='verify the stack without recording')
    parser.add_argument('--monitor', nargs='?', type=int, const=15, metavar='SECONDS',
                        help='live microphone and speech-detection meter')
    parser.add_argument('--speech-threshold', type=float, default=None,
                        help=f'VAD probability to count as speech (default {SPEECH_ON})')
    parser.add_argument('--lang', default='si', help="language code, or 'auto' to detect")
    parser.add_argument('--device', type=int, default=None, help='input device number')
    parser.add_argument('--whisper-device', default='auto', choices=['auto', 'cuda', 'cpu'])
    parser.add_argument('--model', default=WHISPER_MODEL)
    args = parser.parse_args()

    if args.speech_threshold is not None:
        SPEECH_ON = args.speech_threshold
        SPEECH_OFF = max(.05, args.speech_threshold - .2)
        print(f'Speech threshold set to {SPEECH_ON} (off at {SPEECH_OFF:.2f})')

    if args.monitor:
        return monitor(Vad(), args.device, args.monitor)

    if args.devices:
        import sounddevice
        for index, info in enumerate(sounddevice.query_devices()):
            if info['max_input_channels'] > 0:
                default = ' (default)' if index == sounddevice.default.device[0] else ''
                print(f"{index:3}  {info['name']}{default}")
        return

    if args.check:
        import sounddevice
        print('sounddevice   ', sounddevice.get_portaudio_version()[1])
        print('default input ', sounddevice.query_devices(kind='input')['name'])
        vad = Vad()
        import numpy
        print('vad silence   ', f'{vad.probability(numpy.zeros(FRAME, dtype=numpy.float32)):.3f} (expect near 0)')
        ears = Ears(args.model, args.whisper_device)
        text, seconds, _ = ears.transcribe(numpy.zeros(SAMPLE_RATE, dtype=numpy.float32), language=None)
        print('whisper silence', f'{seconds:.2f}s ->', repr(text))
        print('OK: microphone, VAD and Whisper all load.')
        return

    language = None if args.lang == 'auto' else args.lang
    vad = Vad()
    ears = Ears(args.model, args.whisper_device)

    if args.session:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return session(ears, vad, args.device, language, args.session, out_path, args.push)

    while True:
        audio = record_utterance(vad, args.device)
        if audio is None:
            print('(nothing heard)', flush=True)
            if args.once:
                return
            continue
        seconds = len(audio) / SAMPLE_RATE
        text, elapsed, info = ears.transcribe(audio, language)
        detected = getattr(info, 'language', language)
        print(f'\n[{seconds:.1f}s audio, {elapsed:.2f}s transcribe, lang={detected}]')
        print(text or '(no words recognised)', flush=True)
        if args.once:
            return


if __name__ == '__main__':
    main()
