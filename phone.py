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
Add-Type @"
using System; using System.Runtime.InteropServices;
public class PL {
 [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
 [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, IntPtr e);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint f);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 public struct POINT { public int X, Y; }
}
"@
# Real screen pixels, as UI Automation reports them. Without this, display scaling moved
# the click elsewhere (measured 5 Nov: the window measured 922x531 instead of 1152x664).
[PL]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]

function Matching {
  foreach ($p in Get-Process PhoneExperienceHost -ErrorAction SilentlyContinue) {
    if ($p.MainWindowHandle -eq 0) { continue }
    $id = New-Object System.Windows.Automation.PropertyCondition($A::AutomationIdProperty, 'ButtonCall')
    $button = $A::FromHandle($p.MainWindowHandle).FindFirst(
      [System.Windows.Automation.TreeScope]::Descendants, $id)
    if (-not $button) { continue }
    $shown = $button.Current.Name -replace '\D', ''
    if ($shown -eq $Digits -or ($Local -and $shown -eq $Local)) { return @($p, $button) }
  }
  return $null
}

function Outcome($p) {
  # "no iphone" if Phone Link said it has none; "placed" once the dial pad clears -- what a
  # call that really started looked like (5 Nov); "" while neither has happened.
  $text = New-Object System.Windows.Automation.PropertyCondition(
    $A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Text)
  $mine = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $p.Id)
  foreach ($window in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $mine)) {
    foreach ($t in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $text)) {
      if ($t.Current.Name -match "find your iPhone|not connected|connect your iPhone|need permission to sync|almost there") {
        return 'no iphone'
      }
    }
  }
  if (-not (Matching)) { return 'placed' }
  return ''
}

function Wait-Outcome($p, $seconds) {
  $until = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $until) {
    $now = Outcome $p
    if ($now) { return $now }
    Start-Sleep -Milliseconds 400
  }
  return ''
}

function To-Front($p) {
  # Phone Link dials only from its window in front: pressed in the background, nothing
  # happened (21 Oct, 5 Nov). A tap of Alt lets this process bring it forward.
  [PL]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
  [PL]::keybd_event(0x12, 0, 0, [IntPtr]::Zero); [PL]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)
  [PL]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
}

function Aim($p, $button) {
  # The centre of the button -- its own position, in real pixels -- if Phone Link is in
  # front and its own window is what lies under that point. (Asking what element is at
  # the point does not work here: WinUI answers with its outer container, 5 Nov.)
  $r = $button.Current.BoundingRectangle
  if ($button.Current.IsOffscreen -or $r.Width -lt 10) { return $null }
  $x = [int]($r.X + $r.Width / 2); $y = [int]($r.Y + $r.Height / 2)
  $point = New-Object PL+POINT; $point.X = $x; $point.Y = $y
  $under = [PL]::GetAncestor([PL]::WindowFromPoint($point), 2)
  if ([PL]::GetForegroundWindow() -eq $p.MainWindowHandle -and $under -eq $p.MainWindowHandle) {
    return @($x, $y)
  }
  return $null
}

$deadline = (Get-Date).AddSeconds(8)
$found = $null
while (-not $found -and (Get-Date) -lt $deadline) { $found = Matching; if (-not $found) { Start-Sleep -Milliseconds 400 } }
if (-not $found) { 'not found'; exit 0 }
$p, $button = $found
To-Front $p
if ($DryRun) { if (Aim $p $button) { 'found' } else { 'found, aim off' }; exit 0 }

$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
$now = Wait-Outcome $p 4
if ($now -eq 'no iphone') { 'no iphone'; exit 0 }
if ($now -eq 'placed') { 'pressed'; exit 0 }

# The press did not take: click it, as a person would -- only the confirmed number's button.
$found = Matching
if (-not $found) { 'pressed'; exit 0 }
$p, $button = $found
To-Front $p
$at = Aim $p $button
if (-not $at) { 'not placed'; exit 0 }
$old = New-Object PL+POINT; [PL]::GetCursorPos([ref]$old) | Out-Null
[PL]::SetCursorPos($at[0], $at[1]) | Out-Null; Start-Sleep -Milliseconds 120
[PL]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 80
[PL]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 200
[PL]::SetCursorPos($old.X, $old.Y) | Out-Null
$now = Wait-Outcome $p 6
if ($now -eq 'no iphone') { 'no iphone' } elseif ($now -eq 'placed') { 'pressed' } else { 'not placed' }
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
