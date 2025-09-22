"""Email processing module for fetching and storing email messages as tickets."""
import os
import imaplib
import email
import logging
import random
import time
import uuid
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime
from sqlalchemy import create_engine, Table, MetaData, Column, Integer, String, Text, DateTime
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

logger = logging.getLogger('email-ticketing')
logger.setLevel(logging.DEBUG)

# Email configuration 
EMAIL_HOST = os.getenv('EMAIL_HOST', 'imap.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))
EMAIL_USER = os.getenv('EMAIL_USER', 'your-email@gmail.com')
EMAIL_PASS = os.getenv('EMAIL_PASS', 'your-app-password')
# Search for all messages in the last day to avoid missing any
EMAIL_SEARCH_CRITERIA = os.getenv('EMAIL_SEARCH_CRITERIA', '(SINCE "21-Sep-2025")')

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Initialize database engine
engine = create_engine(DATABASE_URL)
metadata = MetaData()

# Define tables
tickets = Table('tickets', metadata,
    Column('id', Integer, primary_key=True),
    Column('ticket_id', String, unique=True),
    Column('subject', String),
    Column('sender_email', String),
    Column('sender_name', String),
    Column('content', Text),
    Column('status', String, server_default='open'),
    Column('priority', String, server_default='medium'),
    Column('created_at', DateTime, server_default='CURRENT_TIMESTAMP'),
    Column('updated_at', DateTime, server_default='CURRENT_TIMESTAMP'),
    Column('assigned_to', String),
    Column('category', String)
)

def generate_unique_ticket_id(base_id=None, retry_count=0, max_retries=10):
    """Generate a unique ticket ID with collision handling."""
    if retry_count >= max_retries:
        raise RuntimeError(f'Failed to generate unique ticket ID after {max_retries} attempts')
        
    now = datetime.now()
    if not base_id:
        # Generate base ID with timestamp and UUID
        uuid_suffix = str(uuid.uuid4())[:8]
        base_id = f'TK{now:%Y%m%d%H%M%S}-{uuid_suffix}'
        
    # Add random retry suffix if needed
    ticket_id = base_id
    if retry_count > 0:
        retry_suffix = str(uuid.uuid4())[:4]  # Use UUID instead of random numbers
        ticket_id = f'{base_id}-{retry_suffix}'
        
    # Verify uniqueness in database
    try:
        with engine.connect() as conn:
            result = conn.execute(
                tickets.select().where(tickets.c.ticket_id == ticket_id)
            )
            if result.first() is None:
                return ticket_id
    except SQLAlchemyError as e:
        logger.error('Database error checking ticket ID: %s', e)
        
    # If we got here, ID already exists or db error occurred - try again
    time.sleep(0.1 * (retry_count + 1))  # Exponential backoff
    return generate_unique_ticket_id(base_id, retry_count + 1)

def create_ticket(subject, sender_email, sender_name, content, retry_count=0, max_retries=5):
    """Create a ticket with retry logic for handling race conditions."""
    last_error = None
    
    for attempt in range(max_retries):
        try:
            ticket_id = generate_unique_ticket_id()
            logger.info('Attempting to create ticket %s...', ticket_id)
            
            with engine.begin() as conn:  # Use begin() for proper transaction handling
                # Insert ticket
                ins = tickets.insert().values(
                    ticket_id=ticket_id,
                    subject=subject,
                    sender_email=sender_email,
                    sender_name=sender_name,
                    content=content,
                    status='open',  # Always set initial status
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                result = conn.execute(ins)
                
                # Verify the ticket was created by querying it back
                verify_query = tickets.select().where(tickets.c.ticket_id == ticket_id)
                created_ticket = conn.execute(verify_query).first()
                
                if created_ticket:
                    logger.info('Created and verified new ticket %s (ID: %s) from email: %s',
                             ticket_id, created_ticket.id, subject)
                    return ticket_id
                else:
                    raise SQLAlchemyError("Ticket not found after creation")
                
        except IntegrityError as e:
            last_error = e
            logger.warning('Integrity error creating ticket (attempt %d): %s', attempt + 1, e)
            time.sleep(0.5 * (attempt + 1))  # Exponential backoff
            continue
            
        except SQLAlchemyError as e:
            logger.error('Database error creating ticket: %s', e)
            raise
            
    raise RuntimeError(f'Failed to create ticket after {max_retries} attempts: {last_error}')

def check_emails():
    """Check IMAP for new emails and process them into tickets"""
    try:
        logger.info('Database initialized')
        logger.info(f'Starting email check with HOST={EMAIL_HOST} PORT={EMAIL_PORT} USER={EMAIL_USER}')
        logger.info(f'Using search criteria: {EMAIL_SEARCH_CRITERIA}')
        
        # Connect to IMAP server
        logger.info('Connecting to IMAP server...')
        mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
        
        # Login
        logger.info('Attempting login...')
        mail.login(EMAIL_USER, EMAIL_PASS)
        logger.info('Login successful!')
        
        # List mailboxes for debugging
        typ, mailboxes = mail.list()
        if typ == 'OK':
            logger.info('Available mailboxes:')
            for mb in mailboxes:
                logger.info(str(mb.decode()))
        
        # Select and check inbox
        logger.info('Selecting inbox...')
        typ, inbox_info = mail.select('inbox')
        if typ == 'OK':
            num_messages = int(inbox_info[0])
            logger.info(f'Inbox selected successfully. Contains {num_messages} messages.')
        else:
            logger.error(f'Failed to select inbox: {typ}')
            return
        
        # Search for messages
        logger.info(f'Searching for messages with criteria: {EMAIL_SEARCH_CRITERIA}')
        status, messages = mail.search(None, EMAIL_SEARCH_CRITERIA)
        
        if status != 'OK':
            logger.error(f'Failed to search emails. Status: {status}')
            return
        
        if not messages or not messages[0]:
            logger.info('Search returned no messages')
            return
            
        message_numbers = messages[0].split()
        if not message_numbers:
            logger.info('No new messages found')
            return
            
        logger.info(f'Found {len(message_numbers)} message(s) to process: {message_numbers}')
        for num in message_numbers:
            try:
                logger.info(f'Processing message ID {num.decode() if isinstance(num, bytes) else num}')
                typ, msg_data = mail.fetch(num, '(RFC822)')
                if typ != 'OK':
                    logger.error(f'Failed to fetch message {num}: {typ}')
                    continue
                
                if not msg_data or not msg_data[0]:
                    logger.error(f'No data returned for message {num}')
                    continue
                
                logger.info(f'Successfully fetched message {num}')
                email_body = msg_data[0][1]
                logger.info(f'Email body size: {len(email_body)} bytes')
                message = email.message_from_bytes(email_body)
                
                # Parse subject
                subject_raw = message['subject']
                logger.info(f'Raw subject: {subject_raw}')
                subject_parts = decode_header(subject_raw)
                logger.info(f'Decoded subject parts: {subject_parts}')
                subject = subject_parts[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                logger.info(f'Final subject: {subject}')
                
                # Parse sender
                from_ = message['from']
                logger.info(f'Raw from: {from_}')
                sender_name, sender_email = parseaddr(from_)
                logger.info(f'Parsed sender: {sender_name} <{sender_email}>')
                
                # Extract content
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
                logger.info('Creating ticket...')
                logger.info(f'Subject: {subject}')
                logger.info(f'From: {sender_name} <{sender_email}>')
                logger.info(f'Content length: {len(content)} chars')
                
                ticket_id = create_ticket(subject, sender_email, sender_name, content)
                logger.info(f'Created ticket: {ticket_id}')
                
                # Mark as read
                logger.info(f'Marking message {num} as read...')
                typ, data = mail.store(num, '+FLAGS', '(\Seen)')
                if typ != 'OK':
                    logger.error(f'Failed to mark message as read: {typ}')
                else:
                    logger.info('Message marked as read')
                
            except Exception as e:
                logger.exception('Error processing message %s: %s', num, e)
                continue
        
        logger.info('Message processing complete')
        mail.logout()
        logger.info('IMAP logout successful')
        logger.info('Email check complete')
        
    except Exception as e:
        logger.exception('Error checking emails: %s', e)
        raise