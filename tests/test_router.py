"""Command routing: does the right action fire, and does the wrong one stay silent.

Every transcript here came out of Whisper for a real spoken Sinhala command. The router
sits between speech and the model, so a mistake here is a mistake the user feels
directly — the wrong action, or none at all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from router import ACCEPT, CONFIDENT, COMMANDS, extract_duration, match, route


class MatchingTests(unittest.TestCase):
    def test_real_transcripts_reach_the_right_command(self):
        """Tamil, Latin and accented spellings all of real recorded commands."""
        cases = [
            ('System ege tattvaya kianne.', 'system_status'),
            ('அம்மது மேசேஜ் இக்காக் யாவன்', 'send_message'),
            ('Minitu dahayaking matak kerana.', 'reminder'),
            ('சிந்துவக் தான்ன', 'play_music'),
            ('வோலியும் இக்க வேடிகரான்', 'volume_up'),
            ('Kromn ekki ofan krönnu', 'open_app'),
            ('மகே சடகன் பென்னன்ன', 'list_notes'),
            ('E-mail barane.', 'email'),
            ('Baríganegu lókkarann', 'lock_pc'),
            ('ஜாவிஸ் ஓயாட்ட முனவாது கரண்ண புழுவா', 'capabilities'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                name, score, _, _ = match(text)
                self.assertEqual(name, expected, f'{text!r} -> {name} at {score:.2f}')

    def test_sinhala_script_matches(self):
        for text, expected in [('අද කාලගුණය කොහොමද', 'weather'),
                               ('බැටරිය කීයද', 'battery'),
                               ('ස්ක්‍රීන්ෂොට් එකක් ගන්න', 'screenshot')]:
            with self.subTest(text=text):
                self.assertEqual(match(text)[0], expected)

    def test_every_command_matches_each_of_its_own_phrasings(self):
        """A phrasing that does not match its own command is a typo in the table."""
        for name, phrasings in COMMANDS.items():
            for phrase in phrasings:
                with self.subTest(command=name, phrase=phrase):
                    best, score, _, _ = match(phrase)
                    self.assertEqual(best, name, f'{phrase!r} matched {best} at {score:.2f}')


class RoutingTests(unittest.TestCase):
    def test_confident_matches_act_directly(self):
        outcome = route('system eke thathwaya kiyanna')
        self.assertEqual(outcome['action'], 'command')
        self.assertEqual(outcome['command'], 'system_status')

    def test_weak_matches_go_to_the_model_not_to_an_action(self):
        outcome = route('can you help me think about something complicated')
        self.assertEqual(outcome['action'], 'model')

    def test_noise_is_never_acted_on(self):
        """Wrong actions are unrecoverable; asking again costs a second."""
        for noise in ('hana', 'Thank you.', 'වවවවවවවව', 'ven ven ven', ''):
            with self.subTest(noise=noise):
                self.assertNotEqual(route(noise)['action'], 'command')

    def test_ambiguity_is_refused_rather_than_guessed(self):
        """Two commands scoring within MARGIN is not a decision, it is a coin toss.

        Driven through match() directly: finding a naturally ambiguous utterance is
        fiddly, and what matters is that the guard fires when the scores are close.
        """
        from unittest.mock import patch
        with patch('router.match', return_value=('volume_up', .82, 'volume_down', .80)):
            outcome = route('some utterance')
        self.assertEqual(outcome['action'], 'unclear')
        self.assertIn('ambiguous', outcome['reason'])

    def test_a_clear_winner_is_acted_on(self):
        from unittest.mock import patch
        with patch('router.match', return_value=('volume_up', .82, 'volume_down', .60)):
            outcome = route('some utterance')
        self.assertEqual(outcome['action'], 'command')

    def test_thresholds_stay_in_their_measured_order(self):
        self.assertLess(ACCEPT, CONFIDENT)
        self.assertGreater(CONFIDENT, .55, 'below the worst wrong match observed')


class DurationTests(unittest.TestCase):
    def test_sinhala_and_english_durations(self):
        cases = [
            ('Minitu dahayaking matak kerana.', 600),
            ('minitthu pahakin mathak karanna', 300),
            ('remind me in 5 minutes', 300),
            ('remind me in 2 hours', 7200),
            ('remind me in 30 seconds', 30),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_duration(text), expected)

    def test_compound_numbers_are_refused_not_guessed(self):
        """"visi paha" is twenty-five. Reading it as twenty sets the wrong reminder."""
        self.assertIsNone(extract_duration('minitthu visi pahakin mathak karanna'))

    def test_no_duration_means_no_duration(self):
        for text in ('heta ude atata mathak karanna', 'send a message to amma', ''):
            with self.subTest(text=text):
                self.assertIsNone(extract_duration(text))

    def test_a_reminder_without_a_time_is_handed_to_the_model(self):
        outcome = route('heta ude atata mathak karanna')
        self.assertNotEqual(outcome['action'], 'command')


if __name__ == '__main__':
    unittest.main()


class ContentExtractionTests(unittest.TestCase):
    """Separating the order from what was asked for.

    From the live log: "සටහනක් තබන්න අද රෑට කෑම තියන්න එපා කියලා" scored 0.366 because
    the note drowned the command, so it went to the model, which announced it had saved
    a note and never called the tool. The database had none.
    """

    def test_the_command_survives_a_long_attachment(self):
        from router import CONFIDENT
        outcome = route('සටහනක් තබන්න අද රෑට කෑම තියන්න එපා කියලා')
        self.assertEqual(outcome['command'], 'save_note')
        self.assertGreaterEqual(outcome['score'], CONFIDENT)
        self.assertEqual(outcome['action'], 'command')

    def test_note_content_excludes_the_order(self):
        outcome = route('සටහනක් තබන්න අද රෑට කෑම තියන්න එපා කියලා')
        self.assertEqual(outcome['content'], 'අද රෑට කෑම තියන්න එපා')

    def test_content_words_that_sound_like_the_order_are_kept(self):
        """තබන්න is the order and තියන්න may be the note. Eating the note is worse."""
        outcome = route('සටහනක් තබන්න අද රෑට කෑම තියන්න එපා කියලා')
        self.assertIn('තියන්න', outcome['content'])

    def test_reminder_content_is_separated_from_the_time(self):
        outcome = route('මිනිත්තු දහයකින් මතක් කරන්න ලයිට් බිල ගෙවන්න')
        self.assertEqual(outcome['seconds'], 600)
        self.assertEqual(outcome['content'], 'ලයිට් බිල ගෙවන්න')

    def test_a_bare_order_asks_instead_of_saving_nothing(self):
        for text, detail in [('සටහනක් තබන්න', 'note'),
                             ('මැසේජ් එකක් යවන්න', 'message')]:
            with self.subTest(text=text):
                outcome = route(text)
                self.assertEqual(outcome['action'], 'ask')
                self.assertEqual(outcome['detail'], detail)

    def test_message_content_is_separated(self):
        outcome = route('අම්මට මැසේජ් එකක් යවන්න මම රාත්‍රී එනවා')
        self.assertEqual(outcome['command'], 'send_message')
        self.assertIn('එනවා', outcome['content'])

    def test_short_commands_are_unaffected(self):
        for text, expected in [('දැන් වෙලාව කීයද', 'time'), ('බැටරිය කීයද', 'battery')]:
            with self.subTest(text=text):
                outcome = route(text)
                self.assertEqual(outcome['command'], expected)
                self.assertEqual(outcome['action'], 'command')
