"""Multi-step tasks: one sentence, a plan you can see, steps you can stop (Day 20).

The model writes the plan; code carries it out. Left to drive a long chain of tool calls
itself, a nine-billion-parameter model loses its place, repeats steps and -- observed
here before -- reports work it never did. So the model only chooses steps from a short
list and fills in their inputs, the plan is checked before anything runs, and every
step after that is ordinary code with a journal entry to show for it.

    python tasks.py "find every PDF invoice from last month, total them, save a summary, email it to me"
    python tasks.py --plan-only "..."      show the plan and stop
    python tasks.py --list                 recent tasks and how they ended

Steps refer to earlier results as "$1", "$2": "$1" is whatever step one produced.
"""
import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
TASK_DIR = ROOT / 'data' / 'tasks'

# Limits. An agent with none is how you find out at 2 a.m. that it has looped for hours.
STEP_LIMIT = 12
WALL_CLOCK_SECONDS = 300
# Plans longer than this are read out and wait for a yes. Shorter ones just run, unless
# a step reaches outside the computer.
CONFIRM_OVER = 3
RETRIES = 1
# Said aloud when a step needs the internet. Measured on Day 28: the raw error went out
# as "HTTPSConnectionPool(host='api.open-meteo.com', port=443): Max retries...".
NO_INTERNET = 'there is no internet connection'
FILE_VISIT_LIMIT = 20000
DOCUMENT_LIMIT = 200

PERIODS = ('all', 'today', 'yesterday', 'this_week', 'last_week', 'this_month',
           'last_month', 'this_year', 'last_year')

# Words that mark a document as an invoice when the user asks for invoices. Searching
# for the literal word misses "Tax Invoice" written as "TAX INV." and every receipt.
SYNONYMS = {
    'invoice': ['invoice', 'tax invoice', 'bill', 'receipt', 'ඉන්වොයිස්', 'බිල්පත', 'බිල',
                'රිසිට්'],
    'receipt': ['receipt', 'invoice', 'රිසිට්', 'ලදුපත'],
    'bill': ['bill', 'invoice', 'බිල', 'බිල්පත'],
}


