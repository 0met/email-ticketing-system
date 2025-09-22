"""Email processing module for fetching and storing email messages as tickets."""
import os
import imaplib
import email
import logging
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime
import sqlite3

logger = logging.getLogger('email-processor')
logger.setLevel(logging.DEBUG)

# Email configuration 
EMAIL_HOST = os.getenv('EMAIL_HOST', 'imap.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))
EMAIL_USER = os.getenv('EMAIL_USER', 'your-email@gmail.com')
EMAIL_PASS = os.getenv('EMAIL_PASS', 'your-app-password')
EMAIL_SEARCH_CRITERIA = os.getenv('EMAIL_SEARCH_CRITERIA', 'UNSEEN')
SQLITE_PATH = os.getenv('SQLITE_PATH', 'tickets.db')

def check_emails():
    """Check IMAP for new emails and process them into tickets"""
    try:
        logger.info('Connecting to IMAP server %s:%s', EMAIL_HOST, EMAIL_PORT)
        mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
        mail.login(EMAIL_USER, EMAIL_PASS)
        logger.info('Successfully logged into IMAP')
        
        mail.select('inbox')
        status, messages = mail.search(None, EMAIL_SEARCH_CRITERIA)
        
        if status != 'OK':
            logger.error('Failed to search emails')
            return
            
        message_numbers = messages[0].split()
        if not message_numbers:
            logger.info('No new messages found')
            return
            
        logger.info('Found %d new messages', len(message_numbers))
        
        for num in message_numbers:
            try:
                logger.debug('Processing message %s', num)
                typ, msg_data = mail.fetch(num, '(RFC822)')
                if typ != 'OK':
                    logger.error('Failed to fetch message %s', num)
                    continue
                    
                email_body = msg_data[0][1]
                message = email.message_from_bytes(email_body)
                
                subject = decode_header(message['subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                    
                from_ = message['from']
                sender_name, sender_email = parseaddr(from_)
                
                content = ''
                if message.is_multipart():
                    for part in message.walk():
                        if part.get_content_type() == 'text/plain':
                            try:
                                payload = part.get_payload(decode=True)
                                if payload:
                                    content = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                                    break
                            except Exception as e:
                                logger.error('Error decoding message part: %s', e)
                else:
                    try:
                        payload = message.get_payload(decode=True)
                        if payload:
                            content = payload.decode(message.get_content_charset() or 'utf-8', errors='replace')
                    except Exception as e:
                        logger.error('Error decoding message: %s', e)
                        
                # Create ticket
                conn = sqlite3.connect(SQLITE_PATH)
                cursor = conn.cursor()
                try:
                    ticket_id = f'TK{datetime.now().strftime("%Y%m%d%H%M%S")}'
                    logger.info('Creating ticket %s from email %s', ticket_id, subject)
                    
                    cursor.execute('''
                        INSERT INTO tickets (ticket_id, subject, sender_email, sender_name, content)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (ticket_id, subject, sender_email, sender_name, content))
                    conn.commit()
                    
                    # Mark as read
                    mail.store(num, '+FLAGS', '(\Seen)')
                    logger.info('Successfully processed message %s', num)
                    
                except sqlite3.IntegrityError:
                    logger.warning('Duplicate ticket %s, skipping', ticket_id)
                except Exception as e:
                    logger.error('Error creating ticket from message %s: %s', num, e)
                finally:
                    conn.close()
                    
            except Exception as e:
                logger.exception('Error processing message %s: %s', num, e)
                
        mail.logout()
        logger.info('Email check completed successfully')
        
    except Exception as e:
        logger.exception('Error checking emails: %s', e)
        raise