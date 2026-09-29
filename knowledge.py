"""Facts from the live web: weather, the dollar rate, the news.

The existing web_search tool only opened a browser tab and said so in its own return
value. This answers instead.

Everything here has a hard timeout and degrades to a plain "I cannot reach the internet"
rather than hanging, because the connection here drops. Every answer carries its source,
so a fact can be told apart from an invention.

    python knowledge.py --weather
    python knowledge.py --dollar
    python knowledge.py --news
"""
import argparse
import json
import re
import sys
import time
import urllib.parse

TIMEOUT = 10
MAX_PAGE = 200_000
CACHE_SECONDS = {'weather': 600, 'rate': 1800, 'news': 900}
_cache = {}

# Towns people actually name, with their coordinates. Open-Meteo needs numbers, and a
# geocoding round trip for "Colombo" every time is a second wasted.
PLACES = {
    'colombo': (6.93, 79.86), 'කොළඹ': (6.93, 79.86),
    'kandy': (7.29, 80.64), 'මහනුවර': (7.29, 80.64), 'නුවර': (7.29, 80.64),
    'galle': (6.05, 80.22), 'ගාල්ල': (6.05, 80.22),
    'jaffna': (9.66, 80.02), 'යාපනය': (9.66, 80.02),
    'negombo': (7.21, 79.84), 'මීගමුව': (7.21, 79.84),
    'kurunegala': (7.49, 80.36), 'කුරුණෑගල': (7.49, 80.36),
    'matara': (5.95, 80.54), 'මාතර': (5.95, 80.54),
    'anuradhapura': (8.31, 80.40), 'අනුරාධපුර': (8.31, 80.40),
    'nuwara eliya': (6.97, 80.77), 'නුවරඑළිය': (6.97, 80.77),
    'batticaloa': (7.72, 81.70), 'trincomalee': (8.59, 81.21),
}
DEFAULT_PLACE = 'colombo'

# WMO weather codes, in both languages, kept short enough to say.
CONDITIONS = {
    0: ('clear', 'අව්ව'), 1: ('mostly clear', 'අව්ව'), 2: ('partly cloudy', 'වළාකුළු ටිකක්'),
    3: ('cloudy', 'වළාකුළු'), 45: ('foggy', 'මීදුම'), 48: ('foggy', 'මීදුම'),
    51: ('drizzling', 'පොද වැස්ස'), 53: ('drizzling', 'පොද වැස්ස'),
    55: ('drizzling', 'පොද වැස්ස'), 61: ('light rain', 'සැහැල්ලු වැස්ස'),
    63: ('raining', 'වැස්ස'), 65: ('heavy rain', 'තද වැස්ස'),
    80: ('showers', 'වැසි'), 81: ('showers', 'වැසි'), 82: ('heavy showers', 'තද වැසි'),
    95: ('thunderstorms', 'ගිගුරුම් සහිත වැසි'),
    96: ('thunderstorms', 'ගිගුරුම් සහිත වැසි'),
    99: ('thunderstorms', 'ගිගුරුම් සහිත වැසි'),
}


class Offline(Exception):
    """The internet could not be reached."""


def fetch(url, params=None, timeout=TIMEOUT, as_json=True):
    """One guarded request. Never retries and never waits longer than it says."""
    import requests
    try:
        response = requests.get(url, params=params, timeout=timeout,
                                headers={'User-Agent': 'JARVIS/1.0'})
        response.raise_for_status()
        if as_json:
            return response.json()
        return response.text[:MAX_PAGE]
    except requests.RequestException as error:
        raise Offline(str(error)[:120]) from error
    except ValueError as error:
        raise Offline(f'Unexpected reply: {error}') from error


def cached(kind, key, build):
    """Short-lived cache. The dollar rate does not move between two questions."""
    now = time.monotonic()
    entry = _cache.get((kind, key))
    if entry and now - entry[0] < CACHE_SECONDS.get(kind, 600):
        return entry[1]
    value = build()
    _cache[(kind, key)] = (now, value)
    return value


# Sinhala names of the same towns, so a match returns one canonical key. Without this
# find_place returns whichever spelling was spoken, and every caller has to know both.
CANONICAL = {
    'කොළඹ': 'colombo', 'මහනුවර': 'kandy', 'නුවර': 'kandy', 'ගාල්ල': 'galle',
    'යාපනය': 'jaffna', 'මීගමුව': 'negombo', 'කුරුණෑගල': 'kurunegala',
    'මාතර': 'matara', 'අනුරාධපුර': 'anuradhapura', 'නුවරඑළිය': 'nuwara eliya',
}


def find_place(text):
    """A known town named in an utterance, or Colombo. Always the English key."""
    from sinhala import normalize
    if not text:
        return DEFAULT_PLACE
    heard = normalize(text)
    # Longest first, so "නුවරඑළිය" is not read as "නුවර".
    for name in sorted(PLACES, key=len, reverse=True):
        spelled = normalize(name)
        if spelled and spelled in heard:
            return CANONICAL.get(name, name)
    return DEFAULT_PLACE


