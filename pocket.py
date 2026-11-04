"""JARVIS in your pocket (Day 25): talk to it from your iPhone, anywhere in the house.

    python pocket.py --on            allow phones (and make the certificate if needed)
    python pocket.py --pair          show a code to pair a phone, valid ten minutes
    python pocket.py --devices       the paired phones
    python pocket.py --forget NAME   unpair one
    python pocket.py --off           the off switch: no phone can reach JARVIS

Until Day 25 JARVIS listened on 127.0.0.1 only -- nothing else on the network could reach
it, deliberately. This opens one door, and only as far as it must:

  - off unless switched on; switched off, every request is refused at once
  - your home network only: a private address, and a certificate that is valid for private
    addresses and nothing else, so trusting it on the iPhone cannot be used for any real
    website
  - only paired phones: a six-digit code shown on the laptop, typed once on the phone,
    five tries; after that the phone holds a long secret of its own, kept here only as a
    hash
  - the same brain and the same rules as the microphone. A phone counts as you, like the
    console, but anything sent in your name is still read back and waits for "ඔව්".

The iPhone allows a web page the microphone only over https, so JARVIS has its own
certificate authority. Install jarvis-ca.crt on the iPhone once and trust it (Settings >
General > About > Certificate Trust Settings).
"""
import argparse
import datetime as dt
import hashlib
import hmac
import io
import ipaddress
import json
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / 'data' / 'pocket'
CA_CERT, CA_KEY = DIR / 'jarvis-ca.crt', DIR / 'jarvis-ca.key'
CERT, KEY = DIR / 'server.crt', DIR / 'server.key'
WEB = ROOT / 'web' / 'pocket'
PORT = 4192                     # https, for the phone
CA_PORT = 4193                  # plain http: the public CA certificate and nothing else
PAIR_SECONDS = 600
PAIR_TRIES = 5
MAX_AUDIO = 4_000_000           # a minute of iPhone audio is about 1 MB
AUDIO_KEEP = 300
SPEECH_RATE = 24000
PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]


class PocketError(Exception):
    pass


# ---------------------------------------------------------------- the switch

def store():
    from assistant_core import Store
    return Store(ROOT / 'data' / 'jarvis.db')


def enabled(db=None):
    return bool((db or store()).config('pocket_enabled', False))


def set_enabled(on, db=None):
    (db or store()).set_config('pocket_enabled', bool(on))


# ---------------------------------------------------------------- addresses

def is_private(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in PRIVATE)


