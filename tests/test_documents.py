"""It knows your own files (Day 13): found, cited, kept current, and never a secret."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as agent_module
import documents
import memory
from agent import Agent
from assistant_core import Store, Tools
from pdf_fixture import make_pdf
from test_memory import embed, gloss


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.files = root / 'me'
        self.files.mkdir()
        self.store = Store(root / 'j.db')
        self.read = []

        def counting_embed(texts):
            self.read.extend(texts)
            return embed(texts)
        self.memory = memory.Memory(self.store, gloss=gloss, embed=counting_embed)
        self.library = documents.Library(self.store, self.memory, roots=[str(self.files)])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text):
        path = self.files / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path


class CitationTests(LibraryCase):
    def test_lines_are_cited(self):
        lines = [f'filler line {n} about nothing much' for n in range(60)]
        lines[50] = 'The deployment runs on Fridays from the release branch'
        self.write('notes/deploy.md', '\n'.join(lines))
        self.library.index()
        found = self.library.search('when does deployment run', floor=-1)
        self.assertTrue(found[0]['where'].startswith('deploy.md, lines '))
        self.assertLessEqual(found[0]['start'], 51)
        self.assertGreaterEqual(found[0]['end'], 51)

    def test_pdf_pages_are_cited(self):
        make_pdf(self.files / 'cv.pdf', ['Curriculum vitae', 'Skills: Python, Kotlin, Figma'])
        self.library.index()
        found = self.library.search('skills python kotlin', floor=-1)
        self.assertEqual(found[0]['where'], 'cv.pdf, page 1')

    def test_html_is_read_as_text(self):
        self.write('cv.html', '<html><style>.x{}</style><body><h1>Skills</h1>'
                              '<li>Kotlin for Android apps</li><li>Figma for interface '
                              'design</li></body></html>')
        self.assertNotIn('<li>', documents.chunks_of(self.files / 'cv.html')[0][3])


class SecretTests(LibraryCase):
    def test_secrets_are_never_indexed(self):
        self.write('.env', 'API_KEY=abc')
        self.write('app/credentials.json', '{"user": "x"}')
        self.write('scripts/deploy.py', 'TOKEN = "ghp_' + 'a' * 36 + '"\nprint("deploy")\n' * 3)
        self.write('notes/plain.md', 'Nothing secret here at all, just notes about the garden.')
        report = self.library.index()
        with self.store.connect() as db:
            texts = ' '.join(r[0] for r in db.execute('SELECT text FROM file_chunks'))
        self.assertNotIn('ghp_', texts)
        self.assertNotIn('API_KEY', texts)
        self.assertIn('garden', texts)
        self.assertGreaterEqual(report['skipped'], 2)
        reasons = dict(self.library.status()['skipped'])
        self.assertIn('token', reasons[str(self.files / 'scripts' / 'deploy.py')])

    def test_dependency_and_hidden_folders_are_skipped(self):
        self.write('node_modules/lib/index.js', 'module.exports = 1 // a long enough line here')
        self.write('.git/config', 'not a type we read anyway but hidden')
        self.write('.hidden/notes.md', 'hidden notes that should never be read by anyone')
        self.write('src/app.js', 'function start() { return "a real source file of ours"; }')
        self.library.index()
        paths = {Path(p).name for p in self.library.known()}
        self.assertEqual(paths, {'app.js'})


class IncrementalTests(LibraryCase):
    def test_only_what_changed_is_read_again(self):
        self.write('a.md', 'The first document is about mangoes and their season.')
        self.write('b.md', 'The second document is about cricket and the weather.')
        self.assertEqual(self.library.index()['read'], 2)
        self.read.clear()
        self.assertEqual(self.library.index()['read'], 0)
        self.assertEqual(self.read, [], 'nothing unchanged is embedded again')
        time.sleep(0.05)
        path = self.write('b.md', 'The second document is now about rugby instead of cricket.')
        os.utime(path, (time.time() + 5, time.time() + 5))
        report = self.library.index()
        self.assertEqual((report['read'], report['unchanged']), (1, 1))
        self.assertTrue(self.library.search('rugby', floor=-1)[0]['text'].count('rugby'))

    def test_a_deleted_file_is_forgotten(self):
        path = self.write('gone.md', 'A document that is about to be deleted for good.')
        self.library.index()
        path.unlink()
        self.assertEqual(self.library.index()['removed'], 1)
        self.assertEqual(self.library.search('deleted document', floor=-1), [])


class AgentTests(LibraryCase):
    def test_files_are_read_only_when_the_question_is_about_them(self):
        """Small talk matched stray files at 0.69; the words decide, not the score."""
        agent = Agent(self.store, Tools(self.store, Path(self.temp.name) / 'w'))
        with patch('documents.Library.search', return_value=[{'where': 'CV.pdf, page 2'}]) as search:
            self.assertEqual(agent.file_excerpts('hello how are you'), [])
            self.assertEqual(agent.file_excerpts('what time is it'), [])
            search.assert_not_called()
            self.assertTrue(agent.file_excerpts('mage CV eke mona skills tikada thiyenne'))
            self.assertTrue(agent.file_excerpts('මගේ ප්‍රොජෙක්ට් එකේ කෝඩ් එක'))

    def test_an_answer_from_files_always_names_one(self):
        sources = ['CV.pdf, page 2', 'cv.html, lines 1-40']
        self.assertEqual(Agent.with_source('Python and Kotlin.', sources),
                         'Python and Kotlin. (CV.pdf, page 2)')
        self.assertEqual(Agent.with_source('Your CV.pdf lists Python.', sources),
                         'Your CV.pdf lists Python.', 'already cited, left alone')
        self.assertEqual(Agent.with_source('Hello.', []), 'Hello.')

    def test_an_answer_that_found_nothing_is_not_given_a_source(self):
        """Measured: "I cannot find a file named project-notes (project-notes.md, ...)"."""
        sources, texts = ['project-notes.md, lines 1-5'], ['Demo moved to Thursday.']
        self.assertEqual(Agent.with_source('I cannot find a file named project-notes.',
                                           sources, texts),
                         'I cannot find a file named project-notes.')
        self.assertTrue(Agent.with_source('The demo moved to Thursday.', sources, texts)
                        .endswith('(project-notes.md, lines 1-5)'))

    def test_the_model_can_search_too(self):
        tools = Tools(self.store, Path(self.temp.name) / 'w')
        found = [{'where': 'a.md, lines 1-2', 'path': 'x', 'text': 'mangoes'}]
        with patch('documents.Library.search', return_value=found):
            result = tools.execute('search_files', {'question': 'mangoes'})
        self.assertEqual(result['excerpts'][0]['where'], 'a.md, lines 1-2')
        import permissions
        self.assertEqual(permissions.tier('search_files'), permissions.READ)


if __name__ == '__main__':
    unittest.main()
