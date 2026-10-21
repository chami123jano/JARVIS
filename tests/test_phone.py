"""The phone bridge for an iPhone (Day 8): calls through Phone Link, read back and placed
only on a spoken yes; Find My for a lost phone. No test here dials anyone."""
import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contacts
import permissions
import phone
import responses
import router

AMMA = {'name': 'amma', 'nickname': 'අම්මා', 'phone': '94771234567', 'channel': 'whatsapp'}


class NumberTests(unittest.TestCase):
    def test_a_number_said_in_the_sentence(self):
        for text in ('0771234567ට call කරන්න', 'call 077 123 4567', '+94 77 123 4567 ekata call karanna'):
            with self.subTest(text=text):
                self.assertEqual(phone.number_in(text), '94771234567')
        for text in ('ammata call ekak ganna', '2 + 2', 'call 12345'):
            with self.subTest(text=text):
                self.assertEqual(phone.number_in(text), '')

    def test_read_out_the_local_way(self):
        self.assertEqual(phone.speakable_number('94771234567'), '077 123 4567')

    def test_a_call_is_a_tel_link_for_phone_link(self):
        with patch.object(phone.os, 'startfile', create=True) as opened:
            self.assertEqual(phone.call('94771234567'), {'dialing': '+94771234567'})
        opened.assert_called_once_with('tel:+94771234567')
        with self.assertRaises(phone.PhoneError):
            phone.call('123')

    def test_a_call_is_tier_two(self):
        self.assertEqual(permissions.tier('call'), permissions.OUTWARD)
        self.assertIn('077 123 4567', permissions.describe(
            'call', {'to': 'අම්මා', 'number': '077 123 4567'}, 'si'))


class RoutingTests(unittest.TestCase):
    def test_calls_and_the_lost_phone(self):
        for text, command in (('අම්මාට call එකක් ගන්න', 'call'), ('kasunta call karanna', 'call'),
                              ('0771234567ට call කරන්න', 'call'), ('මගේ phone එක කොහෙද', 'find_phone'),
                              ('where is my phone', 'find_phone')):
            with self.subTest(text=text):
                self.assertEqual(router.route(text)['command'], command)

    def test_remind_me_to_call_is_a_reminder(self):
        """Measured 21 Oct: the new call command tied with the reminder for this."""
        decision = router.route('පැය දෙකකින් අම්මාට කෝල් කරන්න මතක් කරන්න')
        self.assertEqual((decision['command'], decision['seconds']), ('reminder', 7200))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        import documents
        import memory
        import notifications
        import presence
        import routines
        import voice_loop
        self.patches = [
            patch.object(presence.Presence, 'start'), patch.object(routines.Routines, 'start'),
            patch.object(documents.Library, 'keep_current'),
            patch.object(memory.Memory, 'reindex_in_background'),
            patch.object(notifications, 'available', return_value=False),
            patch.object(permissions, 'log'),
            patch('speaker.verdict', return_value=('owner', 0.8)),
            patch.object(contacts, 'find', side_effect=lambda store, text:
                         AMMA if 'amm' in text.lower() or 'අම්මා' in text else None),
            patch.object(phone.os, 'startfile', create=True),
            patch.object(phone.webbrowser, 'open', return_value=True),
        ]
        for p in self.patches:
            p.start()
        self.dialled = phone.os.startfile
        self.respond, _, _ = voice_loop.build_brain(argparse.Namespace(
            silent=True, stt='auto', model=voice_loop.WHISPER_MODEL, whisper_device='auto'))

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_read_back_then_yes_dials_once(self):
        readback = self.respond('අම්මාට call එකක් ගන්න', speak=False)
        self.assertIn('077 123 4567', readback)
        self.dialled.assert_not_called()
        self.assertEqual(self.respond('ඔව්', speak=False), responses.say_calling('අම්මා', 'si'))
        self.dialled.assert_called_once_with('tel:+94771234567')
        self.respond('ඔව්', speak=False)
        self.dialled.assert_called_once()           # a second yes dials nothing more

    def test_no_dials_nothing(self):
        self.respond('අම්මාට call එකක් ගන්න', speak=False)
        self.respond('නෑ', speak=False)
        self.dialled.assert_not_called()

    def test_a_number_said_aloud_can_be_called(self):
        self.assertIn('077 123 4567', self.respond('0771234567ට call කරන්න', speak=False))
        self.respond('ඔව්', speak=False)
        self.dialled.assert_called_once_with('tel:+94771234567')

    def test_nobody_known_is_asked_about(self):
        text = 'kasunta call karanna'
        self.assertEqual(self.respond(text, speak=False),
                         responses.say_who_to_call(router.route(text)['language']))

    def test_another_voice_cannot_call(self):
        with patch('speaker.verdict', return_value=('other', 0.1)):
            self.respond('අම්මාට call එකක් ගන්න', speak=False)
            self.respond('ඔව්', speak=False)
        self.dialled.assert_not_called()

    def test_finding_the_phone_opens_find_my(self):
        self.assertEqual(self.respond('මගේ phone එක කොහෙද', speak=False),
                         responses.say_find_phone('si'))
        phone.webbrowser.open.assert_called_once_with(phone.FIND_MY)


if __name__ == '__main__':
    unittest.main()
