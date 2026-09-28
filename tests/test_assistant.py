import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests

from assistant_core import Store, Tools, calculate
from agent import Agent
from server import create_server


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state.db')
        self.tools = Tools(self.store, self.root / 'workspace')

    def tearDown(self):
        self.temp.cleanup()

    def test_arithmetic_limits(self):
        self.assertEqual(calculate('(120 + 80) * 1.5'), 300)
        for expression in ['__import__("os")', '9**99999999', '(1).__class__', '1/0']:
            with self.assertRaises(Exception):
                calculate(expression)

    def test_documents_cannot_escape_or_overwrite(self):
        self.tools.execute('write_document', {'path':'report.md', 'content':'A useful report'})
        self.assertEqual(self.tools.execute('read_document', {'path':'report.md'})['text'], 'A useful report')
        self.assertEqual(self.tools.execute('find_files', {'query':'report'})['files'], ['report.md'])
        with self.assertRaises(FileExistsError):
            self.tools.execute('write_document', {'path':'report.md', 'content':'replacement'})
        with self.assertRaises(ValueError):
            self.tools.execute('read_document', {'path':'../state.db'})
        with self.assertRaises(ValueError):
            self.tools.execute('open_app', {'app':'notepad & echo injected'})

    def test_records_persist_and_reminders_validate(self):
        self.store.add('memory', 'Tea', 'No sugar')
        due = (dt.datetime.now().astimezone() + dt.timedelta(minutes=5)).isoformat()
        result = self.tools.execute('set_reminder', {'title':'Take a break','due':due})
        self.assertEqual(len(Store(self.root / 'state.db').records()), 2)
        self.store.update(result['id'], 'complete')
        self.assertTrue(self.store.records()[0]['done'])
        with self.assertRaises(ValueError):
            self.tools.execute('set_reminder', {'title':'Old reminder','due':'2000-01-01T00:00:00+00:00'})

    def await_job(self, agent, job_id):
        for _ in range(150):
            job = agent.snapshot(job_id)
            if job['state'] not in ('running','stopping'):
                return job
            time.sleep(.02)
        self.fail('Job did not finish')

    def test_direct_command_without_model(self):
        agent = Agent(self.store, self.tools)
        job = self.await_job(agent, agent.start('calculate 15 * 12'))
        self.assertEqual(job['state'], 'done')
        self.assertIn('180', job['answer'])
        self.assertEqual(len(self.store.history()), 2)

    def test_multistep_agent_uses_real_tool_results(self):
        self.store.set_config('model','test')
        agent = Agent(self.store, self.tools)
        replies = [
            {'role':'assistant','content':'','tool_calls':[{'function':{'name':'calculate','arguments':{'expression':'25*4'}}}]},
            {'role':'assistant','content':'','tool_calls':[{'function':{'name':'save_note','arguments':{'title':'Total','content':'100'}}}]},
            {'role':'assistant','content':'Calculated 100 and saved your note.'},
        ]
        with patch.object(agent, 'generate', side_effect=replies) as mock:
            job = self.await_job(agent, agent.start('Work out my total and record it'))
            self.assertEqual(job['state'], 'done')
            self.assertEqual(mock.call_count, 3)
            self.assertEqual(self.store.records()[0]['content'], '100')
            messages = mock.call_args.args[2]
            self.assertTrue(any(m['role'] == 'tool' and '100' in m['content'] for m in messages))

    def test_cancel_blocks_following_tools(self):
        self.store.set_config('model', 'test')
        agent = Agent(self.store, self.tools)
        entered = threading.Event()
        def generation(job_id, *_):
            entered.set()
            agent.jobs[job_id]['cancel'].wait(2)
            return {'role':'assistant','content':'','tool_calls':[{'function':{'name':'save_note','arguments':{'title':'Should not save','content':''}}}]}
        with patch.object(agent, 'generate', side_effect=generation):
            job_id = agent.start('Please do something')
            self.assertTrue(entered.wait(1))
            agent.stop(job_id)
            self.assertEqual(self.await_job(agent, job_id)['state'], 'cancelled')
            self.assertEqual(self.store.records(), [])

    def test_server_session_and_persistence(self):
        server = create_server(0, self.root / 'server-data')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            state = requests.get(url + '/api/state', timeout=5).json()
            self.assertEqual(requests.post(url + '/api/chat',json={'prompt':'hello'},timeout=5).status_code,403)
            headers = {'X-Jarvis-Token':state['token']}
            response = requests.post(url + '/api/records', headers=headers, json={'kind':'note','title':'Server test'},timeout=5)
            self.assertEqual(response.status_code,200)
            self.assertEqual(requests.get(url + '/api/state',timeout=5).json()['records'][0]['title'],'Server test')
            headers['Origin'] = 'https://untrusted.example'
            self.assertEqual(requests.post(url + '/api/records',headers=headers,json={'kind':'note','title':'No'},timeout=5).status_code,403)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()


class ConcurrencyTests(unittest.TestCase):
    """The console must stay responsive while Ollama is busy.

    /api/state used to refresh the model list while holding a lock, so a slow reply from
    Ollama blocked every other caller behind it. Under load the endpoint exceeded its own
    timeout and the connection was dropped.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fetch(url, timeout=10):
        """urllib, not requests: the test patches requests.get on the server side."""
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())

    def test_state_stays_fast_while_ollama_is_slow(self):
        release = threading.Event()

        def crawling_get(*_args, **_kwargs):
            release.wait(3)
            raise requests.ConnectionError('slow')

        server = create_server(0, self.root / 'server-data')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/api/state'
        try:
            with patch('server.requests.get', side_effect=crawling_get):
                # One caller is stuck refreshing the model list.
                threading.Thread(target=lambda: self.fetch(url), daemon=True).start()
                time.sleep(.4)
                started = time.monotonic()
                state = self.fetch(url)
                elapsed = time.monotonic() - started
            release.set()
            self.assertIn('token', state)
            self.assertLess(elapsed, 1.5,
                            f'second caller waited {elapsed:.2f}s behind the refresh')
        finally:
            release.set()
            server.shutdown()
            server.server_close()
