"""Can an embedding model find a memory by meaning, in Sinhala as well as English?

    python bench_memory.py                     every installed embedding model
    python bench_memory.py nomic-embed-text

Each question is worded differently from the memory it should find -- the point of
searching by meaning is that shared words are not needed. Top-1 and top-3 are counted.
"""
import math
import sys
import time

import requests

ENDPOINT = 'http://127.0.0.1:11434'

MEMORIES = [
    'මම කෝපි බොන්නේ නෑ, තේ විතරයි',                                  # 0 no coffee, tea only
    'අම්මගේ උපන්දිනය දෙසැම්බර් 12',                                    # 1 mother's birthday
    'Pharmacy POS project eka next week client ta demo karanna one',   # 2 POS demo
    'The car service is due at 60,000 km, at the Toyota agent in Kurunegala',  # 3
    'මගේ ලැප්ටොප් එකට RAM 32GB දාන්න හිතාගෙන ඉන්නේ',                     # 4 RAM upgrade
    'Prefers replies in Sinhala, short and spoken',                    # 5
    'Wi-Fi password eka router eke yata thiyena sticker eke',          # 6
    'Dentist appointment moved to the 3rd, Dr. Perera, 4.30 pm',       # 7
    'නංගිට ගණිතය උගන්වන්න සෙනසුරාදා උදේ',                               # 8 maths tuition
    'Electricity bill comes on the 20th, pay it at the post office',   # 9
    'JARVIS project eke deep model eka gpt-oss, fast eka qwen',        # 10
    'මට බෝංචි ඇලර්ජි',                                                 # 11 bean allergy
]
QUESTIONS = [
    ('මම මොනවද බොන්නේ?', 0),                                       # what do I drink
    ('when is my mother born', 1),
    ('mokakda mama parata kiwwe project eka gana', 2),              # what did I say about the project
    ('when should the car go for maintenance', 3),
    ('computer memory upgrade plan', 4),
    ('how do I like you to answer me', 5),
    ('internet eke password eka koheda', 6),                        # where is the wifi password
    ('දත් දොස්තර ළඟට යන්නේ කවදද', 7),                                 # when do I go to the dentist
    ('my sister tuition', 8),
    ('කරන්ට් බිල ගෙවන්නේ කොහෙද', 9),                                  # where do I pay the power bill
    ('which model thinks hard', 10),
    ('මට කන්න බැරි මොනවද', 11),                                       # what can't I eat
]


def embed(model, texts, prefix=''):
    response = requests.post(ENDPOINT + '/api/embed', json={
        'model': model, 'input': [prefix + t for t in texts]}, timeout=300)
    response.raise_for_status()
    return response.json()['embeddings']


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def score(model, query_prefix='', doc_prefix=''):
    started = time.monotonic()
    memories = embed(model, MEMORIES, doc_prefix)
    questions = embed(model, [q for q, _ in QUESTIONS], query_prefix)
    top1 = top3 = 0
    misses = []
    for (question, expected), vector in zip(QUESTIONS, questions):
        ranked = sorted(range(len(MEMORIES)), key=lambda i: -cosine(vector, memories[i]))
        top1 += ranked[0] == expected
        top3 += expected in ranked[:3]
        if ranked[0] != expected:
            misses.append(f'{question!r} -> {MEMORIES[ranked[0]][:40]!r}')
    return top1, top3, time.monotonic() - started, misses


PREFIXES = {'nomic-embed-text': ('search_query: ', 'search_document: ')}


def gloss(text, model='qwen3.5:9b'):
    """A one-line English rendering, by the fast brain, which reads Sinhala well."""
    response = requests.post(ENDPOINT + '/api/chat', json={
        'model': model, 'stream': False, 'think': False, 'keep_alive': '30m',
        'messages': [{'role': 'user', 'content':
                      'Rewrite this in plain English in one short line, keeping names, '
                      f'numbers and dates. Reply with the line only.\n\n{text}'}],
        'options': {'temperature': 0, 'num_predict': 120}}, timeout=120)
    return response.json()['message']['content'].strip()


def score_glossed(model):
    global MEMORIES, QUESTIONS
    started = time.monotonic()
    original = (MEMORIES, QUESTIONS)
    MEMORIES = [f'{gloss(m)} | {m}' for m in original[0]]
    QUESTIONS = [(gloss(q), i) for q, i in original[1]]
    try:
        top1, top3, _, misses = score(model, *PREFIXES.get(model, ('', '')))
    finally:
        MEMORIES, QUESTIONS = original
    return top1, top3, time.monotonic() - started, misses


def main():
    if sys.argv[1:2] == ['--gloss']:
        top1, top3, seconds, misses = score_glossed(sys.argv[2])
        print(f'{sys.argv[2]} + English gloss  top-1 {top1}/{len(QUESTIONS)}  '
              f'top-3 {top3}/{len(QUESTIONS)}  {seconds:.1f}s for all glosses')
        for miss in misses:
            print(f'    miss: {miss}')
        return 0
    models = sys.argv[1:] or [m['name'].split(':')[0] for m in
                              requests.get(ENDPOINT + '/api/tags').json()['models']
                              if 'embed' in m['name'] or 'bge' in m['name']]
    for model in models:
        top1, top3, seconds, misses = score(model, *PREFIXES.get(model, ('', '')))
        print(f'{model:24} top-1 {top1}/{len(QUESTIONS)}  top-3 {top3}/{len(QUESTIONS)}  '
              f'{seconds:.1f}s')
        for miss in misses:
            print(f'    miss: {miss}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
