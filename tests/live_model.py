"""Optional real Ollama integration check; all writes use a temporary workspace.

Run directly:  .venv\\Scripts\\python.exe tests\\live_model.py
Needs Ollama running with the configured model installed. Not part of the unit suite,
which stays deterministic by mocking the model.
"""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_core import Store, Tools
from agent import Agent

MODEL = 'qwen3.5:9b'


def run(agent, prompt, limit=240):
    """Start a request, sample streaming progress, and return the finished job."""
    job_id = agent.start(prompt)
    saw_partial = False
    started = time.monotonic()
    for _ in range(limit):
        job = agent.snapshot(job_id)
        if job.get('partial'):
            saw_partial = True
        if job['state'] not in ('running', 'stopping'):
            return job, time.monotonic() - started, saw_partial
        time.sleep(.25)
    agent.stop(job_id)
    time.sleep(1)
    raise RuntimeError(f'Live check timed out: {prompt[:60]}')


with tempfile.TemporaryDirectory(prefix='jarvis-model-') as folder:
    root = Path(folder)
    store = Store(root / 'test.db')
    store.set_config('model', MODEL)
    agent = Agent(store, Tools(store, root / 'workspace'))

    print(f'=== model: {MODEL} ===\n')

    # 1. Several tools driven from one request, using the real results.
    job, seconds, _ = run(agent, 'Use calculate to compute 137 * 23. Then use save_note to save '
                                 'the result with title Test calculation and the numeric result as '
                                 'content. Finally tell me what you saved.')
    print(f'[1] multi-tool  {job["state"]}  {seconds:.1f}s')
    for event in job['events']:
        print('     ', event['label'], event['detail'][:120])
    assert job['state'] == 'done', job['answer']
    assert any('3151' in record['content'] for record in store.records()), 'Model did not save the calculated result'
    tools_used = sum(1 for event in job['events'] if event['label'] == 'Tool completed')
    assert tools_used >= 2, f'Expected at least two tools, saw {tools_used}'
    print(f'PASS: {tools_used} tools in one request, real results used.\n')

    # 2. Plain conversation, no tools, and streaming must be visible while it runs.
    job, seconds, saw_partial = run(agent, 'What can you help me with besides programming? '
                                           'Answer in one sentence without using tools.', limit=130)
    print(f'[2] conversation  {job["state"]}  {seconds:.1f}s  streaming_seen={saw_partial}')
    assert job['state'] == 'done' and len(job['answer']) > 10, job
    print(f'PASS: {job["answer"][:140]}\n')

    # 3. The thinking gate. A trivial request must not trigger extended reasoning:
    #    measured at 176s with thinking on, against about 9s with it off.
    job, seconds, _ = run(agent, 'Say hello in Sinhala.', limit=130)
    print(f'[3] thinking gate  {job["state"]}  {seconds:.1f}s')
    assert job['state'] == 'done', job['answer']
    assert seconds < 45, f'Trivial request took {seconds:.1f}s - thinking mode is probably still on'
    print(f'PASS: trivial request answered in {seconds:.1f}s -> {job["answer"][:100]}\n')

    # 4. Sinhala in, Sinhala out.
    job, seconds, _ = run(agent, 'මගේ නම චමින්දු. මට සිංහලෙන් උත්තර දෙන්න. ඔබට කරන්න පුළුවන් මොනවද?', limit=200)
    print(f'[4] sinhala  {job["state"]}  {seconds:.1f}s')
    assert job['state'] == 'done', job['answer']
    sinhala = sum(1 for character in job['answer'] if '඀' <= character <= '෿')
    assert sinhala > 15, f'Reply was not in Sinhala script ({sinhala} Sinhala characters): {job["answer"][:200]}'
    print(f'PASS: replied in Sinhala ({sinhala} Sinhala characters) -> {job["answer"][:160]}\n')

    print('ALL LIVE CHECKS PASSED')
