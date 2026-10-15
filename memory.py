"""Memory that remembers: search by meaning, summaries, and facts it picks up (Day 14).

    python memory.py "mokakda mama parata kiwwe project eka gana"   search
    python memory.py --reindex                                      index everything
    python memory.py --status

How a search works, and why each step is there:

  1. Every note, memory and conversation summary gets a one-line English gloss, written
     by the fast brain, and a vector from nomic-embed-text. Both live in jarvis.db.
  2. A question is glossed the same way and compared with every vector.

The gloss is the important part. Measured on twelve memories and twelve questions worded
differently from them (bench_memory.py): nomic-embed-text alone found 7 of 12 -- every
question in Sinhala script missed, because to that model all Sinhala looks alike. With
the gloss, 11 of 12 first time and 12 of 12 within the top three.

Nothing leaves the computer: both models run in Ollama here.
"""
import argparse
import datetime as dt
import json
import re
import sys
import threading
from array import array

import requests

from sinhala import singlish_to_script

ENDPOINT = 'http://127.0.0.1:11434'
# Every call to the fast model must ask for the same context size. Ollama reloads a model
# whenever it changes, and these calls asking for the default while the chat asked for
# 8192 cost a 13-second reload on almost every request (measured 16 Oct 2026).
FAST_CTX = 8192
EMBED_MODEL = 'nomic-embed-text'
QUERY_PREFIX, DOC_PREFIX = 'search_query: ', 'search_document: '
SEARCHABLE = ('note', 'memory', 'summary')
TOP = 5
# Below this, a "match" is noise. Measured on the bench set: right answers scored 0.58 to
# 0.76, wrong ones mostly under 0.57 but up to 0.68 -- they overlap, so no floor separates
# them. 0.5 keeps every right answer; the model sees the top five and judges.
FLOOR = 0.5
# Facts closer than this to one already kept are the same fact said again.
DUPLICATE = 0.9
# Conversation is summarised in chunks once this much is waiting, well before the store
# drops anything (it keeps 200 messages).
SUMMARY_AFTER = 40
SUMMARY_CHUNK = 30

# Someone talking about themselves. Only these utterances are checked for facts.
FIRST_PERSON = re.compile(r'\b(i|i\'m|my|me|mine|mama|mata|mage|mange)\b|මම|මට|මගේ|මාව|මගෙ',
                          re.I)
# A question is never a fact. Measured 16 Oct: "mata hondai coffee da tea da?" (which is
# better for me, coffee or tea?) was saved as "The user prefers coffee or tea" -- the
# model was told questions do not count and saved it anyway.
QUESTION = re.compile(r'(\?|\b(da|neda)|ද)\s*[.!]*\s*$', re.I)


