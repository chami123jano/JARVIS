"""Local persistence and explicit tools for the JARVIS console."""
import ast
import datetime as dt
import json
import math
import operator
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid
import webbrowser
from contextlib import contextmanager
from urllib.parse import quote

import psutil

ROOT = Path(__file__).resolve().parent


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                  content TEXT NOT NULL, due TEXT, done INTEGER DEFAULT 0,
                  created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def config(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_config(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (key, json.dumps(value)))

    def add(self, kind, title, content='', due=None):
        if kind not in ('note', 'memory', 'reminder') or not str(title).strip():
            raise ValueError('A record needs a type and title.')
        if due:
            due = dt.datetime.fromisoformat(due).astimezone().isoformat()
        record_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?,?,0,?)',
                       (record_id, kind, str(title)[:200], str(content)[:20000], due,
                        dt.datetime.now().astimezone().isoformat()))
        return {'id': record_id, 'title': title, 'due': due, 'saved': True}

    def records(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM records ORDER BY created DESC')]

    def update(self, record_id, action):
        with self.connect() as db:
            if action == 'delete':
                cursor = db.execute('DELETE FROM records WHERE id=?', (record_id,))
            elif action == 'complete':
                cursor = db.execute('UPDATE records SET done=1 WHERE id=?', (record_id,))
            else:
                raise ValueError('Unknown record action')
            return {'changed': cursor.rowcount > 0}

    def message(self, role, content):
        with self.connect() as db:
            db.execute('INSERT INTO messages(role,content) VALUES (?,?)', (role, content))
            db.execute('DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 200)')

    def history(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT role,content FROM messages ORDER BY id')]

    def import_legacy(self):
        if self.config('legacy_imported'):
            return
        # Original JSON files remain intact. Only known record fields are imported.
        for filename, kind, field in [('notes.json', 'note', 'notes'), ('scheduler.json', 'reminder', 'tasks'), ('calendar.json', 'reminder', 'events')]:
            try:
                data = json.loads((ROOT / filename).read_text(encoding='utf-8'))
                for item in data.get(field, []):
                    title = item.get('title') or item.get('description') or item.get('content')
                    if title:
                        self.add(kind, title, 'Imported from the previous desktop app')
            except (OSError, ValueError, AttributeError, TypeError):
                pass
        try:
            for key, value in json.loads((ROOT / 'memory.json').read_text(encoding='utf-8')).items():
                self.add('memory', key, str(value))
        except (OSError, ValueError, AttributeError):
            pass
        self.set_config('legacy_imported', True)


def calculate(expression):
    if len(expression) > 200:
        raise ValueError('Expression too long')
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}

    def evaluate(node, depth=0):
        if depth > 16:
            raise ValueError('Expression too complex')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            result = evaluate(node.operand, depth + 1) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            left, right = evaluate(node.left, depth + 1), evaluate(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError('Exponent exceeds 12')
            result = operations[type(node.op)](left, right)
        else:
            raise ValueError('Use numbers and arithmetic operators only')
        if not isinstance(result, (int, float)) or not math.isfinite(result) or abs(result) > 1e15:
            raise ValueError('Result out of range')
        return result
    return evaluate(ast.parse(expression, mode='eval').body)


def system_status():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(ROOT.anchor))
    battery = psutil.sensors_battery()
    return {'cpu': psutil.cpu_percent(interval=.1), 'memory': memory.percent,
            'memory_gb': round(memory.total / 2**30, 1), 'disk': disk.percent,
            'battery': round(battery.percent) if battery else None,
            'time': dt.datetime.now().astimezone().isoformat()}


class Tools:
    def __init__(self, store, workspace):
        self.store = store
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        path = (self.workspace / name).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError('Path is outside the selected workspace')
        if any(part.startswith('.') for part in path.relative_to(self.workspace).parts):
            raise ValueError('Hidden files are excluded')
        return path

    def execute(self, name, args):
        if name == 'system_status':
            return system_status()
        if name == 'calculate':
            return {'result': calculate(str(args['expression']))}
        if name == 'list_records':
            return self.store.records()
        if name == 'save_note':
            return self.store.add('note', args['title'], args.get('content', ''))
        if name == 'remember':
            return self.store.add('memory', args['title'], args['content'])
        if name == 'set_reminder':
            due = dt.datetime.fromisoformat(args['due']).astimezone()
            if due <= dt.datetime.now().astimezone():
                raise ValueError('Reminder must be in the future')
            return self.store.add('reminder', args['title'], due=due.isoformat())
        if name == 'find_files':
            query = str(args.get('query', '')).lower()
            result = []
            visited = 0
            for parent, dirs, files in os.walk(self.workspace):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'blobs', '__pycache__') and not (Path(parent) / d).is_symlink()]
                for filename in files:
                    visited += 1
                    if visited > 10000:
                        return {'files': result, 'truncated': True}
                    if query in filename.lower() and not filename.startswith('.'):
                        path = Path(parent) / filename
                        if path.resolve().is_relative_to(self.workspace):
                            result.append(str(path.relative_to(self.workspace)))
                    if len(result) >= 60:
                        return {'files': result, 'truncated': True}
            return {'files': result, 'truncated': False}
        if name == 'read_document':
            path = self.path(str(args['path']))
            if path.stat().st_size > 10_000_000:
                raise ValueError('Document exceeds 10 MB')
            if path.suffix.lower() == '.pdf':
                from pypdf import PdfReader
                text = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages[:30])
            elif path.suffix.lower() in ('.txt', '.md', '.csv', '.json', '.py', '.js', '.html', '.css'):
                text = path.read_text(encoding='utf-8', errors='replace')
            else:
                raise ValueError('Supported documents: PDF, text, Markdown, CSV, JSON and source files')
            return {'path': str(path.relative_to(self.workspace)), 'text': text[:16000], 'truncated': len(text) > 16000}
        if name == 'write_document':
            path = self.path(str(args['path']))
            if path.suffix.lower() not in ('.txt', '.md', '.csv'):
                raise ValueError('Create reports as .txt, .md or .csv')
            content = str(args['content'])
            if len(content) > 100000:
                raise ValueError('Report too long')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as output:
                output.write(content)
            return {'created': str(path.relative_to(self.workspace)), 'characters': len(content)}
        if name == 'open_app':
            apps = {'notepad': ['notepad.exe'], 'calculator': ['calc.exe'], 'paint': ['mspaint.exe'], 'explorer': ['explorer.exe', str(self.workspace)]}
            app = str(args['app']).lower()
            if app not in apps:
                raise ValueError('Available applications: notepad, calculator, paint, explorer')
            proc = subprocess.Popen(apps[app], shell=False)
            return {'launched': app, 'process_id': proc.pid}
        if name == 'translate':
            # Translation runs on the model, not here. This tool exists so the agent has
            # an explicit place to put it and so the direction is never guessed.
            text = str(args.get('text', '')).strip()[:2000]
            if not text:
                raise ValueError('Nothing to translate')
            target = str(args.get('to', '')).lower().strip()
            if target not in ('si', 'en', 'sinhala', 'english'):
                raise ValueError("Translate to 'si' or 'en'")
            return {'text': text, 'to': 'si' if target.startswith('si') else 'en',
                    'note': 'Translate this yourself and reply with the translation only. '
                            'Leave names of people and places unchanged.'}
        if name in ('web_search', 'play_music'):
            query = str(args['query']).strip()[:500]
            base = 'https://www.youtube.com/results?search_query=' if name == 'play_music' else 'https://www.google.com/search?q='
            opened = webbrowser.open(base + quote(query))
            return {'opened': opened, 'query': query, 'note': 'Search page opened; results have not been read and playback is not verified.'}
        raise ValueError('Unknown tool')


