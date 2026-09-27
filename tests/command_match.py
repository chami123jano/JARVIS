"""The metric that actually matters: does the right command win?

whisper_bench.py scores transcription against the exact sentence, which is the wrong
target. JARVIS never needs the words — it needs to pick one of about forty commands. A
transcript scoring 0.45 against its own sentence can still be far closer to the correct
command than to any of the other thirty-nine, and that is a success.

    .venv\\Scripts\\python.exe tests\\command_match.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sinhala import similarity

# The command vocabulary, with the spoken forms that should reach each one. Several
# phrasings per command, in Sinhala and in the English-mixed way it is really said.
COMMANDS = {
    'time': ['dan velava kiyada', 'velava kiyada', 'time kiyada', 'what time is it'],
    'weather': ['ada kalaguna kohomada', 'kalaguna kohomada', 'weather kohomada'],
    'system_status': ['system eke thathwaya kiyanna', 'system status', 'pc eka kohomada',
                      'pariganakaye thathwaya'],
    'send_message': ['ammata message ekak yavanna', 'message ekak yavanna',
                     'message yavanna', 'ekata message ekak yavanna'],
    'send_message_text': ['ammata kiyanna mama rathri enava', 'kiyanna mama enava',
                          'mama rathri enava kiyala message ekak yavanna'],
    'reminder_relative': ['minitthu dahayakin mathak karanna', 'minitthu dahayakin mathak',
                          'dahayakin mathak karanna', 'minutes dahayakin remind karanna'],
    'reminder_absolute': ['heta ude atata mathak karanna', 'heta ude mathak karanna',
                          'heta atata mathak karanna'],
    'play_music': ['sinduvak danna', 'sindu danna', 'music එka danna', 'song ekak danna'],
    'volume_up': ['volume eka vadi karanna', 'volume vadi karanna', 'sound eka vadi karanna'],
    'open_app': ['chrome eka open karanna', 'chrome open karanna', 'eka open karanna'],
    'dollar_rate': ['ada dollar rate kiyada', 'dollar rate kiyada', 'dollar eka kiyada'],
    'save_note': ['satahanak save karanna', 'note ekak save karanna', 'satahanak liyanna'],
    'list_notes': ['mage satahan pennanna', 'satahan pennanna', 'notes pennanna'],
    'calculate': ['desiya panaha guna hathara kiyada', 'guna hathara kiyada',
                  'calculate karanna'],
    'screenshot': ['screenshot ekak ganna', 'screenshot ganna', 'screen eka ganna'],
    'battery': ['batteriya kiyada', 'battery kiyada', 'battery eka kiyada'],
    'news': ['ada puvath monavada', 'puvath monavada', 'news monavada'],
    'email': ['email balanna', 'email eka balanna', 'mail balanna'],
    'lock_pc': ['pariganakaya lock karanna', 'lock karanna', 'pc eka lock karanna'],
    'capabilities': ['jarvis oyata monava karanna puluvanda', 'monava karanna puluvanda',
                     'oyata mokada karanna puluvan'],
}

# Which command each recorded clip was meant to trigger, in the order they were spoken.
TRUTH = ['time', 'weather', 'system_status', 'send_message', 'send_message_text',
         'reminder_relative', 'reminder_absolute', 'play_music', 'volume_up', 'open_app',
         'dollar_rate', 'save_note', 'list_notes', 'calculate', 'screenshot', 'battery',
         'news', 'email', 'lock_pc', 'capabilities']

ACCEPT = .45   # below this, treat it as "did not understand" rather than guessing


def classify(text):
    """Best command for this transcript, with its score and the runner-up."""
    scored = []
    for name, phrasings in COMMANDS.items():
        scored.append((max(similarity(text, phrase) for phrase in phrasings), name))
    scored.sort(reverse=True)
    return scored[0], scored[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bench', default='data/voice/bench.json')
    parser.add_argument('--config', default=None, help='only this config')
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    path = root / args.bench
    if not path.exists():
        raise SystemExit(f'Run whisper_bench.py first: {path} missing')
    bench = json.loads(path.read_text(encoding='utf-8'))

    summary = []
    for config in bench:
        if args.config and args.config.lower() not in config['config'].lower():
            continue
        correct = rejected = wrong = 0
        details = []
        for row in config['rows']:
            number = row['n']
            if number > len(TRUTH):
                continue
            expected = TRUTH[number - 1]
            (score, best), (second_score, second) = classify(row['text'])
            if score < ACCEPT:
                rejected += 1
                verdict = 'unsure'
            elif best == expected:
                correct += 1
                verdict = 'OK'
            else:
                wrong += 1
                verdict = 'WRONG'
            details.append((number, verdict, expected, best, score, second, second_score))
        total = correct + rejected + wrong
        summary.append((correct / total if total else 0, correct, rejected, wrong,
                        config['config'], details))

    summary.sort(reverse=True)
    for rate, correct, rejected, wrong, name, details in summary:
        print(f'{name:24} correct {correct:2}/{correct+rejected+wrong}  '
              f'({rate*100:.0f}%)   unsure {rejected:2}  WRONG {wrong:2}')

    print('\n' + '=' * 78)
    best = summary[0]
    print(f'BEST CONFIG: {best[4]} -> {best[1]} of {best[1]+best[2]+best[3]} commands correct')
    print('\nPer-clip detail for that config:')
    print(f'{"clip":>4} {"verdict":>8}  {"expected":22} {"picked":22} {"score":>5}')
    for number, verdict, expected, picked, score, second, second_score in best[5]:
        flag = '' if verdict == 'OK' else f'   (2nd: {second} {second_score:.2f})'
        print(f'{number:>4} {verdict:>8}  {expected:22} {picked:22} {score:>5.2f}{flag}')

    print(f'\nA "WRONG" is the dangerous outcome: it acts on the wrong command.')
    print(f'"unsure" is safe: JARVIS asks you to repeat. Threshold is {ACCEPT}.')


if __name__ == '__main__':
    main()