class Memory:
    def __init__(self, store, endpoint=ENDPOINT, gloss=None, embed=None):
        self.store, self.endpoint = store, endpoint
        self.gloss = gloss or self.ollama_gloss
        self.embed = embed or self.ollama_embed
        self.lock = threading.Lock()
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors (record_id TEXT PRIMARY KEY, '
                       'gloss TEXT NOT NULL, vector BLOB NOT NULL, model TEXT NOT NULL, '
                       'updated TEXT NOT NULL)')

    # ------------------------------------------------------------ models

    def fast_model(self):
        return self.store.config('model', '') or 'qwen3.5:9b'

    def ollama_gloss(self, text):
        text = singlish_to_script(text)
        response = requests.post(self.endpoint + '/api/chat', json={
            'model': self.fast_model(), 'stream': False, 'think': False, 'keep_alive': '30m',
            'messages': [{'role': 'user', 'content':
                          'Rewrite this in plain English in one short line, keeping names, '
                          f'numbers and dates. Reply with the line only.\n\n{text}'}],
            'options': {'temperature': 0, 'num_ctx': FAST_CTX, 'num_predict': 120}}, timeout=(5, 120))
        response.raise_for_status()
        return (response.json().get('message') or {}).get('content', '').strip() or text

    def ollama_embed(self, texts):
        response = requests.post(self.endpoint + '/api/embed', json={
            'model': EMBED_MODEL, 'input': texts, 'keep_alive': '30m'}, timeout=(5, 120))
        response.raise_for_status()
        return response.json()['embeddings']

    # ------------------------------------------------------------ indexing

    @staticmethod
    def text_of(record):
        content = record.get('content') or ''
        # A learned fact's content only says where it was heard; indexing that too diluted
        # the fact, so the same thing said twice was not recognised as the same.
        if content.startswith('Heard on'):
            content = ''
        return f"{record['title']}. {content}".strip(' .')

    def index(self, record):
        """Gloss and embed one record. Returns the gloss."""
        original = self.text_of(record)
        gloss = self.gloss(original)
        vector = self.embed([DOC_PREFIX + f'{gloss} | {original}'])[0]
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)',
                       (record['id'], gloss, array('f', vector).tobytes(), EMBED_MODEL,
                        dt.datetime.now().isoformat(timespec='seconds')))
        return gloss

    def missing(self):
        with self.store.connect() as db:
            done = {row[0] for row in db.execute('SELECT record_id FROM vectors')}
        return [r for r in self.store.records() if r['kind'] in SEARCHABLE and r['id'] not in done]

    def reindex(self, on_progress=lambda done, total: None):
        """Index whatever has no vector yet. Safe to run at every start."""
        todo = self.missing()
        for number, record in enumerate(todo, 1):
            try:
                self.index(record)
            except Exception as error:
                print(f'  memory: could not index {record["title"][:40]!r}: {error}',
                      file=sys.stderr)
            on_progress(number, len(todo))
        return len(todo)

    def reindex_in_background(self):
        thread = threading.Thread(target=self.reindex, daemon=True, name='memory-index')
        thread.start()
        return thread

    # ------------------------------------------------------------ searching

    def vectors(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT v.record_id, v.gloss, v.vector, r.kind, r.title, '
                              'r.content, r.created FROM vectors v JOIN records r '
                              'ON r.id = v.record_id').fetchall()
        return [dict(row, vector=array('f', row['vector'])) for row in rows]

    def search(self, question, top=TOP, kinds=SEARCHABLE, floor=FLOOR):
        """The records closest in meaning to the question, best first, with their scores."""
        import numpy
        rows = [r for r in self.vectors() if r['kind'] in kinds]
        if not rows or not str(question).strip():
            return []
        query = numpy.array(self.embed([QUERY_PREFIX + self.gloss(question)])[0])
        matrix = numpy.array([r['vector'] for r in rows])
        scores = matrix @ query / (numpy.linalg.norm(matrix, axis=1) *
                                   numpy.linalg.norm(query) + 1e-9)
        ranked = sorted(zip(scores.tolist(), rows), key=lambda pair: -pair[0])
        return [dict({k: v for k, v in row.items() if k != 'vector'}, score=round(score, 3))
                for score, row in ranked[:top] if score >= floor]

    # ------------------------------------------------------------ summaries

    def unsummarised(self):
        through = int(self.store.config('summarised_through', 0) or 0)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id, role, content FROM messages WHERE id > ? ORDER BY id', (through,))]

    def roll_up(self, summarise=None):
        """Turn the oldest waiting conversation into a short dated summary, and index it.

        The conversation table keeps 200 messages and the model sees 14; without this,
        anything older than a few days was simply gone.
        """
        waiting = self.unsummarised()
        if len(waiting) < SUMMARY_AFTER:
            return None
        chunk = waiting[:SUMMARY_CHUNK]
        transcript = '\n'.join(f"{m['role']}: {m['content'][:500]}" for m in chunk)
        text = (summarise or self.summarise)(transcript)
        record = self.store.add('summary', f'Conversation, {dt.date.today():%d %b %Y}', text)
        record.update(kind='summary', content=text, title=record['title'])
        self.store.set_config('summarised_through', chunk[-1]['id'])
        self.index(record)
        return record

    def summarise(self, transcript):
        response = requests.post(self.endpoint + '/api/chat', json={
            'model': self.fast_model(), 'stream': False, 'think': False, 'keep_alive': '30m',
            'messages': [{'role': 'user', 'content':
                          'Summarise this conversation in at most five short lines: what was '
                          'asked, what was decided or done, and anything the user said about '
                          'themselves, their work or their plans. Keep names, numbers and '
                          f'dates.\n\n{transcript}'}],
            'options': {'temperature': 0, 'num_ctx': FAST_CTX, 'num_predict': 300}}, timeout=(5, 180))
        response.raise_for_status()
        return (response.json().get('message') or {}).get('content', '').strip()

    # ------------------------------------------------------------ facts

    def extract_facts(self, utterance, ask=None):
        """Lasting facts the user said about themselves, as short sentences.

        Only utterances in the first person are looked at, and only lasting things count:
        "I don't drink coffee" is a fact; "remind me to buy coffee" is a request.
        """
        if not FIRST_PERSON.search(str(utterance)) or QUESTION.search(str(utterance)):
            return []
        schema = {'type': 'object', 'required': ['facts'], 'properties': {
            'facts': {'type': 'array', 'maxItems': 3, 'items': {
                'type': 'object', 'required': ['en', 'spoken'],
                'properties': {'en': {'type': 'string'}, 'spoken': {'type': 'string'}}}}}}
        prompt = ('Did the user state a lasting fact about themselves -- a preference, an '
                  'allergy, a habit, a relationship, their work, a date that matters to them? '
                  'Requests, questions, commands and passing moods are NOT facts. For each '
                  'fact give "en": a short sentence about "the user" in English, and '
                  '"spoken": the same fact said back to them in the language they used, '
                  'addressing them as "you" (in Sinhala, "ඔයා"), in everyday spoken words. '
                  f'If there is none, return an empty list.\n\nThe user said: '
                  f'"{singlish_to_script(utterance)}"')
        try:
            if ask:
                answer = ask(prompt, schema)
            else:
                response = requests.post(self.endpoint + '/api/chat', json={
                    'model': self.fast_model(), 'stream': False, 'think': False,
                    'format': schema, 'keep_alive': '30m',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'options': {'temperature': 0, 'num_ctx': FAST_CTX, 'num_predict': 200}}, timeout=(5, 60))
                response.raise_for_status()
                answer = json.loads(response.json()['message']['content'])
        except Exception as error:
            print(f'  memory: fact check failed: {error}', file=sys.stderr)
            return []
        facts = []
        for fact in answer.get('facts', [])[:3]:
            if isinstance(fact, dict) and str(fact.get('en', '')).strip():
                facts.append({'en': str(fact['en']).strip(),
                              'spoken': str(fact.get('spoken') or fact['en']).strip()})
        return facts

    def learn(self, utterance, ask=None):
        """Save new facts from what was said. Returns what was saved -- [{en, spoken}] -- so
        that it can be said out loud. Secret memory is the fastest way to lose trust."""
        saved = []
        for fact in self.extract_facts(utterance, ask):
            close = self.search(fact['en'], top=1, kinds=('memory',), floor=DUPLICATE)
            if close:
                continue
            content = f'Heard on {dt.date.today():%d %b %Y}: "{utterance[:300]}"'
            record = self.store.add('memory', fact['en'][:200], content)
            record.update(kind='memory', content=content, title=fact['en'][:200])
            try:
                self.index(record)
            except Exception as error:
                print(f'  memory: saved but not indexed: {error}', file=sys.stderr)
            saved.append(fact)
        return saved


