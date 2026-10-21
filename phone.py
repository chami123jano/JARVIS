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


def call(digits):
    """Place the call through Phone Link. Only ever after a spoken yes."""
    digits = re.sub(r'\D', '', str(digits))
    if not 10 <= len(digits) <= 15:
        raise PhoneError(f'{digits!r} is not a phone number')
    try:
        os.startfile(f'tel:+{digits}')
    except OSError as error:
        raise PhoneError('Phone Link did not open the call') from error
    return {'dialing': f'+{digits}'}


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
