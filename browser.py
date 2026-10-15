"""Real browser control (Day 11): it uses the web instead of merely opening it.

    python browser.py "Sigiriya"                              look it up, show the excerpts
    python browser.py --price "iphone 16"                     prices in Sri Lankan shops
    python browser.py --read https://example.com "question"   read one page
    python browser.py --fill URL '{"Name": "Chamindu"}'       fill a form, visibly

Pages are read with your installed Chrome, driven by Playwright, in two separate ways:

  reading                headless and private, keeping nothing. Never logged in to anything,
                         so a page read for an answer cannot act with your accounts,
                         whatever it says.
  data/browser_profile/  visible, for forms. Your logins there survive between uses.

Where answers come from -- only places that let a program ask:
  facts    Wikipedia's own API, English or Sinhala, by the language of the question
  prices   PricePlease.lk, which compares 48 Sri Lankan shops
  a page   any address you name, read in the browser
Measured 16 Oct: Google and DuckDuckGo answered this browser with a robot check, and Bing
quietly returned nonsense (Barbara Palvin, for "who won the last cricket world cup").
That is a site saying no. JARVIS takes no for an answer and never tries to get past one.

What it will never do, in code rather than in a prompt:
  - press submit. A form is filled in a window you can see; you check it and submit it.
  - type into a password, card, CVV, OTP or PIN field.
  - touch a checkout, cart or payment page. No purchases, ever.
Everything read from the web is someone else's words: data for the answer, never an
instruction (the agent marks the turn untrusted, as it does for documents).
"""
import argparse
import base64
import json
import queue
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
READER_PROFILE = ROOT / 'data' / 'browser_reader'    # named, never written
FORM_PROFILE = ROOT / 'data' / 'browser_profile'
WIKIPEDIA = 'https://{}.wikipedia.org/w/api.php'
PRICES = 'https://priceplease.lk/search?q={}'
# Wikipedia asks programs to say who they are.
AGENT = {'User-Agent': 'JARVIS-personal-assistant/1.0 (personal use; ambawattaj@gmail.com)'}
IDLE_SECONDS = 300            # the reader closes after five quiet minutes
PAGE_SECONDS = 15
EXCERPT_CHARS = 1500
ARTICLES_READ = 2
SINHALA = re.compile('[඀-෿]')
ROBOT_CHECK = re.compile(r'unusual traffic|not a robot|verify you are human|are you a robot|'
                         r'captcha|checking your browser|press (and|&) hold', re.I)
SECRET_FIELD = re.compile(r'pass(word|wd|code)?|card|cc-|cvv|cvc|csc|otp|one.?time|\bpin\b|'
                          r'security.?code|iban|account.?number|මුරපද', re.I)
PAYMENT_PAGE = re.compile(r'checkout|/cart\b|payment|/pay\b|billing|place.?order|'
                          r'ගෙවීම|ගෙවන්න', re.I)
STOP_WORDS = {'the', 'and', 'for', 'what', 'who', 'how', 'when', 'where', 'which', 'is',
              'are', 'was', 'of', 'in', 'on', 'a', 'an', 'to', 'me', 'tell', 'about', 'with',
              'price', 'cost', 'much', 'sri', 'lanka'}


class BrowserError(Exception):
    pass