class StepError(Exception):
    """A step failed in a way worth telling the user about."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------- dates

def period_range(period, today=None):
    """[start, end) dates for a named period. 'all' is (None, None)."""
    today = today or dt.date.today()
    if period == 'today':
        return today, today + dt.timedelta(days=1)
    if period == 'yesterday':
        return today - dt.timedelta(days=1), today
    if period == 'this_week':
        start = today - dt.timedelta(days=today.weekday())
        return start, start + dt.timedelta(days=7)
    if period == 'last_week':
        start = today - dt.timedelta(days=today.weekday() + 7)
        return start, start + dt.timedelta(days=7)
    if period == 'this_month':
        start = today.replace(day=1)
        return start, (start + dt.timedelta(days=32)).replace(day=1)
    if period == 'last_month':
        end = today.replace(day=1)
        return (end - dt.timedelta(days=1)).replace(day=1), end
    if period == 'this_year':
        return dt.date(today.year, 1, 1), dt.date(today.year + 1, 1, 1)
    if period == 'last_year':
        return dt.date(today.year - 1, 1, 1), dt.date(today.year, 1, 1)
    return None, None


MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_abbr) if name})
DATE_PATTERNS = [
    (r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', lambda m: (m[1], m[2], m[3])),
    (r'\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b', lambda m: (m[3], m[2], m[1])),
    (r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b',
     lambda m: (m[3], MONTHS.get(m[2].lower()), m[1])),
    (r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b',
     lambda m: (m[3], MONTHS.get(m[1].lower()), m[2])),
]


def document_date(text):
    """The date a document is about, from its text: the one labelled as a date first.

    A file's modified time is when it was downloaded, which for a September invoice may
    well be October. The date printed on it is the one that matters.
    """
    text = str(text)
    labelled = re.search(r'(?:invoice date|date of issue|issue date|bill date|date)\s*[:\-]?\s*'
                         r'(.{6,30})', text, re.I)
    for chunk in ([labelled.group(1)] if labelled else []) + [text]:
        for pattern, parts in DATE_PATTERNS:
            for match in re.finditer(pattern, chunk):
                try:
                    year, month, day = parts(match)
                    found = dt.date(int(year), int(month), int(day))
                except (TypeError, ValueError):
                    continue
                if 2000 <= found.year <= 2100:
                    return found
    return None


# ---------------------------------------------------------------- money

AMOUNT = r'([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)'
CURRENCY = r'(Rs\.?|LKR|රු\.?|USD|US\$|\$|EUR|€|GBP|£)?'
# Most specific label first. "Total" alone comes last, and never "Subtotal".
TOTAL_LABELS = [r'grand\s+total', r'total\s+amount\s+due', r'amount\s+due', r'balance\s+due',
                r'total\s+due', r'total\s+payable', r'net\s+total', r'total\s+amount',
                r'මුළු\s*එකතුව', r'(?<!sub)(?<!sub\s)total', r'එකතුව']
CURRENCIES = {'rs': 'LKR', 'rs.': 'LKR', 'lkr': 'LKR', 'රු': 'LKR', 'රු.': 'LKR',
              'usd': 'USD', 'us$': 'USD', '$': 'USD', 'eur': 'EUR', '€': 'EUR',
              'gbp': 'GBP', '£': 'GBP'}


def find_total(text, default_currency='LKR'):
    """(amount, currency) from an invoice's text, or None.

    Read by pattern, not by the model, so the figure cannot be invented. The label
    order matters: "Grand Total" beats "Total", and of several "Total" lines the last
    is the bottom line.
    """
    text = str(text)
    currency_hint = re.search(r'\b(LKR|USD|EUR|GBP)\b|Rs\.|රු\.|US\$', text)
    for label in TOTAL_LABELS:
        pattern = (label + r'[^\n0-9]{0,25}?' + CURRENCY + r'\s*' + AMOUNT
                   + r'\s*' + CURRENCY)
        matches = list(re.finditer(pattern, text, re.I))
        if not matches:
            continue
        match = matches[-1]
        amount = float(match.group(2).replace(',', ''))
        if amount <= 0:
            continue
        symbol = (match.group(1) or match.group(3) or
                  (currency_hint.group(0) if currency_hint else '') or '').lower()
        return amount, CURRENCIES.get(symbol, default_currency)
    return None


def amount_appears(amount, text):
    """Does this figure actually occur in the document? The check on the model's answer."""
    plain = re.sub(r'(?<=\d),(?=\d{3})', '', str(text))     # 45,300.00 -> 45300.00
    forms = {f'{amount:.2f}'}
    if amount == int(amount):
        forms.add(str(int(amount)))
    return any(re.search(rf'(?<![0-9.]){re.escape(form)}(?![0-9])', plain) for form in forms)


# ---------------------------------------------------------------- the steps

def mentions(text, words):
    """Does the text contain one of these words, as a word?

    Found on the first live run: "bill" matched inside "billing", and two purchase
    indents from Downloads were totalled as invoices. Latin words must stand alone;
    Sinhala words are matched as written, because they take suffixes ("බිලේ", "බිල්පත").
    """
    lowered = str(text).lower()
    for word in words:
        word = str(word).lower().strip()
        if not word:
            continue
        if re.search(r'[a-z]', word):
            if re.search(r'(?<![a-z])' + re.escape(word) + r's?(?![a-z])', lowered):
                return True
        elif word in lowered:
            return True
    return False


def _text_of(path, pages=10):
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        return '\n'.join(page.extract_text() or '' for page in reader.pages[:pages])
    except Exception as error:
        raise StepError(f'could not read {Path(path).name}: {error}')


