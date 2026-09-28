"""End-to-end health check for everything built so far.

Runs the real components against the real models and the real recordings, not mocks, and
prints one line per check. Intended to be run before starting a new day's work and after
anything that touches the model, the audio path or the matcher.

    .venv\\Scripts\\python.exe verify.py
    .venv\\Scripts\\python.exe verify.py --quick     skip the slow model calls
"""
import argparse
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS, FAIL, WARN = 'PASS', 'FAIL', 'WARN'
results = []


def check(day, name):
    """Decorator turning a function into a reported check. Return (status, detail)."""
    def wrap(function):
        def run():
            started = time.monotonic()
            try:
                status, detail = function()
            except Exception as error:
                status, detail = FAIL, f'{type(error).__name__}: {str(error)[:150]}'
            seconds = time.monotonic() - started
            results.append((day, name, status, detail, seconds))
            mark = {PASS: '[ ok ]', FAIL: '[FAIL]', WARN: '[warn]'}[status]
            print(f'{mark} {day:6} {name:38} {detail[:70]:70} {seconds:5.1f}s', flush=True)
            return status
        return run
    return wrap


# ---------------------------------------------------------------- environment

@check('setup', 'Ollama running, correct version')
def ollama_version():
    import requests
    response = requests.get('http://localhost:11434/api/version', timeout=5)
    version = response.json()['version']
    major, minor = (int(part) for part in version.split('.')[:2])
    if (major, minor) < (0, 34):
        return FAIL, f'{version} is too old for qwen3.5'
    return PASS, f'v{version}'


@check('setup', 'Models installed')
def models_present():
    import requests
    names = [m['name'] for m in requests.get('http://localhost:11434/api/tags', timeout=5).json()['models']]
    needed = ['qwen3.5:9b']
    missing = [n for n in needed if n not in names]
    if missing:
        return FAIL, f'missing {missing}'
    deep = 'gpt-oss:20b' in names
    return PASS, f'{len(names)} models' + ('' if deep else ', no deep model')


@check('setup', 'GPU visible')
def gpu():
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
    if not count:
        return WARN, 'no CUDA device; speech recognition will use the CPU'
    out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total',
                          '--format=csv,noheader'], capture_output=True, text=True, timeout=20)
    return PASS, out.stdout.strip() or f'{count} device'


@check('setup', 'Unit tests')
def unit_tests():
    # No top_level_dir: tests/ has no __init__.py, so treating it as a package fails with
    # "Start directory is not importable". This mirrors `python -m unittest discover -s tests`.
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / 'tests'))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if result.wasSuccessful():
        return PASS, f'{result.testsRun} tests'
    broken = [str(t).split()[0] for t, _ in result.failures + result.errors]
    return FAIL, f'{len(broken)} of {result.testsRun} failing: {", ".join(broken[:3])}'


# ---------------------------------------------------------------- day 1, the brain

def temp_agent(**config):
    import tempfile
    from agent import Agent
    from assistant_core import Store, Tools
    folder = tempfile.mkdtemp(prefix='jarvis-verify-')
    store = Store(Path(folder) / 'v.db')
    store.set_config('model', 'qwen3.5:9b')
    for key, value in config.items():
        store.set_config(key, value)
    return Agent(store, Tools(store, Path(folder) / 'workspace')), store


def await_job(agent, job_id, limit=400):
    partial_seen = False
    for _ in range(limit):
        job = agent.snapshot(job_id)
        if job.get('partial'):
            partial_seen = True
        if job['state'] not in ('running', 'stopping'):
            return job, partial_seen
        time.sleep(.25)
    agent.stop(job_id)
    raise TimeoutError('job did not finish')


@check('day 1', 'Native tool calling, several in one turn')
def multi_tool():
    agent, store = temp_agent()
    job, _ = await_job(agent, agent.start(
        'Use calculate for 137 * 23, then save_note titled Verify with the result as content.'))
    if job['state'] != 'done':
        return FAIL, job['answer'][:120]
    used = sum(1 for e in job['events'] if e['label'] == 'Tool completed')
    saved = any('3151' in r['content'] for r in store.records())
    if used < 2 or not saved:
        return FAIL, f'{used} tools, saved={saved}'
    return PASS, f'{used} tools, result stored'


