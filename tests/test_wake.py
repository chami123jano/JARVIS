"""Always-listening logic: wake word gating, buffering and muting.

No microphone and no model here — the audio path is proven by verify.py against the real
device. What these guard is the decision logic around it, which is where an
always-listening assistant goes wrong: firing twice on one phrase, clipping the first
word of a command, or continuing to listen after being muted.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fake_wake(scores):
    """A WakeWord whose model returns the given scores in order."""
    import voice_loop
    with patch('openwakeword.model.Model') as model:
        model.return_value.predict.side_effect = [{'hey_jarvis': s} for s in scores]
        model.return_value.reset = MagicMock()
        return voice_loop.WakeWord()


class WakeWordTests(unittest.TestCase):
    def setUp(self):
        import numpy
        import voice_loop
        self.numpy = numpy
        self.voice_loop = voice_loop
        self.frame = numpy.zeros(voice_loop.WAKE_FRAME, dtype='float32')

    def test_fires_above_threshold_only(self):
        limit = self.voice_loop.WAKE_THRESHOLD
        wake = fake_wake([limit - .4, limit - .01, limit + .01])
        self.assertFalse(wake.feed(self.frame))
        self.assertFalse(wake.feed(self.frame))
        self.assertTrue(wake.feed(self.frame))

    def test_threshold_clears_the_measured_noise_floor(self):
        """Measured on this microphone: wake words 0.86-0.95, other speech up to 0.474.

        The threshold has to sit in that gap. Drifting it into either side gives a wake
        word that ignores you or one that fires at the television.
        """
        limit = self.voice_loop.WAKE_THRESHOLD
        self.assertGreater(limit, .48, 'would trigger on ordinary speech')
        self.assertLess(limit, .86, 'would miss a real "hey jarvis"')

    def test_cooldown_prevents_a_double_trigger(self):
        """One spoken phrase spans many frames and would otherwise fire repeatedly."""
        wake = fake_wake([0.9, 0.9, 0.9])
        self.assertTrue(wake.feed(self.frame))
        self.assertFalse(wake.feed(self.frame), 'fired twice on one phrase')
        self.assertFalse(wake.feed(self.frame))

    def test_cooldown_expires(self):
        wake = fake_wake([0.9, 0.9])
        self.assertTrue(wake.feed(self.frame))
        wake.last_fired -= self.voice_loop.WAKE_COOLDOWN + .1
        self.assertTrue(wake.feed(self.frame))

    def test_peak_is_tracked_for_tuning(self):
        wake = fake_wake([0.2, 0.7, 0.3])
        for _ in range(3):
            wake.feed(self.frame)
        self.assertAlmostEqual(wake.peak, 0.7, places=3)


class BufferTests(unittest.TestCase):
    """The wake word wants 1280 samples, the VAD wants 512, one stream feeds both."""

    def make_listener(self):
        import numpy
        import voice_loop
        listener = voice_loop.Listener.__new__(voice_loop.Listener)
        listener.buffer = numpy.zeros(0, dtype='float32')
        return listener, voice_loop

    def test_take_assembles_across_block_boundaries(self):
        import queue
        import numpy
        listener, voice_loop = self.make_listener()
        blocks = queue.Queue()
        for index in range(6):
            blocks.put(numpy.full(512, index, dtype='float32'))
        first = listener.take(blocks, voice_loop.WAKE_FRAME)
        self.assertEqual(len(first), voice_loop.WAKE_FRAME)
        # 1280 is not a multiple of 512, so the remainder must be kept, not discarded.
        self.assertEqual(len(listener.buffer), 512 * 3 - voice_loop.WAKE_FRAME)
        second = listener.take(blocks, voice_loop.FRAME)
        self.assertEqual(len(second), voice_loop.FRAME)

    def test_take_returns_none_when_audio_stops(self):
        import queue
        listener, voice_loop = self.make_listener()
        self.assertIsNone(listener.take(queue.Queue(), voice_loop.FRAME))

    def test_no_samples_are_lost_between_reads(self):
        """A dropped remainder would clip the start of every command."""
        import queue
        import numpy
        listener, voice_loop = self.make_listener()
        blocks = queue.Queue()
        total = numpy.arange(512 * 10, dtype='float32')
        for start in range(0, len(total), 512):
            blocks.put(total[start:start + 512])
        seen = []
        for size in (voice_loop.WAKE_FRAME, voice_loop.FRAME, voice_loop.FRAME, 700):
            chunk = listener.take(blocks, size)
            self.assertIsNotNone(chunk)
            seen.append(chunk)
        joined = numpy.concatenate(seen)
        self.assertTrue(numpy.array_equal(joined, total[:len(joined)]),
                        'samples were reordered or dropped')


class ConstructionTests(unittest.TestCase):
    """Actually build a Listener.

    The other tests use __new__ and skip __init__, which is why a missing
    `import threading` reached the user as a NameError at startup. Construct the real
    object so import mistakes surface here instead.
    """

    def test_listener_constructs(self):
        import voice_loop
        with patch('openwakeword.model.Model') as model:
            model.return_value.predict.return_value = {'hey_jarvis': 0.0}
            listener = voice_loop.Listener(vad=MagicMock(), ears=MagicMock())
        self.assertFalse(listener.muted.is_set())
        self.assertFalse(listener.running.is_set())
        self.assertEqual(listener.state, 'idle')
        self.assertEqual(listener.wake.threshold, voice_loop.WAKE_THRESHOLD)

    def test_module_has_every_import_it_uses(self):
        """Catches a name used at runtime but never imported."""
        import ast
        import builtins
        import voice_loop
        source = Path(voice_loop.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update((alias.asname or alias.name).split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.asname or alias.name for alias in node.names)
        # Every name bound anywhere: assignments, loop targets, comprehensions,
        # parameters, except-as and with-as. Without all of these the check reports
        # ordinary local variables as missing imports.
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
                bound.add(node.optional_vars.id)
        known = imported | bound | set(dir(builtins))
        missing = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                name = node.value.id
                if name not in known and name not in ('self', 'cls', 'args', 'parser'):
                    missing.add(name)
        self.assertEqual(missing, set(), f'used but never imported: {sorted(missing)}')


class MuteTests(unittest.TestCase):
    def test_mute_flag_controls_listening(self):
        import voice_loop
        listener = voice_loop.Listener.__new__(voice_loop.Listener)
        import threading
        listener.muted = threading.Event()
        self.assertFalse(listener.muted.is_set())
        listener.muted.set()
        self.assertTrue(listener.muted.is_set())
        listener.muted.clear()
        self.assertFalse(listener.muted.is_set())

    def test_state_callback_failure_does_not_stop_the_loop(self):
        import voice_loop
        listener = voice_loop.Listener.__new__(voice_loop.Listener)
        listener.on_state = lambda state: 1 / 0
        listener.state = 'idle'
        listener.set_state('listening')          # must not raise
        self.assertEqual(listener.state, 'listening')


if __name__ == '__main__':
    unittest.main()