def weather(place=None):
    place = (place or DEFAULT_PLACE).lower()
    latitude, longitude = PLACES.get(place, PLACES[DEFAULT_PLACE])

    def build():
        data = fetch('https://api.open-meteo.com/v1/forecast', {
            'latitude': latitude, 'longitude': longitude,
            'current': 'temperature_2m,relative_humidity_2m,weather_code',
            'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'timezone': 'Asia/Colombo', 'forecast_days': 1})
        current, daily = data.get('current', {}), data.get('daily', {})
        code = int(current.get('weather_code', 0))
        return {
            'place': place, 'temperature': round(current.get('temperature_2m', 0)),
            'humidity': round(current.get('relative_humidity_2m', 0)),
            'condition_en': CONDITIONS.get(code, ('unsettled', 'නොසන්සුන්'))[0],
            'condition_si': CONDITIONS.get(code, ('unsettled', 'නොසන්සුන්'))[1],
            'high': round((daily.get('temperature_2m_max') or [0])[0]),
            'low': round((daily.get('temperature_2m_min') or [0])[0]),
            'rain_chance': round((daily.get('precipitation_probability_max') or [0])[0]),
            'source': 'open-meteo.com'}
    return cached('weather', place, build)


def dollar_rate():
    """US dollars to Sri Lankan rupees."""
    def build():
        data = fetch('https://api.exchangerate-api.com/v4/latest/USD')
        rate = (data.get('rates') or {}).get('LKR')
        if not rate:
            raise Offline('The rupee rate was not in the reply')
        return {'rate': round(float(rate), 2), 'date': data.get('date', ''),
                'source': 'exchangerate-api.com'}
    return cached('rate', 'usd-lkr', build)


def convert(amount, source='USD', target='LKR'):
    def build():
        data = fetch(f'https://api.exchangerate-api.com/v4/latest/{source.upper()}')
        rate = (data.get('rates') or {}).get(target.upper())
        if not rate:
            raise Offline(f'No rate for {source} to {target}')
        return float(rate)
    rate = cached('rate', f'{source}-{target}', build)
    return {'amount': amount, 'from': source.upper(), 'to': target.upper(),
            'rate': round(rate, 2), 'result': round(amount * rate, 2),
            'source': 'exchangerate-api.com'}


def news(limit=3):
    """Sri Lankan headlines, by RSS. No scraping, no key."""
    feeds = [('Ada Derana', 'https://www.adaderana.lk/rss.php'),
             ('Daily Mirror', 'https://www.dailymirror.lk/RSS_Feeds/breaking-news'),
             ('Hiru News', 'https://www.hirunews.lk/rss/local-news.xml')]

    def build():
        headlines = []
        for name, url in feeds:
            if len(headlines) >= limit:
                break
            try:
                body = fetch(url, timeout=6, as_json=False)
            except Offline:
                continue
            titles = re.findall(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', body,
                                re.S)[1:limit + 1]
            for title in titles:
                text = re.sub(r'<[^>]+>', '', title).strip()
                if text and len(text) > 12:
                    headlines.append({'title': text[:180], 'source': name})
                if len(headlines) >= limit:
                    break
        if not headlines:
            raise Offline('No news feed could be reached')
        return headlines
    return cached('news', str(limit), build)


def read_page(url):
    """Readable text from one page, for a question the other tools do not cover."""
    if not str(url).lower().startswith(('http://', 'https://')):
        raise Offline('That is not a web address')
    body = fetch(url, as_json=False)
    body = re.sub(r'(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>', ' ', body)
    text = re.sub(r'(?s)<[^>]+>', ' ', body)
    text = re.sub(r'&nbsp;?', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    return {'url': url, 'text': ' '.join(text.split())[:8000], 'source': urllib.parse.urlsplit(url).netloc}


def search(query, limit=3):
    """Search results with their snippets, using DuckDuckGo's HTML endpoint."""
    body = fetch('https://html.duckduckgo.com/html/', {'q': str(query)[:300]},
                 as_json=False)
    results = []
    for match in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S):
        link, title, snippet = match.groups()
        clean = lambda part: ' '.join(re.sub(r'(?s)<[^>]+>', ' ', part).split())
        link = urllib.parse.unquote(link)
        if 'uddg=' in link:
            link = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get('uddg', [link])[0]
        results.append({'title': clean(title)[:160], 'snippet': clean(snippet)[:400],
                        'url': link})
        if len(results) >= limit:
            break
    if not results:
        raise Offline('No results came back')
    return results


def main():
    parser = argparse.ArgumentParser(description='Live facts')
    parser.add_argument('--weather', nargs='?', const=DEFAULT_PLACE, metavar='PLACE')
    parser.add_argument('--dollar', action='store_true')
    parser.add_argument('--convert', nargs=3, metavar=('AMOUNT', 'FROM', 'TO'))
    parser.add_argument('--news', action='store_true')
    parser.add_argument('--search', metavar='QUERY')
    parser.add_argument('--read', metavar='URL')
    args = parser.parse_args()
    try:
        if args.weather:
            print(json.dumps(weather(args.weather), ensure_ascii=False, indent=2))
        elif args.dollar:
            print(json.dumps(dollar_rate(), indent=2))
        elif args.convert:
            print(json.dumps(convert(float(args.convert[0]), args.convert[1],
                                     args.convert[2]), indent=2))
        elif args.news:
            print(json.dumps(news(), ensure_ascii=False, indent=2))
        elif args.search:
            print(json.dumps(search(args.search), ensure_ascii=False, indent=2))
        elif args.read:
            print(json.dumps(read_page(args.read), ensure_ascii=False, indent=2)[:1500])
        else:
            parser.print_help()
    except Offline as error:
        print(f'offline: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
