"""The phone bridge, for an iPhone (Day 8).

    python phone.py --call 0771234567      dial through the iPhone (asks nothing: test only)
    python phone.py --find                  open Find My in the browser

The plan said KDE Connect. On an iPhone it can do none of what Day 8 asks -- Apple lets no
app send an SMS, place a call or read notifications -- so this goes through Microsoft's
Phone Link, which pairs with the iPhone over Bluetooth:

  - calls: a tel: link opens Phone Link, which places the call through the iPhone. Phone
    Link must be the default app for TEL (Settings > Apps > Default apps); until 21 Oct
    it was Skype for Business.
  - notifications: Phone Link shows the iPhone's as Windows notifications, and Day 7b's
    listener already reads those -- allowed by name, codes never read out.
  - finding the phone: no program may ring an iPhone. Find My opens; you press Play Sound.
  - SMS: Phone Link offers no way for a program to send one. Not built.

A call is said in your name, so it is tier 2: read back, and placed only on a spoken yes.
"""
import argparse
import os
import re
import sys
import webbrowser

FIND_MY = 'https://www.icloud.com/find'


class PhoneError(Exception):
    pass


def number_in(text):
    """A phone number said in the sentence -- "0771234567ට call කරන්න" -- in the full
    international form contacts use (94771234567), or ''."""
    import contacts
    for found in re.findall(r'\+?\d[\d\s-]{7,}\d', str(text)):
        try:
            return contacts.clean_phone(found)
        except ValueError:
            continue
    return ''


def speakable_number(digits):
    """94771234567 -> "077 123 4567": the way a Sri Lankan number is read out."""
    digits = re.sub(r'\D', '', str(digits))
    if digits.startswith('94') and len(digits) == 11:
        local = '0' + digits[2:]
        return f'{local[:3]} {local[3:6]} {local[6:]}'
    return '+' + digits


# Phone Link's dial pad, read through Windows' accessibility interface. The Call button's
# name carries the number it will dial ("Call Dialed number + 9 4   7 1 ..."), so it is
# pressed only when that is exactly the number you said yes to.
PRESS_CALL = r'''
param([string]$Digits, [string]$Local = '', [switch]$DryRun)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline) {
  foreach ($p in Get-Process PhoneExperienceHost -ErrorAction SilentlyContinue) {
    if ($p.MainWindowHandle -eq 0) { continue }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
    $id = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'ButtonCall')
    $button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $id)
    $shown = $button.Current.Name -replace '\D', ''
    if ($button -and ($shown -eq $Digits -or ($Local -and $shown -eq $Local))) {
      if ($DryRun) { 'found'; exit 0 }
      $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
      # Pressed is not placed: measured 21 Oct, Phone Link answered "Couldn't find your
      # iPhone" in a window of its own. Watch for it for a few seconds.
      $watch = (Get-Date).AddSeconds(6)
      $text = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Text)
      while ((Get-Date) -lt $watch) {
        $desktop = [System.Windows.Automation.AutomationElement]::RootElement
        $pid_ = New-Object System.Windows.Automation.PropertyCondition(
          [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)
        foreach ($window in $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, $pid_)) {
          foreach ($t in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $text)) {
            if ($t.Current.Name -match "find your iPhone|not connected|connect your iPhone") {
              'no iphone'; exit 0
            }
          }
        }
        Start-Sleep -Milliseconds 500
      }
      'pressed'; exit 0
    }
  }
  Start-Sleep -Milliseconds 400
}
'not found'
'''


def press_call(digits, dry_run=False):
    """Press Phone Link's Call button if it shows exactly these digits. True if pressed
    (or, dry run, found). Measured 21 Oct: a tel: link fills the dial pad and waits.

    The same number may be shown either way -- "+94 71 150 8512" one time, "077 311 8512"
    the next -- so the local form of a Sri Lankan number counts too; no other number does."""
    import subprocess
    digits = re.sub(r'\D', '', str(digits))
    local = '0' + digits[2:] if digits.startswith('94') and len(digits) == 11 else ''
    command = ['powershell', '-NoProfile', '-NonInteractive', '-Command',
               f'& {{{PRESS_CALL}}} -Digits {digits}' + (f' -Local {local}' if local else '')
               + (' -DryRun' if dry_run else '')]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    said = done.stdout.strip().splitlines()[-1:]
    if said == ['no iphone']:
        raise PhoneError('Phone Link could not find the iPhone')
    return said == ['found' if dry_run else 'pressed']


def call(digits):
    """Place the call through Phone Link. Only ever after a spoken yes.
    {'dialing', 'pressed'}: pressed is False when Phone Link only filled the dial pad."""
    digits = re.sub(r'\D', '', str(digits))
    if not 10 <= len(digits) <= 15:
        raise PhoneError(f'{digits!r} is not a phone number')
    try:
        os.startfile(f'tel:+{digits}')
    except OSError as error:
        raise PhoneError('Phone Link did not open the call') from error
    return {'dialing': f'+{digits}', 'pressed': press_call(digits)}


def find():
    """Open Apple's Find My, where the iPhone can be made to play a sound."""
    if not webbrowser.open(FIND_MY):
        raise PhoneError('the browser did not open')
    return {'opened': FIND_MY}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--call', metavar='NUMBER')
    parser.add_argument('--find', action='store_true')
    args = parser.parse_args()
    try:
        if args.call:
            print(call(number_in(args.call) or args.call))
        elif args.find:
            print(find())
        else:
            parser.print_help()
    except PhoneError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