class Steps:
    """What a plan may do. Each step takes plain inputs and returns plain data."""

    # name: (inputs, required inputs, outward). Outward steps reach beyond this computer
    # and always need a yes, however short the plan.
    SPEC = {
        'find_documents': ({'type': 'pdf, txt, docx or any', 'period': ' | '.join(PERIODS),
                            'contains': 'a word the document must contain, or ""'},
                           [], False),
        'read_totals': ({'files': 'a reference to a find_documents step, like "$1"'},
                        ['files'], False),
        'add_up': ({'items': 'a reference to a read_totals step'}, ['items'], False),
        'write_summary': ({'title': 'a short title',
                           'parts': 'references to earlier steps to include, like ["$2", "$3"]'},
                          ['title', 'parts'], False),
        'email_me': ({'subject': 'the subject line',
                      'body': 'a reference to a write_summary step, or text'},
                     ['subject', 'body'], True),
        'save_note': ({'title': 'the note', 'content': 'more detail, a reference, or ""'},
                      ['title'], False),
        'set_reminder': ({'title': 'what to remind', 'due': 'ISO date and time, in the future'},
                         ['title', 'due'], False),
        'weather': ({'place': 'a town in Sri Lanka, or ""'}, [], False),
        'dollar_rate': ({}, [], False),
        'news': ({}, [], False),
    }

    def __init__(self, store, tools, folders=None, ask_model=None):
        self.store, self.tools = store, tools
        self.fixed_folders = folders
        self.ask_model = ask_model

    @property
    def folders(self):
        """Read at every search, not once at start-up.

        Found on the first live run: the voice loop builds this object when it starts,
        so a folder setting changed afterwards was ignored and the search went through
        Documents and Downloads instead of the folder it had been given.
        """
        if self.fixed_folders:
            return [Path(f) for f in self.fixed_folders]
        configured = self.store.config('search_folders', None)
        if configured:
            return [Path(f) for f in configured]
        home = Path.home()
        return [self.tools.workspace, home / 'Documents', home / 'Downloads']

    # Each returns a dict. Raising StepError reports a failure the user can act on.

    def find_documents(self, type='pdf', period='all', contains=''):
        kind = str(type or 'any').lower().lstrip('.')
        if period not in PERIODS:
            raise StepError(f'unknown period {period!r}')
        start, end = period_range(period)
        words = [w.lower() for w in SYNONYMS.get(str(contains).lower().strip(), [contains])
                 if str(w).strip()]
        found, visited, opened = [], 0, 0
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for parent, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                           ('node_modules', '__pycache__', 'AppData', 'blobs', '.venv')]
                for name in files:
                    visited += 1
                    if visited > FILE_VISIT_LIMIT:
                        break
                    path = Path(parent) / name
                    if kind != 'any' and path.suffix.lower() != '.' + kind:
                        continue
                    text = ''
                    if words or start:
                        if opened >= DOCUMENT_LIMIT:
                            continue
                        opened += 1
                        try:
                            text = (_text_of(path, pages=3) if path.suffix.lower() == '.pdf'
                                    else path.read_text(encoding='utf-8', errors='replace')[:20000])
                        except (StepError, OSError):
                            text = ''
                    if words and not mentions(name + '\n' + text, words):
                        continue
                    when = document_date(text) or dt.date.fromtimestamp(path.stat().st_mtime)
                    if start and not start <= when < end:
                        continue
                    found.append({'path': str(path), 'name': name, 'date': when.isoformat()})
        found.sort(key=lambda item: item['date'])
        return {'files': found, 'searched': [str(f) for f in self.folders],
                'period': period, 'from': start.isoformat() if start else None,
                'to': (end - dt.timedelta(days=1)).isoformat() if end else None}

    def read_totals(self, files):
        files = files.get('files', []) if isinstance(files, dict) else files or []
        currency = self.store.config('currency', 'LKR')
        items, unreadable = [], []
        for entry in files:
            path = entry['path'] if isinstance(entry, dict) else str(entry)
            try:
                text = _text_of(path)
            except StepError as error:
                unreadable.append({'name': Path(path).name, 'reason': str(error)})
                continue
            total, source = find_total(text, currency), 'text'
            if not total and self.ask_model:
                total, source = self.total_by_model(text, currency), 'model'
            if not total:
                unreadable.append({'name': Path(path).name, 'reason': 'no total found'})
                continue
            items.append({'name': Path(path).name, 'path': path, 'amount': total[0],
                          'currency': total[1], 'read_by': source,
                          'date': (entry.get('date') if isinstance(entry, dict) else None)})
        return {'items': items, 'unreadable': unreadable}

    def total_by_model(self, text, currency):
        """Ask the model, then believe it only if the figure is printed in the document."""
        try:
            answer = self.ask_model(
                'From this invoice, give the final amount payable as JSON '
                '{"amount": number or null, "currency": "LKR", "USD" or other}. '
                'Only a figure printed in the document.\n\n' + text[:6000],
                {'type': 'object', 'properties': {'amount': {'type': ['number', 'null']},
                                                  'currency': {'type': 'string'}},
                 'required': ['amount', 'currency']})
            amount = float(answer.get('amount') or 0)
        except Exception:
            return None
        if amount > 0 and amount_appears(amount, text):
            return amount, (answer.get('currency') or currency).upper()[:3]
        return None

    def add_up(self, items):
        items = items.get('items', []) if isinstance(items, dict) else items or []
        totals = {}
        for item in items:
            totals[item['currency']] = round(totals.get(item['currency'], 0) + item['amount'], 2)
        return {'totals': totals, 'count': len(items)}

    def write_summary(self, title, parts):
        """A Markdown summary built from the results themselves, not retold by the model."""
        parts = parts if isinstance(parts, list) else [parts]
        lines = [f'# {title}', '', f'Made by JARVIS on {dt.datetime.now():%Y-%m-%d %H:%M}.', '']
        for part in parts:
            lines += render(part)
        text = '\n'.join(lines).rstrip() + '\n'
        slug = re.sub(r'[^A-Za-z0-9]+', '-', str(title)).strip('-').lower()[:50] or 'summary'
        folder = self.tools.workspace / 'summaries'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{slug}-{dt.datetime.now():%Y%m%d-%H%M%S}.md'
        path.write_text(text, encoding='utf-8')
        return {'path': str(path), 'name': path.name, 'text': text}

    def email_me(self, subject, body):
        """Open a draft in the mail app. Sending stays with you, as with WhatsApp."""
        if isinstance(body, dict):
            body = body.get('text') or json.dumps(body, ensure_ascii=False, indent=2)
        to = self.store.config('my_email', '')
        body = str(body)
        # Mail links are cut off by some mail apps beyond a few thousand characters.
        if len(body) > 1800:
            body = body[:1800] + '\n\n(cut short; the full summary is saved on the computer)'
        link = f'mailto:{quote(to)}?subject={quote(str(subject))}&body={quote(body)}'
        try:
            os.startfile(link)
        except OSError:
            if not webbrowser.open(link):
                raise StepError('no mail app opened the draft')
        return {'drafted': True, 'to': to, 'subject': subject, 'sent': False}

    def save_note(self, title, content=''):
        if isinstance(content, dict):
            content = content.get('text') or json.dumps(content, ensure_ascii=False)
        return self.tools.execute('save_note', {'title': str(title), 'content': str(content)})

    def set_reminder(self, title, due):
        try:
            return self.tools.execute('set_reminder', {'title': str(title), 'due': str(due)})
        except ValueError as error:
            raise StepError(str(error))

    def weather(self, place=''):
        import knowledge
        try:
            return knowledge.weather(knowledge.find_place(place) if place else None)
        except knowledge.Offline as error:
            raise StepError(NO_INTERNET, retryable=True) from error

    def dollar_rate(self):
        import knowledge
        try:
            return knowledge.dollar_rate()
        except knowledge.Offline as error:
            raise StepError(NO_INTERNET, retryable=True) from error

    def news(self):
        import knowledge
        try:
            return {'headlines': knowledge.news()}
        except knowledge.Offline as error:
            raise StepError(NO_INTERNET, retryable=True) from error

    def run(self, name, args):
        if name not in self.SPEC:
            raise StepError(f'no such step {name!r}')
        return getattr(self, name)(**args)


