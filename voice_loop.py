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
import threading
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

# Kept only so tests/whisper_bench.py can re-measure it. Do not use it: priming Whisper
# with Sinhala scored WORST of every configuration tried (0 of 20 commands recognised,
# against 19 of 20 without it). It pushes the weak Sinhala decoder into repetition loops.
SINHALA_PROMPT = 'දැන් වෙලාව කීයද? අද කාලගුණය කොහොමද? අම්මාට මැසේජ් එකක් යවන්න. මිනිත්තු දහයකින් මතක් කරන්න.'

# Measured over 20 real Sinhala commands, scoring which command was recognised rather
# than whether the words were spelled right:
#
#   language=None (auto)  19/20 correct, 0 wrong    <- chosen
#   language='si'          8/20 correct, 1 wrong
#   language='si' + prompt  0/20 correct, 3 wrong
#   vad_filter=True        2/20 correct, 3 wrong
#
# Forcing Sinhala is counter-productive. Left to choose, Whisper writes Sinhala speech in
# Tamil or Latin script with the sounds intact, and sinhala.py matches on sound. Letting
# it pick the script it is confident in beats forcing the one we want.
DEFAULT_LANGUAGE = None
TEMPERATURE_FALLBACK = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


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


VAD_CONTEXT = 64        # samples of the previous frame that v5 needs prepended


class Vad:
    """Silero voice-activity detection over onnxruntime, no PyTorch.

    Version 5 of the model does NOT take a bare 512-sample frame. It expects the last 64
    samples of the previous frame prepended, so 576 samples in total, and silently
    returns near-zero for everything without them. Measured on real recordings of speech:
    0.097 peak without the context, 1.000 with it. That looked exactly like a badly
    chosen threshold and was not one.
    """

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
        self.context = self.numpy.zeros(VAD_CONTEXT, dtype=self.numpy.float32)

    def probability(self, frame):
        """Speech probability for exactly one 512-sample frame."""
        frame = self.numpy.ascontiguousarray(frame, dtype=self.numpy.float32)
        payload = self.numpy.concatenate([self.context, frame])
        feed = {'input': payload.reshape(1, -1)}
        if 'sr' in self.inputs:
            feed['sr'] = self.numpy.array(SAMPLE_RATE, dtype=self.numpy.int64)
        if 'state' in self.inputs:
            feed['state'] = self.state
        outputs = self.session.run(None, feed)
        if len(outputs) > 1 and getattr(outputs[1], 'shape', None) == self.state.shape:
            self.state = outputs[1]
        self.context = frame[-VAD_CONTEXT:]
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

    def transcribe(self, audio, language=DEFAULT_LANGUAGE, prompt=None):
        """Return (text, seconds, info). language=None lets Whisper choose the script."""
        started = time.monotonic()
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=5, vad_filter=False,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            # The temperature ladder is Whisper's own defence against repetition loops;
            # pinning a single temperature disables it and produces "වවවවවව" output.
            temperature=TEMPERATURE_FALLBACK, compression_ratio_threshold=2.4,
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


WAKE_MODEL = 'hey_jarvis'
WAKE_FRAME = 1280               # 80 ms at 16 kHz, what openWakeWord expects
# Set from a real 30-second recording on this microphone, not guessed:
#   "hey jarvis"      0.950, 0.868, 0.863
#   everything else   0.474 at the 99th percentile
# 0.6 sits in the gap with margin on both sides. Re-run --wake-test after changing
# microphone or room.
WAKE_THRESHOLD = .6
WAKE_COOLDOWN = 1.5             # seconds before the same word can fire again
FOLLOW_UP_SECONDS = 8           # after a reply, listen again without the wake word


class WakeWord:
    """openWakeWord's pretrained 'hey jarvis', over onnxruntime.

    The tflite backend it defaults to has no Windows wheel, so the framework has to be
    named explicitly or loading fails with an unhelpful import error.
    """

    def __init__(self, name=WAKE_MODEL, threshold=WAKE_THRESHOLD):
        from openwakeword.model import Model
        try:
            self.model = Model(wakeword_models=[name], inference_framework='onnx')
        except Exception:
            import openwakeword
            openwakeword.utils.download_models()
            self.model = Model(wakeword_models=[name], inference_framework='onnx')
        self.name = name
        self.threshold = threshold
        self.last_fired = 0.0
        self.peak = 0.0

    def feed(self, frame):
        """frame: 1280 float32 samples. True when the wake word just fired."""
        import numpy
        samples = (numpy.clip(frame, -1, 1) * 32767).astype('int16')
        scores = self.model.predict(samples)
        score = float(max(scores.values()))
        self.peak = max(self.peak, score)
        if score < self.threshold:
            return False
        now = time.monotonic()
        if now - self.last_fired < WAKE_COOLDOWN:
            return False
        self.last_fired = now
        self.model.reset()
        return True


