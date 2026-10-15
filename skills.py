"""New tools that JARVIS writes and you approve (Day 21).

    python skills.py --request "convert kilometres to miles and back"   write one (deep model)
    python skills.py --list                                             pending and active
    python skills.py --show NAME                                        read the code
    python skills.py --approve NAME                                     test it, then use it
    python skills.py --reject NAME                                      throw it away
    python skills.py --call NAME '{"amount": 5}'                        run an active one

The order of events is the whole design:

  1. The deep model writes a skill: one small file with its description, the phrases
     that call it, its inputs, worked examples and a run() function.
  2. It is checked without being run -- parsed, never imported -- against a short list of
     what a skill may do: arithmetic, dates, text. No files, no network, no shell, no
     eval, no reaching into Python's internals. Anything else is refused before you see it.
  3. It waits in skills/pending/. Nothing loads from there.
  4. You read it and approve it, in the console or here. Not by voice: approving code
     you have not read is the one thing this must never allow.
  5. On approval its own examples run as tests, and only if they pass does it move to
     skills/active/ and get committed to git -- so a bad one is one revert away.
  6. Every call runs in a separate Python process (skill_host.py) with a time limit, so a
     skill never runs inside JARVIS and never sees your messages or settings.
"""
import argparse
import ast
import datetime as dt
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ROOT / 'skills'
PENDING = SKILLS / 'pending'
ACTIVE = SKILLS / 'active'
HOST = ROOT / 'skill_host.py'

DEEP_MODEL = 'gpt-oss:20b'
# Medium, not the high the plan asked for. Measured on this machine, the same request:
#   high    12,000 tokens of reasoning in 426 s, then cut off -- no code at all
#   medium   9,473 tokens in 319 s, code that passed the check first time
EFFORT = 'medium'
WRITE_SECONDS = 15 * 60
CALL_SECONDS = 5
MAX_CHARS = 12000

# What a skill may import. Calculation, dates and text -- nothing that touches the
# computer or the network. Widening this is a decision, not a convenience.
ALLOWED_IMPORTS = {'math', 'cmath', 'datetime', 're', 'statistics', 'fractions', 'decimal',
                   'calendar', 'json', 'string', 'itertools', 'functools', 'collections',
                   'unicodedata', 'random', 'operator', 'textwrap'}
# Built-ins that reach outside a calculation, or around these rules.
FORBIDDEN_NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals',
                   'vars', 'getattr', 'setattr', 'delattr', 'input', 'breakpoint',
                   'memoryview', 'help', 'exit', 'quit', 'print', 'dir', 'type', 'object',
                   'super', 'classmethod', 'staticmethod', 'property', '__builtins__'}
INPUT_TYPES = {'number', 'integer', 'string'}
TIERS = {0}             # pure calculation only, for now

lock = threading.Lock()


class SkillError(ValueError):
    """A ValueError, so the console reports it as a refused request, not a crash."""


# ---------------------------------------------------------------- reading without running

def literal_skill(tree):
    """The SKILL dictionary, read as data with ast.literal_eval. Nothing is executed."""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'SKILL'):
            return ast.literal_eval(node.value)
    raise SkillError('there is no SKILL description')


def describe(path):
    """A skill's description, from a file that is parsed but never imported."""
    path = Path(path)
    skill = literal_skill(ast.parse(path.read_text(encoding='utf-8')))
    return dict(skill, path=str(path), file=path.name)


