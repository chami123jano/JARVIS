"""It knows your own files (Day 13).

    python documents.py --index                       read what is new or changed
    python documents.py "mage CV eke mona skills tikada thiyenne"
    python documents.py --status

Your documents, code and PDFs under D:\\me are cut into chunks that remember where they
came from -- file and line, or file and page -- and each gets a vector in jarvis.db.
A question finds the closest chunks, and the answer has to say which file it used.
An answer you cannot check is worse than none.

Re-indexing is incremental: a file is read again only when its size or modified time has
changed, and chunks of deleted files are dropped. Nothing leaves the computer.

Never indexed: dependency and build folders, anything hidden, and anything that looks like
it holds a secret -- by name (.env, credentials, keys) or by content (a GitHub token, an
API key). A token in a file is not something JARVIS should ever read back to you.
"""
import argparse
import datetime as dt
import html
import os
import re
import sys
import threading
import time
from array import array
from pathlib import Path

ROOTS = [r'D:\me']
SKIP_DIRS = {'node_modules', '.git', '.venv', 'venv', '__pycache__', 'dist', 'build',
             'target', '.next', 'blobs', 'data', '.gradle', '.idea', 'bin', 'obj', 'vendor',
             'coverage', 'site-packages', 'workspace',
             # Training the offline voice (Day 29): gigabytes of tools, not your documents.
             'piper-train', 'piper1-gpl'}
SKIP_PREFIXES = ('.', 'apache-tomcat')
TYPES = {'.md', '.txt', '.pdf', '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.html',
         '.css', '.json', '.csv', '.sql', '.php'}
MAX_BYTES = 2_000_000
SECRET_NAMES = re.compile(r'(^\.env|secret|credential|password|private[_-]?key|id_rsa|'
                          r'\.pem$|\.key$|token)', re.I)
SECRET_CONTENT = re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|'
                            r'sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|'
                            r'-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[bp]-[A-Za-z0-9-]{20,}')
CHUNK_LINES, OVERLAP = 40, 5
CHUNK_CHARS = 1800
TOP = 6
RESCAN_SECONDS = 600
PAUSE_BETWEEN_FILES = 0.2
SINHALA = re.compile('[\u0d80-\u0dff]')


def is_secret_file(path, text=''):
    return bool(SECRET_NAMES.search(Path(path).name) or SECRET_CONTENT.search(text))


def candidates(roots):
    """Every file worth indexing under these folders."""
    for root in roots:
        for parent, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(SKIP_PREFIXES)]
            for name in files:
                path = Path(parent) / name
                if path.suffix.lower() in TYPES and not name.startswith('.'):
                    try:
                        if path.stat().st_size <= MAX_BYTES:
                            yield path
                    except OSError:
                        continue


def read_text(path):
    """[(where, text)] -- 'page N' for PDFs, line numbers for everything else."""
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [(('page', number), page.extract_text() or '')
                for number, page in enumerate(reader.pages[:60], 1)]
    text = path.read_text(encoding='utf-8', errors='replace')
    if path.suffix.lower() == '.html':
        text = re.sub(r'(?is)<(script|style)\b.*?</\1>', lambda m: '\n' * m.group(0).count('\n'), text)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    return [(('lines', 1), text)]


def chunks_of(path):
    """[(start, end, kind, text)] with kind 'page' or 'lines'."""
    out = []
    for (kind, first), text in read_text(path):
        if kind == 'page':
            text = re.sub(r'[ \t]+', ' ', text).strip()
            for start in range(0, len(text), CHUNK_CHARS):
                if text[start:start + CHUNK_CHARS].strip():
                    out.append((first, first, 'page', text[start:start + CHUNK_CHARS]))
            continue
        lines = text.splitlines()
        step = CHUNK_LINES - OVERLAP
        for start in range(0, max(len(lines), 1), step):
            piece = '\n'.join(line.rstrip() for line in lines[start:start + CHUNK_LINES])
            piece = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]{2,}', ' ', piece)).strip()
            if len(piece) >= 40:
                out.append((start + 1, min(start + CHUNK_LINES, len(lines)), 'lines',
                            piece[:CHUNK_CHARS]))
            if start + CHUNK_LINES >= len(lines):
                break
    return out


def cite(row):
    name = Path(row['path']).name
    if row['kind'] == 'page':
        return f'{name}, page {row["start"]}'
    return f'{name}, lines {row["start"]}-{row["end"]}'


