"""See and click (Day 18): the window read through UI Automation, a plan of control names,
a look after every step, and nothing irreversible pressed without a yes.

The window and the model are replaced here, so no real program is touched; hands.py
itself drives the real Calculator in verify.py.
"""
import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hands
import permissions
import responses
import router
from sandbox import private_root


class FakeWindow:
    """A window whose display changes as buttons are pressed."""

    def __init__(self, names):
        self.Name = 'Calculator'
        self.NativeWindowHandle = 1
        self.names = names
        self.display = ''
        self.pressed = []

    def seen(self):
        controls = [{'n': i + 1, 'kind': 'Button', 'name': n, 'id': '', 'enabled': True}
                    for i, n in enumerate(self.names)]
        handles = [MagicMock(name=n) for n in self.names]
        for handle, name in zip(handles, self.names):
            handle.Name = name
        return {'title': self.Name, 'controls': controls,
                'texts': [f'Display is {self.display or 0}']}, handles


def run(task, window):
    def press(_, control):
        window.pressed.append(control.Name)
        window.display += {'Seven': '7', 'Eight': '8', 'Multiply by': 'x'}.get(control.Name, '')
        if control.Name == 'Equals':
            window.display = '56'
        return 'pressed'
    with patch.object(hands, 'window_for', return_value=window), \
            patch.object(hands, 'look', side_effect=lambda w: window.seen()), \
            patch.object(hands, 'press', side_effect=press), \
            patch.object(hands.time, 'sleep'):
        return task.run()


def answers(*replies):
    queue = list(replies)
    return lambda prompt, schema: queue.pop(0)


class TaskTests(unittest.TestCase):
    NAMES = ['Seven', 'Eight', 'Multiply by', 'Equals', 'Clear']

    def test_a_plan_is_carried_out_and_checked(self):
        window = FakeWindow(self.NAMES)
        task = hands.Task('7 x 8', 'calculator', report=lambda line: None, ask=answers(
            {'how': '7, times, 8, equals', 'steps': [{'action': 'click', 'control': n, 'text': ''}
             for n in ('Seven', 'Multiply by', 'Eight', 'Equals')], 'say': ''},
            {'should_show': '56', 'shows': '56', 'done': True, 'say': '7 x 8 is 56.'}))
        self.assertEqual(run(task, window), 'done')
        self.assertEqual(window.pressed, ['Seven', 'Multiply by', 'Eight', 'Equals'])
        self.assertEqual(task.result, '7 x 8 is 56.')

    def test_a_wrong_result_is_said_and_nothing_more_is_pressed_after_two_tries(self):
        """Measured 5 Nov: re-planning without limit after a finished 9 x 9 pressed on."""
        window = FakeWindow(self.NAMES)
        task = hands.Task('7 x 8', ask=answers(
            {'how': '', 'steps': [{'action': 'click', 'control': 'Seven', 'text': ''}], 'say': ''},
            {'should_show': '56', 'shows': '7', 'done': False, 'say': 'Not finished.'},
            {'how': '', 'steps': [{'action': 'click', 'control': 'Eight', 'text': ''}], 'say': ''},
            {'should_show': '56', 'shows': '78', 'done': False, 'say': 'It shows 78.'}),
            report=lambda line: None)
        self.assertEqual(run(task, window), 'stuck')
        self.assertEqual(window.pressed, ['Seven', 'Eight'])
        self.assertEqual(task.result, 'It shows 78.')

    def test_an_irreversible_button_waits_for_a_yes(self):
        window = FakeWindow(['Send', 'Cancel'])
        task = hands.Task('send it', ask=answers(
            {'how': '', 'steps': [{'action': 'click', 'control': 'Send', 'text': ''}], 'say': ''},
            {'should_show': 'sent', 'shows': 'sent', 'done': True, 'say': 'Sent.'}),
            report=lambda line: None)
        self.assertEqual(run(task, window), 'confirm')
        self.assertEqual((window.pressed, task.waiting), ([], 'Send'))
        with patch.object(hands, 'window_for', return_value=window), \
                patch.object(hands, 'look', side_effect=lambda w: window.seen()), \
                patch.object(hands, 'press', side_effect=lambda w, c: window.pressed.append(c.Name)
                             or window.names.append('Sent') or 'pressed'), \
                patch.object(hands.time, 'sleep'):
            self.assertEqual(task.confirm(), 'done')
        self.assertEqual(window.pressed, ['Send'])

    def test_only_controls_in_the_window_are_pressed(self):
        window = FakeWindow(self.NAMES)
        task = hands.Task('7 x 8', ask=answers(
            {'how': '', 'steps': [{'action': 'click', 'control': 'Launch missiles', 'text': ''}], 'say': ''},
            {'how': '', 'steps': [{'action': 'click', 'control': 'Launch missiles', 'text': ''}], 'say': ''}),
            report=lambda line: None)
        self.assertEqual(run(task, window), 'stuck')
        self.assertEqual(window.pressed, [])

    def test_ten_actions_at_most(self):
        window = FakeWindow(self.NAMES)
        many = {'how': '', 'say': '', 'steps': [{'action': 'click', 'control': 'Seven', 'text': ''}] * 10}
        task = hands.Task('press seven forever', ask=answers(
            many, {'should_show': 'x', 'shows': 'y', 'done': False, 'say': 'no'}, many),
            report=lambda line: None)
        outcome = run(task, window)
        self.assertIn(outcome, ('limit', 'stuck'))
        self.assertLessEqual(task.actions, hands.MAX_ACTIONS)


