"""Hearing about incoming messages.

This component sees every notification on the machine, including banking codes and
messages from people the user never asked to be told about. These tests are mostly about
what it must NOT say.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contacts
import notifications as nf
import responses
from assistant_core import Store
from router import route


def notification(app, title, body=''):
    return {'id': 1, 'app': app, 'title': title, 'body': body, 'texts': [title, body]}


class AllowlistTests(unittest.TestCase):
    """Nothing is heard until it is deliberately permitted."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'n.db')
        contacts.add(self.store, 'amma', '0771234567', 'අම්මා')

    def tearDown(self):
        self.temp.cleanup()

    def test_nothing_is_announced_by_default(self):
        self.assertEqual(nf.allowed_apps(self.store), [])
        self.assertIsNone(nf.describe(notification('WhatsApp', 'amma', 'hello'), self.store))

    def test_only_permitted_applications_are_heard(self):
        nf.allow(self.store, 'WhatsApp')
        self.assertIsNotNone(nf.describe(notification('WhatsApp', 'amma', 'hi'), self.store))
        for other in ('Google Chrome', 'Windows Security', 'NVIDIA App', 'Armoury Crate'):
            with self.subTest(app=other):
                self.assertIsNone(nf.describe(notification(other, 'x', 'y'), self.store))

    def test_permission_can_be_withdrawn(self):
        nf.allow(self.store, 'WhatsApp')
        nf.disallow(self.store, 'WhatsApp')
        self.assertIsNone(nf.describe(notification('WhatsApp', 'amma', 'hi'), self.store))

    def test_a_sender_can_be_muted_without_muting_the_app(self):
        nf.allow(self.store, 'WhatsApp')
        self.store.set_config('notify_mute', ['amma'])
        self.assertIsNone(nf.describe(notification('WhatsApp', 'amma', 'hi'), self.store))
        self.assertIsNotNone(nf.describe(notification('WhatsApp', 'nangi', 'hi'), self.store))


class CodeTests(unittest.TestCase):
    """A one-time code must never be read out in a room with other people in it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'n.db')
        nf.allow(self.store, 'WhatsApp')

    def tearDown(self):
        self.temp.cleanup()

    def test_codes_are_recognised(self):
        for text in ('Your OTP is 384521', 'verification code: 992 110',
                     'ඔබගේ කේතය 4821', 'Use PIN 7781 to verify',
                     'Your verification code is 112233'):
            with self.subTest(text=text):
                self.assertTrue(nf.looks_like_a_code(text), f'{text!r} would be read aloud')

    def test_ordinary_messages_are_not_mistaken_for_codes(self):
        for text in ('මම ගෙදර එනවා', 'Meeting at 3', 'අද රෑට කෑම හදන්න එපා',
                     'see you at 5'):
            with self.subTest(text=text):
                self.assertFalse(nf.looks_like_a_code(text), f'{text!r} silenced wrongly')

    def test_a_code_is_shown_but_not_spoken(self):
        result = nf.describe(notification('WhatsApp', 'Bank', 'Your OTP is 384521'),
                             self.store)
        self.assertTrue(result['code'])
        self.assertNotIn('384521', result['spoken'])
        self.assertIn('384521', result['shown'])


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'n.db')
        contacts.add(self.store, 'amma', '0771234567', 'අම්මා')
        nf.allow(self.store, 'WhatsApp')

    def tearDown(self):
        self.temp.cleanup()

    def test_a_known_sender_is_named_the_way_you_call_them(self):
        result = nf.describe(notification('WhatsApp', 'amma', 'මම එනවා'), self.store)
        self.assertIn('අම්මා', result['spoken'])

    def test_sender_only_mode_leaves_the_body_unspoken(self):
        """Announcing only who messaged costs no data, since that phrase is cached."""
        self.store.set_config('read_message_body', False)
        result = nf.describe(notification('WhatsApp', 'amma', 'අද රෑට කෑම හදන්න එපා'),
                             self.store)
        self.assertNotIn('කෑම', result['spoken'])
        self.assertIn('කෑම', result['shown'])

    def test_an_empty_notification_says_nothing(self):
        self.assertIsNone(nf.describe(notification('WhatsApp', '', ''), self.store))


class RoutingTests(unittest.TestCase):
    def test_asking_what_arrived_routes(self):
        for spoken in ('මොනවද ආවේ', 'කවුද මැසේජ් කළේ', 'what messages came',
                       'who messaged me'):
            with self.subTest(spoken=spoken):
                outcome = route(spoken)
                self.assertEqual(outcome['action'], 'command')
                self.assertEqual(outcome['command'], 'read_messages')

    def test_asking_to_send_is_still_a_different_command(self):
        self.assertEqual(route('අම්මට මැසේජ් එකක් යවන්න මම එනවා')['command'],
                         'send_message')


class SummaryTests(unittest.TestCase):
    def test_nothing_new_says_so(self):
        self.assertIn('නෑ', responses.say_recent_messages([], 'si'))
        self.assertIn('Nothing', responses.say_recent_messages([], 'en'))

    def test_counts_are_words_not_digits(self):
        """"three messages", not "3 messages", which a voice reads as a numeral."""
        messages = [{'sender': name, 'body': 'hello'}
                    for name in ('amma', 'nangi', 'thaththa')]
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                spoken = responses.say_recent_messages(messages, lang)
                self.assertFalse(any(c.isdigit() for c in spoken), spoken)
                self.assertIn('තුන' if lang == 'si' else 'three', spoken)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'n.db')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_new_notifications_are_returned(self):
        watcher = nf.Watcher(self.store)
        first = [{'id': 1, 'app': 'WhatsApp', 'title': 'a', 'body': '', 'texts': []}]
        second = first + [{'id': 2, 'app': 'WhatsApp', 'title': 'b', 'body': '', 'texts': []}]
        with patch.object(nf, 'read_all', return_value=first):
            watcher.prime()
        with patch.object(nf, 'read_all', return_value=second):
            fresh = watcher.poll()
        self.assertEqual([item['id'] for item in fresh], [2],
                         'an already-seen notification was announced again')

    def test_starting_up_does_not_announce_the_backlog(self):
        """Twenty-six notifications are already on screen; none of them is news."""
        watcher = nf.Watcher(self.store)
        existing = [{'id': i, 'app': 'WhatsApp', 'title': 't', 'body': '', 'texts': []}
                    for i in range(26)]
        with patch.object(nf, 'read_all', return_value=existing):
            watcher.prime()
            self.assertEqual(watcher.poll(), [])


if __name__ == '__main__':
    unittest.main()
