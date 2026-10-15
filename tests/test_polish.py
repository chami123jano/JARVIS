"""Making what exists work properly, before building more (16 Oct).

Measured from the console that night: a typed "sinduwak play karanna" got an invented
song instead of music, replies took up to 16 s, the voice came seconds after the text,
and "Hey JARVIS" was heard once and then never again.
"""
import inspect
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent as agent_module
agent_module.HOUSEKEEPING_QUIET = 0     # the real wait is for a person; not here
import documents
import live
import memory
import speech
from agent import Agent
from assistant_core import Store, Tools


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class RelayTests(unittest.TestCase):
    """The console goes through the same brain as the microphone."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'state.db')
        self.agent = Agent(self.store, Tools(self.store, root / 'workspace'))

    def tearDown(self):
        for job_id in list(self.agent.threads):
            self.agent.wait(job_id)
        self.temp.cleanup()

    def test_the_voice_loop_answers_and_the_console_does_not_speak_it_twice(self):
        asked = []
        job_id = self.agent.relay('sinduwak play karanna',
                                  lambda text: asked.append(text) or 'හරි, සින්දුවක් දානවා.')
        self.agent.wait(job_id)
        job = self.agent.snapshot(job_id)
        self.assertEqual(asked, ['sinduwak play karanna'])
        self.assertEqual((job['state'], job['answer']), ('done', 'හරි, සින්දුවක් දානවා.'))
        self.assertTrue(job['spoken'], 'already said aloud by the voice loop')

    def test_with_the_voice_loop_down_it_answers_here(self):
        def run(job_id, prompt):
            with self.agent.lock:
                self.agent.jobs[job_id].update(state='done', answer='answered here')
        with patch.object(self.agent, 'run', side_effect=run) as ran:
            job_id = self.agent.relay('what time is it', lambda text: None)
            self.agent.wait(job_id)
        job = self.agent.snapshot(job_id)
        ran.assert_called_once()
        self.assertEqual(job['answer'], 'answered here')
        self.assertFalse(job['spoken'], 'nobody said it yet, so the console may')
        self.assertIn('Voice loop not running', [e['label'] for e in job['events']])


class AskTests(unittest.TestCase):
    """POST /ask: only from this machine, only with the secret."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temp.name) / 'live.token'
        self.port = free_port()
        self.heard = []
        self.server = live.serve(live.Live(), port=self.port, token_file=self.token_file,
                                 answer=lambda text, speak: self.heard.append((text, speak))
                                 or 'Playing.')
        self.assertIsNotNone(self.server)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def post(self, token):
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/ask', method='POST',
            data=json.dumps({'text': 'x'}).encode(), headers={'X-Live-Token': token})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_the_console_gets_the_reply(self):
        with patch.object(live, 'TOKEN_FILE', self.token_file):
            self.assertEqual(live.ask('play a song', speak=False, port=self.port), 'Playing.')
        self.assertEqual(self.heard, [('play a song', False)])

    def test_without_the_secret_nothing_is_done(self):
        for token in ('', 'wrong'):
            with self.subTest(token=token):
                self.assertEqual(self.post(token), 403)
        self.assertEqual(self.heard, [])

    def test_no_secret_file_means_the_voice_loop_is_not_running(self):
        with patch.object(live, 'TOKEN_FILE', Path(self.temp.name) / 'missing.token'):
            self.assertIsNone(live.ask('hello', port=self.port))


class OnlineVoiceBreakerTests(unittest.TestCase):
    """Measured: each reply waited 1.4 s for the refused online voice before speaking."""

    def test_after_one_refusal_the_next_replies_go_straight_offline(self):
        import numpy
        speaker = speech.Speaker()
        fallback = MagicMock(return_value={'spoken': True})
        missing = Path(tempfile.gettempdir()) / 'jarvis-no-such-cache.mp3'
        synthesize = MagicMock(side_effect=[b'mp3', speech.Offline('403')])
        with patch.object(speech, 'synthesize', synthesize), \
                patch.object(speech, 'decode', return_value=numpy.zeros(10)), \
                patch.object(speech, 'cache_path', return_value=missing), \
                patch.object(speaker, 'play', return_value=True), \
                patch.object(speaker, 'offline_fallback', fallback):
            speaker.say('Hello there. ඔයාට කොහොමද?')
            self.assertEqual(fallback.call_args.args[0], 'ඔයාට කොහොමද?',
                             'only what was not said yet, not the whole reply again')
            speaker.say('Second reply.')
        self.assertEqual(synthesize.call_count, 2, 'the second reply did not wait for it')
        self.assertEqual(fallback.call_args.args[0], 'Second reply.')


