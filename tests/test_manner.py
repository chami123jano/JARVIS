"""Day 30, from real use: a friend rather than a form, a poem rather than a question back,
and "ඒක නෙවේ" (not that) understood -- with an undo for the note that should not have been.
"""
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as agent_module
agent_module.HOUSEKEEPING_QUIET = 0
import responses
import router
from agent import Agent
from assistant_core import Store, Tools
from sandbox import private_root


class MannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'a.db')
        self.store.set_config('model', 'test')
        self.agent = Agent(self.store, Tools(self.store, Path(self.temp.name) / 'w'))

    def tearDown(self):
        for job_id in list(self.agent.threads):
            self.agent.wait(job_id)
        self.temp.cleanup()

    def seen(self, text):
        seen = {}

        def generate(job_id, model, messages, think, deep):
            seen['system'], seen['text'] = messages[0]['content'], messages[-1]['content']
            return {'role': 'assistant', 'content': 'ok'}
        with patch.object(self.agent, 'generate', side_effect=generate):
            self.agent.wait(self.agent.start(text))
        return seen

    def test_greetings_by_name_and_sinhala_as_a_friend(self):
        for text in ('හෙලෝ', 'hi', 'ආයුබෝවන්', 'හෙලෝ ඔයාට කොහොමද'):
            with self.subTest(text=text):
                self.assertIn('චමිදූ', self.seen(text)['text'])
        self.assertIn('ඔයා, never ඔබ', self.seen('ඔයා කවුද?')['text'])
        self.assertNotIn('by name', self.seen('hi, open chrome')['text'], 'a command, not a hello')

    def test_a_poem_is_given_and_rudeness_met_warmly(self):
        self.assertIn('do not ask which kind', self.seen('මට කවියක් කියන්න')['text'])
        self.assertIn('annoyed', self.seen('ඔයාට මොලයක් නැද්ද')['text'])

    def test_none_of_it_is_in_the_system_prompt(self):
        """Measured 5 Nov: such sentences there dropped tool use from 6 in 6 to 3 in 10."""
        system = self.seen('Use calculate for 137 * 23')['system']
        for words in ('චමිදූ', 'poem', 'annoyed', 'ඔයා, never'):
            self.assertNotIn(words, system)

    def test_whisper_spelling_of_the_time_is_the_time(self):
        """Regression of 16 Oct's own-word rule, found replaying the live log."""
        self.assertEqual(router.route('Đáng mê lá và kiếm được.')['command'], 'time')
        self.assertEqual(router.route('Đáng mê lá và kiếm được.')['action'], 'command')


class CorrectionTests(unittest.TestCase):
    """The real voice brain, in a data folder of its own."""

    def setUp(self):
        private_root(self)
        import documents
        import memory
        import notifications
        import pocket
        import presence
        import routines
        import voice_loop
        self.patches = [
            patch.object(presence.Presence, 'start'), patch.object(routines.Routines, 'start'),
            patch.object(documents.Library, 'keep_current'),
            patch.object(memory.Memory, 'reindex_in_background'),
            patch.object(notifications, 'available', return_value=False),
            patch.object(pocket.Pocket, 'keep_in_step'),
            patch('speaker.verdict', return_value=('owner', 0.8)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.voice_loop = voice_loop
        self.respond, _, _ = voice_loop.build_brain(argparse.Namespace(
            silent=True, stt='auto', model=voice_loop.WHISPER_MODEL, whisper_device='auto'))
        import assistant_core
        self.store = Store(assistant_core.ROOT / 'data' / 'jarvis.db')

    def notes(self):
        return [r['title'] for r in self.store.records() if r['kind'] == 'note']

    def test_the_words(self):
        for text in ('ඒක නෙවේ මම අදහස් කරේ', 'එහෙම නෙමෙයි', "that's not what I meant",
                     'eka newe', 'වැරදියි'):
            with self.subTest(text=text):
                self.assertTrue(self.voice_loop.CORRECTION.search(text))
        for text in ('නෙවේ ගොඩක් හොඳයි', 'what time is it', 'ඒක හොඳයි'):
            with self.subTest(text=text):
                self.assertFalse(self.voice_loop.CORRECTION.search(text))

    def test_a_note_saved_by_mistake_is_offered_back_and_removed_on_yes(self):
        """Measured 5 Nov: a question about a note was saved as one, then "ඒක නෙවේ"."""
        self.respond('සටහනක් සේව් කරන්න බඩු ගන්න', speak=False)
        self.assertEqual(self.notes(), ['බඩු ගන්න'])
        offer = self.respond('ඒක නෙවේ මම අදහස් කරේ', speak=False)
        self.assertEqual(offer, responses.say_offer_undo_note('බඩු ගන්න', 'si'))
        self.assertEqual(self.notes(), ['බඩු ගන්න'], 'nothing removed before a yes')
        self.assertEqual(self.respond('ඔව්', speak=False), responses.say_note_removed('බඩු ගන්න', 'si'))
        self.assertEqual(self.notes(), [])

    def test_no_keeps_the_note(self):
        self.respond('සටහනක් සේව් කරන්න බඩු ගන්න', speak=False)
        self.respond('ඒක නෙවේ', speak=False)
        self.assertEqual(self.respond('නෑ', speak=False), 'හරි, තියාගත්තා.')
        self.assertEqual(self.notes(), ['බඩු ගන්න'])

    def test_other_corrections_go_to_the_model_with_the_exchange_in_view(self):
        asked = {}

        def start(prompt):
            asked['prompt'] = prompt
            asked['history'] = [m['content'] for m in self.store.history()]
            return 'j'
        with patch.object(agent_module.Agent, 'start', side_effect=start), \
                patch.object(agent_module.Agent, 'snapshot',
                             return_value={'state': 'done', 'answer': 'සමාවෙන්න.'}):
            self.respond('දැන් වෙලාව කීයද', speak=False)
            self.assertEqual(self.respond('ඒක නෙවේ මම අදහස් කරේ', speak=False), 'සමාවෙන්න.')
        self.assertIn('was not what they meant', asked['prompt'])
        self.assertIn('දැන් වෙලාව කීයද', asked['history'],
                      'the exchange answered by the router is in the history the model reads')


if __name__ == '__main__':
    unittest.main()
