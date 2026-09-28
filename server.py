"""Loopback-only HTTP service for the JARVIS desktop console."""
import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

import requests

from assistant_core import ROOT, Store, Tools, system_status
from agent import Agent


def create_server(port=4190, data_dir=None):
    data = Path(data_dir or os.environ.get('JARVIS_DATA_DIR', ROOT / 'data'))
    store = Store(data / 'jarvis.db')
    if data_dir is None and 'JARVIS_DATA_DIR' not in os.environ:
        store.import_legacy()
    tools = Tools(store, store.config('workspace', str(ROOT / 'workspace')))
    agent = Agent(store, tools)
    token = secrets.token_urlsafe(32)
    web = ROOT / 'web'
    model_cache = {'at': 0, 'online': False, 'models': []}
    model_lock = threading.Lock()

    # Whisper and the VAD load once and stay resident; the microphone is exclusive,
    # so one transcription at a time.
    voice = {'ears': None, 'vad': None, 'lock': threading.Lock(), 'available': None}

    def probe_voice():
        """Import the audio stack once, off the request path.

        voice_loop pulls in numpy and onnxruntime and registers the CUDA directories, so
        the first import takes seconds. Doing it inside a request meant the first
        /api/state blocked for that long, sometimes past its own timeout.
        """
        try:
            import voice_loop  # noqa: F401
            voice['available'] = True
        except Exception:
            voice['available'] = False

    threading.Thread(target=probe_voice, daemon=True).start()

    def voice_ready():
        return bool(voice.get('available'))

    def speak(text, rate=None, blocking=False):
        """Say a reply aloud. Never raises: losing the voice must not lose the answer."""
        try:
            import speech
        except Exception as error:
            return {'spoken': False, 'reason': 'unavailable', 'detail': str(error)[:140]}
        rate = rate or store.config('voice_rate', '+0%')
        if blocking:
            return speech.SPEAKER.say(text, rate)
        threading.Thread(target=speech.SPEAKER.say, args=(text, rate), daemon=True).start()
        return {'speaking': True}

    def speaking():
        # Only ask a module already imported: importing speech here would put the same
        # multi-second first-import cost back on the request path.
        module = sys.modules.get('speech')
        return bool(module and module.SPEAKER.speaking)

    def speak_stop():
        try:
            import speech
            speech.SPEAKER.stop()
        except Exception:
            pass

    def speak_answer(answer):
        if store.config('voice_enabled', False):
            speak(answer)

    agent.on_answer = speak_answer

    def transcribe(language, timeout):
        try:
            import voice_loop
        except Exception as error:
            raise ValueError(f'Voice input is unavailable: {error}')
        if not voice['lock'].acquire(blocking=False):
            raise ValueError('Already listening. Wait for the current recording to finish.')
        try:
            if voice['vad'] is None:
                voice['vad'] = voice_loop.Vad()
            if voice['ears'] is None:
                voice['ears'] = voice_loop.Ears()
            audio = voice_loop.record_utterance(voice['vad'], timeout=timeout)
            if audio is None:
                return {'heard': False, 'text': ''}
            text, seconds, info = voice['ears'].transcribe(audio, language)
            return {'heard': True, 'text': text,
                    'language': getattr(info, 'language', language) or 'unknown',
                    'audio_seconds': round(len(audio) / voice_loop.SAMPLE_RATE, 2),
                    'transcribe_seconds': round(seconds, 2)}
        finally:
            voice['lock'].release()

    def models():
        """Installed models, cached.

        The refresh happens outside the lock. Holding it across a two second request to
        Ollama made every concurrent caller wait behind the slowest one, so while Ollama
        was busy loading a model the whole console stalled and /api/state could exceed
        its own timeout. One caller refreshes; the rest are served the previous answer.
        """
        with model_lock:
            stale = time.monotonic() - model_cache['at'] > 8
            if stale and not model_cache.get('refreshing'):
                model_cache['refreshing'] = True
                threading.Thread(target=refresh_models, daemon=True).start()
            return {key: value for key, value in model_cache.items()
                    if key not in ('at', 'refreshing')}

    def refresh_models():
        """Ask Ollama what is installed, off the request path.

        Nobody waits for this. Asking Ollama takes up to two seconds while it is loading
        a model, and no page should stall for that; the console polls state anyway, so a
        slightly stale list corrects itself within a second.
        """
        online, names = False, []
        try:
            response = requests.get(agent.endpoint + '/api/tags', timeout=2)
            response.raise_for_status()
            online, names = True, [m['name'] for m in response.json()['models']]
        except (requests.RequestException, ValueError, KeyError):
            pass
        with model_lock:
            model_cache.update(online=online, models=names,
                               at=time.monotonic(), refreshing=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, value, status=200, content_type='application/json'):
            body = json.dumps(value, ensure_ascii=False).encode() if content_type == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = self.headers.get('Host', '')
            return host in (f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}')

        def do_GET(self):
            if not self.allowed():
                return self.send({'error': 'Invalid host'}, 403)
            path = urlsplit(self.path).path
            try:
                if path == '/api/state':
                    return self.send({'token': token, 'system': system_status(), 'ollama': models(),
                                      'model': store.config('model', ''), 'workspace': str(tools.workspace),
                                      'voice': {'available': voice_ready(), 'loaded': voice['ears'] is not None,
                                                'speaking': speaking(),
                                                'enabled': store.config('voice_enabled', False),
                                                'rate': store.config('voice_rate', '+0%')},
                                      'records': store.records(), 'history': store.history(),
                                      'jobs': [agent.snapshot(key) for key in list(agent.jobs)]})
                if path.startswith('/api/jobs/'):
                    return self.send(agent.snapshot(path.rsplit('/', 1)[-1]))
                file = (web / (path.lstrip('/') or 'index.html')).resolve()
                if not file.is_relative_to(web) or not file.is_file():
                    return self.send({'error': 'Not found'}, 404)
                return self.send(file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
            except KeyError:
                self.send({'error': 'Job not found'}, 404)

        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Jarvis-Token') != token:
                return self.send({'error': 'Invalid session'}, 403)
            if self.headers.get('Origin') not in (None, f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'):
                return self.send({'error': 'Invalid origin'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 120000:
                    raise ValueError('Request size is invalid')
                body = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                if path == '/api/chat':
                    prompt = str(body.get('prompt', '')).strip()
                    if not prompt or len(prompt) > 12000:
                        raise ValueError('Enter a request under 12,000 characters')
                    return self.send({'id': agent.start(prompt)})
                if path == '/api/stop':
                    agent.stop(body['id'])
                    speak_stop()
                    return self.send({'stopped': True})
                if path == '/api/speak':
                    text = str(body.get('text', '')).strip()
                    if not text:
                        raise ValueError('Nothing to say')
                    return self.send(speak(text, body.get('rate'), bool(body.get('wait'))))
                if path == '/api/speak/stop':
                    speak_stop()
                    return self.send({'stopped': True})
                if path == '/api/transcribe':
                    # 'auto' by default: measured 19/20 commands recognised against 8/20
                    # when Sinhala is forced. See voice_loop.DEFAULT_LANGUAGE.
                    language = str(body.get('lang', 'auto')).strip() or 'auto'
                    timeout = min(max(int(body.get('timeout', 20)), 3), 60)
                    return self.send(transcribe(None if language == 'auto' else language, timeout))
                if path == '/api/records':
                    if body.get('kind') == 'reminder':
                        return self.send(tools.execute('set_reminder', {'title':body['title'], 'due':body['due']}))
                    return self.send(store.add(body['kind'], body['title'], body.get('content', ''), body.get('due')))
                if path == '/api/records/update':
                    return self.send(store.update(body['id'], body['action']))
                if path == '/api/settings':
                    if any(job['state'] in ('running', 'stopping') for job in agent.jobs.values()):
                        raise ValueError('Wait for the active request before changing settings')
                    # Partial updates are allowed, so the voice toggle does not have to
                    # resend the workspace and model just to turn speech on.
                    if 'workspace' in body:
                        workspace = Path(body['workspace']).expanduser().resolve()
                        if not workspace.is_dir():
                            raise ValueError('Workspace folder does not exist')
                        tools.workspace = workspace
                        store.set_config('workspace', str(workspace))
                    if 'model' in body:
                        model = str(body.get('model', ''))
                        if model and model not in models()['models']:
                            raise ValueError('Select an installed model')
                        store.set_config('model', model)
                    if 'voice_enabled' in body:
                        store.set_config('voice_enabled', bool(body['voice_enabled']))
                        if not body['voice_enabled']:
                            speak_stop()
                    if 'voice_rate' in body:
                        rate = str(body['voice_rate'])
                        if not re.fullmatch(r'[+-]\d{1,3}%', rate):
                            raise ValueError("Speech rate looks like '+20%' or '-10%'")
                        store.set_config('voice_rate', rate)
                    return self.send({'saved': True})
                return self.send({'error': 'Not found'}, 404)
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.send({'error': str(error)}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=4190)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    server = create_server(args.port)
    print(f'JARVIS console: http://localhost:{server.server_port}', flush=True)
    if args.open:
        webbrowser.open(f'http://localhost:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
