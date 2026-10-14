"""Multi-step tasks: the plan is checked, the steps are code, and nothing is half done
without saying so (Day 20)."""
import datetime as dt
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import responses
import tasks
from assistant_core import Store, Tools
from pdf_fixture import invoice, make_pdf

INVOICE_SENTENCE = 'Find every PDF invoice from last month, total them, save a summary, email it to me'
SINHALA_SENTENCE = 'පසුගිය මාසයේ PDF ඉන්වොයිස් ඔක්කොම හොයලා, එකතුව හදලා, සාරාංශයක් සේව් කරලා, මට ඊමේල් කරන්න'


def stamp(year, month, day):
    return dt.datetime(year, month, day, 12).timestamp()


class TotalTests(unittest.TestCase):
    def test_the_grand_total_not_the_subtotal(self):
        text = 'Subtotal Rs. 15,254.24\nVAT Rs. 2,745.76\nGrand Total Rs. 18,000.00'
        self.assertEqual(tasks.find_total(text), (18000.0, 'LKR'))

    def test_other_labels_and_currencies(self):
        for text, expected in [('Total Amount Due: $1,250.50', (1250.5, 'USD')),
                               ('Amount due LKR 4500', (4500.0, 'LKR')),
                               ('Sub Total 100.00\nTotal 118.00', (118.0, 'LKR')),
                               ('මුළු එකතුව රු. 5,000.00', (5000.0, 'LKR')),
                               ('Balance Due 2,000.00 USD', (2000.0, 'USD'))]:
            with self.subTest(text=text):
                self.assertEqual(tasks.find_total(text), expected)

    def test_no_total_is_none(self):
        self.assertIsNone(tasks.find_total('Meeting notes: discuss the budget next week.'))

    def test_a_model_figure_must_be_printed_in_the_document(self):
        text = 'Payable now: Rs. 12,345.00'
        self.assertTrue(tasks.amount_appears(12345.0, text))
        self.assertFalse(tasks.amount_appears(12000.0, text))
        self.assertFalse(tasks.amount_appears(345.0, text), 'part of a number is not the number')


class DateTests(unittest.TestCase):
    def test_formats(self):
        for text, expected in [('Invoice Date: 2026-09-14', dt.date(2026, 9, 14)),
                               ('Date: 14/09/2026', dt.date(2026, 9, 14)),
                               ('Issued 3 September 2026', dt.date(2026, 9, 3)),
                               ('Sep 21, 2026', dt.date(2026, 9, 21))]:
            with self.subTest(text=text):
                self.assertEqual(tasks.document_date(text), expected)

    def test_the_labelled_date_wins(self):
        text = 'Due by 2026-10-30\nInvoice Date: 2026-09-14'
        self.assertEqual(tasks.document_date(text), dt.date(2026, 9, 14))

    def test_periods(self):
        today = dt.date(2026, 10, 15)
        self.assertEqual(tasks.period_range('last_month', today),
                         (dt.date(2026, 9, 1), dt.date(2026, 10, 1)))
        self.assertEqual(tasks.period_range('last_month', dt.date(2026, 1, 10)),
                         (dt.date(2025, 12, 1), dt.date(2026, 1, 1)))
        self.assertEqual(tasks.period_range('last_week', today),
                         (dt.date(2026, 10, 5), dt.date(2026, 10, 12)))
        self.assertEqual(tasks.period_range('all', today), (None, None))


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.db')
        self.tools = Tools(self.store, self.root / 'workspace')
        self.folder = self.root / 'documents'
        self.steps = tasks.Steps(self.store, self.tools, folders=[self.folder])

    def tearDown(self):
        self.temp.cleanup()

    def last_month(self):
        start, _ = tasks.period_range('last_month')
        return start

    def make_month(self):
        """Three invoices last month, one this month, and a non-invoice last month."""
        start = self.last_month()
        day = lambda n: (start + dt.timedelta(days=n)).isoformat()
        invoice(self.folder / 'acme-1.pdf', 'A1', day(2), [('Paper', 10000)], 11800)
        invoice(self.folder / 'acme-2.pdf', 'A2', day(12), [('Ink', 4000)], 4720,
                label='Total Amount Due')
        # No date in the text: the file's own date decides.
        make_pdf(self.folder / 'scan.pdf', ['TAX INVOICE', 'Grand Total Rs. 2,500.00'],
                 modified=dt.datetime.combine(start + dt.timedelta(days=20),
                                              dt.time(12)).timestamp())
        invoice(self.folder / 'this-month.pdf', 'B1', dt.date.today().replace(day=1).isoformat(),
                [('Toner', 9000)], 10620)
        make_pdf(self.folder / 'minutes.pdf', ['Meeting minutes', f'Date: {day(5)}',
                                               'Total attendance 12'])


