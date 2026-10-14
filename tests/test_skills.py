"""Skills JARVIS writes for itself: refused before you see them, inert until you approve
them, tested before they are used, and run outside JARVIS (Day 21)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import permissions
import router
import skills
from assistant_core import Store, Tools, schemas

GOOD = skills.EXAMPLE_SKILL


def variant(old, new, code=GOOD):
    assert old in code, old
    return code.replace(old, new, 1)


class CheckTests(unittest.TestCase):
    def test_the_example_passes(self):
        self.assertEqual(skills.check(GOOD), [])

    def test_what_is_refused(self):
        cases = {
            'imports os': variant('import math', 'import os'),
            'imports from subprocess': variant('import math', 'from subprocess import run'),
            'relative import': variant('import math', 'from . import secrets'),
            'opens a file': variant('result = amount', "open('x', 'w'); result = amount"),
            'eval': variant('result = amount', "result = eval('1') * amount"),
            'getattr': variant('result = amount', "result = getattr(math, 'pi') * amount"),
            'dunder attribute': variant('result = amount', 'result = ().__class__ and amount'),
            'dunder in a string': variant("shown = f'{result", "shown = '{0.__class__}'.format(result); shown = f'{result"),
            'runs on load': variant('import math', "import math\nmath.sqrt(2)"),
            'print': variant('result = amount', 'print(1); result = amount'),
            'a class': variant('import math', 'import math\n\nclass Thing:\n    pass'),
            'a decorator': variant('def run(', 'import functools\n@functools.lru_cache\ndef run('),
            'wrong inputs': variant('def run(percent, amount):', 'def run(percent):'),
            'no Sinhala phrase': variant("'phrases': ['2000 න් සියයට 15 කීයද', 'සියයට කීයද',",
                                         "'phrases': ['a', 'b',"),
            'tier 1': variant("'tier': 0", "'tier': 1"),
            'one example': variant("        {'inputs': {'percent': 2.5, 'amount': 80}, 'expect': {'result': 2}},\n", ''),
            'not Python': GOOD + '\ndef broken(:\n',
            'too long': GOOD + '#' * skills.MAX_CHARS,
        }
        for name, code in cases.items():
            with self.subTest(name):
                self.assertTrue(skills.check(code), f'{name} was let through')

    def test_reading_a_skill_never_runs_it(self):
        """The description is read as data. A file that acts on load acts on nothing."""
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'ran.txt'
            path = Path(folder) / 'sneaky.py'
            path.write_text(variant('import math', f"import math\nopen(r'{marker}', 'w')"),
                            encoding='utf-8')
            self.assertEqual(skills.describe(path)['name'], 'percent_of')
            self.assertFalse(marker.exists())


class Folders(unittest.TestCase):
    """Throwaway pending/ and active/ folders, so the real ones are never touched."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.pending, self.active = root / 'pending', root / 'active'
        self.pending.mkdir()
        self.patches = [patch.object(skills, 'PENDING', self.pending),
                        patch.object(skills, 'ACTIVE', self.active)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def write_pending(self, code=GOOD, name='percent_of'):
        path = self.pending / f'{name}.py'
        path.write_text('# JARVIS skill -- test\n# NOT REVIEWED.\n' + code, encoding='utf-8')
        return path


class CallTests(Folders):
    def test_a_skill_runs_and_answers(self):
        path = self.write_pending()
        result = skills.call(path, {'percent': 15, 'amount': 2000})
        self.assertEqual(result['result'], 300)
        self.assertIn('300', result['en'])

    def test_it_runs_in_another_process(self):
        """Never inside JARVIS: a separate interpreter, with no view of this one."""
        path = self.active / 'whoami.py'
        self.active.mkdir()
        path.write_text('import os, sys\ndef run():\n    return {"pid": os.getpid(), '
                        '"isolated": sys.flags.isolated}\n', encoding='utf-8')
        result = skills.call(path, {})
        self.assertNotEqual(result['pid'], os.getpid())
        self.assertEqual(result['isolated'], 1)

    def test_a_skill_that_never_ends_is_stopped(self):
        self.active.mkdir()
        path = self.active / 'loop.py'
        path.write_text('def run():\n    while True:\n        pass\n', encoding='utf-8')
        started = time.monotonic()
        with self.assertRaises(skills.SkillError):
            skills.call(path, {}, timeout=1)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_failing_skill_reports_not_raises_into_jarvis(self):
        self.active.mkdir()
        path = self.active / 'broken.py'
        path.write_text('def run(x):\n    return 1 / 0\n', encoding='utf-8')
        with self.assertRaises(skills.SkillError) as caught:
            skills.call(path, {'x': 1})
        self.assertIn('ZeroDivisionError', str(caught.exception))


class ApprovalTests(Folders):
    def test_approval_tests_it_then_uses_it(self):
        self.write_pending()
        result = skills.approve('percent_of', commit=False)
        self.assertTrue(result['approved'], result)
        self.assertTrue(all(ok for ok, _ in result['tests']))
        self.assertFalse((self.pending / 'percent_of.py').exists())
        code = (self.active / 'percent_of.py').read_text(encoding='utf-8')
        self.assertNotIn('NOT REVIEWED', code)
        self.assertEqual([s['name'] for s in skills.active()], ['percent_of'])

    def test_a_skill_whose_examples_fail_is_not_used(self):
        self.write_pending(variant("'expect': {'result': 300}", "'expect': {'result': 301}"))
        result = skills.approve('percent_of', commit=False)
        self.assertFalse(result['approved'])
        self.assertTrue((self.pending / 'percent_of.py').exists())
        self.assertEqual(skills.active(), [])

    def test_a_skill_edited_after_writing_is_checked_again(self):
        self.write_pending(variant('import math', 'import os'))
        result = skills.approve('percent_of', commit=False)
        self.assertFalse(result['approved'])
        self.assertIn('imports os', result['problems'][0])

    def test_a_skill_must_say_something(self):
        self.write_pending(variant("            'si': f'{amount} න් සියයට {percent} ක් කියන්නේ {shown}.'",
                                   "            'si': ''"))
        self.assertFalse(skills.approve('percent_of', commit=False)['approved'])

    def test_reject_deletes_and_remembers_why(self):
        self.write_pending()
        skills.reject('percent_of', 'not needed')
        self.assertEqual(skills.pending(), [])
        self.assertIn('not needed', (self.pending.parent / 'rejected.log').read_text(encoding='utf-8'))

    def test_names_cannot_reach_outside_the_folder(self):
        for name in ('../assistant_core', 'percent_of.py', 'C:\\x'):
            with self.subTest(name=name):
                with self.assertRaises(skills.SkillError):
                    skills.approve(name, commit=False)


class WritingTests(Folders):
    def writer(self, *replies):
        replies = list(replies)
        calls = []

        def write(prompt):
            calls.append(prompt)
            return f'```python\n{replies.pop(0)}```', 'test-model'
        write.release = lambda: calls.append('released')
        return write, calls

    def test_a_refused_attempt_is_sent_back_once_with_the_reasons(self):
        write, calls = self.writer(variant('import math', 'import os'), GOOD)
        result = skills.request('percentages', write)
        self.assertEqual(result['problems'], [])
        self.assertIn('imports os', calls[1])
        self.assertEqual(calls[-1], 'released', 'the deep model is unloaded afterwards')
        code = Path(result['path']).read_text(encoding='utf-8')
        self.assertIn('NOT REVIEWED', code)
        self.assertEqual(skills.active(), [], 'writing a skill never makes it active')

    def test_twice_refused_is_kept_and_labelled(self):
        bad = variant('import math', 'import socket')
        write, _ = self.writer(bad, bad)
        result = skills.request('percentages', write)
        self.assertTrue(result['problems'])
        self.assertIn('REFUSED', Path(result['path']).read_text(encoding='utf-8'))

    def test_a_second_skill_of_the_same_name_does_not_replace_the_first(self):
        write, _ = self.writer(GOOD, GOOD)
        first = skills.request('percentages', write)
        second = skills.request('percentages', write)
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(skills.describe(second['path'])['name'], 'percent_of_2')
        self.assertEqual(skills.check(Path(second['path']).read_text(encoding='utf-8')), [])


class VoiceTests(Folders):
    def setUp(self):
        super().setUp()
        self.write_pending()
        skills.approve('percent_of', commit=False)

    def test_its_phrases_find_it(self):
        found = skills.match('what is 20 percent of 5000', skills.active())
        self.assertEqual(found['name'], 'percent_of')
        self.assertIsNone(skills.match('play some music', skills.active()))

    def test_inputs_from_speech_are_type_checked(self):
        skill = skills.active()[0]
        inputs = skills.extract_inputs(skill, 'x', lambda p, s: {'percent': '20', 'amount': 5000})
        self.assertEqual(inputs, {'percent': 20.0, 'amount': 5000.0})
        with self.assertRaises(skills.SkillError):
            skills.extract_inputs(skill, 'x', lambda p, s: {'percent': 20})

    def test_the_chat_model_gets_it_as_a_tool(self):
        names = [s['function']['name'] for s in schemas()]
        self.assertIn('skill_percent_of', names)
        self.assertEqual(permissions.tier('skill_percent_of'), permissions.READ)
        self.assertEqual(permissions.tier('skill_missing'), permissions.OUTWARD)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 't.db')
            tools = Tools(store, Path(folder) / 'w')
            self.assertEqual(tools.execute('skill_percent_of',
                                           {'percent': 10, 'amount': 50})['result'], 5)

    def test_asking_for_a_new_tool(self):
        outcome = router.route('make a new tool that converts kilometres to miles')
        self.assertEqual((outcome['action'], outcome['command']), ('command', 'make_skill'))
        self.assertEqual(outcome['content'], 'converts kilometres to miles')
        self.assertEqual(router.route('අලුත් tool එකක් හදන්න')['action'], 'ask')


if __name__ == '__main__':
    unittest.main()