def render(result):
    """Markdown lines for one step's result, for the written summary."""
    if not isinstance(result, dict):
        return [str(result), '']
    if 'items' in result:
        lines = ['| Document | Date | Amount |', '|---|---|---|']
        lines += [f"| {i['name']} | {i.get('date') or ''} | {i['currency']} {i['amount']:,.2f} |"
                  for i in result['items']]
        for bad in result.get('unreadable', []):
            lines.append(f"| {bad['name']} | | not read: {bad['reason']} |")
        return lines + ['']
    if 'totals' in result:
        if not result['totals']:
            return ['Nothing to add up.', '']
        return [f"**Total of {result['count']}: " +
                ', '.join(f'{cur} {amount:,.2f}' for cur, amount in result['totals'].items())
                + '**', '']
    if 'files' in result:
        lines = [f"Searched {', '.join(result['searched'])}"
                 + (f" for {result['from']} to {result['to']}" if result.get('from') else '')
                 + f": {len(result['files'])} found.", '']
        return lines
    if 'text' in result:
        return [result['text'], '']
    return [json.dumps(result, ensure_ascii=False), '']


# ---------------------------------------------------------------- planning

class PlanError(Exception):
    pass


def resolve(value, results):
    """Replace "$2" with step two's result, inside lists too."""
    if isinstance(value, str):
        match = re.fullmatch(r'\$(\d+)', value.strip())
        if match:
            index = int(match.group(1)) - 1
            if index not in results:
                raise StepError(f'step {index + 1} has no result to use')
            return results[index]
        return value
    if isinstance(value, list):
        return [resolve(item, results) for item in value]
    return value