TOOL_DEFINITIONS = {
    'system_status': ('Read current time, CPU, RAM, disk and battery.', {}),
    'calculate': ('Calculate arithmetic, no code execution.', {'expression': 'string'}),
    'list_records': ('List saved notes, memories and reminders.', {}),
    'save_note': ('Save a note requested by the user.', {'title': 'string', 'content': 'string'}),
    'remember': ('Remember a user preference explicitly supplied by the user.', {'title': 'string', 'content': 'string'}),
    'set_reminder': ('Save a timed reminder. due must be a future ISO datetime with timezone.', {'title': 'string', 'due': 'string'}),
    'find_files': ('Search filenames inside the selected workspace.', {'query': 'string'}),
    'read_document': ('Read a document relative to the selected workspace.', {'path': 'string'}),
    'write_document': ('Create a NEW text, Markdown or CSV report in the workspace. Never overwrites.', {'path': 'string', 'content': 'string'}),
    'open_app': ('Launch notepad, calculator, paint or explorer.', {'app': 'string'}),
    'translate': ('Translate text between Sinhala and English. Names of people and '
                  'places are left as they are.', {'text': 'string', 'to': 'string'}),
    'web_search': ('Open browser search. Does not retrieve results.', {'query': 'string'}),
    'play_music': ('Open YouTube search. Does not start playback.', {'query': 'string'}),
}


def schemas():
    return [{'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': {key: {'type': typ} for key, typ in fields.items()},
                           'required': list(fields), 'additionalProperties': False}}}
            for name, (description, fields) in TOOL_DEFINITIONS.items()]
