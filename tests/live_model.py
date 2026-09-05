"""Optional real Ollama integration check; all writes use a temporary workspace."""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_core import Store, Tools
from agent import Agent

with tempfile.TemporaryDirectory(prefix='jarvis-model-') as folder:
    root = Path(folder)
    store = Store(root / 'test.db')
    store.set_config('model', 'qwen2.5-coder:7b')
    agent = Agent(store, Tools(store, root / 'workspace'))
    job_id = agent.start('Use calculate to compute 137 * 23. Then use save_note to save the result with title Test calculation and the numeric result as content. Finally tell me what you saved.')
    for _ in range(240):
        job = agent.snapshot(job_id)
        if job['state'] not in ('running','stopping'):
            break
        time.sleep(1)
    else:
        agent.stop(job_id)
        time.sleep(1)
        raise RuntimeError('Live model check timed out')
    print(job['state'], job['answer'])
    for event in job['events']:
        print(event['label'], event['detail'][:180])
    assert job['state'] == 'done', job['answer']
    assert any('3151' in record['content'] for record in store.records()), 'Model did not save the calculated result'
    print('PASS: real Ollama multi-step calculation and saved note.')
    job_id = agent.start('What can you help me with besides programming? Answer in one sentence without using tools.')
    for _ in range(130):
        job = agent.snapshot(job_id)
        if job['state'] not in ('running','stopping'):
            break
        time.sleep(1)
    else:
        agent.stop(job_id)
        time.sleep(1)
        raise RuntimeError('Conversation check timed out')
    assert job['state'] == 'done' and len(job['answer']) > 10, job
    print('PASS: real general conversation:', job['answer'])
