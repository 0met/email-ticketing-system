"""Test IMAP connection and email presence."""
import imaplib
import email
import os
import sys
from email.header import decode_header

def main():
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        mail.login(os.environ['EMAIL_USER'], os.environ['EMAIL_PASS'])
        print('IMAP login successful')
        
        mail.select('inbox')
        status, messages = mail.search(None, 'UNSEEN')
        print('Status:', status)
        if status == 'OK' and messages and messages[0]:
            ids = messages[0].split()
            print(f'Found {len(ids)} unseen messages')
            
            # Check most recent message
            if ids:
                latest_id = ids[-1]
                st, data = mail.fetch(latest_id, '(RFC822)')
                if st == 'OK' and data and data[0]:
                    msg = email.message_from_bytes(data[0][1])
                    subj = msg.get('subject', '')
                    if isinstance(subj, bytes):
                        subj = subj.decode()
                    print('Latest message:', subj)
        else:
            print('No unseen messages')
            
        mail.logout()
        print('IMAP connection test complete')
        return 0
        
    except Exception as e:
        print('Error:', str(e), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())