def references(value):
    if isinstance(value, str):
        match = re.fullmatch(r'\$(\d+)', value.strip())
        return [int(match.group(1))] if match else []
    if isinstance(value, list):
        return [ref for item in value for ref in references(item)]
    return []


def validate(steps):
    """A plan that can actually run, or PlanError saying why not.

    Everything the model wrote is checked here: the step names, the inputs, that each
    "$n" points backwards at a real step, and the length.
    """
    if not isinstance(steps, list) or not steps:
        raise PlanError('the plan has no steps')
    if len(steps) > STEP_LIMIT:
        raise PlanError(f'{len(steps)} steps is over the limit of {STEP_LIMIT}')
    clean = []
    for number, step in enumerate(steps, 1):
        name = (step or {}).get('step') or (step or {}).get('tool')
        args = (step or {}).get('args') or {}
        if name not in Steps.SPEC:
            raise PlanError(f'step {number} uses {name!r}, which JARVIS cannot do')
        allowed, required, _ = Steps.SPEC[name]
        if not isinstance(args, dict):
            raise PlanError(f'step {number} has no inputs')
        args = {key: value for key, value in args.items() if key in allowed}
        missing = [key for key in required if key not in args or args[key] in ('', None, [])]
        if missing:
            raise PlanError(f'step {number} ({name}) is missing {", ".join(missing)}')
        for ref in references(list(args.values())):
            if not 1 <= ref < number:
                raise PlanError(f'step {number} refers to step {ref}, which has not run yet')
        if name == 'find_documents' and args.get('period', 'all') not in PERIODS:
            args['period'] = 'all'
        clean.append({'step': name, 'args': args})
    return clean


PLAN_SCHEMA = {
    'type': 'object',
    'properties': {'steps': {'type': 'array', 'maxItems': STEP_LIMIT, 'items': {
        'type': 'object',
        'properties': {'step': {'type': 'string', 'enum': list(Steps.SPEC)},
                       'args': {'type': 'object'}},
        'required': ['step', 'args']}}},
    'required': ['steps'],
}


def planning_prompt(request):
    lines = [f'- {name}({", ".join(f"{k}: {v}" for k, v in spec[0].items())})'
             for name, spec in Steps.SPEC.items()]
    return (
        'Turn the request into the shortest list of steps that does it, using ONLY these '
        'steps:\n' + '\n'.join(lines) + '\n\n'
        'Refer to an earlier step\'s result as "$1", "$2" and so on, by step number. '
        'Do not add steps that were not asked for. If the request asks for something none '
        'of these steps can do, leave that part out.\n'
        f'Today is {dt.date.today():%A %Y-%m-%d}; the time is {dt.datetime.now():%H:%M}.\n\n'
        'Example. Request: "find this week\'s PDF receipts, add them up and save a note"\n'
        '{"steps": [{"step": "find_documents", "args": {"type": "pdf", "period": "this_week", '
        '"contains": "receipt"}}, {"step": "read_totals", "args": {"files": "$1"}}, '
        '{"step": "add_up", "args": {"items": "$2"}}, {"step": "save_note", "args": '
        '{"title": "This week\'s receipts", "content": "$3"}}]}\n\n'
        f'Request: "{request}"')


def plan(request, ask_model):
    """The model proposes; validate() decides; the request itself has the last word."""
    try:
        proposal = ask_model(planning_prompt(request), PLAN_SCHEMA)
    except Exception as error:
        raise PlanError(f'could not make a plan: {error}')
    return hold_to_request(validate(proposal.get('steps') if isinstance(proposal, dict)
                                    else None), request)


