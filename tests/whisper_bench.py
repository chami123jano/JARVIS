"""Replay saved Sinhala clips against different Whisper settings and score each one.

The point is to stop guessing. Every configuration is measured against the same real
recordings, scored phonetically so the script Whisper chooses does not matter, and the
best configuration wins on the number.

    .venv\\Scripts\\python.exe tests\\whisper_bench.py
    .venv\\Scripts\\python.exe tests\\whisper_bench.py --clips data/voice/clips --show 5
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_loop
from sinhala import normalize, similarity

# What was actually said, in order, from the list given to the user. Romanised, because
# scoring is phonetic; the reference spelling only has to sound right.
EXPECTED = [
    'dan velava kiyada',                 # 1  what time is it
    'ada kalaguna kohomada',             # 2  weather
    'system eke thathwaya kiyanna',      # 3  system status
    'ammata message ekak yavanna',       # 4  send a message
    'ammata kiyanna mama rathri enava',  # 5  message with content
    'minitthu dahayakin mathak karanna', # 6  reminder in ten minutes
    'heta ude atata mathak karanna',     # 7  reminder tomorrow
    'sinduvak danna',                    # 8  play music
    'volume eka vadi karanna',           # 9  volume up
    'chrome eka open karanna',           # 10 open an app
    'ada dollar rate kiyada',            # 11 dollar rate
    'satahanak save karanna',            # 12 save a note
    'mage satahan pennanna',             # 13 list notes
    'desiya panaha guna hathara kiyada', # 14 arithmetic
    'screenshot ekak ganna',             # 15 screenshot
    'batteriya kiyada',                  # 16 battery
    'ada puvath monavada',               # 17 news
    'email balanna',                     # 18 email
    'pariganakaya lock karanna',         # 19 lock the pc
    'jarvis oyata monava karanna puluvanda',  # 20 open question
]

# faster-whisper's default: on detecting a repetition loop it retries hotter. Passing a
# single temperature disables that defence, which is what produced the "වවවවවව" output.
FALLBACK = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

CONFIGS = [
    ('turbo si plain',        'large-v3-turbo', {'language': 'si', 'temperature': 0.0}),
    ('turbo si prompted',     'large-v3-turbo', {'language': 'si', 'temperature': 0.0,
                                                'initial_prompt': 'PROMPT'}),
    ('turbo si vadtrim',      'large-v3-turbo', {'language': 'si', 'temperature': 0.0,
                                                'vad_filter': True}),
    ('turbo auto plain',      'large-v3-turbo', {'language': None, 'temperature': 0.0}),

    # The loop defences, which the configurations above had switched off.
    ('turbo si fallback',     'large-v3-turbo', {'language': 'si', 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4}),
    ('turbo auto fallback',   'large-v3-turbo', {'language': None, 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4}),
    ('turbo si norepeat',     'large-v3-turbo', {'language': 'si', 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4,
                                                'no_repeat_ngram_size': 3}),
    ('turbo auto norepeat',   'large-v3-turbo', {'language': None, 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4,
                                                'no_repeat_ngram_size': 3}),
    ('turbo si reppenalty',   'large-v3-turbo', {'language': 'si', 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4,
                                                'repetition_penalty': 1.15}),

    ('large-v3 si fallback',  'large-v3',       {'language': 'si', 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4}),
    ('large-v3 auto fallback', 'large-v3',      {'language': None, 'temperature': FALLBACK,
                                                'compression_ratio_threshold': 2.4}),
]

BASE = {'beam_size': 5, 'vad_filter': False, 'initial_prompt': None,
        'condition_on_previous_text': False, 'no_speech_threshold': .5,
        'log_prob_threshold': -1.0}


def transcribe(model, audio, options):
    settings = {**BASE, **options}
    if settings.get('initial_prompt') == 'PROMPT':
        settings['initial_prompt'] = voice_loop.SINHALA_PROMPT
    segments, info = model.transcribe(audio, **settings)
    return ' '.join(s.text.strip() for s in segments).strip(), info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clips', default='data/voice/clips')
    parser.add_argument('--show', type=int, default=0, help='print N transcripts per config')
    parser.add_argument('--only', default=None, help='substring of a config name to run alone')
    args = parser.parse_args()

    folder = Path(args.clips)
    if not folder.is_absolute():
        folder = voice_loop.ROOT / folder
    clips = sorted(folder.glob('*.wav'))
    if not clips:
        raise SystemExit(f'No clips in {folder}. Record a session first.')
    print(f'{len(clips)} clips from {folder}\n')

    audio = {int(clip.stem): voice_loop.load_wav(clip) for clip in clips}
    configs = [c for c in CONFIGS if not args.only or args.only.lower() in c[0].lower()]
    results = []
    loaded = {}

    for name, model_name, options in configs:
        if model_name not in loaded:
            print(f'loading {model_name} ...', flush=True)
            loaded[model_name] = voice_loop.Ears(model_name).model
        model = loaded[model_name]

        scores, seconds, rows = [], 0.0, []
        for number in sorted(audio):
            if number > len(EXPECTED):
                continue
            started = time.monotonic()
            try:
                text, _ = transcribe(model, audio[number], options)
            except Exception as error:
                text = f'<error: {str(error)[:60]}>'
            seconds += time.monotonic() - started
            score = similarity(text, EXPECTED[number - 1])
            scores.append(score)
            rows.append((number, score, text))

        average = sum(scores) / len(scores) if scores else 0
        good = sum(1 for s in scores if s >= .6)
        partial = sum(1 for s in scores if .4 <= s < .6)
        results.append((average, good, partial, name, seconds, rows))
        print(f'{name:22} avg {average:.3f}   good(>=.60) {good:2}/{len(scores)}   '
              f'partial {partial:2}   {seconds:.0f}s total')
        for number, score, text in rows[:args.show]:
            print(f'    [{number:02}] {score:.2f}  said: {EXPECTED[number-1]}')
            print(f'          heard: {text[:100]}')
            print(f'          phonetic: {normalize(text)[:100]}')
        print()

    print('=' * 78)
    print('RANKED')
    for average, good, partial, name, seconds, _ in sorted(results, reverse=True):
        print(f'  {average:.3f}  good {good:2}  partial {partial:2}  {name}')
    best = max(results)
    print(f'\nBEST: {best[3]}  (avg {best[0]:.3f}, {best[1]} of {len(EXPECTED)} usable)')

    out = voice_loop.ROOT / 'data' / 'voice' / 'bench.json'
    out.write_text(json.dumps([{'config': r[3], 'average': round(r[0], 4), 'good': r[1],
                                'partial': r[2], 'seconds': round(r[4], 1),
                                'rows': [{'n': n, 'score': round(s, 3), 'text': t} for n, s, t in r[5]]}
                               for r in results], ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Full results: {out}')


if __name__ == '__main__':
    main()
