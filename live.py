"""What the voice loop is doing right now, for the console to show (Day 24).

The voice loop and the console are separate processes: the microphone, the state and the
words heard live in the voice loop, the screen in the console. The voice loop keeps a small
picture of itself in memory and serves it as JSON on 127.0.0.1 only; the console's server
passes it on. Nothing is written to disk -- the levels change twenty times a second.

    GET http://127.0.0.1:4191/live
    {"state": "listening", "levels": [...], "lines": [{"who": "you", "text": ...}], ...}
"""
import collections
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 4191
LEVELS = 64          # about three seconds of microphone, at twenty a second
LINES = 6            # the last three exchanges


class Live:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'idle'
        self.levels = collections.deque([0.0] * LEVELS, maxlen=LEVELS)
        self.lines = collections.deque(maxlen=LINES)
        self.started = time.time()
        self.last_level = 0.0

    def set_state(self, state):
        with self.lock:
            self.state = state

    def level(self, rms, every=0.05):
        """One microphone level; at most twenty a second are kept."""
        now = time.monotonic()
        if now - self.last_level >= every:
            self.last_level = now
            with self.lock:
                self.levels.append(round(min(1.0, float(rms) * 8), 3))

    def heard(self, text, speaker=None):
        self.line('you', text, speaker=speaker)

    def said(self, text, unprompted=False):
        self.line('jarvis', text, unprompted=unprompted)

    def line(self, who, text, **extra):
        if str(text).strip():
            with self.lock:
                self.lines.append(dict(who=who, text=str(text)[:400],
                                       at=time.strftime('%H:%M:%S'), **extra))

    def snapshot(self, speaking=False):
        with self.lock:
            state = 'speaking' if speaking and self.state in ('idle', 'thinking') else self.state
            return {'running': True, 'state': state, 'levels': list(self.levels),
                    'lines': list(self.lines), 'since': self.started}


LIVE = Live()


class ExclusiveServer(ThreadingHTTPServer):
    """One server per port. Python's default asks for address reuse, and on Windows that
    lets a second process bind the same port and take some of the requests."""
    allow_reuse_address = False


def serve(live=LIVE, speaking=lambda: False, port=PORT):
    """Serve the live picture on 127.0.0.1 in a background thread. Returns the server."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?')[0] != '/live' or \
                    self.headers.get('Host', '') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(live.snapshot(speaking()), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = ExclusiveServer(('127.0.0.1', port), Handler)
    except OSError:
        return None          # already served -- a second copy, or a test
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name='live').start()
    return server
