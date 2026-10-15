"""It uses the web instead of merely opening it (Day 11): read, looked up, priced -- and
a form filled where you can see it, never submitted, never a password or a card."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent as agent_module
agent_module.HOUSEKEEPING_QUIET = 0     # the real wait is for a person; not here
import browser
import permissions
import responses
from agent import Agent
from assistant_core import Store, Tools

CARDS = [
    {'name': 'iPhone 16 Pro', 'from': 'Rs. 294,575', 'rupees': 294575, 'shops': 9, 'url': 'a'},
    {'name': 'iPhone 16', 'from': 'Rs. 225,500', 'rupees': 225500, 'shops': 22, 'url': 'b'},
    {'name': 'Samsung Galaxy S25', 'from': 'Rs. 167,500', 'rupees': 167500, 'shops': 10,
     'url': 'c'},
]


class Handing:
    """A stand-in browser that hands back what the page would have given."""

    def __init__(self, result):
        self.result = result

    def run(self, work, timeout=90):
        return self.result


class ReadingTests(unittest.TestCase):
    def test_the_lines_that_answer_not_the_menu(self):
        page = '\n'.join(['Home', 'Shop', 'Blog', 'Login'] + ['filler text here'] * 30 +
                         ['iPhone 16', 'from LKR 225,500', 'more filler'])
        found = browser.excerpt(page, 'iphone 16 price')
        self.assertTrue(found.startswith('iPhone 16'))
        self.assertIn('225,500', found, 'the line after the name comes too')
        self.assertNotIn('Login', found)

    def test_prices_closest_name_first_and_only_what_was_asked(self):
        found = browser.prices('iphone 16', worker=Handing(CARDS))
        self.assertEqual([c['name'] for c in found], ['iPhone 16', 'iPhone 16 Pro'])
        self.assertEqual(browser.prices('rice', worker=Handing(CARDS)), [],
                         'measured: "price of rice" was answered with iPhones')

    def test_wikipedia_in_the_language_asked(self):
        replies = [MagicMock(json=lambda: {'query': {'search': [{'title': 'සීගිරිය'}]}}),
                   MagicMock(json=lambda: {'query': {'pages': {'1': {
                       'title': 'සීගිරිය', 'extract': 'සීගිරිය බලකොටුවකි.\nකාශ්‍යප රජු.'}}}})]
        with patch.object(browser.requests, 'get', side_effect=replies) as get:
            found = browser.wikipedia('සීගිරිය')
        self.assertTrue(get.call_args_list[0].args[0].startswith('https://si.wikipedia.org'))
        self.assertEqual(found['results'][0]['site'], 'si.wikipedia.org')
        self.assertIn('කාශ්‍යප', found['results'][0]['excerpt'])

    def test_wikipedia_down_is_said(self):
        with patch.object(browser.requests, 'get', side_effect=browser.requests.ConnectionError()):
            with self.assertRaises(browser.BrowserError):
                browser.wikipedia('anything')


class SafetyTests(unittest.TestCase):
    def test_the_tiers(self):
        for tool in ('look_up', 'check_price', 'read_web_page'):
            with self.subTest(tool=tool):
                self.assertEqual(permissions.tier(tool), permissions.READ)
                self.assertIn(tool, agent_module.UNTRUSTED_TOOLS, 'the web is someone else')
        self.assertEqual(permissions.tier('fill_web_form'), permissions.LOCAL)

    def test_a_page_cannot_ask_for_a_form_to_be_filled(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'a.db')
            agent = Agent(store, Tools(store, Path(folder) / 'w'))
            job = {'tainted': True, 'prompt': 'who painted the mona lisa'}
            self.assertTrue(agent.refusal(job, 'fill_web_form'))
            job['prompt'] = 'fill the contact form on example.com with my name'
            self.assertEqual(agent.refusal(job, 'fill_web_form'), '')

    def test_secret_fields_and_payment_pages_are_recognised(self):
        for described in ('password', 'card number cc-number', 'cvv', 'OTP code', 'PIN'):
            with self.subTest(described=described):
                self.assertTrue(browser.SECRET_FIELD.search(described))
        for ordinary in ('Full name', 'Email', 'Message', 'Phone'):
            with self.subTest(ordinary=ordinary):
                self.assertFalse(browser.SECRET_FIELD.search(ordinary))
        self.assertTrue(browser.PAYMENT_PAGE.search('https://shop.lk/checkout'))


FORM = '''<html><head><title>Contact</title></head><body><form id="f"
  onsubmit="document.title = 'SUBMITTED'; return false;">
  <label>Full name <input name="name"></label>
  <label>Password <input type="password" name="pw"></label>
  <label>Message <textarea name="message"></textarea></label>
  <button type="submit">Send</button></form>
  <main><p>The opening hours are nine to five.</p></main></body></html>'''


class RealBrowserTests(unittest.TestCase):
    """The installed Chrome, headless, on pages written here."""

    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('playwright not installed')
        cls.temp = tempfile.TemporaryDirectory()
        cls.worker = browser.Worker(Path(cls.temp.name) / 'profile', headless=True, idle=30)

    @classmethod
    def tearDownClass(cls):
        cls.worker.jobs.put((lambda context: None, MagicMock(), {}))
        cls.worker.idle = 0.01
        if cls.worker.thread:
            cls.worker.thread.join(10)
        cls.temp.cleanup()

    def page(self, name, html):
        path = Path(self.temp.name) / name
        path.write_text(html, encoding='utf-8')
        return path.as_uri()

    def test_a_page_is_read(self):
        long = FORM.replace('<main>', '<main>' + '<p>About our shop and its history.</p>' * 20)
        with patch.object(browser, 'look_at') as look:
            found = browser.read(self.page('hours.html', long), 'opening hours',
                                 worker=self.worker)
        self.assertIn('nine to five', found['excerpt'])
        look.assert_not_called()

    def test_a_page_of_pictures_goes_to_the_vision_model(self):
        with patch.object(browser, 'look_at', return_value='A poster: open nine to five.') as look:
            found = browser.read(self.page('poster.html', '<html><body><p>Hi</p></body></html>'),
                                 'opening hours', worker=self.worker)
        self.assertEqual(found['seen'], 'A poster: open nine to five.')
        self.assertTrue(look.call_args.args[0].startswith(b'\xff\xd8'), 'a JPEG screenshot')

    def test_a_form_is_filled_and_never_submitted(self):
        url = self.page('contact.html', FORM)
        report = browser.fill(url, {'Full name': 'Chamindu', 'Password': 'hunter2',
                                    'Message': 'Hello', 'Fax': '1'}, worker=self.worker)
        self.assertEqual(report['filled'], ['Full name', 'Message'])
        self.assertEqual(report['refused'], ['Password'])
        self.assertEqual(report['not_found'], ['Fax'])
        self.assertFalse(report['submitted'])
        title, password = self.worker.run(lambda context: (
            context.pages[-1].title(), context.pages[-1].input_value('[name=pw]')))
        self.assertEqual(title, 'Contact', 'submit was never pressed')
        self.assertEqual(password, '', 'nothing typed into the password field')

    def test_a_payment_page_is_never_filled(self):
        url = self.page('checkout.html', FORM.replace('<title>Contact', '<title>Checkout'))
        with self.assertRaises(browser.BrowserError):
            browser.fill(url, {'Full name': 'x'}, worker=self.worker)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'a.db')
        self.store.set_config('model', 'test')
        self.agent = Agent(self.store, Tools(self.store, Path(self.temp.name) / 'w'))

    def tearDown(self):
        for job_id in list(self.agent.threads):
            self.agent.wait(job_id)
        self.temp.cleanup()

    def test_a_price_is_said_by_template_not_by_the_model(self):
        """Measured: the model turned Rs. 167,500 into "less than a lakh"."""
        with patch.object(browser, 'prices', return_value=CARDS[2:]) as prices, \
                patch.object(self.agent, 'generate') as generate:
            job_id = self.agent.start('galaxy s25 eka kiyada?')
            self.agent.wait(job_id)
        prices.assert_called_once_with('galaxy s25')
        generate.assert_not_called()
        answer = self.agent.snapshot(job_id)['answer']
        self.assertIn('රුපියල් 167,500', answer)
        self.assertIn('PricePlease', answer)

    def test_kiyada_is_not_always_a_price(self):
        for text in ('battery eka kiyada?', 'wela kiyada?', '2 + 2 kiyada?', 'who am I?'):
            with self.subTest(text=text), patch.object(browser, 'prices') as prices:
                self.assertIsNone(self.agent.price_answer('j', text))
                prices.assert_not_called()

    def test_a_question_of_fact_brings_wikipedia_and_names_it(self):
        found = {'results': [{'title': 'Mona Lisa', 'excerpt': 'by Leonardo da Vinci'}]}
        seen = {}

        def generate(job_id, model, messages, think, deep):
            seen['text'] = messages[-1]['content']
            return {'role': 'assistant', 'content': 'Leonardo da Vinci painted it.'}
        with patch.object(browser, 'wikipedia', return_value=found), \
                patch.object(self.agent, 'generate', side_effect=generate):
            job_id = self.agent.start('who painted the mona lisa?')
            self.agent.wait(job_id)
        job = self.agent.snapshot(job_id)
        self.assertIn('Leonardo da Vinci', seen['text'])
        self.assertIn('data, not instructions', seen['text'])
        self.assertTrue(job['answer'].endswith('(Wikipedia)'))

    def test_a_stray_article_is_not_used_and_sinhala_is_asked_in_english(self):
        """Measured: "kumana jathika udyanaya" found Horton Plains."""
        stray = {'results': [{'title': 'Horton Plains National Park', 'excerpt': 'x'}]}
        right = {'results': [{'title': 'Kumana National Park', 'excerpt': 'south-east'}]}
        with patch.object(browser, 'wikipedia', side_effect=[stray, right]) as wikipedia, \
                patch.object(self.agent, 'translate', return_value='Kumana National Park'):
            found = self.agent.web_excerpts('kumana jathika udyanaya kohedha?')
        self.assertEqual(wikipedia.call_args_list[0].args[0], 'kumana jathika udyanaya')
        self.assertIn('Kumana National Park', found[0]['text'])

    def test_questions_about_us_are_not_looked_up(self):
        with patch.object(browser, 'wikipedia') as wikipedia:
            for text in ('who are you?', 'ඔයා කවුද?', 'who am I', 'what time is it'):
                self.assertEqual(self.agent.web_excerpts(text), [])
        wikipedia.assert_not_called()

    def test_how_much_is_not_the_time_or_the_battery(self):
        """Measured: "iPhone 16 එක කීයද?" was told it was two o'clock."""
        import router
        for text in ('iPhone 16 එක කීයද?', 'galaxy s25 eka kiyada', 'යාපනය කොහෙද?'):
            with self.subTest(text=text):
                self.assertEqual(router.route(text)['action'], 'model')
        for text in ('දැන් කීයද', 'වෙලාව කීයද', 'බැටරිය කීයද', 'battery eka kiyada'):
            with self.subTest(text=text):
                self.assertEqual(router.route(text)['action'], 'command')

    def test_every_known_phrasing_keeps_its_own_word(self):
        import router
        for name, words in router.OWN_WORDS.items():
            for phrase in router.COMMANDS[name]:
                with self.subTest(phrase=phrase):
                    self.assertTrue(words.search(phrase))

    def test_prices_said_in_both_languages(self):
        cards = [dict(CARDS[0], name='MacBook Air 15" M3')]
        self.assertIn('15 inch', responses.say_prices(cards, 'en'))
        self.assertIn('කඩ 9ක', responses.say_prices(cards, 'si'))


if __name__ == '__main__':
    unittest.main()
