"""A test's own JARVIS data folder, so the voice brain a test builds never writes to the
real data/jarvis.db. Day 30 began recording every exchange in the conversation history;
without this, the conversation tests would have written their chatter into yours."""
import tempfile
from pathlib import Path
from unittest.mock import patch


def private_root(test):
    """Point assistant_core.ROOT at a fresh folder for the rest of this test."""
    # A background thread started by the brain may still hold the database open when the
    # test ends, and Windows will not delete an open file: leave it for the temp cleaner.
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(folder.name)
    (root / 'data').mkdir()
    patcher = patch('assistant_core.ROOT', root)
    patcher.start()
    # The brain will not start without a model chosen; the real one is named, never run.
    from assistant_core import Store
    Store(root / 'data' / 'jarvis.db').set_config('model', 'qwen3.5:9b')
    test.addCleanup(folder.cleanup)
    test.addCleanup(patcher.stop)
    return root