def beep(frequency=880, seconds=.15, volume=.5):
    """Short acknowledging tone, so you know it heard its name.

    Failures are reported rather than swallowed: a silent beep is indistinguishable from
    a wake word that never fired, which makes the whole loop impossible to debug.
    """
    try:
        import numpy
        import sounddevice
        t = numpy.linspace(0, seconds, int(SAMPLE_RATE * seconds), endpoint=False)
        tone = (numpy.sin(2 * numpy.pi * frequency * t) * volume).astype('float32')
        fade = int(len(tone) * .15)
        tone[:fade] *= numpy.linspace(0, 1, fade)
        tone[-fade:] *= numpy.linspace(1, 0, fade)
        sounddevice.play(tone, SAMPLE_RATE, blocking=True)
        return True
    except Exception as error:
        print(f'  (beep failed: {type(error).__name__}: {str(error)[:90]})', file=sys.stderr)
        return False


class Listener:
    """Always-on loop: wake word, then command, then answer, then follow-up.

    One audio stream serves both the wake word and the recorder. Opening a fresh stream
    per utterance costs hundreds of milliseconds and sometimes fails outright while the
    device is still releasing, which is long enough to clip the first word.
    """

    def __init__(self, vad, ears, device=None, threshold=WAKE_THRESHOLD, language=None):
        self.vad, self.ears, self.device = vad, ears, device
        self.language = language
        self.wake = WakeWord(threshold=threshold)
        self.muted = threading.Event()
        self.running = threading.Event()
        self.state = 'idle'
        self.on_state = None
        self.buffer = None

    def set_state(self, state):
        self.state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    def log_utterance(self, audio, text, language):
        """Keep every live command as audio plus transcript.

        Real use finds phrasings no test session contains, and a misheard command is
        only fixable if the recording still exists. Never fails the request.
        """
        try:
            import json
            folder = ROOT / 'data' / 'voice' / 'live'
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            save_wav(folder / f'{stamp}.wav', audio)
            line = json.dumps({'time': stamp, 'text': text, 'language': language,
                               'seconds': round(len(audio) / SAMPLE_RATE, 2)},
                              ensure_ascii=False)
            with (folder / 'log.jsonl').open('a', encoding='utf-8') as output:
                output.write(line + '\n')
        except Exception:
            pass

    def stream(self):
        import numpy
        import sounddevice
        self.buffer = numpy.zeros(0, dtype='float32')
        blocks = queue.Queue()

        def callback(indata, _frames, _time, status):
            if status:
                print(f'audio: {status}', file=sys.stderr)
            blocks.put(indata[:, 0].copy())

        return sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                                       blocksize=512, device=self.device,
                                       callback=callback), blocks

    def flush(self, blocks):
        """Throw away audio captured while JARVIS was talking or beeping.

        The input stream keeps filling during playback, so without this the next read
        returns JARVIS's own voice and transcribes it as a new command. That is the
        feedback loop, and there is no echo cancellation to rescue us from it.
        """
        import numpy
        dropped = 0
        while True:
            try:
                dropped += len(blocks.get_nowait())
            except queue.Empty:
                break
        self.buffer = numpy.zeros(0, dtype='float32')
        self.vad.reset()
        return dropped

    def take(self, blocks, size):
        """Pull exactly `size` samples from the rolling buffer, refilling as needed."""
        import numpy
        while len(self.buffer) < size:
            try:
                self.buffer = numpy.concatenate([self.buffer, blocks.get(timeout=1)])
            except queue.Empty:
                return None
        chunk, self.buffer = self.buffer[:size], self.buffer[size:]
        return chunk

    def record_command(self, blocks, timeout=12):
        """Record until the speaker stops, reusing the open stream."""
        import numpy
        self.vad.reset()
        collected, speech, quiet, speaking = [], 0.0, 0.0, False
        frame_seconds = FRAME / SAMPLE_RATE
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.take(blocks, FRAME)
            if frame is None:
                break
            probability = self.vad.probability(frame)
            if probability >= SPEECH_ON:
                speaking = True
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
        if speech < MIN_SPEECH or not collected:
            return None
        return numpy.concatenate(collected)

    def handle(self, blocks, on_command, prompt_beep=True):
        """One command: record, transcribe, act, speak."""
        if prompt_beep:
            beep()
        # Discard the beep and anything else already buffered, so recording starts clean.
        self.flush(blocks)
        self.set_state('listening')
        audio = self.record_command(blocks)
        if audio is None:
            self.set_state('idle')
            return False
        self.set_state('thinking')
        text, elapsed, info = self.ears.transcribe(audio, self.language)
        detected = getattr(info, 'language', '?')
        print(f'  heard [{len(audio)/SAMPLE_RATE:.1f}s, {elapsed:.2f}s, {detected}]: {text}', flush=True)
        try:
            from sinhala import normalize
            print(f'  phonetic: {normalize(text)}', flush=True)
        except Exception:
            pass
        self.log_utterance(audio, text, detected)
        if not text.strip():
            self.set_state('idle')
            return False
        try:
            on_command(text)
        except Exception as error:
            print(f'  command failed: {error}', file=sys.stderr)
        # on_command speaks the reply, which the microphone hears. Drop all of it before
        # the follow-up window opens, or JARVIS answers itself.
        dropped = self.flush(blocks)
        if dropped:
            print(f'  (discarded {dropped/SAMPLE_RATE:.1f}s of own audio)', flush=True)
        self.set_state('idle')
        return True

    def busy_speaking(self):
        """Is JARVIS talking right now?"""
        try:
            import speech
            return speech.SPEAKER.speaking
        except Exception:
            return False

    def run(self, on_command, on_barge_in=None):
        """Listen until stopped. on_command(text) does the work and may speak.

        on_barge_in enables listening during playback so the wake word can cut JARVIS
        off. It is off by default because without echo cancellation the microphone hears
        the speakers, and a loop that answers its own voice is worse than one you have
        to wait for.
        """
        self.running.set()
        stream, blocks = self.stream()
        print(f'Listening for "{self.wake.name.replace("_", " ")}". Ctrl+C to stop.', flush=True)
        with stream:
            follow_until = 0.0
            while self.running.is_set():
                frame = self.take(blocks, WAKE_FRAME)
                if frame is None:
                    continue
                if self.muted.is_set():
                    continue
                if self.busy_speaking() and not on_barge_in:
                    # Half duplex: ignore everything while talking, then start clean.
                    continue

                # Follow-up window: a question straight after a reply needs no wake word.
                if time.monotonic() < follow_until:
                    speech_now = any(
                        self.vad.probability(frame[start:start + FRAME]) >= SPEECH_ON
                        for start in range(0, WAKE_FRAME - FRAME + 1, FRAME))
                    if speech_now:
                        self.buffer = None if self.buffer is None else self.buffer
                        if self.handle(blocks, on_command, prompt_beep=False):
                            follow_until = time.monotonic() + FOLLOW_UP_SECONDS
                        continue

                if self.wake.feed(frame):
                    print('\n* wake word *', flush=True)
                    if on_barge_in:
                        on_barge_in()          # cut off whatever JARVIS is saying
                    if self.handle(blocks, on_command):
                        follow_until = time.monotonic() + FOLLOW_UP_SECONDS

    def stop(self):
        self.running.clear()


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


