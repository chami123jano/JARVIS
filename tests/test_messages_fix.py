"""Found 7 Nov in a real conversation: a message request sent to the model (which said it
could not send WhatsApp messages), an answer to "who?" not taken as one, and Phone Link's
summary notice announced as a sender. Plus a guard on the code itself."""
import argparse
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contacts
import notifications
import permissions
import responses
import router
from sandbox import private_root

AMMA = {'name': 'amma', 'nickname': 'අම්මා', 'phone': '94771234567', 'channel': 'whatsapp'}


class RoutingTests(unittest.TestCase):
    def test_a_message_and_a_sending_word_mean_a_message(self):
        for text, content in (('send msg to amma', None), ('send a msg to amma "kohomada"', 'kohomada'),
                              ('send a msg to amma from whatsapp "kohomada"', 'kohomada')):
            with self.subTest(text=text):
                decision = router.route(text)
                self.assertEqual((decision['action'], decision['command']), ('command', 'send_message'))
                if content:
                    self.assertEqual(decision['content'], content)

    def test_either_alone_does_not(self):
        self.assertEqual(router.route('read my messages')['command'], 'read_messages')
        self.assertNotEqual(router.route('what is a text message')['action'], 'command')
        self.assertEqual(router.route('සින්දුවක් දාන්න')['command'], 'play_music')


class RecipientTests(unittest.TestCase):
    def setUp(self):
        private_root(self)
        import documents
        import memory
        import pocket
        import presence
        import routines
        import voice_loop
        self.people = []
        for p in (patch.object(presence.Presence, 'start'), patch.object(routines.Routines, 'start'),
                  patch.object(documents.Library, 'keep_current'),
                  patch.object(memory.Memory, 'reindex_in_background'),
                  patch.object(notifications, 'available', return_value=False),
                  patch.object(pocket.Pocket, 'keep_in_step'),
                  patch.object(permissions, 'log'),
                  patch('speaker.verdict', return_value=('owner', 0.8)),
                  patch.object(contacts, 'all_contacts', side_effect=lambda store: self.people),
                  patch.object(contacts, 'find', side_effect=lambda store, text:
                               AMMA if self.people and 'amm' in text.lower() else None)):
            p.start()
            self.addCleanup(p.stop)
        self.respond, _, _ = voice_loop.build_brain(argparse.Namespace(
            silent=True, stt='auto', model=voice_loop.WHISPER_MODEL, whisper_device='auto'))

    def test_with_nobody_saved_it_says_so(self):
        said = self.respond('send a msg to amma "kohomada"', speak=False)
        self.assertEqual(said, responses.say_who_to_message(True, 'en'))

    def test_the_next_words_answer_who(self):
        self.people = [AMMA]
        with patch.object(contacts, 'find', side_effect=[None, AMMA]):
            self.assertEqual(self.respond('send a msg to kasun "kohomada"', speak=False),
                             responses.say_who_to_message(False, 'en'))
            readback = self.respond('amma mother', speak=False)
        self.assertIn('kohomada', readback)
        self.assertIn('?', readback, 'read back for a yes, nothing sent yet')

    def test_someone_unknown_is_said_plainly(self):
        self.people = [AMMA]
        with patch.object(contacts, 'find', return_value=None):
            self.respond('send a msg to kasun "kohomada"', speak=False)
            self.assertIn('not in your contacts', self.respond('kasun', speak=False))


class NotificationTests(unittest.TestCase):
    def test_a_summary_is_not_a_sender(self):
        import tempfile
        from assistant_core import Store
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'n.db')
            notifications.allow(store, 'Messages')
            item = dict(notifications.shaped('Phone Link', ['Messages', 'You may have new messages']), id=1)
            self.assertIsNone(notifications.describe(item, store))
            real = dict(notifications.shaped('Phone Link', ['Messages', 'Kasun', 'hi']), id=2)
            self.assertIsNotNone(notifications.describe(real, store))


class CodeTests(unittest.TestCase):
    def test_no_invisible_control_characters_in_the_code(self):
        """Three times a scripted edit turned "\b" into a backspace character inside a
        pattern, silently breaking it (5 and 7 Nov)."""
        root = Path(__file__).resolve().parents[1]
        found = []
        for path in list(root.glob('*.py')) + list(root.glob('tests/*.py')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', line):
                    found.append(f'{path.name}:{number}')
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