class PlanTests(unittest.TestCase):
    def test_a_second_plan_does_not_start_over(self):
        """Measured 5 Nov: after Seven, Multiply by, Eight, the next plan began with them
        again, and 7 x 8 became 7 x 88 = 616."""
        steps = lambda *names: [{'action': 'click', 'control': n, 'text': ''} for n in names]
        done = ['pressed Seven', 'pressed Multiply by', 'pressed Eight']
        self.assertEqual([s['control'] for s in hands.not_again(
            steps('Seven', 'Multiply by', 'Eight', 'Equals'), done)], ['Equals'])
        self.assertEqual(hands.not_again(steps('Seven', 'Multiply by', 'Eight'), done), [])
        self.assertEqual([s['control'] for s in hands.not_again(steps('Equals'), done)], ['Equals'])


class GoalTests(unittest.TestCase):
    def test_only_the_task_reaches_the_planner(self):
        """Measured 5 Nov: with the Sinhala words around it, 64 / 8 lost its Four."""
        for text, app, goal in (('calculator එකේ 64 / 8 ඔබන්න', 'calculator', '64 / 8'),
                                ('calculator eke 9 x 9 obanna', 'calculator', '9 x 9'),
                                ('in calculator press 9 times 9', 'calculator', '9 times 9')):
            with self.subTest(text=text):
                self.assertEqual(hands.goal_of(text, app), goal)

    def test_the_numbers_decide_not_the_models_word(self):
        """Measured 5 Nov: "done" for 12 + 30 with 246 on the display."""
        self.assertFalse(hands.numbers_agree({'should_show': '42', 'shows': 'Display is 246'}))
        self.assertTrue(hands.numbers_agree({'should_show': '42', 'shows': 'Display is 42'}))
        self.assertTrue(hands.numbers_agree({'should_show': 'the File menu open', 'shows': 'menu'}))


class SafetyTests(unittest.TestCase):
    def test_what_counts_as_irreversible(self):
        for name in ('Send', 'Delete', 'Buy now', 'Pay', 'Install', 'Sign out', 'Submit', 'Confirm'):
            with self.subTest(name=name):
                self.assertTrue(hands.IRREVERSIBLE.search(name))
        for name in ('Seven', 'Equals', 'File', 'Bold', 'Open', 'Next'):
            with self.subTest(name=name):
                self.assertFalse(hands.IRREVERSIBLE.search(name))

    def test_no_secrets_typed_and_no_password_managers(self):
        control = MagicMock(Name='Password', AutomationId='pw')
        with self.assertRaises(hands.HandsError):
            hands.type_into(MagicMock(), control, 'hunter2')
        self.assertTrue(hands.NO_GO.search('Bitwarden'))
        self.assertEqual(permissions.tier('click'), permissions.OUTWARD)

    def test_a_press_that_was_accepted_is_not_pressed_again(self):
        """Measured 5 Nov: the second Nine of 9 x 9 changed nothing on screen, the mouse
        pressed it again, and the answer was 891."""
        control = MagicMock()
        with patch.object(hands, 'to_front'):
            self.assertEqual(hands.press(MagicMock(), control), 'pressed')
        control.GetInvokePattern.return_value.Invoke.assert_called_once()
        control.Click.assert_not_called()


class VoiceTests(unittest.TestCase):
    def setUp(self):
        private_root(self)
        import documents
        import memory
        import notifications
        import pocket
        import presence
        import routines
        import voice_loop
        for p in (patch.object(presence.Presence, 'start'), patch.object(routines.Routines, 'start'),
                  patch.object(documents.Library, 'keep_current'),
                  patch.object(memory.Memory, 'reindex_in_background'),
                  patch.object(notifications, 'available', return_value=False),
                  patch.object(pocket.Pocket, 'keep_in_step'),
                  patch('speaker.verdict', return_value=('owner', 0.8))):
            p.start()
            self.addCleanup(p.stop)
        self.respond, _, _ = voice_loop.build_brain(argparse.Namespace(
            silent=True, stt='auto', model=voice_loop.WHISPER_MODEL, whisper_device='auto'))

    def test_routing(self):
        for text in ('calculator එකේ 7 x 8 ඔබන්න', 'notepad එකේ File ක්ලික් කරන්න', 'click the OK button'):
            with self.subTest(text=text):
                self.assertEqual(router.route(text)['command'], 'operate')
        self.assertEqual(router.route('calculate 5 times 6')['command'], 'calculate')

    def test_the_app_named_is_the_one_worked_in_and_an_irreversible_step_asks(self):
        seen = {}

        def run(self_task):
            seen['app'], seen['goal'] = self_task.app, self_task.goal
            self_task.waiting = 'Send'
            return 'confirm'
        with patch.object(hands.Task, 'run', run), \
                patch.object(hands.Task, 'confirm', lambda self_task: 'done'):
            asked = self.respond('calculator එකේ 7 x 8 ඔබන්න', speak=False)
            self.assertEqual(seen['app'], 'calculator')
            self.assertIn('Send', asked)
            self.assertEqual(self.respond('නෑ', speak=False), 'හරි, ඔබන්නේ නෑ.')

    def test_another_voice_cannot_operate(self):
        with patch('speaker.verdict', return_value=('other', 0.1)), \
                patch.object(hands.Task, 'run') as ran:
            self.respond('calculator එකේ 7 x 8 ඔබන්න', speak=False)
        ran.assert_not_called()


if __name__ == '__main__':
    unittest.main()