class FindTests(WorkspaceCase):
    def test_last_months_invoices_only(self):
        self.make_month()
        found = self.steps.find_documents('pdf', 'last_month', 'invoice')
        self.assertEqual([f['name'] for f in found['files']],
                         ['acme-1.pdf', 'acme-2.pdf', 'scan.pdf'])

    def test_the_date_printed_beats_the_download_date(self):
        """A September invoice downloaded in October is still September's."""
        start = self.last_month()
        invoice(self.folder / 'late.pdf', 'L1', (start + dt.timedelta(days=3)).isoformat(),
                [('x', 100)], 118, modified=time.time())
        found = self.steps.find_documents('pdf', 'last_month', 'invoice')
        self.assertEqual([f['name'] for f in found['files']], ['late.pdf'])

    def test_words_match_whole(self):
        """Seen live: "bill" inside "billing" put two purchase indents in the total."""
        self.assertFalse(tasks.mentions('Billing address: Colombo', ['bill']))
        self.assertTrue(tasks.mentions('Bill No. 442', ['bill']))
        self.assertTrue(tasks.mentions('TAX INVOICE', ['invoice']))
        self.assertTrue(tasks.mentions('Invoices for September', ['invoice']))
        self.assertTrue(tasks.mentions('විදුලි බිලේ මුදල', ['බිල']), 'Sinhala takes suffixes')

    def test_the_folder_setting_is_read_at_search_time(self):
        """Seen live: set after start-up, it was ignored, and Downloads was searched."""
        steps = tasks.Steps(self.store, self.tools)
        self.store.set_config('search_folders', [str(self.folder)])
        self.assertEqual(steps.folders, [self.folder])

    def test_an_unknown_period_is_refused(self):
        with self.assertRaises(tasks.StepError):
            self.steps.find_documents('pdf', 'last_fortnight', 'invoice')

    def test_a_missing_folder_is_not_an_error(self):
        steps = tasks.Steps(self.store, self.tools, folders=[self.root / 'nowhere'])
        self.assertEqual(steps.find_documents('pdf', 'all', '')['files'], [])


class TotalsTests(WorkspaceCase):
    def test_read_add_and_summarise(self):
        self.make_month()
        found = self.steps.find_documents('pdf', 'last_month', 'invoice')
        totals = self.steps.read_totals(found)
        self.assertEqual(sorted(i['amount'] for i in totals['items']), [2500.0, 4720.0, 11800.0])
        added = self.steps.add_up(totals)
        self.assertEqual(added, {'totals': {'LKR': 19020.0}, 'count': 3})
        summary = self.steps.write_summary('Invoices', [found, totals, added])
        written = Path(summary['path']).read_text(encoding='utf-8')
        self.assertTrue(Path(summary['path']).is_relative_to(self.tools.workspace))
        for expected in ('acme-1.pdf', '11,800.00', 'LKR 19,020.00', 'Total of 3'):
            self.assertIn(expected, written)

    def test_an_unreadable_total_is_reported_not_guessed(self):
        make_pdf(self.folder / 'odd.pdf', ['INVOICE', 'Thanks for shopping'])
        totals = self.steps.read_totals({'files': [{'path': str(self.folder / 'odd.pdf')}]})
        self.assertEqual(totals['items'], [])
        self.assertEqual(totals['unreadable'][0]['name'], 'odd.pdf')

    def test_the_model_is_believed_only_when_the_figure_is_printed(self):
        make_pdf(self.folder / 'odd.pdf', ['INVOICE', 'Pay us 7,350.00 by Friday'])
        entry = {'files': [{'path': str(self.folder / 'odd.pdf')}]}
        honest = tasks.Steps(self.store, self.tools, [self.folder],
                             ask_model=lambda p, s: {'amount': 7350, 'currency': 'LKR'})
        self.assertEqual(honest.read_totals(entry)['items'][0]['read_by'], 'model')
        invented = tasks.Steps(self.store, self.tools, [self.folder],
                               ask_model=lambda p, s: {'amount': 9999, 'currency': 'LKR'})
        self.assertEqual(invented.read_totals(entry)['items'], [])

    def test_two_currencies_are_kept_apart(self):
        items = [{'amount': 100, 'currency': 'LKR'}, {'amount': 5, 'currency': 'USD'},
                 {'amount': 50, 'currency': 'LKR'}]
        self.assertEqual(self.steps.add_up(items)['totals'], {'LKR': 150, 'USD': 5})


