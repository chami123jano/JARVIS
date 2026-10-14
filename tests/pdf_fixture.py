"""Real, minimal PDFs for tests: one page of text that pypdf reads back.

Written by hand rather than with a PDF library, so the tests need nothing new installed.
"""
import os
import time
from pathlib import Path


def make_pdf(path, lines, modified=None):
    """Write a one-page PDF containing these lines. modified: a timestamp for the file."""
    def escape(text):
        return str(text).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

    body = ['BT', '/F1 11 Tf', '14 TL', '50 780 Td']
    for line in lines:
        body.append(f'({escape(line)}) Tj T*')
    body.append('ET')
    stream = '\n'.join(body).encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + obj + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1, xref)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    if modified is not None:
        os.utime(path, (modified, modified))
    return path


def invoice(path, number, date, lines, total, currency='Rs.', label='Grand Total',
            modified=None):
    """An invoice with items, a subtotal that must not be mistaken for the total, and tax."""
    content = ['ACME Supplies (Pvt) Ltd', 'Colombo 03', '', f'TAX INVOICE  No. {number}',
               f'Invoice Date: {date}', '']
    for item, amount in lines:
        content.append(f'{item}    {currency} {amount:,.2f}')
    subtotal = sum(amount for _, amount in lines)
    content += ['', f'Subtotal    {currency} {subtotal:,.2f}',
                f'VAT 18%    {currency} {total - subtotal:,.2f}',
                f'{label}    {currency} {total:,.2f}', '', 'Thank you for your business.']
    return make_pdf(path, content, modified=modified if modified is not None else time.time())