def wake_test(device=None, threshold=WAKE_THRESHOLD, seconds=30):
    """Report wake word scores without acting, so the threshold can be set from data.

    Say "hey jarvis" a few times, then stay quiet and talk normally. The gap between the
    two sets of numbers is the threshold; guessing it produces either a wake word that
    ignores you or one that fires at the television.
    """
    import numpy
    import sounddevice

    wake = WakeWord(threshold=threshold)
    blocks = queue.Queue()

    def callback(indata, _frames, _time, status):
        blocks.put(indata[:, 0].copy())

    print(f'Say "hey jarvis" a few times over the next {seconds}s, then talk normally.')
    print(f'Current threshold: {threshold}\n')
    buffer = numpy.zeros(0, dtype='float32')
    hits, peaks = [], []
    with sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                                 blocksize=512, device=device, callback=callback):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                buffer = numpy.concatenate([buffer, blocks.get(timeout=1)])
            except queue.Empty:
                continue
            while len(buffer) >= WAKE_FRAME:
                frame, buffer = buffer[:WAKE_FRAME], buffer[WAKE_FRAME:]
                samples = (numpy.clip(frame, -1, 1) * 32767).astype('int16')
                score = float(max(wake.model.predict(samples).values()))
                peaks.append(score)
                if score >= .1:
                    bar = '#' * int(score * 40)
                    marker = '  <-- WOULD FIRE' if score >= threshold else ''
                    print(f'  {score:.3f} {bar}{marker}', flush=True)
                if score >= threshold:
                    hits.append(score)
                    wake.model.reset()

    if not peaks:
        print('\nNo audio arrived.')
        return 1
    peaks.sort()
    loud = [p for p in peaks if p >= .1]
    print(f'\nframes analysed  {len(peaks)}')
    print(f'would have fired {len(hits)} times at threshold {threshold}')
    print(f'highest score    {peaks[-1]:.3f}')
    print(f'99th percentile  {peaks[int(len(peaks) * .99)]:.3f}')
    print(f'frames above 0.1 {len(loud)}')
    if not hits:
        print('\nThe wake word never fired. Lower --wake-threshold towards the highest')
        print('score above, or say "hey JARVIS" as one phrase rather than two words.')
    elif len(hits) > 12:
        print('\nThat is a lot of triggers. Raise --wake-threshold to cut false alarms.')
    else:
        print('\nThreshold looks reasonable.')
    return 0


