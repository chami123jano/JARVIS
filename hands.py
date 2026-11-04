"""See and click (Day 18): JARVIS operates a program that has no API, the way you would.

    python hands.py calculator "7 x 8"          try it, printing every step
    python hands.py --look calculator           what JARVIS can see in that window

How it sees: Windows' accessibility interface (UI Automation) -- the window's real
buttons, menus and fields, each with its name and place -- not guesses at pixels. The
fast model picks the next step *from that numbered list*, by name; a 9B model is far
more reliable choosing "7. Multiply by" than reading coordinates off a screenshot.

How it acts, learnt from Phone Link's Call button (Day 8): the window is brought to the
front; the control is pressed through its own interface; and if the window did not change,
clicked with the mouse in real pixels, at that control and nowhere else.

What holds it back, in code:
  - at most ten actions a task; it stops the moment a step changes nothing twice, or the
    model is unsure, and says so
  - anything that cannot be taken back -- Send, Delete, Buy, Pay, Submit, Install, Sign
    out... -- waits for your "ඔව්"
  - never types into a password, card, PIN or code field; never works in a password
    manager or a banking app
  - every step is printed, and said in the console's subtitles, as it happens
Honest expectation, as the plan says: the least reliable thing JARVIS does. For real
annoyances only, never for anything that matters.
"""
import argparse
import ctypes
import json
import re
import sys
import time

MAX_ACTIONS = 10
# Room for every control: at 90, after Plus, Calculator's extra items pushed "Three" off
# the list, and 12 + 30 was re-planned into 12 + 12 (5 Nov).
MAX_LISTED = 250
SETTLE = 0.5
ACTIONABLE = {'ButtonControl', 'MenuItemControl', 'ListItemControl', 'TabItemControl',
              'CheckBoxControl', 'RadioButtonControl', 'HyperlinkControl', 'TreeItemControl',
              'ComboBoxControl', 'EditControl', 'SplitButtonControl', 'MenuBarControl'}
# Labels drawn with an icon font (private-use characters) say nothing a model can read.
ICON_ONLY = re.compile(r'^[-\s]*$')
IRREVERSIBLE = re.compile(
    r'\b(send|delete|remove|uninstall|install|buy|pay|purchase|checkout|order|submit|post|'
    r'publish|share|sign out|log ?out|format|erase|reset|empty|discard|transfer|confirm|'
    r'accept|agree|allow|restart|shut ?down|forward|reply all)\b|යවන්න|මකන්න|ගෙවන්න|'
    r'ඉවත් කරන්න', re.I)
SECRET = re.compile(r'pass(word|wd|code)?|card|cvv|cvc|otp|one.?time|\bpin\b|security.?code|'
                    r'මුරපද', re.I)
NO_GO = re.compile(r'keepass|bitwarden|1password|lastpass|dashlane|credential|'
                   r'\bbank\b|banking|wallet|paypal', re.I)


class HandsError(Exception):
    pass