class PlanTests(unittest.TestCase):
    GOOD = [{'step': 'find_documents', 'args': {'type': 'pdf', 'period': 'last_month',
                                                 'contains': 'invoice'}},
            {'step': 'read_totals', 'args': {'files': '$1'}},
            {'step': 'add_up', 'args': {'items': '$2'}},
            {'step': 'write_summary', 'args': {'title': 'Invoices', 'parts': ['$2', '$3']}},
            {'step': 'email_me', 'args': {'subject': 'Invoices', 'body': '$4'}}]

    def test_a_good_plan_passes(self):
        self.assertEqual(len(tasks.validate(self.GOOD)), 5)

    def test_what_is_refused(self):
        cases = {
            'unknown step': [{'step': 'run_shell', 'args': {'command': 'del *'}}],
            'missing input': [{'step': 'read_totals', 'args': {}}],
            'forward reference': [{'step': 'read_totals', 'args': {'files': '$2'}},
                                  {'step': 'find_documents', 'args': {}}],
            'self reference': [{'step': 'read_totals', 'args': {'files': '$1'}}],
            'too long': [{'step': 'news', 'args': {}}] * (tasks.STEP_LIMIT + 1),
            'empty': [],
        }
        for name, steps in cases.items():
            with self.subTest(name):
                with self.assertRaises(tasks.PlanError):
                    tasks.validate(steps)

    def test_invented_inputs_are_dropped(self):
        steps = tasks.validate([{'step': 'news', 'args': {'url': 'http://evil.example'}}])
        self.assertEqual(steps[0]['args'], {})

    def test_the_request_has_the_last_word(self):
        """Measured: the model searched for every PDF, dropping 'invoice'."""
        dropped = [{'step': 'find_documents', 'args': {'type': 'pdf', 'period': 'this_year',
                                                       'contains': ''}}]
        for request in (INVOICE_SENTENCE, SINHALA_SENTENCE):
            with self.subTest(request=request[:20]):
                fixed = tasks.hold_to_request([dict(s, args=dict(s['args'])) for s in dropped],
                                              request)
                self.assertEqual(fixed[0]['args']['contains'], 'invoice')
                self.assertEqual(fixed[0]['args']['period'], 'last_month')

    def test_the_summary_covers_everything_before_it(self):
        """Seen live: the model gave the summary only the total, "Total of 5" of what."""
        steps = [dict(s, args=dict(s['args'])) for s in self.GOOD]
        steps[3]['args']['parts'] = ['$3']
        fixed = tasks.hold_to_request(steps, INVOICE_SENTENCE)
        self.assertEqual(fixed[3]['args']['parts'], ['$1', '$2', '$3'])

    def test_plan_uses_the_model_then_checks_it(self):
        steps = tasks.plan(INVOICE_SENTENCE, lambda prompt, schema: {'steps': self.GOOD})
        self.assertEqual([s['step'] for s in steps][-1], 'email_me')
        with self.assertRaises(tasks.PlanError):
            tasks.plan('x', lambda prompt, schema: {'steps': [{'step': 'format_disk', 'args': {}}]})

    def test_which_plans_wait_for_a_yes(self):
        self.assertTrue(tasks.needs_yes(self.GOOD))
        self.assertTrue(tasks.needs_yes([{'step': 'email_me', 'args': {}}]),
                        'anything leaving the computer waits, however short')
        self.assertFalse(tasks.needs_yes([{'step': 'weather', 'args': {}},
                                          {'step': 'save_note', 'args': {}}]))