# A matched command becomes a clear English instruction. The model still runs, so it can
# phrase the answer and use its tools, but it is no longer trying to read Tamil script.
COMMAND_PROMPTS = {
    'time': 'Tell me the current time.',
    'weather': 'What is the weather in Colombo right now?',
    'system_status': 'Report the system status using the system_status tool.',
    'battery': 'Report the battery level using the system_status tool.',
    'send_message': 'The user wants to send a message. Ask who it should go to and what it should say.',
    'reminder': 'Set a reminder.',
    'play_music': 'Play some music.',
    'volume_up': 'Turn the volume up.',
    'volume_down': 'Turn the volume down.',
    'open_app': 'Open the application the user asked for.',
    'dollar_rate': 'What is the US dollar to Sri Lankan rupee rate today?',
    'save_note': 'Save a note. Ask what it should say if it is not clear.',
    'list_notes': 'List my saved notes using list_records.',
    'news': 'What is in the news today in Sri Lanka?',
    'email': 'Check my email.',
    'screenshot': 'Take a screenshot.',
    'lock_pc': 'Lock the computer.',
    'calculate': 'Work out the calculation the user asked for.',
    'capabilities': 'Briefly say what you can help with.',
    'stop': 'Acknowledge briefly and stop talking.',
}


def command_prompt(decision):
    """Turn a matched command into an instruction the model can act on."""
    instruction = COMMAND_PROMPTS.get(decision['command'], decision['text'])
    if decision['command'] == 'reminder' and decision.get('seconds'):
        minutes = decision['seconds'] / 60
        instruction = (f'Set a reminder {decision["seconds"]} seconds from now '
                       f'(about {minutes:.0f} minutes). Ask what it is for if unclear.')
    # The original words go along too, so the model can pick up any detail the command
    # table does not model, and can reply in the language it was spoken in.
    return (f'{instruction}\n\n(The user said, as transcribed: "{decision["text"]}". '
            'Reply in Sinhala if that looks like Sinhala.)')


def build_brain(args):
    """Shared setup for the talk and wake loops: agent, router, speech."""
    import router as command_router
    import speech
    from agent import Agent
    from assistant_core import ROOT as CORE_ROOT, Store, Tools

    store = Store(CORE_ROOT / 'data' / 'jarvis.db')
    tools = Tools(store, store.config('workspace', str(CORE_ROOT / 'workspace')))
    agent = Agent(store, tools)
    if not store.config('model', ''):
        raise SystemExit('No model configured. Choose one in Settings first.')

    def respond(text, speak=True, on_state=None):
        """Route, act, and say the answer. Returns the reply text."""
        decision = command_router.route(text)
        print(f'  router: {decision["action"]} {decision.get("command")} '
              f'({decision.get("score")})', flush=True)
        if decision['action'] == 'unclear':
            reply = 'මට තේරුණේ නැහැ. ආයෙත් කියන්න.'      # I didn't catch that, say again
        else:
            prompt = command_prompt(decision) if decision['action'] == 'command' else text
            job_id = agent.start(prompt)
            for _ in range(1200):
                job = agent.snapshot(job_id)
                if job['state'] not in ('running', 'stopping'):
                    break
                time.sleep(.25)
            else:
                agent.stop(job_id)
                job = agent.snapshot(job_id)
            reply = job['answer']
        print(f'  JARVIS: {reply[:160]}', flush=True)
        if speak:
            if on_state:
                on_state('speaking')
            speech.SPEAKER.say(reply)
        return reply

    return respond, speech