@check('day 1', 'Streaming visible while running')
def streaming():
    agent, _ = temp_agent()
    job, partial = await_job(agent, agent.start(
        'In two sentences, what is a local AI assistant good for? Do not use tools.'))
    if job['state'] != 'done':
        return FAIL, job['answer'][:120]
    return (PASS, 'partial text observed') if partial else (WARN, 'finished before a partial was sampled')


@check('day 1', 'Thinking gate keeps simple replies fast')
def thinking_gate():
    agent, _ = temp_agent()
    started = time.monotonic()
    job, _ = await_job(agent, agent.start('Say hello in Sinhala.'))
    seconds = time.monotonic() - started
    if job['state'] != 'done':
        return FAIL, job['answer'][:120]
    if seconds > 45:
        return FAIL, f'{seconds:.1f}s - thinking mode is probably on'
    return PASS, f'{seconds:.1f}s (was 176s with thinking on)'


@check('day 1', 'Sinhala in, Sinhala out')
def sinhala_reply():
    agent, _ = temp_agent()
    job, _ = await_job(agent, agent.start('මට සිංහලෙන් කෙටි උත්තරයක් දෙන්න. ඔබට මොනවද කරන්න පුළුවන්?'))
    if job['state'] != 'done':
        return FAIL, job['answer'][:120]
    sinhala = sum(1 for c in job['answer'] if '඀' <= c <= '෿')
    if sinhala < 10:
        return FAIL, f'only {sinhala} Sinhala characters: {job["answer"][:70]}'
    return PASS, f'{sinhala} Sinhala characters'


@check('day 1', 'Deep request degrades safely')
def deep_fallback():
    agent, _ = temp_agent()
    model, think, deep = agent.brain('Think hard about this: what is 20 percent off 3000?')
    if not deep:
        return WARN, 'deep tier not selected; is gpt-oss installed?'
    job, _ = await_job(agent, agent.start(
        'Think hard about this: a shop sells an item at Rs. 2400 after a 20% discount. '
        'What was the original price?'), limit=1600)
    if job['state'] != 'done':
        return FAIL, job['answer'][:120]
    if '3000' not in job['answer'].replace(',', ''):
        return FAIL, f'wrong answer: {job["answer"][:80]}'
    fell_back = any('will not fit' in e['label'] for e in job['events'])
    return PASS, ('answered via fast brain (deep would not fit)' if fell_back
                  else 'answered by the deep model')


# ---------------------------------------------------------------- day 2, the ears

@check('day 2', 'Google Sinhala recogniser reachable')
def google_stt():
    import voice_loop
    ears = voice_loop.Hearing('auto')
    if ears.google is None:
        return WARN, 'unavailable; Whisper will be used, at lower Sinhala accuracy'
    clips = sorted((ROOT / 'data' / 'voice' / 'clips').glob('*.wav'))
    if not clips:
        return PASS, 'available, no clips to test against'
    text, seconds, _ = ears.google.transcribe(voice_loop.load_wav(clips[0]))
    sinhala = sum(1 for c in text if '඀' <= c <= '෿')
    if sinhala < 5:
        return FAIL, f'returned no Sinhala: {text[:60]!r}'
    if ears.whisper is not None:
        return WARN, 'Whisper was loaded although Google answered'
    return PASS, f'{seconds:.2f}s -> {text[:40]}'


@check('day 2', 'Whisper loads on the GPU')
def whisper_loads():
    import voice_loop
    ears = voice_loop.Ears()
    if ears.device != 'cuda':
        return WARN, f'running on {ears.device}; CUDA libraries may be missing'
    return PASS, f'{ears.device}/{ears.compute} in {ears.load_seconds:.1f}s'


