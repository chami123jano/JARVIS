"""Memory that remembers (Day 14): found by meaning, summarised before it is lost, and
never learned in secret.

The models are replaced by stand-ins -- a fixed "gloss" and a bag-of-words embedding --
so these tests check the plumbing. bench_memory.py measures the real models.
"""
import hashlib
import math
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory
import responses
from agent import Agent
import agent as agent_module
agent_module.HOUSEKEEPING_QUIET = 0     # the real wait is for a person; not here
from assistant_core import Store, Tools

GLOSSES = {
    'මම කෝපි බොන්නේ නෑ': 'I do not drink coffee',
    'මම මොනවද බොන්නේ?': 'what do I drink',
    'අම්මගේ උපන්දිනය දෙසැම්බර් 12': "mother's birthday is December 12",
}


def gloss(text):
    return GLOSSES.get(text, text)


def embed(texts):
    """Words hashed into 64 buckets: shared words, close vectors."""
    vectors = []
    for text in texts:
        vector = [0.0] * 64
        for word in re.findall(r'[a-z]+', text.lower().split('|')[0]):
            if word in ('search', 'query', 'document', 'i', 'do', 'not', 'is', 'what'):
                continue
            stem = word.rstrip('s')[:5]
            vector[int(hashlib.md5(stem.encode()).hexdigest(), 16) % 64] += 1
        vectors.append(vector if any(vector) else [1e-3] * 64)
    return vectors


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'm.db')
        self.memory = memory.Memory(self.store, gloss=gloss, embed=embed)

    def tearDown(self):
        self.temp.cleanup()

    def remember(self, title, kind='memory', content=''):
        return self.store.add(kind, title, content)['id']


class SearchTests(MemoryCase):
    def test_found_through_the_gloss(self):
        """Sinhala question, Sinhala memory, no shared words: the glosses meet."""
        coffee = self.remember('මම කෝපි බොන්නේ නෑ')
        self.remember('අම්මගේ උපන්දිනය දෙසැම්බර් 12')
        self.remember('The car service is due at 60,000 km')
        self.memory.reindex()
        GLOSSES['මම මොනවද බොන්නේ?'] = 'what do I drink, coffee or tea'
        found = self.memory.search('මම මොනවද බොන්නේ?')
        self.assertEqual(found[0]['record_id'], coffee)
        self.assertIn('score', found[0])

    def test_only_notes_memories_and_summaries_are_searched(self):
        self.remember('pay the electricity bill', kind='reminder')
        self.remember('electricity bill is paid at the post office')
        self.memory.reindex()
        kinds = {r['kind'] for r in self.memory.search('electricity bill', floor=-1)}
        self.assertEqual(kinds, {'memory'})

    def test_reindex_only_does_what_is_missing(self):
        self.remember('first')
        self.assertEqual(self.memory.reindex(), 1)
        self.remember('second')
        self.assertEqual(self.memory.reindex(), 1)
        self.assertEqual(self.memory.reindex(), 0)

    def test_a_deleted_record_is_never_found(self):
        record = self.remember('a secret I deleted')
        self.memory.reindex()
        self.store.update(record, 'delete')
        self.assertEqual(self.memory.search('secret deleted', floor=-1), [])

    def test_nothing_indexed_means_no_model_calls(self):
        def refuse(*_):
            raise AssertionError('called a model with nothing to search')
        quiet = memory.Memory(self.store, gloss=refuse, embed=refuse)
        self.assertEqual(quiet.search('anything'), [])


class ContextTests(MemoryCase):
    def test_the_prompt_gets_the_relevant_few_not_the_newest_25(self):
        for number in range(40):
            self.remember(f'unrelated fact number {number} about gardening')
        target = self.remember('Pharmacy POS demo for the client next week')
        self.memory.reindex()
        context = memory.context_for(self.store, 'pharmacy demo', self.memory)
        self.assertLessEqual(len(context), memory.TOP)
        self.assertEqual(context[0]['title'], 'Pharmacy POS demo for the client next week')
        self.assertIn('saved', context[0])

    def test_without_the_models_the_newest_few_still_go(self):
        def down(*_):
            raise ConnectionError('Ollama is down')
        self.remember('likes short answers')
        self.memory.reindex()
        broken = memory.Memory(self.store, gloss=down, embed=down)
        context = memory.context_for(self.store, 'anything', broken)
        self.assertEqual(context[0]['title'], 'likes short answers')

    def test_the_agent_uses_it(self):
        agent = Agent(self.store, Tools(self.store, Path(self.temp.name) / 'w'))
        with patch.object(memory, 'context_for', return_value=[{'title': 'SENTINEL'}]) as found:
            prompt = agent.system_prompt('what do I drink')
        self.assertIn('SENTINEL', prompt)
        self.assertEqual(found.call_args.args[1], 'what do I drink')


