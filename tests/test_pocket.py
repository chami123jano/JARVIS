"""JARVIS in your pocket (Day 25): off unless switched on, home network only, paired
phones only, and a certificate that vouches for nothing but private addresses.

The server runs here for real, over TLS on 127.0.0.1, with the brain and the ears
replaced. Nothing reaches the network."""
import http.client
import io
import json
import ssl
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pocket
from assistant_core import Store


def free_port():
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def tone_wav(seconds=1.0, rate=16000):
    import numpy
    t = numpy.linspace(0, seconds, int(rate * seconds), endpoint=False)
    samples = (numpy.sin(2 * numpy.pi * 220 * t) * 0.3 * 32767).astype('<i2')
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return out.getvalue()


class Folder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(pocket, 'DIR', root),
                        patch.object(pocket, 'CA_CERT', root / 'ca.crt'),
                        patch.object(pocket, 'CA_KEY', root / 'ca.key'),
                        patch.object(pocket, 'CERT', root / 'server.crt'),
                        patch.object(pocket, 'KEY', root / 'server.key')]
        for p in self.patches:
            p.start()
        self.db = Store(root / 'j.db')

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()


class CertificateTests(Folder):
    def test_the_ca_vouches_only_for_private_addresses(self):
        from cryptography import x509
        cert, _ = pocket.make_ca()
        constraints = cert.extensions.get_extension_for_class(x509.NameConstraints)
        self.assertTrue(constraints.critical)
        permitted = {str(n.value) for n in constraints.value.permitted_subtrees}
        self.assertEqual(permitted, {'10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'local'})

    def test_the_server_certificate_and_when_it_is_renewed(self):
        from cryptography import x509
        pocket.ensure_certificates(['192.168.1.3'])
        cert = x509.load_pem_x509_certificate(pocket.CERT.read_bytes())
        self.assertLessEqual((cert.not_valid_after_utc - cert.not_valid_before_utc).days, 398,
                             'an iPhone refuses a longer one')
        ca_before = pocket.CA_CERT.read_bytes()
        self.assertTrue(pocket.covered(['192.168.1.3']))
        self.assertFalse(pocket.covered(['192.168.1.7']), 'a new address needs a new one')
        pocket.ensure_certificates(['192.168.1.7'])
        self.assertTrue(pocket.covered(['192.168.1.7']))
        self.assertEqual(pocket.CA_CERT.read_bytes(), ca_before, 'the phone keeps trusting it')

    def test_private_addresses(self):
        for address, private in (('192.168.1.3', True), ('10.1.2.3', True), ('172.20.0.1', True),
                                 ('8.8.8.8', False), ('127.0.0.1', False), ('nonsense', False)):
            with self.subTest(address=address):
                self.assertEqual(pocket.is_private(address), private)


class PairingTests(Folder):
    def test_a_code_works_once_and_only_a_hash_is_kept(self):
        code = pocket.start_pairing(self.db)
        token, name = pocket.pair(code, 'Chamindu iPhone', self.db)
        self.assertEqual(pocket.device(token, self.db), 'Chamindu iPhone')
        with self.db.connect() as conn:
            stored = ' '.join(str(c) for row in conn.execute('SELECT * FROM pocket_devices')
                              for c in row)
        self.assertNotIn(token, stored)
        with self.assertRaises(pocket.PocketError):
            pocket.pair(code, 'again', self.db)

    def test_five_wrong_codes_and_it_is_gone(self):
        code = pocket.start_pairing(self.db)
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(pocket.PAIR_TRIES):
            with self.assertRaises(pocket.PocketError):
                pocket.pair(wrong, 'x', self.db)
        with self.assertRaises(pocket.PocketError):
            pocket.pair(code, 'x', self.db)          # even the right one, now

    def test_an_old_code_expires(self):
        code = pocket.start_pairing(self.db)
        with patch.object(pocket.time, 'time', return_value=time.time() + pocket.PAIR_SECONDS + 1):
            with self.assertRaises(pocket.PocketError):
                pocket.pair(code, 'x', self.db)

    def test_a_phone_can_be_forgotten(self):
        token, _ = pocket.pair(pocket.start_pairing(self.db), 'old phone', self.db)
        self.assertEqual(pocket.forget('old phone', self.db), 1)
        self.assertIsNone(pocket.device(token, self.db))


class ServerTests(Folder):
    def setUp(self):
        super().setUp()
        self.asked = []
        self.port, self.ca_port = free_port(), free_port()
        self.door = pocket.Pocket(answer=lambda text: self.asked.append(text) or 'හරි.',
                                  transcribe=lambda audio: ('වෙලාව කීයද', 0.1, None),
                                  db=self.db, port=self.port, ca_port=self.ca_port,
                                  speak=lambda text: b'RIFF-fake-wav')
        # 127.0.0.1 stands in for the home network here; is_private is tested above.
        self.patches.append(patch.object(pocket, 'is_private', return_value=True))
        self.patches[-1].start()
        pocket.set_enabled(True, self.db)
        self.assertTrue(self.door.start(['127.0.0.1']))

    def tearDown(self):
        self.door.stop()
        super().tearDown()

    def request(self, method, path, body=None, token=None, kind='application/json'):
        context = ssl.create_default_context(cafile=str(pocket.CA_CERT))
        context.check_hostname = False         # 127.0.0.1 is outside the CA's constraints
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=context, timeout=10)
        headers = {'Content-Type': kind}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def paired(self):
        status, data = self.request('POST', '/api/pair',
                                    {'code': pocket.start_pairing(self.db), 'name': 'iPhone'})
        self.assertEqual(status, 200)
        return json.loads(data)['token']

    def test_the_page_is_served_over_tls(self):
        status, data = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Hold to talk', data)

    def test_unpaired_is_refused(self):
        self.assertEqual(self.request('POST', '/api/ask', {'text': 'hi'})[0], 401)
        self.assertEqual(self.request('POST', '/api/ask', {'text': 'hi'}, token='made-up')[0], 401)
        self.assertEqual(self.asked, [])

    def test_a_paired_phone_asks_and_hears_the_reply(self):
        token = self.paired()
        status, data = self.request('POST', '/api/ask', {'text': 'වෙලාව කීයද'}, token)
        result = json.loads(data)
        self.assertEqual((status, result['reply'], self.asked), (200, 'හරි.', ['වෙලාව කීයද']))
        status, audio = self.request('GET', result['audio'], token=token)
        self.assertEqual((status, audio), (200, b'RIFF-fake-wav'))
        self.assertEqual(self.request('GET', result['audio'])[0], 401, 'audio needs the phone too')

    def test_talking_goes_through_the_ears(self):
        token = self.paired()
        status, data = self.request('POST', '/api/voice', tone_wav(), token, kind='audio/wav')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['heard'], 'වෙලාව කීයද')
        self.assertEqual(self.asked, ['වෙලාව කීයද'])

    def test_the_off_switch_refuses_everything_at_once(self):
        token = self.paired()
        pocket.set_enabled(False, self.db)
        self.assertEqual(self.request('POST', '/api/ask', {'text': 'hi'}, token)[0], 403)
        self.assertEqual(self.request('GET', '/')[0], 403)
        self.assertEqual(self.asked, [])

    def test_only_the_home_network(self):
        self.patches[-1].stop()                # the real is_private: 127.0.0.1 is not home
        try:
            self.assertEqual(self.request('GET', '/')[0], 403)
        finally:
            self.patches[-1].start()


class SoundTests(unittest.TestCase):
    def test_a_reply_becomes_one_wav_even_offline(self):
        import numpy
        import offline_voice
        import speech
        with patch.object(speech, 'synthesize', side_effect=speech.Offline('down')), \
                patch.object(pocket, 'sapi_audio', return_value=numpy.zeros(2400, numpy.float32)), \
                patch.object(offline_voice, 'available', return_value=True), \
                patch.object(offline_voice, 'synthesize', return_value=numpy.zeros(2400, numpy.float32)):
            data = pocket.speech_audio('හරි. Done.')
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getnchannels(), w.getframerate()), (1, pocket.SPEECH_RATE))
            self.assertGreater(w.getnframes(), 4800)

    def test_iphone_audio_is_decoded(self):
        audio = pocket.decode_audio(tone_wav(0.5), 16000)
        self.assertAlmostEqual(len(audio), 8000, delta=400)


if __name__ == '__main__':
    unittest.main()