@check('day 2', 'Voice activity detection')
def vad_check():
    import numpy
    import voice_loop
    vad = voice_loop.Vad()
    silence = vad.probability(numpy.zeros(voice_loop.FRAME, dtype='float32'))
    if silence > .3:
        return FAIL, f'silence scored {silence:.2f}, expected near 0'
    return PASS, f'silence {silence:.3f}'


@check('day 2', 'Transcribes the saved recordings')
def transcribe_clips():
    import voice_loop
    clips = sorted((ROOT / 'data' / 'voice' / 'clips').glob('*.wav'))
    if not clips:
        return WARN, 'no recordings in data/voice/clips'
    ears = voice_loop.Ears()
    seconds, produced = 0.0, 0
    for clip in clips[:5]:
        text, elapsed, _ = ears.transcribe(voice_loop.load_wav(clip))
        seconds += elapsed
        produced += bool(text.strip())
    return (PASS if produced >= 4 else FAIL), f'{produced}/5 produced text, {seconds/5:.2f}s each'


@check('day 2', 'Commands recognised from real audio')
def command_accuracy():
    sys.path.insert(0, str(ROOT / 'tests'))
    from command_match import ACCEPT, TRUTH, classify
    bench = ROOT / 'data' / 'voice' / 'bench.json'
    if not bench.exists():
        return WARN, 'no bench.json; run tests/whisper_bench.py'
    configs = json.loads(bench.read_text(encoding='utf-8'))
    chosen = next((c for c in configs if c['config'] == 'turbo auto plain'), configs[0])
    correct = wrong = 0
    for row in chosen['rows']:
        if row['n'] > len(TRUTH):
            continue
        (score, best), _ = classify(row['text'])
        if score < ACCEPT:
            continue
        correct += best == TRUTH[row['n'] - 1]
        wrong += best != TRUTH[row['n'] - 1]
    total = len([r for r in chosen['rows'] if r['n'] <= len(TRUTH)])
    if wrong:
        return FAIL, f'{correct}/{total} correct but {wrong} WRONG'
    return (PASS if correct >= 17 else WARN), f'{correct}/{total} correct, 0 wrong'


@check('day 2', 'Noise cannot trigger a command')
def noise_rejected():
    sys.path.insert(0, str(ROOT / 'tests'))
    from command_match import ACCEPT, classify
    for noise in ('hana', 'Thank you.', 'වවවවවවවව', 'ven ven ven'):
        (score, name), _ = classify(noise)
        if score >= ACCEPT:
            return FAIL, f'{noise!r} would trigger {name} at {score:.2f}'
    return PASS, 'all rejected below the floor'


# ---------------------------------------------------------------- day 3, the voice

@check('day 3', 'Sinhala speech synthesis')
def tts_sinhala():
    import speech
    data = speech.synthesize('ආයුබෝවන් චමින්දු', speech.SINHALA_VOICE)
    if len(data) < 2000:
        return FAIL, f'only {len(data)} bytes'
    audio = speech.decode(data)
    return PASS, f'{len(data)/1024:.1f} KB, {len(audio)/speech.SAMPLE_RATE:.2f}s audio'


@check('day 3', 'Cache avoids a second request')
def tts_cache():
    import speech
    speech.synthesize('ආයුබෝවන් චමින්දු', speech.SINHALA_VOICE)
    started = time.monotonic()
    speech.synthesize('ආයුබෝවන් චමින්දු', speech.SINHALA_VOICE)
    elapsed = time.monotonic() - started
    if elapsed > .25:
        return FAIL, f'cached call took {elapsed:.3f}s'
    return PASS, f'{elapsed*1000:.1f} ms'


@check('day 3', 'Mixed reply uses both voices')
def tts_routing():
    import speech
    pieces = speech.segments('CPU is at 40 percent. මතකය හොඳින් තිබේ.')
    voices = [voice for voice, _ in pieces]
    if speech.SINHALA_VOICE not in voices or speech.ENGLISH_VOICE not in voices:
        return FAIL, str(voices)
    return PASS, 'Sinhala and English routed separately'


