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
        ]
        for p in self.patches:
            p.start()
        args = argparse.Namespace(silent=True, stt='auto', model=voice_loop.WHISPER_MODEL,
                                  whisper_device='auto')
        self.respond, _, self.start_watching = voice_loop.build_brain(args)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_start_watching_starts_everything(self):
        self.start_watching()
        for name in ('presence', 'routines', 'files', 'memory'):
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
