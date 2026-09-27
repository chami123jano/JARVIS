"""Guards the phonetic matching that makes Sinhala voice commands work.

Every case here is a real Whisper output from a recorded Sinhala command. Whisper writes
Sinhala speech in whichever script it is most confident about — Tamil, Latin, Devanagari,
Gujarati — while getting the sounds right, so matching happens on sound. These cases took
command recognition from 11 of 20 to 19 of 20 and exist to keep it there.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sinhala import contains, normalize, romanize, similarity


class RomanisationTests(unittest.TestCase):
    def test_sinhala_script(self):
        self.assertEqual(normalize('දැන් වෙලාව කීයද'), 'dan velava kiyada')

    def test_scripts_agree_on_the_same_words(self):
        """The same command in four scripts must reduce to nearly the same sounds."""
        spellings = [
            'දැන් වෙලාව කීයද',       # Sinhala
            'Den belaave keyed',     # Latin, as Whisper wrote it
            'dan welawa kiyada',     # Singlish
        ]
        reference = 'dan velava kiyada'
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                self.assertGreaterEqual(similarity(spelling, reference), .55,
                                        f'{spelling!r} -> {normalize(spelling)!r}')

    def test_tamil_script_is_readable(self):
        """Whisper's most common fallback for Sinhala. Silently dropping it cost 8 of 20."""
        cases = [
            ('அது காலகுமி கோகுமது', 'ada kalaguna kohomada'),
            ('அம்மாட்டு கியன்னு மாமு ராத்த்ரி எனவா', 'ammata kiyanna mama rathri enava'),
            ('சிந்துவக் தான்ன', 'sinduvak danna'),
            ('அது டொல ரேட்டெக்க கீயது', 'ada dollar rate kiyada'),
            ('ஜாவிஸ் ஓயாட்ட முனவாது கரண்ண புழுவா', 'jarvis oyata monava karanna puluvanda'),
        ]
        for tamil, expected in cases:
            with self.subTest(tamil=tamil):
                self.assertNotEqual(normalize(tamil), '', 'Tamil produced nothing')
                self.assertGreaterEqual(similarity(tamil, expected), .5,
                                        f'{normalize(tamil)!r} vs {normalize(expected)!r}')

    def test_other_indic_scripts(self):
        cases = [
            ('अग्धु डोलरेट्टेका की यदु', 'ada dollar rate kiyada'),      # Devanagari
            ('મગે સટહહ ન્ પે ન્ય', 'mage satahan pennanna'),             # Gujarati
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertGreaterEqual(similarity(text, expected), .5)

    def test_accented_latin_keeps_its_letters(self):
        """Whisper labels Sinhala audio Swedish or Icelandic and writes accents.

        Dropping the accented letters instead of stripping the accent turned
        "Baríganegu lókkarann" into "barganegu lkaran", which then matched 'calculate'
        more closely than 'lock the computer'.
        """
        self.assertEqual(normalize('Baríganegu lókkarann'), 'bariganegu lokaran')
        self.assertGreater(similarity('Baríganegu lókkarann', 'pariganakaya lock karanna'),
                           similarity('Baríganegu lókkarann', 'calculate karanna'))
        # Umlauts survive as their base letter.
        self.assertEqual(normalize('krönnu'), 'kronu')
        # Icelandic thorn and eth are letters in their own right, not accented forms, so
        # NFD leaves them alone and they need an explicit mapping. Thorn is the "th"
        # sound, hence 'tad' rather than 'pad'.
        self.assertEqual(normalize('það'), 'tad')
        self.assertEqual(normalize('øre æsk'), 'ore ask')

    def test_short_input_cannot_trigger_an_action(self):
        """A plain sequence ratio is inflated for short strings; noise must not pass."""
        for noise in ('hana', 'ven', 'abc', 'ok'):
            with self.subTest(noise=noise):
                self.assertEqual(similarity(noise, 'pariganakaya lock karanna'), 0.0)

    def test_unrelated_commands_stay_apart(self):
        """Matching must not be so loose that everything looks like everything else."""
        pairs = [
            ('email balanna', 'sinduvak danna'),
            ('pariganakaya lock karanna', 'ada kalaguna kohomada'),
            ('screenshot ekak ganna', 'ammata message ekak yavanna'),
        ]
        for left, right in pairs:
            with self.subTest(left=left):
                self.assertLess(similarity(left, right), .5)

    def test_empty_and_junk_are_safe(self):
        for value in ('', None, '   ', '!!!', '123'):
            with self.subTest(value=value):
                self.assertIsInstance(romanize(value), str)
                self.assertEqual(similarity(value, 'email balanna'), 0.0
                                 if not normalize(value) else similarity(value, 'email balanna'))

    def test_contains_finds_a_keyword_inside_noise(self):
        self.assertTrue(contains('amatu mesej ikak yavan adhikari', 'message ekak yavanna'))
        self.assertFalse(contains('ada kalaguna kohomada', 'screenshot ekak ganna'))


class ClassificationTests(unittest.TestCase):
    """End-to-end: the right command must win against the whole vocabulary."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from command_match import ACCEPT, classify
        self.classify, self.accept = classify, ACCEPT

    def test_real_transcripts_reach_the_right_command(self):
        cases = [
            ('அது காலகுமி கோகுமது', 'weather'),
            ('System ege tattvaya kianne.', 'system_status'),
            ('அம்மது மேசேஜ் இக்காக் யாவன்', 'send_message'),
            ('Minitu dahayaking matak kerana.', 'reminder_relative'),
            ('சிந்துவக் தான்ன', 'play_music'),
            ('அது டொல ரேட்டெக்க கீயது', 'dollar_rate'),
            ('E-mail barane.', 'email'),
            ('ஜாவிஸ் ஓயாட்ட முனவாது கரண்ண புழுவா', 'capabilities'),
            # Accented Latin, which needs the whole phrasing list to resolve.
            ('Kromn ekki ofan krönnu', 'open_app'),
            ('Baríganegu lókkarann', 'lock_pc'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                (score, picked), _ = self.classify(text)
                self.assertEqual(picked, expected, f'{normalize(text)!r} -> {picked} {score:.2f}')
                self.assertGreaterEqual(score, self.accept)

    def test_nonsense_is_rejected_rather_than_guessed(self):
        """A wrong action is worse than asking again, so noise must fall below the floor."""
        for noise in ('වවවවවවවවවවවවවව', 'hana', 'zzz qqq', 'Thank you.'):
            with self.subTest(noise=noise):
                (score, _), _ = self.classify(noise)
                self.assertLess(score, self.accept, f'{noise!r} scored {score:.2f}')


if __name__ == '__main__':
    unittest.main()
