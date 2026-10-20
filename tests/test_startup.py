"""Starting JARVIS the way the service does (Day 16).

Added after a deploy that crashed on every start: inside start_watching, the message
watcher and the presence watcher were both called `watcher`, so Python treated the name as
local and the presence check raised UnboundLocalError. Every unit test passed, because
none of them ever called start_watching. This one does -- with the background threads
replaced, so nothing listens, speaks or indexes.
"""
import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import documents
import mail
import memory
import notifications
import presence
import routines
import voice_loop


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.started = []
        self.watcher = MagicMock()
        record = lambda name: (lambda *a, **k: self.started.append(name))
        self.patches = [
            patch.object(presence.Presence, 'start', record('presence')),
            patch.object(routines.Routines, 'start', record('routines')),
            patch.object(documents.Library, 'keep_current', record('files')),
            patch.object(memory.Memory, 'reindex_in_background', record('memory')),
            patch.object(notifications, 'allowed_apps', return_value=['whatsapp']),
            patch.object(notifications, 'available', return_value=True),
            patch.object(notifications, 'Watcher', return_value=self.watcher),
            # Day 9: the inbox watcher would reach the real Gmail from a test.
            patch.object(mail.Watcher, 'start', record('mail')),
        ]
        for p in self.patches:
            p.start()
        args = argparse.Namespace(silent=True, stt='auto', model=voice_loop.WHISPER_MODEL,
                                  whisper_device='auto')
        self.respond, _, self.start_watching = voice_loop.build_brain(args)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_a_failed_model_is_not_read_aloud(self):
        """Measured 16 Oct: "HTTPConnectionPool(host='127.0.0.1', port=11434): Read timed
        out." was spoken as the answer."""
        import agent
        import responses
        failed = {'state': 'error', 'answer': "HTTPConnectionPool(host='127.0.0.1'): Read timed out."}
        with patch.object(agent.Agent, 'start', return_value='j'), \
                patch.object(agent.Agent, 'snapshot', return_value=failed):
            reply = self.respond('who painted the mona lisa?', speak=False)
        self.assertEqual(reply, responses.say_no_answer('en'))

    def test_a_second_question_while_busy_gets_an_answer(self):
        import agent
        import responses
        with patch.object(agent.Agent, 'start', side_effect=ValueError('already running')):
            reply = self.respond('who painted the mona lisa?', speak=False)
        self.assertEqual(reply, responses.say_still_thinking('en'))

    def test_start_watching_starts_everything(self):
        self.start_watching()
        for name in ('presence', 'routines', 'files', 'memory', 'mail'):
            self.assertIn(name, self.started)

    def test_a_message_while_here_is_said_and_while_away_is_kept(self):
        self.start_watching()
        for _ in range(50):
            if self.watcher.watch.call_args:
                break
            __import__('time').sleep(.02)
        announce = self.watcher.watch.call_args.args[0]
        message = {'shown': 'amma: coming', 'spoken': 'අම්මාගෙන් මැසේජ් එකක්'}
        with patch.object(self.respond.routines, 'remark') as remark:
            self.respond.presence.state = 'here'
            announce(message)
            self.respond.presence.state = 'away'
            announce(message)
        self.assertEqual(remark.call_count, 1, 'said once, kept the second time')


if __name__ == '__main__':
    unittest.main()