def lan_addresses():
    """This laptop's private IPv4 addresses (home Wi-Fi or Ethernet; the hotspot too)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.168.1.1', 9))          # no packet is sent
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if is_private(a))


# ---------------------------------------------------------------- the certificate

def make_ca():
    """JARVIS's own certificate authority, limited by name constraints to private
    addresses and .local names: trusted on the iPhone, it vouches for nothing else."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'JARVIS home CA')])
    now = dt.datetime.now(dt.timezone.utc)
    allowed = [x509.IPAddress(net) for net in PRIVATE] + [x509.DNSName('local')]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True,
                                         crl_sign=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(permitted_subtrees=allowed,
                                                excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    DIR.mkdir(parents=True, exist_ok=True)
    CA_KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def make_server_cert(addresses):
    """A certificate for these addresses, signed by the CA. 397 days: the longest an
    iPhone accepts."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    ca_cert = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
    ca_key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'JARVIS')]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(a)) for a in addresses]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert


def covered(addresses):
    """Does the current certificate cover these addresses for at least 30 more days?"""
    if not (CERT.exists() and KEY.exists()):
        return False
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(CERT.read_bytes())
    if cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=30):
        return False
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    have = {str(ip) for ip in names.get_values_for_type(x509.IPAddress)}
    return set(addresses) <= have


def ensure_certificates(addresses=None):
    """The CA once (installed on the phone once), a server certificate whenever the
    laptop's address changes or the old one nears its end."""
    addresses = addresses or lan_addresses()
    if not addresses:
        raise PocketError('this laptop has no address on a home network')
    if not (CA_CERT.exists() and CA_KEY.exists()):
        make_ca()
    if not covered(addresses):
        make_server_cert(addresses)
    return addresses


# ---------------------------------------------------------------- pairing

def ensure_tables(db):
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS pocket_devices (
                          token_hash TEXT PRIMARY KEY, name TEXT NOT NULL,
                          paired_at TEXT NOT NULL, last_seen TEXT)''')


def digest(secret):
    return hashlib.sha256(str(secret).encode()).hexdigest()


def start_pairing(db=None):
    """A six-digit code, valid ten minutes, five tries. Only its hash is kept."""
    db = db or store()
    code = f'{secrets.randbelow(1_000_000):06d}'
    db.set_config('pocket_pairing', {'hash': digest(code), 'until': time.time() + PAIR_SECONDS,
                                     'tries': 0})
    return code


def pair(code, name, db=None):
    """A paired phone's secret, or raises PocketError. The code works once."""
    db = db or store()
    ensure_tables(db)
    waiting = db.config('pocket_pairing', None) or {}
    if not waiting or time.time() > waiting.get('until', 0):
        raise PocketError('no pairing code is waiting: run pocket.py --pair on the laptop')
    if waiting.get('tries', 0) >= PAIR_TRIES:
        db.set_config('pocket_pairing', None)
        raise PocketError('too many wrong codes: make a new one')
    if not hmac.compare_digest(digest(re.sub(r'\D', '', str(code))), waiting['hash']):
        waiting['tries'] = waiting.get('tries', 0) + 1
        db.set_config('pocket_pairing', waiting)
        raise PocketError('wrong code')
    db.set_config('pocket_pairing', None)
    token = secrets.token_urlsafe(32)
    label = re.sub(r'[^\w .\'-]', '', str(name))[:40].strip() or 'iPhone'
    with db.connect() as conn:
        conn.execute('INSERT INTO pocket_devices VALUES (?,?,?,?)',
                     (digest(token), label, dt.datetime.now().astimezone().isoformat(), None))
    return token, label


def device(token, db=None):
    """The paired phone this secret belongs to, or None."""
    if not token:
        return None
    db = db or store()
    ensure_tables(db)
    with db.connect() as conn:
        row = conn.execute('SELECT name FROM pocket_devices WHERE token_hash=?',
                           (digest(token),)).fetchone()
        if row:
            conn.execute('UPDATE pocket_devices SET last_seen=? WHERE token_hash=?',
                         (dt.datetime.now().astimezone().isoformat(), digest(token)))
    return row[0] if row else None


def devices(db=None):
    db = db or store()
    ensure_tables(db)
    with db.connect() as conn:
        return [{'name': r[0], 'paired_at': r[1], 'last_seen': r[2]} for r in
                conn.execute('SELECT name, paired_at, last_seen FROM pocket_devices ORDER BY paired_at')]


def forget(name, db=None):
    db = db or store()
    ensure_tables(db)
    with db.connect() as conn:
        return conn.execute('DELETE FROM pocket_devices WHERE name=?', (name,)).rowcount


# ---------------------------------------------------------------- sound

def wav_bytes(audio, rate=SPEECH_RATE):
    import numpy
    samples = (numpy.clip(audio, -1, 1) * 32767).astype('<i2')
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def decode_audio(data, rate):
    """Any audio the phone sends (Safari records AAC in MP4) as mono float32 at rate."""
    import av
    import numpy
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format='flt', layout='mono', rate=rate)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return numpy.concatenate(chunks).astype(numpy.float32) if chunks else numpy.zeros(0, numpy.float32)


def sapi_audio(text, rate=SPEECH_RATE):
    """English by the Windows voice, into a file rather than the speakers."""
    import os
    import tempfile
    import win32com.client
    path = Path(tempfile.gettempdir()) / f'jarvis-pocket-{uuid.uuid4().hex}.wav'
    try:
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Open(str(path), 3)
        voice = win32com.client.Dispatch('SAPI.SpVoice')
        voice.AudioOutputStream = stream
        voice.Speak(str(text), 0)
        stream.Close()
        return decode_audio(path.read_bytes(), rate)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def speech_audio(text):
    """The reply as one WAV, the way the laptop would have said it: the online voices
    when they answer, else the offline Sinhala voice and the Windows English one."""
    import numpy
    import speech
    pieces, online = [], time.monotonic() >= speech.SPEAKER.online_down_until
    for voice, piece in speech.segments(text):
        audio = None
        if online:
            try:
                audio = speech.decode(speech.synthesize(piece, voice))
            except speech.Offline:
                online = False
        if audio is None:
            if voice == speech.ENGLISH_VOICE:
                audio = sapi_audio(piece)
            else:
                import offline_voice
                audio = offline_voice.synthesize(piece, SPEECH_RATE) if offline_voice.available() \
                    else numpy.zeros(0, numpy.float32)
        pieces += [audio, numpy.zeros(int(SPEECH_RATE * 0.2), numpy.float32)]
    return wav_bytes(numpy.concatenate(pieces) if pieces else numpy.zeros(0, numpy.float32))


# ---------------------------------------------------------------- the server

class Pocket:
    """The door for phones. answer(text) -> reply is the voice loop's own brain;
    transcribe(float32 16 kHz) -> text is its ears."""

    def __init__(self, answer, transcribe, db=None, port=PORT, ca_port=CA_PORT,
                 speak=speech_audio):
        self.answer, self.transcribe, self.db = answer, transcribe, db or store()
        self.port, self.ca_port, self.speak = port, ca_port, speak
        self.servers, self.audio, self.lock = [], {}, threading.Lock()

    # -- the work behind each request
    def reply_to(self, text):
        reply = self.answer(text)
        clip = uuid.uuid4().hex
        try:
            sound = self.speak(reply)
        except Exception as error:
            print(f'  pocket: no audio for the reply: {error}', file=sys.stderr)
            sound = None
        with self.lock:
            now = time.monotonic()
            self.audio = {k: v for k, v in self.audio.items() if now - v[0] < AUDIO_KEEP}
            if sound:
                self.audio[clip] = (now, sound)
        return {'reply': reply, 'audio': f'/api/audio/{clip}' if sound else None}

    def heard(self, data):
        import voice_loop
        audio = decode_audio(data, voice_loop.SAMPLE_RATE)
        if len(audio) < voice_loop.SAMPLE_RATE * 0.3:
            return ''
        text, _, _ = self.transcribe(audio)
        return (text or '').strip()

    # -- serving
    def handler(pocket):
        class Handler(BaseHTTPRequestHandler):
            server_version = 'JARVIS'

            def log_message(self, *args):
                pass

            def send(self, status, body=b'', kind='application/json; charset=utf-8',
                     extra=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body, ensure_ascii=False).encode('utf-8')
                elif isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy',
                                 "default-src 'self'; media-src 'self' blob:; "
                                 "img-src 'self' data:; style-src 'self' 'unsafe-inline'")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def allowed(self):
                # The switch first: off means off, for paired phones too.
                if not enabled(pocket.db):
                    self.send(403, {'error': 'JARVIS is not taking phone requests (pocket.py --on)'})
                    return False
                if not is_private(self.client_address[0]):
                    self.send(403, {'error': 'home network only'})
                    return False
                return True

            def phone(self):
                token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
                name = device(token, pocket.db)
                if not name:
                    self.send(401, {'error': 'this phone is not paired'})
                return name

            def body(self, limit):
                size = int(self.headers.get('Content-Length', '0') or 0)
                if size > limit:
                    raise PocketError('too large')
                return self.rfile.read(size) if size else b''

            def do_GET(self):
                if not self.allowed():
                    return
                path = self.path.split('?')[0]
                if path.startswith('/api/audio/'):
                    if not self.phone():
                        return
                    with pocket.lock:
                        clip = pocket.audio.get(path.rsplit('/', 1)[-1])
                    return self.send(200, clip[1], 'audio/wav') if clip else self.send(404, {})
                if path == '/api/ping':
                    name = self.phone()
                    return self.send(200, {'ok': True, 'phone': name}) if name else None
                files = {'/': ('index.html', 'text/html; charset=utf-8'),
                         '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                         '/sw.js': ('sw.js', 'text/javascript; charset=utf-8'),
                         '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
                         '/icon.png': ('icon.png', 'image/png')}
                if path not in files:
                    return self.send(404, {'error': 'not found'})
                name, kind = files[path]
                self.send(200, (WEB / name).read_bytes(), kind)

            def do_POST(self):
                if not self.allowed():
                    return
                path = self.path.split('?')[0]
                try:
                    if path == '/api/pair':
                        request = json.loads(self.body(2000) or b'{}')
                        token, name = pair(request.get('code', ''), request.get('name', ''), pocket.db)
                        print(f'  pocket: paired {name}', flush=True)
                        return self.send(200, {'token': token, 'name': name})
                    name = self.phone()
                    if not name:
                        return
                    if path == '/api/ask':
                        text = str(json.loads(self.body(20000) or b'{}').get('text', '')).strip()[:2000]
                        if not text:
                            return self.send(400, {'error': 'nothing asked'})
                        print(f'  pocket ({name}): {text[:80]}', flush=True)
                        return self.send(200, dict(pocket.reply_to(text), heard=text))
                    if path == '/api/voice':
                        text = pocket.heard(self.body(MAX_AUDIO))
                        print(f'  pocket ({name}) heard: {text[:80]}', flush=True)
                        if not text:
                            import responses
                            said = responses.say_not_understood('si')
                            return self.send(200, {'heard': '', 'reply': said, 'audio': None})
                        return self.send(200, dict(pocket.reply_to(text), heard=text))
                    self.send(404, {'error': 'not found'})
                except PocketError as error:
                    self.send(400, {'error': str(error)})
                except Exception as error:      # reported, never raised into the server
                    print(f'  pocket: {type(error).__name__}: {error}', file=sys.stderr)
                    self.send(500, {'error': 'JARVIS could not answer that'})
        return Handler

    def ca_handler(pocket):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                # Only the CA's public certificate, so the iPhone can install it; plain
                # http because it cannot yet trust the https server.
                if not enabled(pocket.db) or not is_private(self.client_address[0]) \
                        or self.path.split('?')[0] not in ('/', '/jarvis-ca.crt'):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = CA_CERT.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-x509-ca-cert')
                self.send_header('Content-Disposition', 'attachment; filename="jarvis-ca.crt"')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        return Handler

    def start(self, addresses=None):
        """Listen on the home-network addresses only. Returns the https addresses."""
        import live
        addresses = ensure_certificates(addresses)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(CERT, KEY)
        urls = []
        for address in addresses:
            for port, handler, secure in ((self.port, self.handler(), True),
                                          (self.ca_port, self.ca_handler(), False)):
                try:
                    server = live.ExclusiveServer((address, port), handler)
                except OSError as error:
                    print(f'  pocket: cannot listen on {address}:{port}: {error}', file=sys.stderr)
                    continue
                if secure:
                    server.socket = context.wrap_socket(server.socket, server_side=True)
                    urls.append(f'https://{address}:{port}/')
                server.daemon_threads = True
                threading.Thread(target=server.serve_forever, daemon=True,
                                 name=f'pocket-{address}-{port}').start()
                self.servers.append(server)
        return urls

    def stop(self):
        for server in self.servers:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        self.servers = []

    def keep_in_step(self, every=5):
        """Open while switched on, closed while off -- checked every few seconds, so
        `pocket.py --off` shuts the door without restarting JARVIS."""
        def loop():
            while True:
                on = enabled(self.db)
                try:
                    if on and not self.servers:
                        for url in self.start():
                            print(f'  pocket: open at {url}', flush=True)
                    elif not on and self.servers:
                        self.stop()
                        print('  pocket: closed', flush=True)
                except Exception as error:
                    print(f'  pocket: {error}', file=sys.stderr)
                time.sleep(every)
        threading.Thread(target=loop, daemon=True, name='pocket').start()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--on', action='store_true')
    parser.add_argument('--off', action='store_true')
    parser.add_argument('--pair', action='store_true')
    parser.add_argument('--devices', action='store_true')
    parser.add_argument('--forget', metavar='NAME')
    args = parser.parse_args()
    db = store()
    if args.off:
        set_enabled(False, db)
        print('Off. No phone can reach JARVIS (within five seconds, if it is running).')
        return 0
    if args.on:
        addresses = ensure_certificates()
        set_enabled(True, db)
        print('On, for your home network. On the iPhone, in Safari:')
        for a in addresses:
            print(f'  1. the certificate, once:  http://{a}:{CA_PORT}/jarvis-ca.crt')
            print(f'  2. then JARVIS:            https://{a}:{PORT}/')
        print('JARVIS must be running for these to answer.')
        return 0
    if args.pair:
        if not enabled(db):
            print('Switch it on first: python pocket.py --on')
            return 1
        print(f'Pairing code: {start_pairing(db)}   (type it on the phone; ten minutes, five tries)')
        return 0
    if args.forget:
        print(f'Unpaired {forget(args.forget, db)} phone(s) named {args.forget!r}.')
        return 0
    print(f"Phones: {'ON' if enabled(db) else 'off'}")
    for d in devices(db):
        print(f"  {d['name']:20} paired {d['paired_at'][:16]}  last seen {(d['last_seen'] or 'never')[:16]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