class Worker:
    """One browser, owned by one thread. Playwright's objects belong to the thread that
    made them; requests from the voice loop and the agent arrive on many threads, so
    they queue here and are done one at a time."""

    def __init__(self, profile, headless=True, idle=IDLE_SECONDS):
        self.profile, self.headless, self.idle = Path(profile), headless, idle
        self.jobs = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()

    def run(self, work, timeout=90):
        """work(context) on the browser thread; its result, or its exception, here."""
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.loop, daemon=True,
                                               name=f'browser-{self.profile.name}')
                self.thread.start()
        done, box = threading.Event(), {}
        self.jobs.put((work, done, box))
        if not done.wait(timeout):
            raise BrowserError('the browser took too long')
        if 'error' in box:
            raise box['error']
        return box['result']

    def loop(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            context = None
            try:
                while True:
                    try:
                        work, done, box = self.jobs.get(timeout=self.idle or None)
                    except queue.Empty:
                        return                  # quiet for a while: give the memory back
                    try:
                        if context is None or not alive(context):
                            context = self.launch(playwright)
                            closed = []
                            # Closing the window yourself ends it: the next form opens a new one.
                            context.on('close', lambda _: closed.append(True))
                            context.closed = closed
                        box['result'] = work(context)
                    except Exception as error:
                        box['error'] = error
                    finally:
                        done.set()
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

    def launch(self, playwright):
        if self.headless:
            # The reader keeps nothing: a fresh private session, like an incognito window.
            # Measured: a saved profile is locked by whichever program opened it first,
            # so verify.py could not read while JARVIS was running (Chrome exit code 21).
            chrome = playwright.chromium.launch(channel='chrome', headless=True)
            context = chrome.new_context(locale='en-US', viewport={'width': 1280, 'height': 900})
            # Reading needs the words, not the pictures.
            context.route('**/*', lambda route: route.abort()
                          if route.request.resource_type in ('image', 'media', 'font')
                          else route.continue_())
            return context
        self.profile.mkdir(parents=True, exist_ok=True)
        return playwright.chromium.launch_persistent_context(
            str(self.profile), channel='chrome', headless=False, locale='en-US', no_viewport=True)


def alive(context):
    try:
        if getattr(context, 'closed', None):
            return False
        context.pages
        return context.browser is None or context.browser.is_connected()
    except Exception:
        return False


READER = Worker(READER_PROFILE, headless=True)
FORMS = Worker(FORM_PROFILE, headless=False, idle=0)


# ---------------------------------------------------------------- reading

READ_SCRIPT = '''() => {
    const root = document.querySelector('main, article, [role=main]') || document.body;
    const copy = root.cloneNode(true);
    copy.querySelectorAll('script, style, noscript, nav, header, footer, aside, form, ' +
                          '[role=navigation], [aria-hidden=true]').forEach(e => e.remove());
    document.body.appendChild(copy); copy.style.display = 'block';
    const text = copy.innerText; copy.remove();
    return {title: document.title, text: text};
}'''

LINKS_SCRIPT = '''() => Array.from(document.querySelectorAll('a[href]'))
    .map(a => ({text: a.innerText.trim(), url: a.href})).filter(l => l.text)'''


def site_of(url):
    return urllib.parse.urlparse(url).netloc.removeprefix('www.')


def clean(text):
    lines = [re.sub(r'[ \t ]+', ' ', line).strip() for line in str(text).splitlines()]
    return '\n'.join(line for line in lines if line)


def words_of(text):
    return {w for w in re.findall(r'[a-z0-9඀-෿‍]+', str(text).lower())
            if len(w) > 2 and w not in STOP_WORDS}


def excerpt(text, question='', limit=EXCERPT_CHARS):
    """The lines that answer the question, in page order -- not the menu at the top."""
    lines = clean(text).splitlines()
    wanted = words_of(question)
    scored = sorted(range(len(lines)), key=lambda i: -len(wanted & words_of(lines[i])))
    chosen, size = set(), 0
    for index in scored:
        if not wanted & words_of(lines[index]) or size >= limit:
            break
        for near in (index, index + 1):           # a price is often on the line after
            if near < len(lines) and near not in chosen:
                chosen.add(near)
                size += len(lines[near]) + 1
    if not chosen:
        return '\n'.join(lines)[:limit]
    return '\n'.join(lines[i] for i in sorted(chosen))[:limit]


def open_page(context, url):
    page = context.new_page()
    page.goto(url, wait_until='domcontentloaded', timeout=PAGE_SECONDS * 1000)
    try:
        page.wait_for_load_state('networkidle', timeout=2500)
    except Exception:
        pass                                       # some pages never go quiet
    return page


def read_in(context, url, question=''):
    page = open_page(context, url)
    try:
        found = page.evaluate(READ_SCRIPT)
        text = clean(found['text'])
        if ROBOT_CHECK.search(text[:2000]) and len(text) < 3000:
            raise BrowserError(f'{site_of(url)} asked to check I am not a robot')
        shot = page.screenshot(type='jpeg', quality=70) if len(text) < 300 else None
        return {'url': page.url, 'site': site_of(page.url), 'title': found['title'][:200],
                'excerpt': excerpt(text, question), 'screenshot': shot}
    finally:
        page.close()


def read(url, question='', worker=None):
    """One page, read. {'url', 'site', 'title', 'excerpt'} -- plus 'seen', what the vision
    model made of a screenshot, when the page had almost no text to read."""
    url = str(url).strip()
    if not re.match(r'(https?|file)://', url):
        url = 'https://' + url
    result = (worker or READER).run(lambda context: read_in(context, url, question))
    shot = result.pop('screenshot')
    if shot:
        result['seen'] = look_at(shot, question)
    return result


def look_at(image, question):
    """A page that is all pictures: ask the vision model (Day 15) what it shows."""
    try:
        import vision
        answer, _ = vision.ask(base64.b64encode(image).decode(), mode='describe', lang='en',
                               what='web page', question=question or 'What does this page say?')
        return answer
    except Exception as error:
        return f'(could not read the picture: {error})'


# ---------------------------------------------------------------- facts

def wikipedia(query, articles=ARTICLES_READ, timeout=10):
    """The articles that match, each cut to the lines about the question. In Sinhala
    Wikipedia when the question is in Sinhala, else English.
    Returns {'query', 'results': [{'site', 'title', 'url', 'excerpt'}], 'seconds'}."""
    started = time.monotonic()
    language = 'si' if SINHALA.search(str(query)) else 'en'
    endpoint = WIKIPEDIA.format(language)
    try:
        found = requests.get(endpoint, params={
            'action': 'query', 'list': 'search', 'srsearch': query, 'srlimit': articles,
            'format': 'json'}, headers=AGENT, timeout=timeout).json()['query']['search']
        titles = [hit['title'] for hit in found]
        if not titles:
            return {'query': query, 'results': [], 'seconds': 0}
        pages = requests.get(endpoint, params={
            'action': 'query', 'prop': 'extracts', 'explaintext': 1, 'redirects': 1,
            'titles': '|'.join(titles), 'format': 'json'},
            headers=AGENT, timeout=timeout).json()['query']['pages']
    except (requests.RequestException, ValueError, KeyError) as error:
        raise BrowserError('Wikipedia could not be reached') from error
    by_title = {page.get('title'): page.get('extract', '') for page in pages.values()}
    results = []
    for title in titles:                           # Wikipedia's order: best match first
        text = by_title.get(title, '')
        if text:
            # The opening paragraphs say what the thing is and how it ended -- "Australia
            # won", for the 2023 World Cup, is the second; then the lines asked about.
            text = clean(text)
            lead = text[:900]
            if len(text) > 900 and '\n' in lead:
                lead = lead.rsplit('\n', 1)[0]        # whole paragraphs only
            rest = excerpt(text, query, EXCERPT_CHARS - len(lead))
            results.append({'site': f'{language}.wikipedia.org', 'title': title,
                            'url': f'https://{language}.wikipedia.org/wiki/'
                                   + urllib.parse.quote(title.replace(' ', '_')),
                            'excerpt': lead if rest.startswith(lead) else f'{lead}\n{rest}'})
    return {'query': query, 'results': results,
            'seconds': round(time.monotonic() - started, 1)}


# ---------------------------------------------------------------- prices

def prices_in(context, product):
    page = open_page(context, PRICES.format(urllib.parse.quote(product)))
    try:
        cards = []
        for link in page.evaluate(LINKS_SCRIPT):
            text = clean(link['text'])
            price = re.search(r'LKR\s*([\d,]+)', text)
            if not price:
                continue
            lines = text.splitlines()
            name = next((l for l in lines[1:] if not l.isupper()), lines[0])
            listings = re.search(r'(\d+)\s+listings?', text)
            cards.append({'name': name, 'from': f'Rs. {price.group(1)}',
                          'rupees': int(price.group(1).replace(',', '')),
                          'shops': int(listings.group(1)) if listings else None,
                          'url': link['url']})
        return cards
    finally:
        page.close()


def prices(product, worker=None):
    """What it costs in Sri Lanka: [{'name', 'from', 'shops', 'url'}], closest name first.
    PricePlease covers phones, tablets, laptops, watches and accessories."""
    cards = (worker or READER).run(lambda context: prices_in(context, product))
    wanted = re.findall(r'[a-z0-9]+', str(product).lower())

    def named(card):
        # Every word asked about, in the name. Measured: "price of rice" was answered
        # with iPhones, which the site shows when it has nothing better.
        name = re.findall(r'[a-z0-9]+', card['name'].lower())
        return all(any(part.startswith(word) for part in name) for word in wanted)

    def closeness(card):
        name = re.findall(r'[a-z0-9]+', card['name'].lower())
        exact = ' '.join(name).endswith(' '.join(wanted))
        return (not exact, len(set(name) ^ set(wanted)))
    return sorted(filter(named, cards), key=closeness)[:4]


# ---------------------------------------------------------------- forms

FIELDS_SCRIPT = '''(el) => [el.type || '', el.name || '', el.id || '',
    el.getAttribute('autocomplete') || '', el.getAttribute('aria-label') || '',
    el.placeholder || ''].join(' ')'''


def find_field(page, key):
    for locate in (lambda: page.get_by_label(key, exact=False),
                   lambda: page.get_by_placeholder(key, exact=False),
                   lambda: page.locator(f'[name="{key}" i]')):
        try:
            found = locate()
            if found.count():
                return found.first
        except Exception:
            continue
    return None


def fill_in(context, url, fields):
    page = open_page(context, url)
    if PAYMENT_PAGE.search(page.url) or PAYMENT_PAGE.search(page.title()):
        page.close()
        raise BrowserError('this is a payment page; I never fill those')
    report = {'url': page.url, 'filled': [], 'refused': [], 'not_found': [], 'submitted': False}
    for key, value in fields.items():
        field = find_field(page, key)
        if field is None:
            report['not_found'].append(key)
            continue
        if SECRET_FIELD.search(f'{key} {field.evaluate(FIELDS_SCRIPT)}'):
            report['refused'].append(key)
            continue
        kind = field.evaluate('(el) => el.tagName.toLowerCase() + ":" + (el.type || "")')
        if kind.startswith('select'):
            field.select_option(label=str(value))
        elif kind.endswith((':checkbox', ':radio')):
            if str(value).lower() in ('yes', 'true', 'on', 'ඔව්', '1'):
                field.check()
        else:
            field.fill(str(value))
        report['filled'].append(key)
    # Show where submit is, and leave it there. Pressing it is yours.
    page.evaluate('''() => { const b = document.querySelector(
        'button[type=submit], input[type=submit], form button');
        if (b) { b.style.outline = '4px solid #e5484d'; b.scrollIntoView({block: 'center'}); } }''')
    page.bring_to_front()
    return report


def fill(url, fields, worker=None):
    """Fill a form in a window you can see, and stop before submitting."""
    if isinstance(fields, str):
        fields = json.loads(fields or '{}')
    if not isinstance(fields, dict) or not fields:
        raise BrowserError('no fields to fill')
    url = str(url).strip()
    if not re.match(r'(https?|file)://', url):
        url = 'https://' + url
    report = (worker or FORMS).run(lambda context: fill_in(context, url, dict(fields)))
    report['note'] = ('Filled, not submitted. Check it in the browser window and press '
                      'submit yourself.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('query', nargs='*')
    parser.add_argument('--read')
    parser.add_argument('--price', action='store_true')
    parser.add_argument('--fill', nargs=2, metavar=('URL', 'FIELDS_JSON'))
    args = parser.parse_args()
    question = ' '.join(args.query)
    if args.read:
        print(json.dumps(read(args.read, question), ensure_ascii=False, indent=1))
    elif args.fill:
        print(json.dumps(fill(*args.fill), ensure_ascii=False, indent=1))
        input('Press Enter to close the browser... ')
    elif args.price:
        for card in prices(question):
            print(f"{card['from']:>14}  {card['name']}  ({card['shops']} shops)")
    elif question:
        found = wikipedia(question)
        print(f"{found['seconds']}s")
        for result in found['results']:
            print(f"\n== {result['site']}: {result['title']}\n{result['excerpt'][:800]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