class SummaryTests(MemoryCase):
    def test_old_conversation_is_summarised_before_it_is_dropped(self):
        for number in range(45):
            self.store.message('user' if number % 2 == 0 else 'assistant', f'message {number}')
        seen = {}

        def summarise(transcript):
            seen['transcript'] = transcript
            return 'Talked about messages zero to twenty-nine.'
        record = self.memory.roll_up(summarise)
        self.assertEqual(record['kind'], 'summary')
        self.assertIn('message 0', seen['transcript'])
        self.assertIn('message 29', seen['transcript'])
        self.assertNotIn('message 30', seen['transcript'])
        self.assertEqual(len(self.memory.unsummarised()), 15)
        self.assertIsNone(self.memory.roll_up(summarise), 'too little left to summarise')
        self.assertTrue(self.memory.search('messages zero twenty', floor=-1))


class LearningTests(MemoryCase):
    def ask_returning(self, *facts):
        calls = []

        def ask(prompt, schema):
            calls.append(prompt)
            return {'facts': list(facts)}
        return ask, calls

    def test_a_fact_is_kept_and_reported(self):
        ask, _ = self.ask_returning({'en': 'The user does not drink coffee',
                                     'spoken': 'ඔයා කෝපි බොන්නේ නෑ'})
        learned = self.memory.learn('mama coffee bonne ne', ask)
        self.assertEqual(learned[0]['spoken'], 'ඔයා කෝපි බොන්නේ නෑ')
        saved = [r for r in self.store.records() if r['kind'] == 'memory']
        self.assertEqual(saved[0]['title'], 'The user does not drink coffee')
        self.assertTrue(saved[0]['content'].startswith('Heard on'))

    def test_the_same_fact_twice_is_kept_once(self):
        fact = {'en': 'The user does not drink coffee', 'spoken': 'x'}
        ask, _ = self.ask_returning(fact)
        self.memory.learn('mama coffee bonne ne', ask)
        self.assertEqual(self.memory.learn('mama kawadawath coffee bonne ne', ask), [])
        self.assertEqual(len([r for r in self.store.records() if r['kind'] == 'memory']), 1)

    def test_only_things_said_about_oneself_are_checked(self):
        ask, calls = self.ask_returning()
        for text in ('what time is it', 'අද කාලගුණය කොහොමද', 'open chrome'):
            self.memory.learn(text, ask)
        self.assertEqual(calls, [], 'no first person, no model call')
        self.memory.learn('මම තේ විතරයි බොන්නේ', ask)
        self.assertEqual(len(calls), 1)

    def test_it_says_what_it_remembered(self):
        said = responses.say_learned([{'en': 'x', 'spoken': 'ඔයා කෝපි බොන්නේ නෑ'}])
        self.assertIn('මතක තියාගන්නම්', said)
        self.assertIn('ඔයා කෝපි බොන්නේ නෑ', said)
        self.assertIn("I'll remember", responses.say_learned([{'en': 'x', 'spoken': 'You like tea'}]))

    def test_the_agent_reports_what_it_learned(self):
        self.store.set_config('model', 'qwen3.5:9b')
        agent = Agent(self.store, Tools(self.store, Path(self.temp.name) / 'w'))
        agent.memory = self.memory
        heard = []
        agent.on_learned = heard.append
        agent.jobs['j'] = {'events': []}
        fact = [{'en': 'The user is allergic to beans', 'spoken': 'ඔයාට බෝංචි ඇලර්ජි'}]
        with patch.object(agent, 'installed', return_value=['qwen3.5:9b']), \
                patch.object(self.memory, 'learn', return_value=fact):
            agent.after_answer('j', 'මට බෝංචි ඇලර්ජි')
        self.assertEqual(heard, [fact])
        self.assertIn('Remembered', [e['label'] for e in agent.jobs['j']['events']])


class SinglishTests(unittest.TestCase):
    """Measured: qwen3.5 learned "mama coffee bonne ne" as "prefers coffee"."""

    def test_singlish_is_spelled_out(self):
        from sinhala import singlish_to_script
        self.assertEqual(singlish_to_script('mama coffee bonne ne, tea witharai'),
                         'මම coffee බොන්නේ නෑ, tea විතරයි')
        self.assertEqual(singlish_to_script('mokakda mama parata kiwwe project eka gana'),
                         'මොකක්ද මම කලින් කිව්වේ project එක ගැන')

    def test_english_is_left_alone(self):
        from sinhala import singlish_to_script
        for text in ('I had a word with mama about it', 'what time is it', 'ok na'):
            with self.subTest(text=text):
                self.assertEqual(singlish_to_script(text), text)

    def test_the_agent_sees_the_spelled_out_version(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'a.db')
            store.set_config('model', 'test')
            agent = Agent(store, Tools(store, Path(folder) / 'w'))
            seen = {}

            def generate(job_id, model, messages, think, deep):
                seen['text'] = messages[-1]['content']
                return {'role': 'assistant', 'content': 'ok'}
            with patch.object(agent, 'generate', side_effect=generate):
                job_id = agent.start('mama coffee bonne ne')
                agent.wait(job_id)
        self.assertIn('මම coffee බොන්නේ නෑ', seen['text'])


if __name__ == '__main__':
    unittest.main()