class SpeedTests(unittest.TestCase):
    def test_every_fast_call_asks_for_the_same_context(self):
        """Ollama reloads a model when num_ctx changes: 13 s, on nearly every request."""
        root = Path(__file__).resolve().parents[1]
        for name in ('agent.py', 'memory.py', 'tasks.py', 'vision.py'):
            source = (root / name).read_text(encoding='utf-8')
            for size in __import__('re').findall(r"'num_ctx':\s*(\d+)", source):
                with self.subTest(file=name):
                    self.assertIn(int(size), (memory.FAST_CTX, 16384))
        self.assertEqual(memory.FAST_CTX, 8192)

    def test_training_folders_are_not_indexed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in ('piper-train/lessac/notes.md', 'piper1-gpl/src/app.py', 'me/cv.md'):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text('x' * 60, encoding='utf-8')
            self.assertEqual([p.name for p in documents.candidates([folder])], ['cv.md'])


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'state.db')
        self.store.set_config('model', 'test')
        self.agent = Agent(self.store, Tools(self.store, root / 'workspace'))

    def tearDown(self):
        for job_id in list(self.agent.threads):
            self.agent.wait(job_id)
        self.temp.cleanup()

    def seen_by_the_model(self, text):
        seen = {}

        def generate(job_id, model, messages, think, deep):
            seen['text'] = messages[-1]['content']
            return {'role': 'assistant', 'content': 'ok'}
        with patch.object(self.agent, 'generate', side_effect=generate):
            self.agent.wait(self.agent.start(text))
        return seen['text']

    def test_it_is_told_which_language_to_answer_in(self):
        """Measured 16 Oct: English answered in Sinhala, Sinhala answered in English."""
        self.assertIn('(Answer in English.)', self.seen_by_the_model('what is the capital of Japan?'))
        for text in ('මට කෝපි හොඳද තේ හොඳද?', 'mata hondai coffee da tea da?'):
            with self.subTest(text=text):
                self.assertIn('(Answer in Sinhala.)', self.seen_by_the_model(text))

    def test_it_is_told_that_advice_needs_no_tool(self):
        """Measured: "what should I eat for dinner?" got "I don't have a tool to suggest meals"."""
        with patch.object(memory, 'context_for', return_value=[]):
            self.assertIn('you need no tool', self.agent.system_prompt('what should I eat'))

    def test_a_question_is_never_remembered_as_a_fact(self):
        """Measured: "which is better for me, coffee or tea?" became "prefers coffee or tea"."""
        recall = memory.Memory(self.store, gloss=lambda t: t, embed=lambda ts: [[1.0]] * len(ts))
        asked = []

        def ask(prompt, schema):
            asked.append(prompt)
            return {'facts': [{'en': 'The user prefers coffee', 'spoken': 'x'}]}
        for text in ('mata hondai coffee da tea da?', 'මට කෝපි හොඳද', 'mage nangi kandy neda',
                     'do I like tea?'):
            with self.subTest(text=text):
                self.assertEqual(recall.extract_facts(text, ask), [])
        self.assertEqual(asked, [], 'not even asked about')
        for text in ('I drive a Honda', 'my mother lives in Canada.', 'මම තේ විතරයි බොන්නේ'):
            with self.subTest(text=text):
                self.assertTrue(recall.extract_facts(text, ask))


class CalledByNameTests(unittest.TestCase):
    """Measured 16 Oct, in the follow-up window: "Hey Chavez!" -> "I am JARVIS, not Chavez"."""

    def test_its_name_however_it_was_heard(self):
        import voice_loop
        for text in ('Hey Chavez!', 'Javis.', 'Hey Jarvis', 'හේ ජාවිස්', 'ජාවිස්', 'Hey Jervis'):
            with self.subTest(text=text):
                self.assertTrue(voice_loop.only_the_name(text))
        for text in ('Enjoy!', 'Thank you.', 'hey', 'hello', 'වෙලාව කීයද', 'what is the time',
                     'Hey JARVIS, play a song'):
            with self.subTest(text=text):
                self.assertFalse(voice_loop.only_the_name(text))

    def test_the_name_in_front_of_a_command_is_dropped(self):
        import voice_loop
        self.assertEqual(voice_loop.without_the_name('Hey JARVIS, play a song'), 'play a song')
        self.assertEqual(voice_loop.without_the_name('Jarvis වෙලාව කීයද'), 'වෙලාව කීයද')
        self.assertEqual(voice_loop.without_the_name('what time is it'), 'what time is it')

    def test_called_by_name_it_beeps_and_listens_again(self):
        import numpy
        import voice_loop
        listener = voice_loop.Listener.__new__(voice_loop.Listener)
        heard = iter(['Hey Chavez!', 'වෙලාව කීයද'])
        listener.ears = MagicMock()
        listener.ears.transcribe.side_effect = lambda audio, language: (
            next(heard), 0.1, MagicMock(language='si'))
        listener.language, listener.last_reply = None, ''
        for name in ('flush', 'set_state', 'log_utterance'):
            setattr(listener, name, MagicMock(return_value=0))
        listener.record_command = MagicMock(return_value=numpy.zeros(16000, dtype='float32'))
        commands = []
        with patch.object(voice_loop, 'beep') as beep:
            self.assertTrue(listener.handle_command([], commands.append, prompt_beep=False))
        self.assertEqual(commands, ['වෙලාව කීයද'], 'the name is not a question')
        beep.assert_called_once()


