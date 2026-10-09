"""Controlling this machine.

The router has matched volume_up, open_app, screenshot and lock_pc since Day 5 and done
nothing, because nothing stood behind them. What these guard is that the reply describes
what actually happened, and that a misheard sentence cannot reach something irreversible.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pc_control
import permissions
import responses
from router import route


class AppMatchingTests(unittest.TestCase):
    """Windows lists 223 applications; the old open_app knew four."""

    def test_common_applications_resolve(self):
        for spoken, expected in [('chrome', 'google chrome'), ('notepad', 'notepad'),
                                 ('whatsapp', 'whatsapp'), ('calculator', 'calculator')]:
            with self.subTest(spoken=spoken):
                found = pc_control.find_app(spoken)
                self.assertIsNotNone(found, f'{spoken} did not resolve')
                self.assertEqual(found['name'], expected)

    def test_sinhala_app_names_resolve(self):
        found = pc_control.find_app('ක්‍රෝම්')
        self.assertIsNotNone(found)
        self.assertIn('chrome', found['name'])

    def test_an_abbreviation_resolves_through_the_alias_table(self):
        """"vs code" shares almost no letters with "Visual Studio Code"."""
        for spoken in ('vs code', 'vscode'):
            with self.subTest(spoken=spoken):
                self.assertEqual(pc_control.find_app(spoken)['name'], 'visual studio code')

    def test_containment_prefers_the_closer_name(self):
        """Flat scoring made "chrome" pick "Chrome Remote Desktop"."""
        self.assertEqual(pc_control.find_app('chrome')['name'], 'google chrome')

    def test_an_unknown_name_returns_nothing(self):
        for spoken in ('nonexistentapplication', '', 'zzzzqqq'):
            with self.subTest(spoken=spoken):
                self.assertIsNone(pc_control.find_app(spoken))

    def test_opening_something_unknown_fails_loudly(self):
        with self.assertRaises(pc_control.ControlError):
            pc_control.open_app('nonexistentapplication')


class RoutingTests(unittest.TestCase):
    def test_the_application_name_survives_the_command_words(self):
        for spoken, expected in [('ක්‍රෝම් එක ඕපන් කරන්න', 'google chrome'),
                                 ('open chrome', 'google chrome'),
                                 ('notepad එක ඕපන් කරන්න', 'notepad'),
                                 ('open whatsapp', 'whatsapp')]:
            with self.subTest(spoken=spoken):
                outcome = route(spoken)
                self.assertEqual(outcome['action'], 'command')
                self.assertEqual(outcome['command'], 'open_app')
                # The extracted name must resolve to the right application, whichever
                # script it was spoken in.
                found = pc_control.find_app(outcome['content'])
                self.assertIsNotNone(found, f'{outcome["content"]!r} resolved to nothing')
                self.assertEqual(found['name'], expected)

    def test_open_with_no_application_asks(self):
        outcome = route('ඕපන් කරන්න')
        self.assertEqual(outcome['action'], 'ask')
        self.assertEqual(outcome['detail'], 'app')

    def test_volume_and_screenshot_still_route(self):
        for spoken, expected in [('වොලියුම් එක වැඩි කරන්න', 'volume_up'),
                                 ('වොලියුම් එක අඩු කරන්න', 'volume_down'),
                                 ('ස්ක්‍රීන්ෂොට් එකක් ගන්න', 'screenshot'),
                                 ('පරිගණකය ලොක් කරන්න', 'lock_pc')]:
            with self.subTest(spoken=spoken):
                self.assertEqual(route(spoken)['command'], expected)


class PermissionTests(unittest.TestCase):
    def test_machine_control_is_local_not_outward(self):
        for action in ('volume_up', 'volume_down', 'screenshot', 'open_app', 'lock_pc'):
            with self.subTest(action=action):
                self.assertFalse(permissions.needs_confirmation(action))
                self.assertLessEqual(permissions.tier(action), permissions.LOCAL)

    def test_the_irreversible_ones_stay_at_the_top_tier(self):
        """Shutting down and deleting are not built, and must not become easy to reach."""
        for action in ('shutdown', 'delete_file', 'purchase'):
            with self.subTest(action=action):
                self.assertEqual(permissions.tier(action), permissions.FINAL)

    def test_there_is_no_shell_tool(self):
        """A misheard sentence must not be able to reach a command line."""
        for forbidden in ('run', 'shell', 'exec', 'cmd', 'powershell', 'system'):
            with self.subTest(name=forbidden):
                self.assertFalse(hasattr(pc_control, forbidden),
                                 f'pc_control.{forbidden} would be a shell')


class SpokenTests(unittest.TestCase):
    def test_volume_is_reported_as_words(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                spoken = responses.say_volume({'volume': 85}, lang)
                self.assertFalse(any(c.isdigit() for c in spoken))

    def test_muting_says_so_rather_than_a_level(self):
        for lang in ('si', 'en'):
            with self.subTest(lang=lang):
                self.assertNotIn('percent', responses.say_volume({'muted': True}, lang))

    def test_a_missing_application_names_what_was_asked_for(self):
        spoken = responses.say_app_missing('zoom', 'en')
        self.assertIn('zoom', spoken)


class VolumeTests(unittest.TestCase):
    def test_a_level_is_clamped_to_the_possible(self):
        fake = MagicMock()
        fake.GetMasterVolumeLevelScalar.return_value = .5
        fake.GetMute.return_value = 0
        with patch.object(pc_control, 'mixer', return_value=fake):
            for asked, expected in [(150, 100), (-20, 0), (40, 40)]:
                with self.subTest(asked=asked):
                    result = pc_control.volume(level=asked)
                    self.assertEqual(result['volume'], expected)

    def test_without_a_mixer_relative_changes_still_work(self):
        """Being able to say "louder" matters more than setting an exact number."""
        with patch.object(pc_control, 'mixer', return_value=None), \
             patch.object(pc_control, 'tap', return_value=True) as tapped:
            result = pc_control.volume(step=10)
        self.assertTrue(result['changed'])
        self.assertFalse(result['exact'])
        tapped.assert_called()

    def test_without_a_mixer_an_exact_level_is_refused(self):
        with patch.object(pc_control, 'mixer', return_value=None):
            with self.assertRaises(pc_control.ControlError):
                pc_control.volume(level=50)


if __name__ == '__main__':
    unittest.main()
