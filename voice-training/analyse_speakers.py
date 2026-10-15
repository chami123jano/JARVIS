"""Per speaker in OpenSLR 30: how many transcribed clips, how much audio, typical pitch."""
import collections
import re
import sys
import wave
from pathlib import Path

import numpy

DATA = Path(__file__).parent / 'data'


def pitch(signal, rate):
    """Median fundamental frequency of the voiced frames, by autocorrelation."""
    frame, hop = int(0.04 * rate), int(0.02 * rate)
    low, high = int(rate / 300), int(rate / 70)            # 70-300 Hz
    found = []
    for start in range(0, len(signal) - frame, hop):
        chunk = signal[start:start + frame]
        if numpy.sqrt(numpy.mean(chunk ** 2)) < 0.02:       # silence
            continue
        chunk = chunk - chunk.mean()
        corr = numpy.correlate(chunk, chunk, 'full')[frame - 1:]
        lag = low + int(numpy.argmax(corr[low:high]))
        if corr[lag] > 0.3 * corr[0]:
            found.append(rate / lag)
    return float(numpy.median(found)) if found else 0.0


lines = {}
for line in open(DATA / 'si_lk.lines.txt', encoding='utf-8'):
    match = re.match(r'\(\s*(\S+)\s+"\s*(.*?)\s*"\s*\)', line)
    if match:
        lines[match.group(1)] = match.group(2)

speakers = collections.defaultdict(lambda: {'clips': 0, 'seconds': 0.0, 'pitch': []})
for name in sorted(lines):
    path = DATA / f'{name}.wav'
    if not path.exists():
        continue
    with wave.open(str(path)) as source:
        rate = source.getframerate()
        audio = numpy.frombuffer(source.readframes(source.getnframes()), dtype=numpy.int16) / 32768
    who = speakers[name.rsplit('_', 1)[0]]
    who['clips'] += 1
    who['seconds'] += len(audio) / rate
    if len(who['pitch']) < 12:
        who['pitch'].append(pitch(audio, rate))

print(f'sample rate {rate}')
for name, who in sorted(speakers.items(), key=lambda kv: -kv[1]['seconds']):
    f0 = numpy.median(who['pitch'])
    print(f"{name}  {who['clips']:3} clips  {who['seconds'] / 60:5.1f} min  pitch {f0:5.0f} Hz  "
          f"{'male' if f0 < 160 else 'female'}")