def talk_loop(args):
    """Push to talk: press Enter, speak, press Enter, hear the answer.

    No wake word and no speech detection, so JARVIS cannot hear itself and cannot mistime
    the end of a sentence. The most reliable way to use it, and the right fallback
    whenever the always-listening loop misbehaves.
    """
    respond, _ = build_brain(args)
    ears = Ears(args.model, args.whisper_device)
    language = None if args.lang == 'auto' else args.lang

    print('\nPush to talk. Enter to start, Enter again to stop. Ctrl+C to quit.\n')
    try:
        while True:
            input('> Press Enter to talk... ')
            audio = record_push(args.device)
            if audio is None:
                print('  (nothing recorded)\n')
                continue
            text, elapsed, info = ears.transcribe(audio, language)
            detected = getattr(info, 'language', '?')
            print(f'  heard [{len(audio)/SAMPLE_RATE:.1f}s, {elapsed:.2f}s, {detected}]: {text}')
            try:
                from sinhala import normalize
                print(f'  phonetic: {normalize(text)}')
            except Exception:
                pass
            if not text.strip():
                print('  (nothing recognised)\n')
                continue
            respond(text, speak=not args.silent)
            print()
    except KeyboardInterrupt:
        print('\nStopped.')
    return 0


def wake_loop(args):
    """Always-listening JARVIS: wake word, Sinhala command, spoken Sinhala reply."""
    respond, speech = build_brain(args)
    vad = Vad()
    ears = Ears(args.model, args.whisper_device)
    listener = Listener(vad, ears, args.device, args.wake_threshold,
                        None if args.lang == 'auto' else args.lang)

    def on_command(text):
        respond(text, speak=not args.silent, on_state=listener.set_state)

    listener.on_state = lambda state: print(f'  [{state}]', flush=True) if args.verbose else None
    # Barge-in means listening while the speakers are playing, which without echo
    # cancellation means hearing ourselves. Opt in only.
    barge_in = speech.SPEAKER.stop if args.barge_in else None
    if not barge_in:
        print('(half duplex: not listening while speaking. --barge-in to change)')
    try:
        listener.run(on_command, on_barge_in=barge_in)
    except KeyboardInterrupt:
        print('\nStopped.')
    return 0


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
    parser.add_argument('--talk', action='store_true',
                        help='push to talk conversation: Enter, speak, Enter, hear the answer')
    parser.add_argument('--wake', action='store_true',
                        help='always listening: wake on "hey jarvis", then act and speak')
    parser.add_argument('--barge-in', action='store_true',
                        help='keep listening while speaking so the wake word interrupts; '
                             'without echo cancellation this can hear itself')
    parser.add_argument('--wake-threshold', type=float, default=WAKE_THRESHOLD,
                        help=f'wake word sensitivity, lower is easier (default {WAKE_THRESHOLD})')
    parser.add_argument('--wake-test', nargs='?', type=int, const=30, metavar='SECONDS',
                        help='report wake word scores without acting, to tune the threshold')
    parser.add_argument('--silent', action='store_true', help='with --wake, do not speak replies')
    parser.add_argument('--verbose', action='store_true', help='print state changes')
    parser.add_argument('--monitor', nargs='?', type=int, const=15, metavar='SECONDS',
                        help='live microphone and speech-detection meter')
    parser.add_argument('--speech-threshold', type=float, default=None,
                        help=f'VAD probability to count as speech (default {SPEECH_ON})')
    parser.add_argument('--lang', default='auto',
                        help="'auto' (default, measured best for Sinhala) or a language code")
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

    if args.wake_test:
        return wake_test(args.device, args.wake_threshold, args.wake_test)

    if args.talk:
        return talk_loop(args)

    if args.wake:
        return wake_loop(args)

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