@check('day 3', 'Speech can be interrupted')
def tts_interrupt():
    import speech
    speaker = speech.Speaker()
    result = {}
    thread = threading.Thread(target=lambda: result.update(speaker.say('ආයුබෝවන්. ' * 10)))
    thread.start()
    time.sleep(1.0)
    speaker.stop()
    thread.join(timeout=20)
    if result.get('reason') != 'interrupted':
        return FAIL, f'did not stop: {result}'
    return PASS, 'stopped mid-sentence'


@check('day 3', 'Offline fallback never fails silently')
def tts_offline():
    from unittest.mock import patch
    import speech
    speaker = speech.Speaker()
    with patch('speech.synthesize', side_effect=speech.Offline('simulated outage')):
        result = speaker.say('Battery is at 80 percent. බැටරිය හොඳයි.')
    if result.get('reason') != 'offline' or 'text' not in result:
        return FAIL, str(result)[:110]
    return PASS, f"fell back via {result.get('fallback')}, text preserved"


# ---------------------------------------------------------------- day 5, the router

@check('day 5', 'Commands route from real recordings')
def routing():
    import json
    from router import route
    truth = ['time', 'weather', 'system_status', 'send_message', 'send_message',
             'reminder', 'reminder', 'play_music', 'volume_up', 'open_app',
             'dollar_rate', 'save_note', 'list_notes', 'calculate', 'screenshot',
             'battery', 'news', 'email', 'lock_pc', 'capabilities']
    path = ROOT / 'data' / 'voice' / 'google.json'
    if not path.exists():
        return WARN, 'no google.json to route against'
    rows = json.loads(path.read_text(encoding='utf-8'))
    direct = wrong = 0
    for row in rows:
        if row['n'] > len(truth):
            continue
        outcome = route(row['text'])
        if outcome['action'] != 'command':
            continue
        if outcome.get('command') == truth[row['n'] - 1]:
            direct += 1
        else:
            wrong += 1
    if wrong:
        return FAIL, f'{direct} direct but {wrong} routed to the WRONG command'
    return PASS, f'{direct}/{len(rows)} acted on directly, 0 wrong'


@check('day 5', 'Noise and self-speech never act')
def routing_safety():
    from router import route
    for noise in ('hana', 'Thank you.', 'වවවවවවවව', 'ven ven ven', '',
                  'Tell me who the message should go to and what you would like it to say.'):
        outcome = route(noise)
        if outcome['action'] == 'command':
            return FAIL, f'{noise[:30]!r} would run {outcome.get("command")}'
    return PASS, 'all refused'


@check('day 5', 'Notes and reminders are really saved')
def saving():
    import tempfile
    from assistant_core import Store, Tools
    from router import route
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        store = Store(root / 'v.db')
        tools = Tools(store, root / 'workspace')
        decision = route('සටහනක් තබන්න අද රෑට කෑම තියන්න එපා කියලා')
        if decision['action'] != 'command' or decision['command'] != 'save_note':
            return FAIL, f'routed to {decision["action"]}/{decision.get("command")}'
        tools.execute('save_note', {'title': decision['content'], 'content': ''})
        notes = [r for r in store.records() if r['kind'] == 'note']
        if not notes:
            return FAIL, 'the note was not stored'
        if 'කෑම' not in notes[0]['title']:
            return FAIL, f'stored the wrong text: {notes[0]["title"][:40]}'
        return PASS, f'stored: {notes[0]["title"][:34]}'


@check('day 5', 'Spoken replies contain no digits')
def spoken_numbers():
    import tempfile
    import responses
    from assistant_core import Store, Tools
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        store = Store(root / 'v.db')
        tools = Tools(store, root / 'workspace')
        replies = [responses.answer(name, tools, store) for name in responses.DIRECT]
        replies.append(responses.say_reminder_set('කිරි ගන්න', 600))
        for reply in replies:
            if reply and any(c.isdigit() for c in reply):
                return FAIL, f'digits would be read as numerals: {reply[:50]}'
    return PASS, 'every number is a Sinhala word'


# ---------------------------------------------------------------- the server