def check(code):
    """Everything wrong with this skill, without running it. An empty list means it may
    be shown to you for approval -- not that it is safe, which is your call."""
    problems = []
    if len(code) > MAX_CHARS:
        return [f'it is {len(code)} characters; a skill is at most {MAX_CHARS}']
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [f'it is not valid Python: {error.msg} on line {error.lineno}']

    # Loading the file must do nothing: only imports, constants, the description and
    # function definitions at the top level.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                problems.append(f'line {node.lineno}: decorators are not allowed')
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                    # a docstring
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            try:
                ast.literal_eval(node.value)
                continue
            except ValueError:
                pass
        problems.append(f'line {node.lineno}: only imports, constants and functions may '
                        f'sit at the top level, so that loading the file does nothing')

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in ALLOWED_IMPORTS:
                    problems.append(f'line {node.lineno}: imports {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or '').split('.')[0] not in ALLOWED_IMPORTS:
                problems.append(f'line {node.lineno}: imports from {node.module}')
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith('__')):
            problems.append(f'line {node.lineno}: uses {node.id}')
        elif isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            problems.append(f'line {node.lineno}: reaches into .{node.attr}')
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and '__' in node.value:
            # "{0.__class__}".format(x) reads attributes through a string.
            problems.append(f'line {node.lineno}: a string containing "__"')
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Yield,
                               ast.YieldFrom)):
            problems.append(f'line {node.lineno}: {type(node).__name__} is not needed in a skill')

    try:
        skill = literal_skill(tree)
    except (SkillError, ValueError) as error:
        return problems + [f'the SKILL description cannot be read: {error}']
    problems += check_description(skill)

    run = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run'), None)
    if not run:
        problems.append('there is no run() function')
    elif isinstance(skill.get('inputs'), dict):
        names = [a.arg for a in run.args.args + run.args.kwonlyargs]
        if sorted(names) != sorted(skill['inputs']) or run.args.vararg or run.args.kwarg:
            problems.append(f'run() takes {names}, but the description lists '
                            f'{list(skill["inputs"])}')
    return problems


def check_description(skill):
    problems = []
    if not isinstance(skill, dict):
        return ['SKILL is not a dictionary']
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', str(skill.get('name', ''))):
        problems.append('the name must be lower_case_with_underscores')
    for key in ('title_en', 'title_si', 'description'):
        if not str(skill.get(key, '')).strip():
            problems.append(f'{key} is missing')
    phrases = skill.get('phrases')
    if not isinstance(phrases, list) or len(phrases) < 3:
        problems.append('it needs at least three phrases that call it')
    elif not any(re.search('[඀-෿]', str(p)) for p in phrases):
        problems.append('none of its phrases is in Sinhala')
    inputs = skill.get('inputs')
    if not isinstance(inputs, dict) or any(t not in INPUT_TYPES for t in inputs.values()):
        problems.append(f'inputs must map names to one of {sorted(INPUT_TYPES)}')
    if skill.get('tier') not in TIERS:
        problems.append('only tier 0 skills -- pure calculation -- are allowed for now')
    examples = skill.get('examples')
    if not isinstance(examples, list) or len(examples) < 2:
        problems.append('it needs at least two worked examples')
    elif isinstance(inputs, dict):
        for number, example in enumerate(examples, 1):
            if not isinstance(example, dict) or set(example.get('inputs', {})) != set(inputs):
                problems.append(f'example {number} does not give exactly the inputs')
            elif not isinstance(example.get('expect'), dict) or not example['expect']:
                problems.append(f'example {number} has no expected result')
    return problems


# ---------------------------------------------------------------- running, elsewhere