# What the user said, in their words, for the parts of a search that must not be lost.
REQUEST_KINDS = {'invoice': ['invoice', 'ඉන්වොයිස්'], 'receipt': ['receipt', 'රිසිට්', 'ලදුපත'],
                 'bill': ['bill', 'බිල්']}
REQUEST_PERIODS = [
    ('last_month', ['last month', 'previous month', 'පසුගිය මාසයේ', 'පහුගිය මාසේ',
                    'පහුගිය මාසයේ', 'ගිය මාසේ', 'ගිය මාසයේ']),
    ('this_month', ['this month', 'මේ මාසයේ', 'මේ මාසේ']),
    ('last_week', ['last week', 'පසුගිය සතියේ', 'ගිය සතියේ', 'පහුගිය සතියේ']),
    ('this_week', ['this week', 'මේ සතියේ']),
    ('yesterday', ['yesterday', 'ඊයේ']),
    ('today', ['today', "today's", 'අද']),
    ('last_year', ['last year', 'පසුගිය අවුරුද්දේ', 'ගිය අවුරුද්දේ']),
    ('this_year', ['this year', 'මේ අවුරුද්දේ']),
]


def hold_to_request(steps, request):
    """Put back what the model dropped from the user's own words.

    Measured: asked for "every PDF invoice from last month", qwen3.5:9b planned a search
    for every PDF from last month -- twice, in English and in Sinhala -- and would have
    totalled whatever PDFs it found. The words "invoice" and "last month" are in the
    request; they are not the model's to leave out.
    """
    lowered = str(request).lower()
    kind = next((name for name, words in REQUEST_KINDS.items()
                 if any(word in lowered for word in words)), None)
    period = next((name for name, words in REQUEST_PERIODS
                   if any(word in lowered for word in words)), None)
    for number, step in enumerate(steps, 1):
        if step['step'] == 'write_summary':
            # A summary of everything found so far. Measured: the model listed only the
            # total, and the file said "Total of 5" without saying of what.
            step['args']['parts'] = [f'${n}' for n in range(1, number)
                                     if not Steps.SPEC[steps[n - 1]['step']][2]]
        if step['step'] != 'find_documents':
            continue
        if kind and not str(step['args'].get('contains') or '').strip():
            step['args']['contains'] = kind
        if period:
            step['args']['period'] = period
        if 'pdf' in lowered and step['args'].get('type') in (None, '', 'any'):
            step['args']['type'] = 'pdf'
    return steps