def real_pixels():
    """Positions in real screen pixels, as UI Automation reports them (Day 8: display
    scaling moved a click elsewhere until this)."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


# ---------------------------------------------------------------- the window

def window_for(app=None, timeout=8):
    """The window to work in: the named application's (opened if need be) or, with no
    name, the one in front."""
    import uiautomation as auto
    import pc_control
    real_pixels()
    if not app:
        window = auto.GetForegroundControl()
        while window and window.GetParentControl() and \
                window.GetParentControl().ControlTypeName != 'PaneControl':
            window = window.GetParentControl()
        return window.GetTopLevelControl() if window else None
    found = pc_control.find_app(app)
    if not found:
        raise HandsError(f'no application matches {app!r}')
    name = found['name']
    for attempt in range(2):
        for window in auto.GetRootControl().GetChildren():
            if name.lower() in (window.Name or '').lower():
                return window
        if attempt == 0:
            pc_control.open_app(app)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(0.5)
                for window in auto.GetRootControl().GetChildren():
                    if name.lower() in (window.Name or '').lower():
                        time.sleep(0.8)       # let it finish drawing its controls
                        return window
    raise HandsError(f'{name} did not open a window')


def to_front(window):
    try:
        if window.GetWindowPattern().WindowVisualState == 2:      # minimised
            window.GetWindowPattern().SetWindowVisualState(0)
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(0x12, 0, 0, 0)                          # a tap of Alt lets us
        user32.keybd_event(0x12, 0, 2, 0)                          # bring it forward
        window.SetActive()
        window.SetFocus()
    except Exception:
        pass
    time.sleep(0.3)


def look(window):
    """What can be seen: {'title', 'controls': [{n, kind, name, id, enabled}], 'texts': [...]}
    -- the numbered controls are the only things a step may act on."""
    import uiautomation as auto
    controls, texts, handles = [], [], []
    for control, _ in auto.WalkControl(window, maxDepth=14):
        kind, name = control.ControlTypeName, (control.Name or '').strip()
        try:
            rect = control.BoundingRectangle
            visible = rect.width() > 0 and rect.height() > 0 and not control.IsOffscreen
        except Exception:
            visible = False
        if not visible or not name or ICON_ONLY.match(name):
            continue
        if kind in ACTIONABLE and len(controls) < MAX_LISTED:
            controls.append({'n': len(controls) + 1, 'kind': kind.replace('Control', ''),
                             'name': name[:80], 'id': control.AutomationId[:40],
                             'enabled': bool(control.IsEnabled)})
            handles.append(control)
        elif kind == 'TextControl' and len(texts) < 25 and name not in texts:
            texts.append(name[:120])
    return {'title': window.Name, 'controls': controls, 'texts': texts}, handles


def fingerprint(seen):
    return json.dumps([seen['texts'], [(c['name'], c['enabled']) for c in seen['controls']]],
                      ensure_ascii=False)


# ---------------------------------------------------------------- acting

def press(window, control):
    """Through the control's own interface; a mouse click at its centre only if it offers
    none -- and then only with its window in front and that control under the point.

    A press it accepted counts, changed screen or not. Measured 5 Nov: in 9 x 9 the second
    Nine leaves the display showing 9, a "nothing changed" was taken for a missed press,
    the mouse pressed it again, and 9 x 99 gave 891. The window comes to the front first
    (Phone Link ignored presses from behind, Day 8)."""
    to_front(window)
    for pattern, act in (('GetInvokePattern', lambda h: h.Invoke()),
                         ('GetTogglePattern', lambda h: h.Toggle()),
                         ('GetSelectionItemPattern', lambda h: h.Select()),
                         ('GetExpandCollapsePattern', lambda h: h.Expand())):
        try:
            handle = getattr(control, pattern)()
        except Exception:
            handle = None
        if not handle:
            continue
        try:
            act(handle)
            return 'pressed'
        except Exception:
            continue
    import uiautomation as auto
    rect = control.BoundingRectangle
    x, y = rect.xcenter(), rect.ycenter()
    under = auto.ControlFromPoint(x, y)
    if under is None or under.GetTopLevelControl().NativeWindowHandle != window.NativeWindowHandle:
        return 'covered'
    control.Click(simulateMove=False, waitTime=SETTLE)
    return 'clicked'


def type_into(window, control, text):
    if SECRET.search(f'{control.Name} {control.AutomationId}'):
        raise HandsError('that is a password or code field; I never type into one')
    to_front(window)
    try:
        control.GetValuePattern().SetValue(text)
        return 'typed'
    except Exception:
        control.Click(simulateMove=False, waitTime=0.2)
        control.SendKeys(text, interval=0.02, waitTime=SETTLE)
        return 'typed'


# ---------------------------------------------------------------- deciding

# "how" first, in words, then the steps: written straight into a list, 9 x 9 came out as
# Nine, Multiply by, Nine, Nine (891) and 250 - 75 without Equals (5 Nov).
PLAN_SCHEMA = {'type': 'object', 'required': ['how', 'steps', 'say'], 'properties': {
    'how': {'type': 'string'},
    'steps': {'type': 'array', 'maxItems': MAX_ACTIONS, 'items': {
        'type': 'object', 'required': ['action', 'control', 'text'], 'properties': {
            'action': {'type': 'string', 'enum': ['click', 'type']},
            'control': {'type': 'string'}, 'text': {'type': 'string'}}}},
    'say': {'type': 'string'}}}
# The working out comes first, the verdict last: with "done" first it answered "not done"
# while its own sentence said the result was correct (5 Nov).
CHECK_SCHEMA = {'type': 'object', 'required': ['should_show', 'shows', 'done', 'say'], 'properties': {
    'should_show': {'type': 'string'}, 'shows': {'type': 'string'},
    'done': {'type': 'boolean'}, 'say': {'type': 'string'}}}


def ask_model(prompt, schema, ask=None):
    if ask:
        return ask(prompt, schema)
    import requests
    import memory
    from assistant_core import ROOT, Store
    model = Store(ROOT / 'data' / 'jarvis.db').config('model', '') or 'qwen3.5:9b'
    try:
        response = requests.post('http://127.0.0.1:11434/api/chat', json={
            'model': model, 'stream': False, 'think': False, 'format': schema,
            'keep_alive': '30m', 'messages': [{'role': 'user', 'content': prompt}],
            'options': {'temperature': 0, 'num_ctx': memory.FAST_CTX, 'num_predict': 500}},
            timeout=(5, 90))
        response.raise_for_status()
        return json.loads(response.json()['message']['content'])
    except Exception as error:
        raise HandsError('the model could not decide what to do') from error


def plan(goal, seen, steps, ask=None):
    """Every step at once, as names from the window. Measured 5 Nov: asked one step at a
    time, qwen3.5 pressed 7, 8, then Multiply by four times, losing the order; and choosing
    by number, it wrote "Clear entry (13)" and picked another number."""
    names = sorted({c['name'] for c in seen['controls'] if c['enabled']})
    done = '; '.join(steps) or 'nothing yet'
    prompt = (
        f'You are operating the window "{seen["title"]}" for the user. The task: "{goal}"\n'
        f'Text showing in the window now: {seen["texts"][:20]}\n'
        f'Already done: {done}\n'
        f'Controls in the window, by name: {names}\n\n'
        '"how": first say in plain words what a person would press, one by one, from start '
        'to finish, ending with the step that shows the result -- in a calculator, Equals '
        '(measured: "7 x 8" planned as Seven, Multiply by, Eight, and no answer). If the '
        'window still holds an earlier entry or result that would spoil this task, clear it '
        'first. Only steps '
        'not already done. Then "steps": exactly those, in order, using control names '
        'exactly as written above. action "click", or "type" with the text to type into a '
        'text field. Think of the order a person would press them in, and press a number '
        'one digit at a time -- 100 is One, Zero, Zero (measured: "100 divided by 4" was '
        'planned as One, Zero, Divide by, Four). If the task cannot be '
        'done with these controls, return no steps and say why in "say".')
    planned = ask_model(prompt, PLAN_SCHEMA, ask)
    return dict(planned, steps=not_again(planned.get('steps') or [], steps))


def not_again(planned, done):
    """A second plan that starts over repeats what is done. Measured 5 Nov: after Seven,
    Multiply by, Eight, the next plan began Seven, Multiply by, Eight again -- 7 x 88. The
    steps already taken are dropped from its start, in code."""
    taken = [d.removeprefix('pressed ') for d in done if d.startswith('pressed ')]
    names = [str(p.get('control', '')).strip().lower() for p in planned]
    for length in range(min(len(taken), len(names)), 0, -1):
        if [t.lower() for t in taken[-length:]] == names[:length] or                 [t.lower() for t in taken[:length]] == names[:length]:
            return planned[length:]
    return planned


def finished(goal, seen, steps, ask=None, lang='en'):
    # Measured: "Display is 2.5" for "100 divided by 4" was called complete. Work the
    # answer out first, then compare.
    prompt = (f'The task was: "{goal}". Steps done: {"; ".join(steps)}.\n'
              f'Text showing in the window "{seen["title"]}" now: {seen["texts"][:20]}\n'
              '"should_show": the answer or state the task should end in. "shows": the answer '
              'or state the text above shows -- ignore everything else in the window. '
              '"done": true only if those two match. '
              '"say": one short sentence for the user -- the result if done, or what is wrong.')
    return ask_model(prompt, CHECK_SCHEMA, ask)


NUMBER = re.compile(r'-?\d+(?:[.,]\d+)?')


def numbers_agree(check):
    """If the expected result has a number, the window must show that number."""
    expected = [n.replace(',', '') for n in NUMBER.findall(str(check.get('should_show', '')))]
    shown = {n.replace(',', '') for n in NUMBER.findall(str(check.get('shows', '')))}
    if not expected:
        return True
    return expected[-1] in shown


def goal_of(text, app=None):
    """Only the task, for the planner: "calculator එකේ 64 / 8 ඔබන්න" -> "64 / 8".
    Measured 5 Nov: with the Sinhala words around it, 64 / 8 was planned without the Four."""
    goal = str(text)
    if app:
        goal = re.sub(rf'^\s*{re.escape(app)}\s*(එකේ|eke|ekē)?\s*', '', goal, flags=re.I)
        goal = re.sub(rf'\b(in|on)\s+(the\s+)?{re.escape(app)}\b', '', goal, flags=re.I)
    goal = re.sub(r'\s*(ක්ලික්\s*කරන්න|ඔබන්න|කරන්න|click(\s+on)?|press|obanna|karanna)\s*[.!]*$',
                  '', goal.strip(), flags=re.I)
    goal = re.sub(r'^\s*(click(\s+on)?|press)\s+', '', goal, flags=re.I)
    return goal.strip() or str(text)


def find(seen, handles, name):
    """The control with this name: exactly, then ignoring case, then by its start."""
    wanted = str(name).strip().lower()
    for test in (lambda c: c['name'].lower() == wanted,
                 lambda c: c['name'].lower().startswith(wanted) and len(wanted) >= 3):
        for index, control in enumerate(seen['controls']):
            if control['enabled'] and test(control):
                return control, handles[index]
    return None, None


class Task:
    """One request, planned once, done step by step with a look after each; paused, not
    ended, at anything irreversible."""

    def __init__(self, goal, app=None, report=print, ask=None, lang='en'):
        self.goal, self.app, self.report, self.ask, self.lang = goal, app, report, ask, lang
        self.steps, self.actions, self.plans = [], 0, 0
        self.queue = []                  # the plan's remaining steps
        self.replanning = False
        self.unchanged = 0
        self.waiting = None              # the irreversible control waiting for a yes
        self.allowed = set()             # ...and the ones you said yes to
        self.result = ''

    def run(self):
        """'done' | 'stuck' | 'limit' | 'confirm'. self.result holds what to say."""
        window = window_for(self.app)
        if window is None:
            raise HandsError('there is no window in front to work in')
        if NO_GO.search(window.Name or ''):
            raise HandsError(f'I do not work in {window.Name}')
        while True:
            seen, handles = look(window)
            if not seen['controls']:
                self.result = f'I cannot see any buttons in {seen["title"]}.'
                return 'stuck'
            if not self.queue:
                if self.steps and not self.replanning:
                    # The plan is carried out: say how it came out. Not done gets one
                    # follow-up plan, no more ("press Equals" -- 7 x 8 was planned without it).
                    check = finished(self.goal, seen, self.steps, self.ask, self.lang)
                    self.result = str(check.get('say') or ('Done.' if check.get('done') else
                                                           'It did not come out right.'))
                    if self.lang == 'si':
                        # Said by template, from what the window shows: the model's own
                        # Sinhala turned 12 + 30 into "කිලෝමීටරය 42" (5 Nov).
                        shows = str(check.get('shows') or '').strip()
                        self.result = (f'හරි, කළා. දැන් පෙන්නන්නේ {shows}.' if check.get('done')
                                       else f'ඒක හරියට වුණේ නෑ. දැන් පෙන්නන්නේ {shows}.')
                    if check.get('done') and not numbers_agree(check):
                        # Measured 5 Nov: "done" for 12 + 30 with 246 showing. The numbers
                        # decide, not the model's word.
                        check['done'] = False
                        self.result = (f"It shows {check.get('shows')}, not "
                                       f"{check.get('should_show')}.")
                        if self.lang == 'si':
                            self.result = (f"ඒක හරියට වුණේ නෑ. දැන් පෙන්නන්නේ {check.get('shows')}, "
                                           f"{check.get('should_show')} නෙවේ.")
                    if check.get('done'):
                        return 'done'
                    if self.plans >= 2:
                        return 'stuck'
                self.replanning = False
                if self.plans >= 2:
                    self.result = 'I tried twice and could not finish it.'
                    return 'stuck'
                planned = plan(self.goal, seen, self.steps, self.ask)
                self.plans += 1
                self.queue = list(planned.get('steps') or [])
                if not self.queue:
                    self.result = str(planned.get('say') or 'I do not see how to do that here.')
                    return 'stuck'
                continue
            step = self.queue[0]
            chosen, control = find(seen, handles, step.get('control', ''))
            if not chosen:
                # The plan no longer fits the window (a menu closed, a page changed): plan
                # the rest again -- the only time it plans twice.
                self.queue, self.replanning = [], True
                continue
            if IRREVERSIBLE.search(chosen['name']) and chosen['name'] not in self.allowed:
                self.waiting = chosen['name']
                self.result = chosen['name']
                return 'confirm'
            if self.actions >= MAX_ACTIONS:
                self.result = f'I stopped after {MAX_ACTIONS} steps without finishing.'
                return 'limit'
            self.queue.pop(0)
            before = fingerprint(seen)
            if step.get('action') == 'type':
                self.report(f"typing into {chosen['name']}")
                type_into(window, control, str(step.get('text', '')))
                self.steps.append(f"typed {step.get('text', '')!r} into {chosen['name']}")
            else:
                self.report(f"pressing {chosen['name']}")
                how = press(window, control)
                if how == 'covered':
                    self.result = f"Something covers {chosen['name']}; I did not click."
                    return 'stuck'
                self.steps.append(f"pressed {chosen['name']}")
            self.actions += 1
            time.sleep(SETTLE)
            # Never assume it landed: look again. One unchanged step is normal (the second
            # Nine of 9 x 9 shows 9 again); two in a row means it is not working.
            self.unchanged = self.unchanged + 1 if fingerprint(look(window)[0]) == before else 0
            if self.unchanged >= 2:
                self.result = f"Nothing changed when I pressed {chosen['name']}, so I stopped."
                return 'stuck'

    def confirm(self):
        """A yes to the waiting step: allow it, and carry on."""
        if self.waiting:
            self.allowed.add(self.waiting)
            self.waiting = None
        return self.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('app')
    parser.add_argument('goal', nargs='*')
    parser.add_argument('--look', action='store_true')
    args = parser.parse_args()
    if args.look:
        seen, _ = look(window_for(args.app))
        print(seen['title'])
        for c in seen['controls']:
            print(f"  {c['n']:3}. {c['kind']:12} {c['name']}")
        print('text:', seen['texts'])
        return 0
    task = Task(' '.join(args.goal), args.app)
    outcome = task.run()
    print(f'{outcome}: {task.result}')
    return 0 if outcome == 'done' else 1


if __name__ == '__main__':
    sys.exit(main())