@check('setup', 'Server boots with every endpoint')
def server_endpoints():
    import requests
    from server import create_server
    server = create_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        state = requests.get(base + '/api/state', timeout=10).json()
        headers = {'X-Jarvis-Token': state['token'], 'Content-Type': 'application/json'}
        missing = [key for key in ('token', 'system', 'ollama', 'voice', 'records') if key not in state]
        if missing:
            return FAIL, f'state missing {missing}'
        if requests.post(base + '/api/chat', json={'prompt': 'x'}, timeout=5).status_code != 403:
            return FAIL, 'unauthenticated POST was accepted'
        body = json.dumps({'text': 'ආයුබෝවන්', 'wait': True}).encode('utf-8')
        spoke = requests.post(base + '/api/speak', data=body, headers=headers, timeout=60).json()
        if not spoke.get('spoken'):
            return FAIL, f'speak failed: {spoke}'
        if not any('si-LK' in v for v in spoke.get('voices', [])):
            return FAIL, f'Sinhala lost in transit: {spoke.get("voices")}'
        return PASS, f'auth enforced, voice={state["voice"]["available"]}, UTF-8 intact'
    finally:
        server.shutdown()
        server.server_close()


@check('day 4', 'Wake word model loads')
def wake_word():
    import numpy
    import voice_loop
    wake = voice_loop.WakeWord()
    fired = wake.feed(numpy.zeros(voice_loop.WAKE_FRAME, dtype='float32'))
    if fired:
        return FAIL, 'fired on silence'
    threshold = voice_loop.WAKE_THRESHOLD
    if not .48 < threshold < .86:
        return FAIL, f'threshold {threshold} is outside the measured gap'
    return PASS, f'{wake.name} loaded, threshold {threshold}'


@check('day 4', 'Own voice is ignored')
def echo_guard():
    from unittest.mock import MagicMock, patch
    import voice_loop
    with patch('openwakeword.model.Model') as model:
        model.return_value.predict.return_value = {'hey_jarvis': 0.0}
        listener = voice_loop.Listener(vad=MagicMock(), ears=MagicMock())
    listener.last_reply = "Tell me who the message should be sent to and what content you'd like."
    if not listener.is_own_voice('Tell me who the message should be sent to and what content'):
        return FAIL, 'would answer its own reply'
    if listener.is_own_voice('ammata message ekak yavanna'):
        return FAIL, 'a real command was mistaken for the reply'
    return PASS, 'self-speech filtered, commands pass'


ORDER = [ollama_version, models_present, gpu, unit_tests, server_endpoints,
         multi_tool, streaming, thinking_gate, sinhala_reply, deep_fallback,
         google_stt, whisper_loads, vad_check, transcribe_clips, command_accuracy,
         noise_rejected, tts_sinhala, tts_cache, tts_routing, tts_interrupt, tts_offline,
         wake_word, echo_guard,
         routing, routing_safety, saving, spoken_numbers]

QUICK_SKIP = {multi_tool, streaming, thinking_gate, sinhala_reply, deep_fallback,
              transcribe_clips}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true', help='skip slow model calls')
    args = parser.parse_args()

    print(f'{"":6} {"":6} {"check":38} {"detail":70} time')
    print('-' * 130)
    started = time.monotonic()
    for function in ORDER:
        if args.quick and function in QUICK_SKIP:
            continue
        # Keep component chatter out of the report.
        noise = io.StringIO()
        with redirect_stdout(noise), redirect_stderr(noise):
            pass
        function()

    print('-' * 130)
    failed = [r for r in results if r[2] == FAIL]
    warned = [r for r in results if r[2] == WARN]
    print(f'{len(results) - len(failed) - len(warned)} passed, {len(warned)} warnings, '
          f'{len(failed)} failed, in {time.monotonic() - started:.0f}s')
    for day, name, _, detail, _ in warned:
        print(f'  warn  {day} {name}: {detail}')
    for day, name, _, detail, _ in failed:
        print(f'  FAIL  {day} {name}: {detail}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
