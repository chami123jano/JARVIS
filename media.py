"""Music that actually plays (Day 26).

    python media.py "mata hitha hodha sindu ekak daanna"     choose and play
    python media.py --history
    python media.py --playing                                 what is making sound now

"Play a song" used to open a YouTube search page and stop there. Now the first result is
found and opened on its own page, where it plays. Pause, next and previous are the
keyboard's media keys, so they work on whatever is playing -- YouTube in the browser, or a
local file in the Windows player.

Whether something is really playing is not assumed: Windows reports, per application,
how loud the sound it is making is right now. That is what the Day 26 check reads.

Spotify needs an account sign-in and is not here. Local music is searched by file name
in the folders listed in MUSIC_FOLDERS (empty on this computer today).
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

AUDIO = ('.mp3', '.m4a', '.flac', '.wav', '.ogg', '.aac')
MUSIC_FOLDERS = [Path.home() / 'Music', Path.home() / 'OneDrive' / 'Music']
HISTORY_KEPT = 50
BROWSER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                 '(KHTML, like Gecko) Chrome/120 Safari/537.36')


class MediaError(Exception):
    pass


# ---------------------------------------------------------------- what to play

# Moods, in the words people use. "හිත හොඳ සින්දුවක්" is a feel-good song, not a search
# for the words "heart good".
MOODS = [
    (('hitha hodha', 'hitha honda', 'හිත හොඳ', 'happy', 'feel good', 'sathutu', 'සතුටු'),
     'feel good'),
    (('dukha', 'දුක', 'sad', 'හිත රිදෙන'), 'sad'),
    (('sansun', 'සන්සුන්', 'relax', 'calm', 'nidiyanna', 'නිදියන්න', 'sleep'), 'relaxing'),
    (('parana', 'පරණ', 'old', 'classic'), 'old classic'),
    (('aluth', 'අලුත්', 'new', 'latest'), 'new'),
    (('baila', 'බයිලා', 'party', 'dance'), 'baila party'),
]
# Words that ask for music without saying which.
ASKING = {'mata', 'මට', 'sindu', 'sinduwak', 'sinduvak', 'sindu', 'සින්දු', 'සින්දුවක්',
          'සිංදු', 'සිංදුවක්', 'song', 'songs', 'music', 'මියුසික්', 'daanna', 'danna',
          'dannako', 'දාන්න', 'දාන්නකෝ', 'play', 'some', 'a', 'ekak', 'එකක්', 'please',
          'karanna', 'ahanna', 'අහන්න', 'me', 'for', 'jarvis', 'ලස්සන', 'lassana', 'hodha',
          'හොඳ', 'hitha', 'හිත', 'one', 'the', 'කරන්න', 'ඕනේ', 'oni',
          # Google writes "play" in Sinhala script too. Measured 16 Oct: "සින්දුවක් ප්ලේ
          # කරන්න" searched YouTube for "ප්ලේ song".
          'ප්ලේ', 'ප්ලෙ', 'කරන්නකෝ', 'කරන්නකො', 'karannako', 'දාපන්', 'දාන්නකො', 'daapan',
          'dapan', 'ple', 'plei', 'any', 'something', 'song', 'ගීතයක්', 'ගීත'}
YESTERDAY = re.compile(r'yesterday|iye|ඊයේ|kalin ahapu|last played|again|ආයෙත්', re.I)


def understand(text):
    """{'kind': 'again' | 'search', 'query': ..., 'mood': ...} from what was said."""
    lowered = str(text).lower()
    if YESTERDAY.search(lowered):
        return {'kind': 'again', 'query': '', 'mood': ''}
    mood = next((name for words, name in MOODS if any(w in lowered for w in words)), '')
    mood_words = {w for words, _ in MOODS for w in words for w in w.split()}
    rest = [w for w in re.findall(r'[\w඀-෿]+', lowered)
            if w not in ASKING and w not in mood_words]
    if rest:
        return {'kind': 'search', 'query': ' '.join(rest) + ' song', 'mood': mood}
    return {'kind': 'search', 'query': f'{mood or "popular"} sinhala songs'.strip(),
            'mood': mood}


# ---------------------------------------------------------------- finding it

def youtube_first(query, timeout=10):
    """The first video for this search: {'id', 'title', 'url'}. Raises MediaError."""
    try:
        page = requests.get('https://www.youtube.com/results?search_query='
                            + urllib.parse.quote(query),
                            headers={'User-Agent': BROWSER_AGENT, 'Accept-Language': 'en'},
                            timeout=timeout).text
    except requests.RequestException as error:
        raise MediaError('YouTube could not be reached') from error
    results = youtube_results(page)
    if not results:
        raise MediaError(f'nothing found for {query}')
    return best_video(results)


SHORTS = re.compile(r'#\s*(yt)?shorts?\b|#shortvideo|\btop\s*\d+\b|\btutorial\b|\bnotation\b|'
                    r'\blesson\b|පාඩම', re.I)


def youtube_results(page, limit=20):
    """Every video on a results page, in order: {'id', 'title', 'url', 'seconds'}.
    seconds is None when the page does not say (a live stream, or a page from a test)."""
    starts = [m.start() for m in re.finditer(r'"videoRenderer":\{"videoId":"', page)][:limit]
    results = []
    for begin, end in zip(starts, starts[1:] + [len(page)]):
        chunk = page[begin:min(end, begin + 30000)]
        video = re.search(r'"videoId":"([\w-]{11})"', chunk)
        title = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', chunk)
        if not (video and title):
            continue
        try:
            # The page is JSON inside HTML: "&" and friends are JSON escapes, and
            # decoding them any other way mangles Sinhala titles.
            text = json.loads(f'"{title.group(1)}"')
        except ValueError:
            text = title.group(1)
        length = re.search(r'"lengthText":\{.*?"simpleText":"([\d:]+)"', chunk)
        seconds = None
        if length:
            seconds = 0
            for part in length.group(1).split(':'):
                seconds = seconds * 60 + int(part)
        results.append({'id': video.group(1), 'title': text, 'seconds': seconds,
                        'url': f'https://www.youtube.com/watch?v={video.group(1)}'})
    return results


def best_video(results):
    """A song, not a 15-second Short or a lesson. Measured 16 Oct: "play a song" got
    "ලොව ආකර්ෂණීයම සිංදු😱Top 10 Most Attitude Song...#ytshorts", 57 seconds long, while a
    3:10 song sat further down the same page. First choice: a song's length (1:30-10:00)
    and not a Short; then a longer collection; only then simply the first."""
    usable = [r for r in results if not SHORTS.search(r['title'])]
    for fits in (lambda s: s is not None and 90 <= s <= 600,
                 lambda s: s is not None and s > 600):
        for result in usable:
            if fits(result['seconds']):
                return result
    return (usable or results)[0]


def local_tracks(folders=None):
    tracks = []
    for folder in folders or MUSIC_FOLDERS:
        if Path(folder).is_dir():
            tracks += [p for p in Path(folder).rglob('*') if p.suffix.lower() in AUDIO]
    return tracks


def find_local(query, folders=None):
    """A local file whose name fits the request, by sound as well as spelling."""
    from sinhala import similarity
    words = [w for w in str(query).lower().split() if w not in ('song', 'sinhala', 'songs')]
    if not words:
        return None
    best, score = None, 0.0
    for track in local_tracks(folders):
        name = re.sub(r'[_\-.]+', ' ', track.stem.lower())
        value = max(similarity(' '.join(words), name),
                    sum(w in name for w in words) / len(words))
        if value > score:
            best, score = track, value
    return best if score >= 0.6 else None


# ---------------------------------------------------------------- playing it

def history(store):
    return list(store.config('media_history', []))


def remember_played(store, item):
    played = history(store)
    played.append(dict(item, when=dt.datetime.now().isoformat(timespec='seconds')))
    store.set_config('media_history', played[-HISTORY_KEPT:])


def last_played_before(store, day=None):
    """The last thing played before today -- "the song I was listening to yesterday"."""
    day = day or dt.date.today()
    earlier = [h for h in history(store) if h['when'][:10] < day.isoformat()]
    return earlier[-1] if earlier else None


def choose(text, store, folders=None):
    """What to play for this sentence: {'title', 'url' or 'path', 'source', 'query'}."""
    wanted = understand(text)
    if wanted['kind'] == 'again':
        item = last_played_before(store) or (history(store) or [None])[-1]
        if not item:
            raise MediaError('nothing has been played yet')
        return dict(item, source='history')
    local = find_local(wanted['query'], folders)
    if local:
        return {'title': local.stem, 'path': str(local), 'source': 'local',
                'query': wanted['query']}
    found = youtube_first(wanted['query'])
    return dict(found, source='youtube', query=wanted['query'])


def start(item):
    """Open it where it plays. YouTube in the browser, a file in its default player."""
    if item.get('path'):
        os.startfile(item['path'])
    elif not webbrowser.open(item['url']):
        raise MediaError('the browser did not open')


def control(action):
    """pause / play / next / previous, by the media keys."""
    import pc_control
    keys = {'pause': 'play_pause', 'play': 'play_pause', 'resume': 'play_pause',
            'next': 'next', 'previous': 'previous', 'stop': 'stop'}
    return pc_control.media(keys[action])


def playing(threshold=0.01):
    """Applications making sound right now: [(process name, peak level)]."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    sounding = []
    for session in AudioUtilities.GetAllSessions():
        try:
            peak = session._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue()
        except Exception:
            continue
        if peak >= threshold:
            name = session.Process.name() if session.Process else 'system sounds'
            sounding.append((name, round(peak, 3)))
    return sounding


def wait_for_sound(processes=('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe',
                              'opera.exe', 'wmplayer.exe', 'Microsoft.Media.Player.exe'),
                   seconds=25):
    """True once one of these applications is making sound."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(name.lower() in (p.lower() for p in processes) for name, _ in playing()):
            return True
        time.sleep(.5)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('request', nargs='*')
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--playing', action='store_true')
    args = parser.parse_args()
    from assistant_core import ROOT, Store
    store = Store(ROOT / 'data' / 'jarvis.db')
    if args.playing:
        print(playing(0.0) or 'nothing')
    elif args.history:
        for item in history(store):
            print(item['when'], item['source'], item['title'])
    elif args.request:
        item = choose(' '.join(args.request), store)
        print(f"{item['source']}: {item['title']}  ({item.get('url') or item.get('path')})")
        start(item)
        remember_played(store, item)
        print('sound from the browser:', wait_for_sound())
    return 0


if __name__ == '__main__':
    sys.exit(main())
