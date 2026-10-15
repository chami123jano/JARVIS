"""Deep think: asked for, given room to finish, never spoken aloud, and the fast brain
put back afterwards (Day 22)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent as agent_module
import bench_think
import responses
from agent import Agent
from assistant_core import Store, Tools


class DeepCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'state.db')
        self.store.set_config('model', 'qwen3.5:9b')
        self.store.set_config('deep_model', 'gpt-oss:20b')
        self.agent = Agent(self.store, Tools(self.store, root / 'workspace'))

    def tearDown(self):
        # The memory housekeeping that follows each answer (Day 14) runs in the
        # background and writes to this folder; removing it underneath made the full suite
        # fail now and then with "directory is not empty".
        for job_id in list(self.agent.threads):
            self.agent.wait(job_id)
        self.temp.cleanup()

    def finish(self, job_id):
        for _ in range(250):
            job = self.agent.snapshot(job_id)
            if job['state'] not in ('running', 'stopping'):
                return job
            time.sleep(.02)
        self.fail('job did not finish')


class AskingTests(DeepCase):
    def test_asked_for_in_either_language(self):
        for text in ('think hard about whether I should buy or rent',
                     'හොඳට හිතලා බලන්න මේ ව්‍යාපාරය ලාභද කියලා',
                     'hondata hithala balanna'):
            with self.subTest(text=text):
                self.assertTrue(self.agent.wants_deep(text))

    def test_not_for_ordinary_requests(self):
        for text in ('what time is it', 'අද කාලගුණය කොහොමද', 'I think it will rain'):
            with self.subTest(text=text):
                self.assertFalse(self.agent.wants_deep(text))

    def test_not_without_a_deep_model(self):
        self.store.set_config('deep_model', '')
        self.assertFalse(self.agent.wants_deep('think hard about it'))


class RoomToFinishTests(DeepCase):
    def payload_for(self, deep):
        captured = {}

        def stream(job_id, payload, deadline):
            captured.update(payload=payload, deadline=deadline)
            return {'role': 'assistant', 'content': 'ok'}
        self.agent.jobs['j'] = {'cancel': MagicMock(is_set=lambda: False), 'events': []}
        with patch.object(self.agent, 'stream', side_effect=stream):
            self.agent.generate('j', 'm', [], True, deep)
        return captured

    def test_the_deep_brain_gets_medium_effort_and_room(self):
        """At high it reasoned 12,000 tokens and never answered; 4,096 cut medium off."""
        captured = self.payload_for(deep=True)
        self.assertEqual(captured['payload']['think'], 'medium')
        self.assertEqual(captured['payload']['options']['num_predict'], 12000)
        self.assertEqual(captured['payload']['options']['num_ctx'], 16384)
        self.assertGreaterEqual(captured['deadline'], 600)

    def test_the_fast_brain_is_unchanged(self):
        captured = self.payload_for(deep=False)
        self.assertIs(captured['payload']['think'], True)
        self.assertEqual(captured['payload']['options']['num_predict'], 1600)


class SwapBackTests(DeepCase):
    def run_job(self, prompt):
        with patch.object(self.agent, 'installed', return_value=['gpt-oss:20b', 'qwen3.5:9b']), \
                patch.object(self.agent, 'generate',
                             return_value={'role': 'assistant', 'content': 'Forty-two.'}), \
                patch.object(agent_module.requests, 'post') as post:
            job = self.finish(self.agent.start(prompt))
        return job, post

    def test_the_fast_brain_is_put_back_after_deep_thinking(self):
        job, post = self.run_job('think hard about the meaning of life')
        self.assertEqual(job['answer'], 'Forty-two.')
        bodies = [call.kwargs['json'] for call in post.call_args_list]
        self.assertIn({'model': 'gpt-oss:20b', 'keep_alive': 0}, bodies)
        self.assertIn({'model': 'qwen3.5:9b', 'keep_alive': '30m'}, bodies)
        labels = [e['label'] for e in job['events']]
        self.assertIn('Loading the deep brain', labels, 'the swap is announced, not hidden')
        self.assertIn('Fast brain back', labels)

    def test_a_quick_question_swaps_nothing(self):
        _, post = self.run_job('what is two plus two')
        self.assertEqual(post.call_count, 0)

    def test_the_swap_back_happens_even_when_thinking_fails(self):
        with patch.object(self.agent, 'installed', return_value=['gpt-oss:20b']), \
                patch.object(self.agent, 'generate', side_effect=TimeoutError('too long')), \
                patch.object(agent_module.requests, 'post') as post:
            job = self.finish(self.agent.start('think hard about it'))
        self.assertEqual(job['state'], 'error')
        self.assertTrue(any(c.kwargs['json'].get('keep_alive') == 0 for c in post.call_args_list))


class ReasoningTests(DeepCase):
    def test_the_whole_chain_is_kept_for_the_console(self):
        lines = [json.dumps({'message': {'thinking': 'Step one. ' * 300}}),
                 json.dumps({'message': {'content': 'The answer is 7.'}, 'done': True})]
        response = MagicMock(ok=True)
        response.iter_lines.return_value = lines
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: None
        self.agent.jobs['j'] = {'cancel': MagicMock(is_set=lambda: False), 'events': [],
                                'partial': ''}
        with patch.object(agent_module.requests, 'post', return_value=response):
            message = self.agent.stream('j', {}, deadline=60)
        self.assertEqual(message['content'], 'The answer is 7.')
        self.assertEqual(len(self.agent.jobs['j']['reasoning']), len('Step one. ' * 300))
        self.assertNotIn('Step one', message['content'])

    def test_only_the_answer_is_spoken(self):
        job = {'state': 'done', 'answer': 'Rent for now.', 'reasoning': 'SECRET CHAIN'}
        for lang in ('si', 'en'):
            spoken = responses.say_deep_answer(job, lang)
            self.assertIn('Rent for now.', spoken)
            self.assertNotIn('SECRET', spoken)

    def test_a_failure_is_said_plainly(self):
        self.assertIn('could not', responses.say_deep_answer({'state': 'error'}, 'en'))


class SinhalaTests(DeepCase):
    def test_number_words_become_digits_before_translation(self):
        """Measured: "පනහක්" (fifty) was translated as "one hundred"."""
        from sinhala import numbers_to_digits
        self.assertEqual(numbers_to_digits('රුපියල් එක, දෙක, පහ, දහය කාසි වලින් රුපියල් පනහක්'),
                         'රුපියල් 1, 2, 5, 10 කාසි වලින් රුපියල් 50ක්')
        self.assertEqual(numbers_to_digits('පැය දෙකකින්'), 'පැය 2කින්')
        self.assertEqual(numbers_to_digits('ෆෝන් එක චාජ් කරන්න'), 'ෆෝන් එක චාජ් කරන්න',
                         '"එක" alone is "the", not one')

    def test_a_sinhala_question_is_translated_there_and_back(self):
        translations = []

        def translate(text, language):
            translations.append((text, language))
            return 'How many ways?' if language == 'English' else 'ක්‍රම 341ක් තියෙනවා.'
        seen = {}

        def generate(job_id, model, messages, think, deep):
            seen['question'] = messages[-1]['content']
            return {'role': 'assistant', 'content': 'There are 341 ways.'}
        with patch.object(self.agent, 'installed', return_value=['gpt-oss:20b']), \
                patch.object(self.agent, 'translate', side_effect=translate), \
                patch.object(self.agent, 'generate', side_effect=generate), \
                patch.object(agent_module.requests, 'post'):
            job = self.finish(self.agent.start('හොඳට හිතලා බලන්න, රුපියල් පනහක් හදන්න ක්‍රම කීයද'))
        self.assertIn('50ක්', translations[0][0], 'digits, not words, go to the translator')
        self.assertTrue(seen['question'].startswith('How many ways?'))
        self.assertEqual(job['answer'], 'ක්‍රම 341ක් තියෙනවා.')

    def test_deep_thinking_gets_only_reading_tools(self):
        """Measured: given every tool, gpt-oss opened a web search mid-problem."""
        captured = {}

        def stream(job_id, payload, deadline):
            captured['tools'] = [t['function']['name'] for t in payload['tools']]
            return {'role': 'assistant', 'content': 'ok'}
        self.agent.jobs['j'] = {'cancel': MagicMock(is_set=lambda: False), 'events': []}
        with patch.object(self.agent, 'stream', side_effect=stream):
            self.agent.generate('j', 'm', [], True, True)
        self.assertIn('calculate', captured['tools'])
        for acting in ('web_search', 'save_note', 'open_app', 'write_document'):
            self.assertNotIn(acting, captured['tools'])


class BenchmarkMarkingTests(unittest.TestCase):
    def test_only_the_answer_line_is_marked(self):
        self.assertTrue(bench_think.correct('I tried 299 first.\nANSWER: 300', '300'))
        self.assertFalse(bench_think.correct('ANSWER: 299, not 300 as some say', '541'))
        self.assertTrue(bench_think.correct('ANSWER: Rs. 143,077', '143077'))
        self.assertTrue(bench_think.correct('ANSWER: 9:24', '09:24'))
        self.assertTrue(bench_think.correct('ANSWER: **Saturday**', 'saturday'))
        self.assertFalse(bench_think.correct('It is 300.', '300'), 'no answer line, no mark')


if __name__ == '__main__':
    unittest.main()