class Library:
    def __init__(self, store, memory=None, roots=None):
        import memory as memory_module
        self.store = store
        self.memory = memory or memory_module.Memory(store)
        self.fixed_roots = roots
        self.lock = threading.Lock()
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS file_chunks (
                  id INTEGER PRIMARY KEY, path TEXT NOT NULL, start INTEGER, end INTEGER,
                  kind TEXT NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS file_chunks_path ON file_chunks(path);
                CREATE TABLE IF NOT EXISTS file_state (
                  path TEXT PRIMARY KEY, mtime REAL, size INTEGER, chunks INTEGER,
                  skipped TEXT);
            ''')

    @property
    def roots(self):
        return self.fixed_roots or self.store.config('file_roots', ROOTS)

    def known(self):
        with self.store.connect() as db:
            return {row['path']: dict(row) for row in db.execute('SELECT * FROM file_state')}

    def index(self, on_file=lambda path, count: None):
        """Bring the index up to date. Returns {'read', 'unchanged', 'removed', 'skipped'}."""
        with self.lock:
            known = self.known()
            seen, report = set(), {'read': 0, 'unchanged': 0, 'removed': 0, 'skipped': 0}
            for path in candidates(self.roots):
                key = str(path)
                seen.add(key)
                stat = path.stat()
                old = known.get(key)
                if old and old['mtime'] == stat.st_mtime and old['size'] == stat.st_size:
                    report['unchanged'] += 1
                    continue
                count, skipped = self.index_file(path, stat)
                # A breath between files: re-reading 268 at once starved the microphone
                # ("input overflow" in the voice log, 16 Oct).
                time.sleep(PAUSE_BETWEEN_FILES)
                report['skipped' if skipped else 'read'] += 1
                on_file(path, count)
            gone = [p for p in known if p not in seen]
            with self.store.connect() as db:
                for path in gone:
                    db.execute('DELETE FROM file_chunks WHERE path=?', (path,))
                    db.execute('DELETE FROM file_state WHERE path=?', (path,))
            report['removed'] = len(gone)
            return report

    def index_file(self, path, stat):
        """(chunks written, reason skipped or '')."""
        skipped = ''
        rows = []
        if is_secret_file(path):
            skipped = 'its name suggests secrets'
        else:
            try:
                pieces = chunks_of(path)
                if any(SECRET_CONTENT.search(text) for *_, text in pieces):
                    skipped, pieces = 'it contains something that looks like a key or token', []
                if pieces:
                    texts = []
                    for start, end, kind, text in pieces:
                        # Sinhala in a document gets the same gloss as a memory would.
                        gloss = self.memory.gloss(text[:600]) if SINHALA.search(text) else ''
                        texts.append(memory_doc(path, gloss, text))
                    vectors = []
                    for at in range(0, len(texts), 32):
                        vectors += self.memory.embed(texts[at:at + 32])
                    rows = [(str(path), s, e, k, t, array('f', v).tobytes())
                            for (s, e, k, t), v in zip(pieces, vectors)]
            except Exception as error:
                skipped = f'unreadable: {error}'[:200]
        with self.store.connect() as db:
            db.execute('DELETE FROM file_chunks WHERE path=?', (str(path),))
            db.executemany('INSERT INTO file_chunks(path,start,end,kind,text,vector) '
                           'VALUES (?,?,?,?,?,?)', rows)
            db.execute('INSERT OR REPLACE INTO file_state VALUES (?,?,?,?,?)',
                       (str(path), stat.st_mtime, stat.st_size, len(rows), skipped))
        return len(rows), skipped

    def search(self, question, top=TOP, floor=0.5):
        """The chunks closest to the question, best first, each with where it came from."""
        import numpy
        import memory as memory_module
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM file_chunks')]
        if not rows or not str(question).strip():
            return []
        query = numpy.array(self.memory.embed(
            [memory_module.QUERY_PREFIX + self.memory.gloss(question)])[0])
        matrix = numpy.array([array('f', r['vector']) for r in rows])
        scores = matrix @ query / (numpy.linalg.norm(matrix, axis=1) *
                                   numpy.linalg.norm(query) + 1e-9)
        ranked = sorted(zip(scores.tolist(), rows), key=lambda pair: -pair[0])
        found = []
        for score, row in ranked[:top]:
            if score < floor:
                break
            found.append({'path': row['path'], 'where': cite(row), 'start': row['start'],
                          'end': row['end'], 'kind': row['kind'], 'text': row['text'],
                          'score': round(score, 3)})
        return found

    def keep_current(self):
        """Index now, then look for changes every few minutes, in the background."""
        def loop():
            while True:
                try:
                    report = self.index()
                    if report['read'] or report['removed']:
                        print(f'  files: {report}', flush=True)
                except Exception as error:
                    print(f'  files: indexing failed: {error}', file=sys.stderr)
                time.sleep(RESCAN_SECONDS)
        thread = threading.Thread(target=loop, daemon=True, name='file-index')
        thread.start()
        return thread

    def status(self):
        with self.store.connect() as db:
            files = db.execute('SELECT COUNT(*), SUM(chunks) FROM file_state WHERE skipped=""').fetchone()
            skipped = db.execute('SELECT path, skipped FROM file_state WHERE skipped!=""').fetchall()
        return {'files': files[0] or 0, 'chunks': files[1] or 0,
                'skipped': [(row[0], row[1]) for row in skipped]}


def memory_doc(path, gloss, text):
    import memory as memory_module
    header = f'{Path(path).name}: '
    return memory_module.DOC_PREFIX + header + (f'{gloss} | ' if gloss else '') + text


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('question', nargs='*')
    parser.add_argument('--index', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    from assistant_core import ROOT, Store
    library = Library(Store(ROOT / 'data' / 'jarvis.db'))
    if args.index:
        started = time.monotonic()
        report = library.index(lambda path, count: print(f'  {count:4} {path}'))
        print(f'{report} in {time.monotonic() - started:.0f}s')
    elif args.question:
        for row in library.search(' '.join(args.question)):
            print(f"{row['score']:.2f}  {row['where']}\n      {row['text'][:150]!r}")
    else:
        status = library.status()
        print(f"{status['files']} files, {status['chunks']} chunks")
        for path, reason in status['skipped']:
            print(f'  skipped {path}: {reason}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