def call(path, inputs, timeout=CALL_SECONDS):
    """Run an approved skill in its own process. Returns its result dict."""
    try:
        done = subprocess.run(
            [sys.executable, '-I', str(HOST)],
            input=json.dumps({'path': str(path), 'inputs': inputs}, ensure_ascii=False),
            capture_output=True, text=True, encoding='utf-8', timeout=timeout,
            cwd=str(SKILLS), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise SkillError(f'it took longer than {timeout} seconds and was stopped')
    try:
        result = json.loads(done.stdout or '{}')
    except ValueError:
        raise SkillError('it did not return a result')
    if done.returncode or 'error' in result:
        raise SkillError(result.get('error') or done.stderr[-300:] or 'it failed')
    return result


def same(expected, actual):
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(expected - actual) <= max(1e-6, abs(expected) * 1e-4)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return expected == actual


def run_examples(path):
    """The skill's own worked examples, as tests. [(ok, detail)]"""
    outcomes = []
    for example in describe(path)['examples']:
        try:
            result = call(path, example['inputs'])
        except SkillError as error:
            outcomes.append((False, f'{example["inputs"]}: {error}'))
            continue
        wrong = [f'{key} was {result.get(key)!r}, expected {value!r}'
                 for key, value in example['expect'].items() if not same(value, result.get(key))]
        for language in ('en', 'si'):
            if not str(result.get(language, '')).strip():
                wrong.append(f'no "{language}" sentence to say')
        outcomes.append((not wrong, f'{example["inputs"]}: ' + ('; '.join(wrong) or 'ok')))
    return outcomes


# ---------------------------------------------------------------- the lists

def pending():
    return sorted(PENDING.glob('*.py')) if PENDING.is_dir() else []


def active():
    """Approved skills' descriptions. A file that no longer reads is skipped, not fatal."""
    found = []
    for path in sorted(ACTIVE.glob('*.py')) if ACTIVE.is_dir() else []:
        try:
            found.append(describe(path))
        except Exception as error:
            print(f'  skill {path.name} unreadable: {error}', file=sys.stderr)
    return found


def find(name, folder):
    path = folder / f'{name}.py'
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', str(name)) or not path.is_file():
        raise SkillError(f'no skill called {name!r} in {folder.name}')
    return path


# ---------------------------------------------------------------- approval

def approve(name, commit=True, pending_dir=None, active_dir=None):
    """Test it, then use it. Returns what happened; never activates a failing skill."""
    pending_dir, active_dir = pending_dir or PENDING, active_dir or ACTIVE
    path = find(name, pending_dir)
    code = path.read_text(encoding='utf-8')
    # Checked again: the file may have been edited since it was written.
    problems = check(code)
    if problems:
        return {'approved': False, 'problems': problems}
    outcomes = run_examples(path)
    if not all(ok for ok, _ in outcomes):
        return {'approved': False, 'tests': outcomes}
    active_dir.mkdir(parents=True, exist_ok=True)
    target = active_dir / path.name
    code = code.replace('# NOT REVIEWED.', f'# Approved on {dt.date.today()}.', 1)
    target.write_text(code, encoding='utf-8')
    path.unlink()
    result = {'approved': True, 'tests': outcomes, 'path': str(target)}
    if commit:
        result['committed'] = commit_skill(target, name)
    return result


def commit_skill(path, name):
    """One commit per approved skill, of that file only, so a revert undoes exactly it."""
    try:
        relative = str(Path(path).resolve().relative_to(ROOT))
        subprocess.run(['git', 'add', '--', relative], cwd=ROOT, check=True,
                       capture_output=True, timeout=20)
        subprocess.run(['git', 'commit', '-m', f'Skill: {name}, approved', '--', relative],
                       cwd=ROOT, check=True, capture_output=True, timeout=20)
        return True
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        print(f'  skill {name} is active but not committed: {error}', file=sys.stderr)
        return False


def reject(name, reason='', pending_dir=None):
    path = find(name, pending_dir or PENDING)
    log = (pending_dir or PENDING).parent / 'rejected.log'
    with log.open('a', encoding='utf-8') as out:
        out.write(json.dumps({'name': name, 'when': dt.datetime.now().isoformat(timespec='seconds'),
                              'reason': reason}, ensure_ascii=False) + '\n')
    path.unlink()
    return {'rejected': name}


# ---------------------------------------------------------------- writing one

EXAMPLE_SKILL = '''"""Percentages: what is X percent of Y."""
import math

SKILL = {
    'name': 'percent_of',
    'title_en': 'Percentage of a number',
    'title_si': 'ප්‍රතිශතය',
    'description': 'Works out a percentage of a number, for example 15 percent of 2000.',
    'phrases': ['2000 න් සියයට 15 කීයද', 'සියයට කීයද', 'what is 15 percent of 2000',
                'percent of', 'percentage of a number'],
    'inputs': {'percent': 'number', 'amount': 'number'},
    'tier': 0,
    'examples': [
        {'inputs': {'percent': 15, 'amount': 2000}, 'expect': {'result': 300}},
        {'inputs': {'percent': 2.5, 'amount': 80}, 'expect': {'result': 2}},
    ],
}


def run(percent, amount):
    result = amount * percent / 100
    shown = f'{result:,.2f}'.rstrip('0').rstrip('.')
    return {'result': result,
            'en': f'{percent} percent of {amount} is {shown}.',
            'si': f'{amount} න් සියයට {percent} ක් කියන්නේ {shown}.'}
'''


def writing_prompt(need, problems=None):
    rules = (
        'Write ONE new JARVIS skill as a single Python file, for this need:\n'
        f'    "{need}"\n\n'
        'Follow the example exactly in shape:\n'
        f'```python\n{EXAMPLE_SKILL}```\n\n'
        'Rules -- a file that breaks one is refused automatically:\n'
        f'- import only from: {", ".join(sorted(ALLOWED_IMPORTS))}\n'
        '- no files, network, shell, eval, exec, getattr, print, classes, decorators, or '
        'anything starting with an underscore\n'
        '- at the top level only imports, constants, the SKILL dictionary and functions\n'
        '- run() takes exactly the inputs named in SKILL["inputs"] (types: number, '
        'integer, string) and returns a dict with the result values plus "en" and "si": '
        'one short spoken sentence each in English and in everyday spoken Sinhala\n'
        '- at least four phrases a person might say, at least two in Sinhala script\n'
        '- at least three examples whose expected values you have worked out by hand\n'
        '- tier is 0\n'
        '- keep it small: under 80 lines\n\n'
        'Reply with the file only, in one ```python block.')
    if problems:
        rules += ('\n\nYour previous attempt was refused for these reasons. Fix every one:\n- '
                  + '\n- '.join(problems))
    return rules


def extract_code(text):
    blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', str(text), re.S)
    return (max(blocks, key=len) if blocks else str(text)).strip() + '\n'


def ollama_writer(store=None, endpoint='http://127.0.0.1:11434'):
    """write(prompt) -> (text, model). The deep model at high effort, the fast one with
    thinking if the deep one will not fit in memory."""
    import requests

    def ask(model, prompt, think):
        """Streamed, so the limit is on the whole answer rather than on silence."""
        import time
        deadline = time.monotonic() + WRITE_SECONDS
        content = ''
        with requests.post(endpoint + '/api/chat', json={
                'model': model, 'stream': True, 'think': think, 'keep_alive': '5m',
                'messages': [{'role': 'user', 'content': prompt}],
                'options': {'num_ctx': 16384, 'num_predict': 12000, 'temperature': 0.2}},
                stream=True, timeout=(5, 300)) as response:
            if not response.ok:
                raise SkillError(response.json().get('error', f'Ollama HTTP {response.status_code}'))
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise SkillError(f'writing took longer than {WRITE_SECONDS // 60} minutes')
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get('error'):
                    raise SkillError(chunk['error'])
                content += (chunk.get('message') or {}).get('content') or ''
                if chunk.get('done'):
                    break
        return content

    def write(prompt):
        deep = (store.config('deep_model', DEEP_MODEL) if store else DEEP_MODEL) or DEEP_MODEL
        try:
            return ask(deep, prompt, EFFORT), deep
        except SkillError as error:
            if not any(w in str(error).lower() for w in ('memory', 'allocate', 'terminated')):
                raise
            fast = store.config('model', '') if store else ''
            if not fast:
                raise
            return ask(fast, prompt, True), fast

    def release():
        """Unload the deep model, so the fast brain is back for the next command."""
        import memory
        try:
            deep = (store.config('deep_model', DEEP_MODEL) if store else DEEP_MODEL)
            requests.post(endpoint + '/api/generate', json={'model': deep, 'keep_alive': 0},
                          timeout=30)
            if store and store.config('model', ''):
                requests.post(endpoint + '/api/generate',
                              json={'model': store.config('model'), 'keep_alive': '30m',
                                    'options': {'num_ctx': memory.FAST_CTX}},
                              timeout=120)
        except Exception:
            pass

    write.release = release
    return write


def request(need, write, pending_dir=None, active_dir=None, attempts=2):
    """Write a skill for this need and leave it in pending/. Nothing is run.

    Returns {'name', 'path', 'model', 'problems'}; problems is empty when it passed the
    check and is waiting for you.
    """
    pending_dir, active_dir = pending_dir or PENDING, active_dir or ACTIVE
    if not str(need).strip():
        raise SkillError('say what the skill should do')
    with lock:
        problems, code, model = None, '', ''
        try:
            for _ in range(attempts):
                text, model = write(writing_prompt(need, problems))
                code = extract_code(text)
                # Seen on the first real run: two attempts, 550 seconds, and no code at all.
                # Said plainly, rather than as "there is no SKILL description".
                problems = (['the model returned no code -- it used its whole allowance '
                             'thinking'] if not code.strip() else check(code))
                if not problems:
                    break
        finally:
            getattr(write, 'release', lambda: None)()
        try:
            name = literal_skill(ast.parse(code))['name']
        except Exception:
            name = ''
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', str(name)):
            name = 'skill_' + dt.datetime.now().strftime('%H%M%S')
        base, number = name, 2
        while (pending_dir / f'{name}.py').exists() or (active_dir / f'{name}.py').exists():
            name, number = f'{base}_{number}', number + 1
        if name != base:
            code = re.sub(r"(['\"]name['\"]\s*:\s*['\"])" + re.escape(base) + r"(['\"])",
                          r'\g<1>' + name + r'\g<2>', code, count=1)
        header = (f'# JARVIS skill -- written by {model} on {dt.datetime.now():%Y-%m-%d %H:%M}\n'
                  f'# Asked for: {str(need)[:200]}\n'
                  '# NOT REVIEWED. Nothing loads from skills/pending/. Read it, then approve '
                  'or reject it in the console.\n')
        if problems:
            header += '# REFUSED by the automatic check:\n' + ''.join(
                f'#   - {p}\n' for p in problems)
        pending_dir.mkdir(parents=True, exist_ok=True)
        path = pending_dir / f'{name}.py'
        path.write_text(header + code, encoding='utf-8')
        return {'name': name, 'path': str(path), 'model': model, 'problems': problems}


# ---------------------------------------------------------------- using them by voice

def match(text, skills, floor=.6, margin=.06):
    """The active skill this sentence asks for, or None. Same rules as the router."""
    from router import phrase_score
    scored = sorted(((max(phrase_score(text, p) for p in s['phrases']), s['name'], s)
                     for s in skills if s.get('phrases')), key=lambda x: -x[0])
    if not scored or scored[0][0] < floor:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < margin:
        return None
    return scored[0][2]


def extract_inputs(skill, text, ask):
    """The skill's inputs from what was said, by the fast model, then type-checked."""
    kinds = {'number': 'number', 'integer': 'integer', 'string': 'string'}
    schema = {'type': 'object', 'required': list(skill['inputs']),
              'properties': {k: {'type': kinds[v]} for k, v in skill['inputs'].items()}}
    answer = ask(f'A request was spoken to a tool that does this: {skill["description"]}\n'
                 f'Inputs: {json.dumps(skill["inputs"])}\n'
                 'Give the inputs as JSON. Sinhala number words become digits '
                 '(පහ = 5, දහය = 10, සියය = 100).\n'
                 f'Request: "{text}"', schema)
    inputs = {}
    for name, kind in skill['inputs'].items():
        value = answer.get(name) if isinstance(answer, dict) else None
        if value is None:
            raise SkillError(f'could not hear the {name}')
        try:
            inputs[name] = (int(value) if kind == 'integer' else float(value) if kind == 'number'
                            else str(value))
        except (TypeError, ValueError):
            raise SkillError(f'{name} should be a {kind}')
    return inputs


def request_in_background(need, store, on_done=lambda result: None):
    """Start writing a skill and return at once; on_done(result or error) when finished."""
    def work():
        try:
            result = request(need, ollama_writer(store))
        except Exception as error:
            result = {'error': str(error)}
        on_done(result)
    if lock.locked():
        raise SkillError('a skill is already being written')
    thread = threading.Thread(target=work, daemon=True, name='skill-writer')
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--request', metavar='NEED')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--show', metavar='NAME')
    parser.add_argument('--approve', metavar='NAME')
    parser.add_argument('--reject', metavar='NAME')
    parser.add_argument('--call', nargs=2, metavar=('NAME', 'JSON'))
    args = parser.parse_args()
    store = None
    if args.request:
        from assistant_core import ROOT as CORE_ROOT, Store
        store = Store(CORE_ROOT / 'data' / 'jarvis.db')
        started = dt.datetime.now()
        result = request(args.request, ollama_writer(store))
        print(f'written by {result["model"]} in {(dt.datetime.now() - started).seconds}s: '
              f'{result["path"]}')
        print('REFUSED: ' + '; '.join(result['problems']) if result['problems']
              else f'waiting for you: python skills.py --show {result["name"]}')
    elif args.show:
        path = PENDING / f'{args.show}.py'
        path = path if path.is_file() else find(args.show, ACTIVE)
        print(path.read_text(encoding='utf-8'))
        print('\nautomatic check:', '; '.join(check(path.read_text(encoding='utf-8'))) or 'passed')
    elif args.approve:
        print(json.dumps(approve(args.approve), ensure_ascii=False, indent=2))
    elif args.reject:
        print(reject(args.reject, 'rejected from the command line'))
    elif args.call:
        print(json.dumps(call(find(args.call[0], ACTIVE), json.loads(args.call[1])),
                         ensure_ascii=False, indent=2))
    else:
        for path in pending():
            print(f'pending  {path.stem}')
        for skill in active():
            print(f'active   {skill["name"]:24} {skill["title_en"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
