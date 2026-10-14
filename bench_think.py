"""Does the deep brain actually answer hard questions better? Measured, not assumed.

    python bench_think.py                 fast brain (as voice uses it) against the deep one
    python bench_think.py --only fast

Each question has one checkable answer, worked out by code rather than by hand. The model
is asked to end with "ANSWER: ..."; only that line is marked.
"""
import argparse
import json
import re
import sys
import time

import requests

ENDPOINT = 'http://127.0.0.1:11434'

QUESTIONS = [
    ('How many times does the digit 7 appear when you write out every whole number from '
     '1 to 1000?', '300'),
    ('I deposit 100,000 rupees at 12% a year, compounded monthly. To the nearest rupee, '
     'what is it worth after exactly 3 years?', '143077'),
    ('What is the 100th prime number?', '541'),
    ('A train leaves Colombo at 08:00 at 60 km/h towards Kandy, 120 km away. Another leaves '
     'Kandy at 08:30 towards Colombo at 40 km/h. At what time do they meet? Give HH:MM.',
     '09:24'),
    ('In how many different ways can you make exactly 50 rupees from 1, 2, 5 and 10 rupee '
     'coins? Order does not matter.', '341'),
    ('What is the sum of all the even Fibonacci numbers below four million?', '4613732'),
    ('What day of the week was 1 January 2000?', 'saturday'),
    ('A shop marks an item up by 40% and then gives 25% off the marked price. The customer '
     'pays 2,100 rupees. What did the shop pay for the item, in rupees?', '2000'),
]
SUFFIX = '\n\nWork it out, then end with one line: ANSWER: <the answer only>'

BRAINS = {
    # What the voice path uses for a short question: the fast model, thinking off.
    'fast': {'model': 'qwen3.5:9b', 'think': False, 'num_predict': 1600, 'num_ctx': 8192},
    'fast+think': {'model': 'qwen3.5:9b', 'think': True, 'num_predict': 6000, 'num_ctx': 8192},
    'deep': {'model': 'gpt-oss:20b', 'think': 'medium', 'num_predict': 12000, 'num_ctx': 16384},
}


def marked(answer):
    lines = re.findall(r'ANSWER:\s*(.+)', str(answer), re.I)
    if not lines:
        return ''
    value = lines[-1].strip().lower().strip('.*` ')
    return re.sub(r'(?<=\d),(?=\d{3})', '', value)


def correct(answer, expected):
    value = marked(answer)
    if expected.isdigit():
        numbers = re.findall(r'\d+(?:\.\d+)?', value)
        return any(abs(float(n) - float(expected)) < 0.51 for n in numbers)
    if ':' in expected:
        return expected.lstrip('0') in value.replace('.', ':').lstrip('0')
    return expected in value


def ask(brain, question):
    config = BRAINS[brain]
    started = time.monotonic()
    response = requests.post(ENDPOINT + '/api/chat', json={
        'model': config['model'], 'stream': False, 'think': config['think'],
        'keep_alive': '10m', 'messages': [{'role': 'user', 'content': question + SUFFIX}],
        'options': {'temperature': 0, 'num_ctx': config['num_ctx'],
                    'num_predict': config['num_predict']}}, timeout=(5, 1200))
    data = response.json()
    return (data.get('message') or {}).get('content', ''), time.monotonic() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='*', default=['fast', 'fast+think', 'deep'])
    parser.add_argument('--out', default='data/bench_think.json')
    args = parser.parse_args()
    results = {}
    for brain in args.only:
        results[brain] = []
        for question, expected in QUESTIONS:
            answer, seconds = ask(brain, question)
            ok = correct(answer, expected)
            results[brain].append({'question': question[:60], 'expected': expected,
                                   'got': marked(answer)[:40], 'ok': ok,
                                   'seconds': round(seconds, 1)})
            print(f'{brain:10} {"ok " if ok else "WRONG"} {seconds:6.1f}s  '
                  f'expected {expected:9} got {marked(answer)[:30]!r}', flush=True)
        right = sum(r['ok'] for r in results[brain])
        total = sum(r['seconds'] for r in results[brain])
        print(f'{brain:10} {right}/{len(QUESTIONS)} right, {total:.0f}s in all\n', flush=True)
    with open(args.out, 'w', encoding='utf-8') as out:
        json.dump(results, out, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