class MusicChoiceTests(unittest.TestCase):
    """Measured 16 Oct: "සින්දුවක් ප්ලේ කරන්න" played a 57-second Short and read out its
    emoji-strewn title."""

    def test_play_in_sinhala_script_is_not_searched_for(self):
        import media
        for text in ('සින්දුවක් ප්ලේ කරන්න', 'sinduwak play karanna', 'සින්දුවක් දාපන්'):
            with self.subTest(text=text):
                self.assertEqual(media.understand(text)['query'], 'popular sinhala songs')

    def test_a_song_not_a_short(self):
        import media
        def video(n, title, length):
            return ('{"videoRenderer":{"videoId":"%s","title":{"runs":[{"text":"%s"}]},'
                    '"lengthText":{"accessibility":{"x":1},"simpleText":"%s"}}' % (n * 11, title, length))
        page = (video('a', 'Top 10 Songs #ytshorts', '0:57') + video('b', 'Jukebox', '1:30:03') +
                video('c', 'Guitar lesson', '4:00') + video('d', 'Dewaduthiyak', '3:10'))
        results = media.youtube_results(page)
        self.assertEqual([r['seconds'] for r in results], [57, 5403, 240, 190])
        self.assertEqual(media.best_video(results)['title'], 'Dewaduthiyak')
        self.assertEqual(media.best_video(results[:2])['title'], 'Jukebox',
                         'no song-length video: a collection before a Short')

    def test_the_title_is_said_without_emoji_or_tags(self):
        import responses
        self.assertEqual(responses.short_title(
            'ලොව ආකර්ෂණීයම සිංදු😱Top 10 Most Attitude Song🥰#ytshorts#song'),
            'ලොව ආකර්ෂණීයම සිංදු Top 10 Most Attitude Song')
        self.assertEqual(responses.short_title('Dewaduthiyak (දේවදූතියක්) | Mihiran | New'),
                         'Dewaduthiyak (දේවදූතියක්)')
        self.assertEqual(responses.short_title('[JUKEBOX] හොදම සින්දු ටික'), 'හොදම සින්දු ටික')


class WhisperPreloadTests(unittest.TestCase):
    """Measured 16 Oct: Whisper loaded on the spot inside a command -- 12.7 s."""

    def test_loaded_in_the_background_once(self):
        import threading
        import voice_loop
        started, release = threading.Event(), threading.Event()
        loads = []

        def slow_ears(model, device):
            loads.append(model)
            started.set()
            release.wait(5)
            return 'whisper'
        hearing = voice_loop.Hearing.__new__(voice_loop.Hearing)
        hearing.whisper, hearing.engine = None, 'auto'
        hearing.model_name, hearing.whisper_device = 'm', 'auto'
        with patch.object(voice_loop, 'Ears', side_effect=slow_ears):
            hearing.preload()
            self.assertTrue(started.wait(5))
            waiting = threading.Thread(target=hearing.load_whisper)
            waiting.start()          # a command arriving during the preload
            release.set()
            waiting.join(5)
        self.assertEqual((loads, hearing.whisper), (['m'], 'whisper'), 'loaded once')


class NameClashTests(unittest.TestCase):
    def test_no_function_hides_one_of_its_modules_functions(self):
        """Twice now: `watcher = ...` in voice_loop (Day 16) and `speak = ...` in the
        console's request handler (16 Oct). Assigning the name anywhere in a function makes
        it local everywhere in it, so the module's function vanishes -- an
        UnboundLocalError only on the path that calls it."""
        import ast
        clashes = []
        for path in sorted(Path(__file__).resolve().parents[1].glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stored = {n.id for n in ast.walk(function)
                              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                    clashes += [f'{path.name}: {function.name} assigns {name}'
                                for name in sorted(stored & top)]
        self.assertEqual(clashes, [])


class WakeTests(unittest.TestCase):
    def test_a_stuck_command_leaves_a_trace(self):
        """16 Oct: heard once, then silent until quit, with nothing in the log."""
        import voice_loop
        self.assertIn('dump_traceback_later', inspect.getsource(voice_loop.Listener.handle))
        self.assertLessEqual(voice_loop.HANG_SECONDS, 120)

    def test_the_beep_cannot_block_forever(self):
        import voice_loop
        played = MagicMock()
        with patch.dict(sys.modules, {'sounddevice': MagicMock(play=played)}), \
                patch.object(voice_loop.time, 'sleep'):
            self.assertTrue(voice_loop.beep())
        self.assertNotIn('blocking', played.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