def ollama_asker(store, endpoint='http://127.0.0.1:11434', timeout=120):
    """ask(prompt, schema) -> parsed JSON, from the configured fast model."""
    import requests

    def ask(prompt, schema):
        model = store.config('model', '')
        if not model:
            raise PlanError('no model is configured')
        response = requests.post(endpoint + '/api/chat', json={
            'model': model, 'stream': False, 'think': False, 'format': schema,
            'messages': [{'role': 'user', 'content': prompt}],
            'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1200}},
            timeout=(5, timeout))
        if not response.ok:
            raise PlanError(response.json().get('error', f'Ollama HTTP {response.status_code}'))
        return json.loads(response.json()['message']['content'])
    return ask


def needs_yes(steps):
    return len(steps) > CONFIRM_OVER or any(Steps.SPEC[s['step']][2] for s in steps)


# Sentences that ask for several things. Only these go to the planner; everything else
# keeps the faster single-command path.
SEQUENCE_WORDS = re.compile(
    r"\b(then|after that|afterwards|and finally)\b|ඊට පස්සේ|ඊටපස්සේ|ඊළඟට|පස්සේ|ඊට පසු", re.I)
ACTION_WORDS = re.compile(
    r'\b(find|search|total|add|sum|save|write|email|mail|send|remind|read|check|list|'
    r'summari[sz]e|open|calculate)\b', re.I)


def looks_multi_step(text):
    """Does this sentence ask for several things in order?

    Sinhala chains actions with the "-ලා" form: "හොයලා, එකතු කරලා, සේව් කරලා, යවන්න".
    Two of those is a sequence. "කියලා" is excluded: it quotes, it does not chain.
    """
    text = str(text)
    if SEQUENCE_WORDS.search(text):
        return True
    # Not \b: the vowel sign "ා" is not a word character, so \b never matches after it.
    chained = [w for w in re.findall(r'\S+ලා(?=[\s,.!?]|$)', text) if not w.endswith('කියලා')]
    if len(chained) >= 2:
        return True
    return len(set(word.lower() for word in ACTION_WORDS.findall(text))) >= 3


# ---------------------------------------------------------------- running

class Task:
    """One request, its plan and its journal. Saved after every change, so the console
    and a later look both see exactly what happened."""

    def __init__(self, request, steps, lang='si', task_id=None):
        self.id = task_id or dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.request, self.lang = request, lang
        self.state = 'planned'
        self.steps = [{'number': n, 'step': s['step'], 'args': s['args'], 'state': 'waiting',
                       'attempts': 0, 'error': '', 'result': None, 'started': None,
                       'ended': None} for n, s in enumerate(steps, 1)]
        self.question = ''
        self.created = dt.datetime.now().isoformat(timespec='seconds')
        self.finished = None
        self.lock = threading.RLock()

    def as_dict(self):
        with self.lock:
            return {'id': self.id, 'request': self.request, 'lang': self.lang,
                    'state': self.state, 'question': self.question, 'created': self.created,
                    'finished': self.finished,
                    'steps': [dict(s, result=brief(s['result'])) for s in self.steps]}

    def save(self, folder=TASK_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{self.id}.json'
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
                        encoding='utf-8')
        return path


def brief(result):
    """A journal-sized copy of a result: enough to see what happened, not the whole PDF."""
    if result is None:
        return None
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= 4000:
        return result
    return {'summary': text[:4000] + '...'}


class Runner:
    """Carries out a task's steps in the background, one at a time.

    on_change(task) is called after every step and state change. A failure is retried
    once if it looks temporary, then the task waits for "retry", "skip" or "stop" --
    it is never quietly abandoned half done.
    """

    def __init__(self, steps, on_change=lambda task: None, wall_clock=WALL_CLOCK_SECONDS,
                 folder=TASK_DIR):
        self.steps, self.on_change = steps, on_change
        self.wall_clock, self.folder = wall_clock, folder
        self.task = None
        self.cancel = threading.Event()
        self.answer = None
        self.answered = threading.Event()
        self.thread = None

    def busy(self):
        return bool(self.task and self.task.state in ('running', 'needs_you'))

    def changed(self):
        self.task.save(self.folder)
        try:
            self.on_change(self.task)
        except Exception as error:
            print(f'  task update failed: {error}', file=sys.stderr)

    def start(self, task):
        if self.busy():
            raise ValueError('Another task is already running.')
        self.task, self.answer = task, None
        self.cancel.clear()
        self.answered.clear()
        task.state = 'running'
        self.changed()
        self.thread = threading.Thread(target=self.run, daemon=True, name=f'task-{task.id}')
        self.thread.start()
        return self.thread

    def stop(self):
        self.cancel.set()
        self.answered.set()

    def reply(self, choice):
        """The user's answer to 'retry, skip or stop?'."""
        self.answer = choice
        self.answered.set()

    def stop_requested(self):
        """Stopped here, or from the console, which cannot reach this process directly."""
        flag = self.folder / f'{self.task.id}.stop'
        if flag.exists():
            self.cancel.set()
        return self.cancel.is_set()

    def run(self):
        task = self.task
        deadline = time.monotonic() + self.wall_clock
        results = {}
        try:
            for step in task.steps:
                if self.stop_requested():
                    raise InterruptedError()
                if time.monotonic() > deadline:
                    raise TimeoutError()
                # A step that needs a skipped step's result cannot run either.
                needed = references(list(step['args'].values()))
                if any(task.steps[ref - 1]['state'] == 'skipped' for ref in needed):
                    step.update(state='skipped', error='needs a step that was skipped')
                    self.changed()
                    continue
                self.run_step(step, results, deadline)
            task.state = 'done'
        except InterruptedError:
            task.state = 'stopped'
        except TimeoutError:
            task.state = 'timed_out'
        except Exception as error:
            task.state = 'failed'
            task.question = str(error)
        finally:
            for step in task.steps:
                if step['state'] in ('waiting', 'running'):
                    step['state'] = 'not run'
            task.finished = dt.datetime.now().isoformat(timespec='seconds')
            self.changed()

    def run_step(self, step, results, deadline):
        task = self.task
        while True:
            step.update(state='running', started=dt.datetime.now().isoformat(timespec='seconds'))
            step['attempts'] += 1
            self.changed()
            try:
                args = {key: resolve(value, results) for key, value in step['args'].items()}
                result = self.steps.run(step['step'], args)
            except Exception as error:
                retryable = getattr(error, 'retryable', False)
                step.update(state='failed', error=str(error)[:300])
                self.changed()
                if retryable and step['attempts'] <= RETRIES:
                    time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
                    continue
                choice = self.ask(step, deadline)
                if choice == 'retry':
                    continue
                if choice == 'skip':
                    step['state'] = 'skipped'
                    task.state = 'running'
                    task.question = ''
                    self.changed()
                    return
                raise InterruptedError()
            results[step['number'] - 1] = result
            step.update(state='done', result=result, error='',
                        ended=dt.datetime.now().isoformat(timespec='seconds'))
            self.changed()
            return

    def ask(self, step, deadline):
        """Wait for retry, skip or stop. Silence until the wall clock means stop."""
        task = self.task
        task.state = 'needs_you'
        task.question = step['error']
        self.answer = None
        self.answered.clear()
        self.changed()
        while not self.answered.wait(1):
            if self.stop_requested() or time.monotonic() > deadline:
                break
        if self.cancel.is_set():
            return 'stop'
        task.state = 'running'
        task.question = ''
        return self.answer or 'stop'


def recent(limit=10, folder=TASK_DIR):
    if not folder.is_dir():
        return []
    files = sorted(folder.glob('*.json'), reverse=True)[:limit]
    tasks = []
    for path in files:
        try:
            tasks.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return tasks


def request_stop(task_id, folder=TASK_DIR):
    """Ask a running task to stop, from another process."""
    if not re.fullmatch(r'[0-9A-Za-z-]{6,40}', str(task_id)):
        raise ValueError('Unknown task')
    (folder / f'{task_id}.stop').write_text('stop', encoding='utf-8')
    return {'stopping': task_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('request', nargs='*')
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--yes', action='store_true', help='run without asking')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--lang', default='en', choices=['si', 'en'])
    args = parser.parse_args()
    if args.list:
        for task in recent():
            done = sum(1 for s in task['steps'] if s['state'] == 'done')
            print(f"{task['id']}  {task['state']:10} {done}/{len(task['steps'])}  {task['request'][:70]}")
        return 0
    request = ' '.join(args.request)
    if not request:
        parser.print_help()
        return 1

    import responses
    from assistant_core import ROOT as CORE_ROOT, Store, Tools
    store = Store(CORE_ROOT / 'data' / 'jarvis.db')
    tools = Tools(store, store.config('workspace', str(CORE_ROOT / 'workspace')))
    ask = ollama_asker(store)
    started = time.monotonic()
    steps = plan(request, ask)
    task = Task(request, steps, args.lang)
    print(f'plan in {time.monotonic() - started:.1f}s:\n')
    print(responses.say_plan(task.steps, args.lang), '\n')
    if args.plan_only:
        return 0
    if needs_yes(steps) and not args.yes:
        if input('Go ahead? [y/N] ').strip().lower() not in ('y', 'yes'):
            print('Not run.')
            return 0
    finished = threading.Event()

    def show(current):
        for step in current.steps:
            if step['state'] in ('running', 'failed'):
                print(f"  [{step['state']:7}] {step['number']}. {step['step']} {step['error']}")
        if current.state == 'needs_you':
            choice = input(f'  {current.question} -- retry, skip or stop? ').strip().lower()
            runner.reply(choice if choice in ('retry', 'skip') else 'stop')
        if current.state not in ('running', 'needs_you'):
            finished.set()

    runner = Runner(Steps(store, tools, ask_model=ask), on_change=show)
    runner.start(task)
    finished.wait()
    print('\n' + responses.say_task_report(task.as_dict(), args.lang))
    print(f'\njournal: {TASK_DIR / (task.id + ".json")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
