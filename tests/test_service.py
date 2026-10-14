"""The background service: restarting what dies, and stopping when it should.

These cover the decisions, not the processes. Starting Ollama and the microphone for
real is verified by verify.py; what goes wrong in a supervisor is the logic around them --
a crash loop that never stops, a second copy opening the microphone twice, a mute that
leaves the microphone open.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_service as service


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class WatchdogTests(unittest.TestCase):
    """A voice loop that dies on startup must not restart every second for ever."""

    def setUp(self):
        self.clock = FakeClock()
        self.dog = service.Watchdog(clock=self.clock)

    def test_backoff_doubles(self):
        delays = []
        for _ in range(4):
            self.dog.record_crash()
            delays.append(self.dog.delay())
        self.assertEqual(delays, [1, 2, 4, 8])

    def test_it_gives_up_after_repeated_crashes(self):
        for _ in range(service.Watchdog.LIMIT - 1):
            self.dog.record_crash()
        self.assertFalse(self.dog.gave_up())
        self.dog.record_crash()
        self.assertTrue(self.dog.gave_up())

    def test_old_crashes_are_forgiven(self):
        """A crash an hour ago says nothing about whether it works now."""
        for _ in range(service.Watchdog.LIMIT - 1):
            self.dog.record_crash()
        self.clock.now += service.Watchdog.WINDOW + 1
        self.dog.record_crash()
        self.assertFalse(self.dog.gave_up())
        self.assertEqual(self.dog.delay(), 1)

    def test_backoff_is_capped(self):
        dog = service.Watchdog(clock=self.clock)
        dog.LIMIT = 100
        for _ in range(12):
            dog.record_crash()
        self.assertLessEqual(dog.delay(), service.Watchdog.CEILING)

    def test_reset_clears_the_history(self):
        for _ in range(service.Watchdog.LIMIT):
            self.dog.record_crash()
        self.dog.reset()
        self.assertFalse(self.dog.gave_up())


class OllamaStateTests(unittest.TestCase):
    """An empty model list is the symptom of the login problem, not data loss."""

    def reply(self, models):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'models': models}
        return response

    def test_no_answer_is_down(self):
        import requests
        with patch('requests.get', side_effect=requests.ConnectionError('refused')):
            self.assertEqual(service.ollama_state(), 'down')

    def test_an_empty_model_list_is_the_wrong_folder(self):
        with patch('requests.get', return_value=self.reply([])):
            self.assertEqual(service.ollama_state(), 'wrong-path')

    def test_models_present_is_ok(self):
        with patch('requests.get', return_value=self.reply([{'name': 'qwen3.5:9b'}])):
            self.assertEqual(service.ollama_state(), 'ok')

    def test_ollama_is_reached_by_address_not_name(self):
        """localhost costs two seconds per call on Windows."""
        self.assertIn('127.0.0.1', service.OLLAMA_URL)

    def test_the_saved_model_path_is_passed_explicitly(self):
        """The tray app only sees OLLAMA_MODELS after a reboot; the service passes it."""
        with patch.object(service, 'user_environment',
                          side_effect=lambda name, default=None: r'E:\models'
                          if name == 'OLLAMA_MODELS' else default):
            env = service.ollama_environment()
        self.assertEqual(env['OLLAMA_MODELS'], r'E:\models')
        self.assertEqual(env['OLLAMA_KV_CACHE_TYPE'], 'q8_0')


class VoiceChildTests(unittest.TestCase):
    def test_the_voice_loop_runs_in_wake_mode(self):
        command = service.voice_command()
        self.assertTrue(command[1].endswith('voice_loop.py'))
        self.assertIn('--wake', command)

    def test_the_child_can_print_sinhala(self):
        """Printing Sinhala under the Windows code page raises and kills the loop."""
        env = service.voice_environment()
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')


class SingleInstanceTests(unittest.TestCase):
    def test_a_second_copy_sees_the_first(self):
        name = 'Local\\JARVIS-test-instance'
        first = service.SingleInstance(name)
        try:
            self.assertFalse(first.already_running)
            second = service.SingleInstance(name)
            try:
                self.assertTrue(second.already_running,
                                'a second copy would open the microphone twice')
            finally:
                second.release()
        finally:
            first.release()

    def test_the_name_is_free_again_after_release(self):
        name = 'Local\\JARVIS-test-release'
        first = service.SingleInstance(name)
        first.release()
        again = service.SingleInstance(name)
        try:
            self.assertFalse(again.already_running)
        finally:
            again.release()


class AutostartTests(unittest.TestCase):
    """Uses a throwaway key, never the real Run key."""

    KEY = r'Software\JARVIS-test-autostart'

    def tearDown(self):
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.KEY)
        except OSError:
            pass

    def test_enable_and_disable(self):
        self.assertFalse(service.autostart_status(self.KEY)['enabled'])
        status = service.enable_autostart(self.KEY)
        self.assertTrue(status['enabled'])
        self.assertTrue(status['current'])
        self.assertFalse(service.disable_autostart(self.KEY)['enabled'])

    def test_disabling_twice_is_harmless(self):
        service.disable_autostart(self.KEY)
        service.disable_autostart(self.KEY)

    def test_login_starts_it_without_a_console_window(self):
        command = service.autostart_command()
        self.assertIn('pythonw.exe', command)
        self.assertIn('jarvis_service.py', command)
        # Quoted, because the project path may contain spaces.
        self.assertTrue(command.startswith('"'))


class MuteTests(unittest.TestCase):
    """Mute closes the microphone. Ignoring audio while it stays open is not mute."""

    def make(self):
        svc = service.Service.__new__(service.Service)
        svc.muted = False
        svc.state = 'listening'
        svc.child = None
        svc.child_output = None
        svc.icon = None
        svc.lock = __import__('threading').RLock()
        svc.wake = __import__('threading').Event()
        svc.watchdog = service.Watchdog()
        return svc

    def test_muting_stops_the_voice_loop(self):
        svc = self.make()
        process = MagicMock()
        process.poll.return_value = None
        svc.child = process
        svc.toggle_mute()
        self.assertTrue(svc.muted)
        self.assertEqual(svc.state, 'muted')
        process.terminate.assert_called_once()
        self.assertIsNone(svc.child)

    def test_unmuting_forgives_earlier_crashes(self):
        svc = self.make()
        svc.muted = True
        for _ in range(service.Watchdog.LIMIT):
            svc.watchdog.record_crash()
        svc.toggle_mute()
        self.assertFalse(svc.muted)
        self.assertFalse(svc.watchdog.gave_up())


class IconTests(unittest.TestCase):
    def test_every_state_has_an_icon(self):
        for state in ('listening', 'muted', 'starting', 'error', 'stopped'):
            with self.subTest(state=state):
                self.assertEqual(service.icon_image(state).size, (64, 64))

    def test_muted_and_listening_look_different(self):
        self.assertNotEqual(list(service.icon_image('muted').getdata()),
                            list(service.icon_image('listening').getdata()))


class HotkeyTests(unittest.TestCase):
    """Ctrl+Alt+M was taken by another program on this machine; the service must cope."""

    def test_the_first_free_shortcut_is_used(self):
        import ctypes
        user32 = ctypes.windll.user32
        # Occupy the first preference ourselves, so the fallback has to be taken.
        key, modifiers, _ = service.MUTE_HOTKEYS[0]
        held = user32.RegisterHotKey(None, 77, modifiers | service.MOD_NOREPEAT, ord(key))
        try:
            hotkey = service.Hotkey(lambda: None)
            hotkey.start()
            hotkey.ready.wait(3)
            try:
                if held:
                    self.assertNotEqual(hotkey.label, service.MUTE_HOTKEYS[0][2],
                                        'took a shortcut that was already in use')
                self.assertIsNotNone(hotkey.label, hotkey.error)
            finally:
                hotkey.stop()
                hotkey.join(3)
        finally:
            if held:
                user32.UnregisterHotKey(None, 77)

    def test_all_taken_is_reported_not_silent(self):
        hotkey = service.Hotkey(lambda: None, candidates=[])
        hotkey.start()
        hotkey.ready.wait(3)
        self.assertIsNone(hotkey.label)
        self.assertIn('taken', hotkey.error)

    def test_the_old_shortcut_is_not_the_default(self):
        """Ctrl+Alt+M is owned by another program here; it must not be tried first."""
        self.assertNotEqual(service.MUTE_HOTKEYS[0][2], 'Ctrl+Alt+M')


class TreeKillTests(unittest.TestCase):
    """Mute must close the microphone, which means ending the real interpreter too."""

    def test_stopping_ends_the_interpreter_behind_the_launcher(self):
        import subprocess
        import time
        import psutil
        svc = MuteTests().make()
        svc.child = subprocess.Popen(
            [str(service.PYTHON), '-c', 'import time; time.sleep(60)'],
            creationflags=service.CREATE_NO_WINDOW)
        time.sleep(2)
        tree = [p.pid for p in psutil.Process(svc.child.pid).children(recursive=True)]
        svc.stop_child()
        time.sleep(1)
        survivors = [pid for pid in tree if psutil.pid_exists(pid)]
        self.assertEqual(survivors, [], 'the real interpreter outlived a stop')


if __name__ == '__main__':
    unittest.main()