def context_for(store, prompt, memory=None, top=TOP):
    """What the model is told it knows about the user, for this request.

    The five records closest in meaning to what was asked. If the search cannot run --
    Ollama down, no embedding model -- the ten newest memories, as before Day 14 but
    fewer, rather than nothing.
    """
    try:
        found = (memory or Memory(store)).search(prompt, top=top)
        return [{'kind': r['kind'], 'title': r['title'], 'content': (r['content'] or '')[:600],
                 'saved': r['created'][:10]} for r in found]
    except Exception:
        recent = [r for r in store.records() if r['kind'] == 'memory'][:10]
        return [{'kind': r['kind'], 'title': r['title'], 'saved': r['created'][:10]}
                for r in recent]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('question', nargs='*')
    parser.add_argument('--reindex', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    from assistant_core import ROOT, Store
    memory = Memory(Store(ROOT / 'data' / 'jarvis.db'))
    if args.reindex:
        count = memory.reindex(lambda done, total: print(f'  {done}/{total}', end='\r'))
        print(f'indexed {count}')
    elif args.question:
        for row in memory.search(' '.join(args.question)):
            print(f"{row['score']:.2f}  {row['kind']:8} {row['title'][:60]}   [{row['gloss'][:50]}]")
    else:
        print(f'{len(memory.vectors())} indexed, {len(memory.missing())} waiting, '
              f'{len(memory.unsummarised())} messages not yet summarised')
    return 0


if __name__ == '__main__':
    sys.exit(main())
