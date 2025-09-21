"""Quick IMAP check script.

Usage: set EMAIL_USER and EMAIL_PASS env vars and run:
    python check_imap.py

This script will attempt to connect to Gmail IMAP and print unseen count and subjects.
"""
import os
import imaplib
import email
from email.header import decode_header

EMAIL_HOST = os.getenv('EMAIL_HOST', 'imap.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASS = os.getenv('EMAIL_PASS')

if not EMAIL_USER or not EMAIL_PASS:
    print('Please set EMAIL_USER and EMAIL_PASS environment variables')
    raise SystemExit(1)

def decode_subj(raw):
    try:
        parts = decode_header(raw)
        return ''.join([p.decode(enc or 'utf-8') if isinstance(p, bytes) else p for p, enc in parts])
    except Exception:
        return raw

try:
    mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
    mail.login(EMAIL_USER, EMAIL_PASS)
    print('IMAP login successful')
    mail.select('inbox')
    status, messages = mail.search(None, 'UNSEEN')
    if status == 'OK' and messages and messages[0]:
        ids = messages[0].split()
        print('Unseen count:', len(ids))
        for eid in ids[-5:]:
            st, data = mail.fetch(eid, '(RFC822)')
            if st == 'OK' and data and data[0]:
                msg = email.message_from_bytes(data[0][1])
                subj = decode_subj(msg.get('subject',''))
                print('-', subj)
    else:
        print('No unseen messages')
    mail.logout()
except Exception as e:
    print('IMAP check failed:', e)
    raise
