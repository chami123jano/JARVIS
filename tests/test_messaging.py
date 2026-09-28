"""Sending messages: who, what, and never without being asked.

This is the first thing JARVIS does that leaves the machine and carries the user's name.
A misheard sentence must not be able to message a family member, and the assistant must
never report having sent something it did not send -- the model has already been caught
announcing a saved note that was never saved.
"""
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contacts
import messaging
import permissions
from assistant_core import Store


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'c.db')
        contacts.add(self.store, 'amma', '0771234567', 'අම්මා')
        contacts.add(self.store, 'nangi', '+94 76 555 1234', 'නංගි')

    def tearDown(self):
        self.temp.cleanup()

    def test_phone_numbers_are_normalised(self):
        self.assertEqual(contacts.clean_phone('0771234567'), '94771234567')
        self.assertEqual(contacts.clean_phone('+94 77 123 4567'), '94771234567')
        for bad in ('', 'abcd', '12'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    contacts.clean_phone(bad)

    def test_the_dative_still_finds_the_person(self):
        """"අම්මට" is "to amma"; without stripping the ending nothing matches."""
        for spoken in ('අම්මට මැසේජ් එකක් යවන්න', 'ammata message ekak yavanna',
                       'send a message to amma', 'අම්මා'):
            with self.subTest(spoken=spoken):
                self.assertEqual(contacts.find(self.store, spoken)['name'], 'amma')

    def test_an_unknown_name_returns_nothing(self):
        """Guessing a recipient sends a private message to the wrong person."""
        for spoken in ('tell appa something', 'send a message to nobody', '', 'yawanna'):
            with self.subTest(spoken=spoken):
                self.assertIsNone(contacts.find(self.store, spoken))

    def test_the_recipient_is_removed_from_the_message(self):
        person = contacts.find(self.store, 'අම්මට මැසේජ් එකක් යවන්න මම රාත්‍රී එනවා')
        body = contacts.strip_name('අම්මට මම රාත්‍රී එනවා', person)
        self.assertEqual(body, 'මම රාත්‍රී එනවා')

    def test_only_the_matched_word_is_removed(self):
        """"මම" (I) is 0.86 similar to "අම්මා" (amma).

        Stripping anything that resembles the name deleted a word from the message.
        Only the word that actually identified the contact may be removed.
        """
        person = contacts.find(self.store, 'අම්මට කියන්න මම එනවා')
        self.assertEqual(person['matched_word'], 'අම්මට')
        body = contacts.strip_name('අම්මට කියන්න මම එනවා', person)
        self.assertIn('මම', body, 'a real word was deleted from the message')
        self.assertNotIn('අම්මට', body)

    def test_english_recipient_is_removed_without_eating_words(self):
        person = contacts.find(self.store, 'send a message to amma I am coming')
        body = contacts.strip_name('to amma I am coming', person)
        self.assertNotIn('amma', body)
        self.assertIn('am', body)

    def test_the_nickname_is_used_aloud_in_sinhala(self):
        person = contacts.find(self.store, 'amma')
        self.assertEqual(contacts.speakable(person, 'si'), 'අම්මා')
        self.assertEqual(contacts.speakable(person, 'en'), 'amma')


class LinkTests(unittest.TestCase):
    def test_sinhala_survives_the_link(self):
        """Careless encoding delivers rubbish, which looks like success."""
        for text in ('mama rathri enawa', 'මම රාත්‍රී එනවා',
                     'අම්මා, මම හවසට එනවා. කෑම හදන්න එපා.'):
            with self.subTest(text=text):
                link = messaging.build_link('94771234567', text)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
                self.assertEqual(query['text'][0], text)
                self.assertEqual(query['phone'][0], '94771234567')

    def test_a_number_without_digits_is_refused(self):
        with self.assertRaises(messaging.SendError):
            messaging.build_link('not-a-number', 'hello')

    def test_long_messages_are_truncated_not_rejected(self):
        link = messaging.build_link('94771234567', 'ක' * 9000)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        self.assertLessEqual(len(query['text'][0]), messaging.MAX_MESSAGE)


class SendingTests(unittest.TestCase):
    def test_nothing_is_sent_without_auto_send(self):
        """The default leaves the message in WhatsApp for a human to send."""
        with patch.object(messaging, 'open_chat', return_value={'opened': True}), \
             patch.object(messaging, 'press_enter',
                          side_effect=AssertionError('pressed Enter unasked')):
            result = messaging.send('94771234567', 'hello')
        self.assertFalse(result['sent'])

    def test_enter_is_refused_when_whatsapp_is_not_in_front(self):
        """A blind keystroke types the message into whatever has focus."""
        with patch.object(messaging, 'foreground_is_whatsapp', return_value=False):
            with self.assertRaises(messaging.SendError):
                messaging.press_enter()

    def test_a_failure_to_open_is_not_reported_as_sent(self):
        with patch.object(messaging, 'installed', return_value=False):
            with self.assertRaises(messaging.SendError):
                messaging.open_chat('94771234567', 'hello')


class PermissionTests(unittest.TestCase):
    def test_messaging_needs_confirmation(self):
        self.assertTrue(permissions.needs_confirmation('send_message'))
        self.assertFalse(permissions.needs_confirmation('system_status'))
        self.assertFalse(permissions.needs_confirmation('save_note'))

    def test_an_unknown_action_is_treated_as_dangerous(self):
        self.assertTrue(permissions.needs_confirmation('something_new'))

    def test_clear_agreement_only(self):
        for yes in ('හරි', 'ඔව්', 'yes', 'ok', 'යවන්න', 'send it'):
            with self.subTest(text=yes):
                self.assertTrue(permissions.is_yes(yes))
        for not_yes in ('හරිද', 'maybe not', 'I think it is ok to send that later',
                        'wait', '', 'නෑ'):
            with self.subTest(text=not_yes):
                self.assertFalse(permissions.is_yes(not_yes))

    def test_refusal_is_recognised(self):
        for no in ('නෑ', 'එපා', 'no', 'stop', 'cancel'):
            with self.subTest(text=no):
                self.assertTrue(permissions.is_no(no))

    def test_the_readback_names_both_the_person_and_the_words(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                spoken = permissions.describe(
                    'send_message', {'to': 'අම්මා', 'text': 'මම එනවා'}, lang)
                self.assertIn('අම්මා', spoken)
                self.assertIn('මම එනවා', spoken)

    def test_outward_actions_are_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'p.db')
            permissions.log(store, 'send_message', {'to': 'amma', 'text': 'hi'}, 'sent')
            entries = permissions.history(store)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['action'], 'send_message')
            self.assertIn('amma', entries[0]['detail'])


if __name__ == '__main__':
    unittest.main()