class MultiStepTests(unittest.TestCase):
    def test_sentences_that_ask_for_several_things(self):
        for text in (INVOICE_SENTENCE, SINHALA_SENTENCE,
                     'check the weather then save a note',
                     'කාලගුණය බලලා, ඩොලර් රේට් එක බලලා සටහනක් දාන්න'):
            with self.subTest(text=text):
                self.assertTrue(tasks.looks_multi_step(text))

    def test_ordinary_commands_stay_ordinary(self):
        for text in ('අද කාලගුණය කොහොමද', 'what time is it', 'save a note buy milk',
                     'අම්මාට මැසේජ් එකක් යවන්න මම එනවා කියලා',
                     'හැමදාම රෑ දහයට ෆෝන් එක චාජ් කරන්න මතක් කරන්න'):
            with self.subTest(text=text):
                self.assertFalse(tasks.looks_multi_step(text))


class FakeSteps:
    """Steps that do what the test says, and record what ran."""

    def __init__(self, behaviour):
        self.behaviour, self.ran = behaviour, []

    def run(self, name, args):
        self.ran.append(name)
        action = self.behaviour.get(name, lambda **a: {'ok': name})
        return action(**args) if callable(action) else action.pop(0)(**args)


def plan_of(*names, **args):
    return [{'step': name, 'args': args.get(name, {})} for name in names]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.updates = []

    def tearDown(self):
        self.temp.cleanup()

    def run_task(self, steps, behaviour, answer=None, wall_clock=30):
        fake = FakeSteps(behaviour)
        task = tasks.Task('test', steps, 'en')

        def changed(current):
            self.updates.append(current.state)
            if current.state == 'needs_you' and answer:
                runner.reply(answer)

        runner = tasks.Runner(fake, on_change=changed, wall_clock=wall_clock, folder=self.folder)
        runner.start(task).join(10)
        return task, fake

    def test_all_steps_run_in_order_and_the_journal_is_saved(self):
        task, fake = self.run_task(plan_of('weather', 'dollar_rate', 'news'), {})
        self.assertEqual(task.state, 'done')
        self.assertEqual(fake.ran, ['weather', 'dollar_rate', 'news'])
        saved = tasks.recent(folder=self.folder)[0]
        self.assertEqual([s['state'] for s in saved['steps']], ['done'] * 3)

    def test_results_flow_between_steps(self):
        steps = plan_of('find_documents', 'read_totals', read_totals={'files': '$1'})
        seen = {}
        task, _ = self.run_task(steps, {
            'find_documents': lambda **a: {'files': ['a.pdf']},
            'read_totals': lambda files: seen.setdefault('files', files)})
        self.assertEqual(seen['files'], {'files': ['a.pdf']})

    def test_a_temporary_failure_is_retried_once(self):
        attempts = []

        def flaky(**_):
            attempts.append(1)
            if len(attempts) == 1:
                raise tasks.StepError('offline', retryable=True)
            return {'rate': 300}
        task, _ = self.run_task(plan_of('dollar_rate'), {'dollar_rate': flaky})
        self.assertEqual(task.state, 'done')
        self.assertEqual(len(attempts), 2)

    def test_a_failure_waits_for_you_then_skip_carries_on(self):
        def broken(**_):
            raise tasks.StepError('no mail app')
        steps = plan_of('email_me', 'save_note', 'news', save_note={'title': 'x', 'content': '$1'})
        task, fake = self.run_task(steps, {'email_me': broken}, answer='skip')
        self.assertIn('needs_you', self.updates)
        self.assertEqual([s['state'] for s in task.steps], ['skipped', 'skipped', 'done'],
                         'the step that needed the skipped one is skipped too')
        self.assertEqual(task.state, 'done')

    def test_stop_ends_it_and_says_what_was_not_run(self):
        def broken(**_):
            raise tasks.StepError('disk full')
        task, _ = self.run_task(plan_of('news', 'write_summary', 'save_note'),
                                {'write_summary': broken}, answer='stop')
        self.assertEqual(task.state, 'stopped')
        self.assertEqual([s['state'] for s in task.steps], ['done', 'failed', 'not run'])

    def test_stop_from_outside_between_steps(self):
        release, started = threading.Event(), threading.Event()
        fake = FakeSteps({'news': lambda **a: started.set() or release.wait(5) and {'ok': 1}})
        task = tasks.Task('test', plan_of('news', 'weather', 'dollar_rate'), 'en')
        runner = tasks.Runner(fake, folder=self.folder)
        thread = runner.start(task)
        started.wait(5)
        runner.stop()
        release.set()
        thread.join(10)
        self.assertEqual(task.state, 'stopped')
        self.assertEqual(fake.ran, ['news'], 'nothing new starts after a stop')

    def test_the_console_can_stop_it(self):
        release = threading.Event()
        fake = FakeSteps({'news': lambda **a: release.wait(5) and {'ok': 1}})
        task = tasks.Task('test', plan_of('news', 'weather'), 'en')
        runner = tasks.Runner(fake, folder=self.folder)
        thread = runner.start(task)
        tasks.request_stop(task.id, folder=self.folder)
        release.set()
        thread.join(10)
        self.assertEqual(task.state, 'stopped')

    def test_the_wall_clock(self):
        slow = lambda **a: time.sleep(0.6) or {'ok': 1}
        task, fake = self.run_task(plan_of('news', 'weather', 'dollar_rate'),
                                   {'news': slow, 'weather': slow}, wall_clock=0.5)
        self.assertEqual(task.state, 'timed_out')
        self.assertLess(len(fake.ran), 3)

    def test_silence_after_a_failure_stops_at_the_wall_clock(self):
        def broken(**_):
            raise tasks.StepError('no')
        task, _ = self.run_task(plan_of('news'), {'news': broken}, wall_clock=1.5)
        self.assertEqual(task.state, 'stopped')

    def test_one_task_at_a_time(self):
        release = threading.Event()
        runner = tasks.Runner(FakeSteps({'news': lambda **a: release.wait(5)}),
                              folder=self.folder)
        thread = runner.start(tasks.Task('a', plan_of('news'), 'en'))
        with self.assertRaises(ValueError):
            runner.start(tasks.Task('b', plan_of('news'), 'en'))
        release.set()
        # Finish before the folder is removed: the journal is written as it ends.
        thread.join(5)


class SpokenTests(unittest.TestCase):
    STEPS = [{'step': 'find_documents', 'args': {'type': 'pdf', 'period': 'last_month',
                                                  'contains': 'invoice'}},
             {'step': 'read_totals', 'args': {}}, {'step': 'add_up', 'args': {}},
             {'step': 'write_summary', 'args': {}}, {'step': 'email_me', 'args': {}}]

    def test_the_plan_read_out(self):
        si = responses.say_plan(self.STEPS, 'si', ask=True)
        self.assertIn('පියවර පහකින්', si)
        self.assertIn('පසුගිය මාසයේ PDF ඉන්වොයිස් හොයනවා', si)
        self.assertTrue(si.endswith('කරන්නද?'))
        en = responses.say_plan(self.STEPS, 'en', ask=True)
        self.assertIn("find last month's pdf invoices", en.lower())
        self.assertIsNone(re.search(r'\d', si + en))

    def test_the_report_comes_from_the_journal(self):
        task = {'state': 'done', 'steps': [
            {'number': 1, 'step': 'find_documents', 'state': 'done', 'result': {'files': [1, 2, 3]}},
            {'number': 2, 'step': 'read_totals', 'state': 'done', 'result': {'unreadable': []}},
            {'number': 3, 'step': 'add_up', 'state': 'done', 'result': {'totals': {'LKR': 19020}}},
            {'number': 4, 'step': 'write_summary', 'state': 'done', 'result': {}},
            {'number': 5, 'step': 'email_me', 'state': 'skipped', 'result': None}]}
        si = responses.say_task_report(task, 'si')
        for part in ('තුනක් හම්බවුණා', 'රුපියල් 19,020', 'සාරාංශය', 'පස්වනුව පියවර කළේ නෑ'):
            self.assertIn(part, si)
        self.assertNotIn('ඊමේල් එක ලෑස්තියි', si, 'a skipped step is never reported as done')

    def test_stopped_part_way(self):
        task = {'state': 'stopped', 'steps': [
            {'number': 1, 'step': 'news', 'state': 'done', 'result': {'headlines': []}},
            {'number': 2, 'step': 'weather', 'state': 'not run', 'result': None}]}
        self.assertIn('Stopped after one of two steps', responses.say_task_report(task, 'en'))


if __name__ == '__main__':
    unittest.main()
