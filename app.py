import os
import smtplib
import imaplib
import email
import logging
from email.header import decode_header
from email.utils import parseaddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from datetime import datetime
import hashlib
import base64
import json
import time
from threading import Thread
from sqlalchemy import (
    create_engine, MetaData, Table, Column,
    Integer, String, Text, DateTime, Boolean,
    func, text, select
)
from sqlalchemy.sql import select, and_, or_
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
CORS(app)

# Enhanced logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('email-ticketing')
logger.setLevel(logging.DEBUG)

# Add root logger for unhandled exceptions
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Configuration - Set these as environment variables
EMAIL_HOST = os.getenv('EMAIL_HOST', 'imap.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER', 'your-email@gmail.com')
EMAIL_PASS = os.getenv('EMAIL_PASS', 'your-app-password')
EMAIL_CHECK_INTERVAL = int(os.getenv('EMAIL_CHECK_INTERVAL', 30))
EMAIL_SEARCH_CRITERIA = os.getenv('EMAIL_SEARCH_CRITERIA', 'UNSEEN')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', '')
UPLOAD_FOLDER = 'attachments'
# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # Heroku's DATABASE_URL needs to be converted to use postgresql://
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

responses = Table('responses', metadata,
    Column('id', Integer, primary_key=True),
    Column('ticket_id', String),
    Column('response_type', String),
    Column('sender', String),
    Column('content', Text),
    Column('created_at', DateTime, server_default='CURRENT_TIMESTAMP')
)

attachments = Table('attachments', metadata,
    Column('id', Integer, primary_key=True),
    Column('ticket_id', String),
    Column('response_id', Integer),
    Column('filename', String),
    Column('file_path', String),
    Column('file_size', Integer),
    Column('created_at', DateTime, server_default='CURRENT_TIMESTAMP')
)

processed_messages = Table('processed_messages', metadata,
    Column('id', Integer, primary_key=True),
    Column('msg_hash', String, unique=True),
    Column('created_at', DateTime, server_default='CURRENT_TIMESTAMP')
)

# Enable dev-only endpoints (unprotected) when set to true. Defaults to False in production.
ENABLE_DEV_ENDPOINTS = os.getenv('ENABLE_DEV_ENDPOINTS', 'false').lower() in ('1', 'true', 'yes')

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

class TicketingSystem:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """Initialize PostgreSQL database"""
        try:
            logger.info("Starting database initialization...")
            logger.debug("Database URL type: %s", DATABASE_URL.split('://')[0] if DATABASE_URL else 'None')
            logger.info("Creating database engine...")
            
            # Test database connection
            with engine.connect() as conn:
                logger.info("Database connection test successful")
            
            # Create all tables
            logger.info("Creating database tables...")
            metadata.create_all(engine)
            logger.info("Database tables created successfully")
            
        except SQLAlchemyError as e:
            logger.error("Database error during initialization: %s", str(e))
            logger.debug("Full database error details:", exc_info=True)
            raise
        except Exception as e:
            logger.error("Unexpected error initializing database: %s", str(e))
            logger.debug("Full error details:", exc_info=True)
            raise

    def is_message_processed(self, msg_hash):
        """Check if a message has already been processed"""
        try:
            with engine.connect() as conn:
                query = select(processed_messages).where(processed_messages.c.msg_hash == msg_hash)
                result = conn.execute(query)
                return result.first() is not None
        except Exception as e:
            logger.error(f"Error checking processed message: {e}")
            return False

    def mark_message_processed(self, msg_hash):
        """Mark a message as processed in the database"""
        try:
            with engine.connect() as conn:
                ins = processed_messages.insert().values(msg_hash=msg_hash)
                conn.execute(ins)
                conn.commit()
        except SQLAlchemyError as e:
            logger.error(f"Error marking message as processed: {e}")
            raise
    
    def generate_ticket_id(self, email_content):
        """Generate unique ticket ID"""
        hash_object = hashlib.md5(f"{email_content}{datetime.now()}".encode())
        return f"TKT-{hash_object.hexdigest()[:8].upper()}"
    
    def create_ticket(self, subject, sender_email, sender_name, content, attachments=None):
        """Create new ticket"""
        ticket_id = self.generate_ticket_id(f"{subject}{sender_email}")
        logger.info(f"Creating ticket {ticket_id} for {sender_email}")
        
        try:
            with engine.begin() as conn:  # Automatically manages transaction
                # Insert ticket
                ins = tickets.insert().values(
                    ticket_id=ticket_id,
                    subject=subject,
                    sender_email=sender_email,
                    sender_name=sender_name,
                    content=content,
                    status='open',
                    created_at=func.current_timestamp(),
                    updated_at=func.current_timestamp()
                )
                result = conn.execute(ins)
                ticket_primary_key = result.inserted_primary_key[0]
                logger.info(f"Ticket {ticket_id} inserted with ID {ticket_primary_key}")
                
                # Insert attachments if any
                if attachments:
                    for attachment in attachments:
                        ins = attachments.insert().values(
                            ticket_id=ticket_id,
                            filename=attachment['filename'],
                            file_path=attachment['path'],
                            file_size=attachment['size']
                        )
                        conn.execute(ins)
                        logger.info(f"Added attachment {attachment['filename']} to ticket {ticket_id}")
                
                # Transaction will be automatically committed here
                logger.info(f"Ticket {ticket_id} (ID: {ticket_primary_key}) created successfully")
                return ticket_id
                
        except SQLAlchemyError as e:
            logger.error(f"Database error creating ticket {ticket_id}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating ticket {ticket_id}: {str(e)}")
            raise
        except SQLAlchemyError as e:
            logger.error(f"Error creating ticket: {e}")
            raise
    
    def add_response(self, ticket_id, response_type, sender, content):
        """Add response to ticket"""
        try:
            with engine.connect() as conn:
                # Insert response
                ins = responses.insert().values(
                    ticket_id=ticket_id,
                    response_type=response_type,
                    sender=sender,
                    content=content
                )
                result = conn.execute(ins)
                response_id = result.inserted_primary_key[0]
                
                # Update ticket timestamp
                update = tickets.update().where(
                    tickets.c.ticket_id == ticket_id
                ).values(
                    updated_at=func.current_timestamp()
                )
                conn.execute(update)
                
                return response_id
        except SQLAlchemyError as e:
            logger.error(f"Error adding response: {e}")
            raise
    
    def get_tickets(self, status=None):
        """Get all tickets or filter by status"""
        try:
            with engine.connect() as conn:
                if status:
                    query = select(tickets).where(tickets.c.status == status).order_by(tickets.c.updated_at.desc())
                else:
                    query = select(tickets).order_by(tickets.c.updated_at.desc())
                result = conn.execute(query)
                return [dict(row) for row in result]
        except SQLAlchemyError as e:
            logger.error(f"Error getting tickets: {e}")
            raise
    
    def get_ticket(self, ticket_id):
        """Get specific ticket with responses"""
        try:
            with engine.connect() as conn:
                # Get ticket
                query = select(tickets).where(tickets.c.ticket_id == ticket_id)
                result = conn.execute(query)
                ticket = result.fetchone()
                
                if ticket:
                    ticket = dict(ticket)
                    
                    # Get responses
                    query = select(responses).where(
                        responses.c.ticket_id == ticket_id
                    ).order_by(responses.c.created_at.asc())
                    result = conn.execute(query)
                    ticket['responses'] = [dict(row) for row in result]
                    
                    # Get attachments
                    query = select(attachments).where(
                        attachments.c.ticket_id == ticket_id
                    ).order_by(attachments.c.created_at.asc())
                    result = conn.execute(query)
                    ticket['attachments'] = [dict(row) for row in result]
                    
                return ticket
        except SQLAlchemyError as e:
            logger.error(f"Error getting ticket: {e}")
            raise
    
    def update_ticket_status(self, ticket_id, status):
        """Update ticket status"""
        try:
            with engine.connect() as conn:
                query = tickets.update().where(
                    tickets.c.ticket_id == ticket_id
                ).values(
                    status=status,
                    updated_at=datetime.now()
                )
                conn.execute(query)
                conn.commit()
        except SQLAlchemyError as e:
            logger.error(f"Error updating ticket status: {e}")
            raise

# Initialize ticketing system and create database
ticketing = TicketingSystem()

class EmailProcessor:
    def __init__(self):
        # persistent deduplication is handled via ticketing database
        self.processed_emails = None
    
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            logging.info(f'Connecting to {EMAIL_HOST}:{EMAIL_PORT} as {EMAIL_USER}')
            mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
            logging.info('SSL connection established, attempting login')
            mail.login(EMAIL_USER, EMAIL_PASS)
            logger.info('IMAP login successful')
            
            # Test mailbox access
            mail.select('INBOX')
            status, messages = mail.search(None, 'ALL')
            logger.info(f'Mailbox access test: status={status}, message count={len(messages[0].split()) if messages[0] else 0}')
            
            return mail
        except Exception as e:
            logger.error(f"IMAP connection failed: {str(e)}")
            if isinstance(e, imaplib.IMAP4.error):
                logger.error(f"IMAP4 specific error: {str(e)}")
            return None
    
    def process_email(self, msg):
        """Process incoming email and create ticket"""
        try:
            # Decode subject safely
            raw_subject = msg.get('subject', 'No Subject')
            try:
                decoded_parts = decode_header(raw_subject)
                subject = ''.join([
                    part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                    for part, enc in decoded_parts
                ])
            except Exception:
                subject = raw_subject

            # Parse sender/email reliably
            raw_from = msg.get('from', '')
            sender_name, sender_email = parseaddr(raw_from)
            sender_name = sender_name or sender_email
            
            # Get email content
            content = ""
            attachments = []
            
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        try:
                            text = payload.decode(charset, errors='ignore')
                        except Exception:
                            text = payload.decode('utf-8', errors='ignore')

                        if content_type == 'text/plain':
                            content += text
                        elif content_type == 'text/html' and not content:
                            content = text

                    # Handle attachments
                    filename = part.get_filename()
                    if filename:
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        try:
                            with open(file_path, 'wb') as f:
                                f.write(part.get_payload(decode=True))

                            attachments.append({
                                'filename': filename,
                                'path': file_path,
                                'size': os.path.getsize(file_path)
                            })
                        except Exception as e:
                            logging.warning(f"Failed to save attachment {filename}: {e}")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    try:
                        content = payload.decode(msg.get_content_charset() or 'utf-8', errors='ignore')
                    except Exception:
                        content = payload.decode('utf-8', errors='ignore')
            
            # Create ticket
            ticket_id = ticketing.create_ticket(subject, sender_email, sender_name, content, attachments)
            logging.info(f"Created ticket %s for %s", ticket_id, sender_email)
            
            return ticket_id
            
        except Exception as e:
            logging.exception(f"Error processing email: {e}")
            return None
    
    def check_emails(self):
        """Check for new emails and process them"""
        mail = None
        try:
            logger.info("Starting email check")
            mail = self.connect_imap()
            if not mail:
                logger.error("Failed to connect to IMAP server")
                return
            
            logger.info("Connected to IMAP server, selecting inbox")
            mail.select('inbox')
            # Use configurable search criteria (default UNSEEN). Can be set to 'ALL' for debugging.
            criteria = EMAIL_SEARCH_CRITERIA or 'UNSEEN'
            status, messages = mail.search(None, criteria)

            if status == 'OK':
                # messages[0] may be an empty byte string when there are no results
                if not messages or not messages[0]:
                    logging.debug('No unseen messages')
                else:
                    email_ids = messages[0].split()

                    for email_id in email_ids:
                        status, msg_data = mail.fetch(email_id, '(RFC822)')
                        if status == 'OK' and msg_data and msg_data[0]:
                            email_body = msg_data[0][1]
                            email_message = email.message_from_bytes(email_body)

                            # Check if we've already processed this email
                            email_hash = hashlib.md5(email_body).hexdigest()
                            if not ticketing.is_message_processed(email_hash):
                                self.process_email(email_message)
                                ticketing.mark_message_processed(email_hash)
                                try:
                                    # Mark message as Seen to avoid reprocessing
                                    mail.store(email_id, '+FLAGS', '\\Seen')
                                except Exception as e:
                                    logging.warning('Failed to mark message seen: %s', e)
                            else:
                                logging.debug('Email already processed (hash=%s)', email_hash)
            else:
                logging.warning('IMAP search returned non-OK status: %s', status)
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            logging.exception('Error checking emails: %s', e)
    
    def send_email(self, to_email, subject, content, ticket_id):
        """Send email response"""
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_USER
            msg['To'] = to_email
            msg['Subject'] = f"Re: {subject} [Ticket: {ticket_id}]"
            
            msg.attach(MIMEText(content, 'plain'))
            
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            server.quit()
            
            # Log response in database
            ticketing.add_response(ticket_id, 'outgoing', EMAIL_USER, content)
            
            return True
            
        except Exception as e:
            print(f"Error sending email: {e}")
            return False

# Initialize email processor
email_processor = EmailProcessor()

# Background email check worker
def background_worker():
    """Background thread to check emails periodically"""
    while True:
        try:
            email_processor.check_emails()
        except Exception as e:
            logging.exception('Background worker error: %s', e)
        time.sleep(EMAIL_CHECK_INTERVAL)

# Start background worker with error handling
def start_worker():
    try:
        logger.info("Starting background worker thread")
        Thread(target=background_worker, daemon=True).start()
    except Exception as e:
        logger.exception("Failed to start background worker: %s", str(e))

# Only start the worker if explicitly enabled
if os.getenv('ENABLE_BACKGROUND_WORKER', '').lower() in ('1', 'true', 'yes'):
    try:
        start_worker()
    except Exception as e:
        logger.exception("Error during startup: %s", str(e))

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analytics')
def analytics():
    """Analytics page showing ticket statistics"""
    try:
        logger.info('Starting analytics page render')
        conn = engine.connect()
        logger.info('Connected to database')

        # Get total tickets
        total_query = select(func.count()).select_from(tickets)
        total_tickets = conn.execute(total_query).scalar() or 0
        logger.info(f'Found {total_tickets} total tickets')
        
        # Get tickets by status
        status_query = select(tickets.c.status, func.count().label('count')).group_by(tickets.c.status)
        result = conn.execute(status_query)
        status_counts = dict((row.status, row.count) for row in result)
        
        # Get tickets by day (last 7 days)
        seven_days_ago = text("NOW() - INTERVAL '7 days'")
        daily_query = select(
            func.date(tickets.c.created_at).label('day'),
            func.count().label('count')
        ).where(
            tickets.c.created_at >= seven_days_ago
        ).group_by(
            text('day')
        ).order_by(
            text('day DESC')
        )
        result = conn.execute(daily_query)
        daily_counts = dict((row.day.strftime('%Y-%m-%d'), row.count) for row in result)
        
        conn.close()
        
        return render_template('analytics.html',
                            total_tickets=total_tickets,
                            status_counts=status_counts,
                            daily_counts=daily_counts)
    except Exception as e:
        logger.error(f"Error in analytics: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get email checking status"""
    imap = email_processor.connect_imap()
    is_connected = imap is not None
    status_info = {
        'connected': is_connected,
        'last_check': datetime.now().isoformat(),
        'email_user': EMAIL_USER,
        'imap_host': EMAIL_HOST,
        'imap_port': EMAIL_PORT
    }
    
    if imap:
        try:
            imap.select('INBOX')
            status, messages = imap.search(None, 'ALL')
            status_info['mailbox_status'] = status
            status_info['message_count'] = len(messages[0].split()) if messages[0] else 0
        except Exception as e:
            status_info['error'] = str(e)
        finally:
            imap.logout()
            
    return jsonify(status_info)

@app.route('/api/force-check')
def force_check():
    """Force an immediate email check"""
    try:
        logger.info("Starting forced email check")
        email_processor.check_emails()
        return jsonify({"status": "success", "message": "Email check completed"})
    except Exception as e:
        logger.exception("Error during forced email check")
        return jsonify({"status": "error", "message": str(e)}), 500
        return jsonify({'success': True})
    except Exception as e:
        logger.exception("Force check failed")
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-connection')
def test_connection():
    """Test Gmail connection and return detailed status"""
    try:
        # Test IMAP connection
        logger.info("Testing IMAP connection...")
        imap = email_processor.connect_imap()
        if not imap:
            return jsonify({
                'success': False,
                'error': 'Failed to establish IMAP connection',
                'config': {
                    'host': EMAIL_HOST,
                    'port': EMAIL_PORT,
                    'user': EMAIL_USER,
                    'has_password': bool(EMAIL_PASS)
                }
            }), 500
            
        # Test SMTP connection
        logger.info("Testing SMTP connection...")
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.starttls()
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.quit()
        
        # Get mailbox statistics
        imap.select('INBOX')
        status, messages = imap.search(None, 'ALL')
        total_messages = len(messages[0].split()) if messages[0] else 0
        
        status, messages = imap.search(None, 'UNSEEN')
        unread_messages = len(messages[0].split()) if messages[0] else 0
        
        imap.logout()
        
        return jsonify({
            'success': True,
            'imap_status': 'Connected',
            'smtp_status': 'Connected',
            'mailbox_stats': {
                'total_messages': total_messages,
                'unread_messages': unread_messages
            }
        })
        
    except Exception as e:
        logger.exception("Connection test failed")
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }), 500

@app.route('/api/tickets')
def get_tickets():
    """Get all tickets or filter by status"""
    try:
        # Get query parameters
        status = request.args.get('status')
        logger.info(f"Fetching tickets with status filter: {status}")
        
        logger.info("About to connect to database...")
        # Connect and execute query
        with engine.connect() as conn:
            logger.info("Database connection established")
            # Check if we have any tickets at all
            logger.info("Executing count query...")
            count_query = select(func.count()).select_from(tickets)
            total_tickets = conn.execute(count_query).scalar() or 0
            logger.info(f"Total tickets in database: {total_tickets}")
            
            logger.info("Building main query...")
            # Build query based on status filter with explicit ordering
            if status:
                query = select(tickets).where(tickets.c.status == status).order_by(tickets.c.created_at.desc())
            else:
                query = select(tickets).order_by(tickets.c.created_at.desc())
            
            # Execute query and get results
            logger.info("Executing main query...")
            result = conn.execute(query).fetchall()
            logger.info(f"Query executed, fetched {len(result) if result else 0} rows")
            
            logger.info("Converting results to list...")
            # Convert to list of dictionaries and handle date serialization
            ticket_list = []
            for row in result:
                ticket_dict = dict(row)
                # Convert datetime objects to ISO format strings
                for key, value in ticket_dict.items():
                    if isinstance(value, datetime):
                        ticket_dict[key] = value.isoformat()
                ticket_list.append(ticket_dict)
            
            # Enhanced logging
            logger.info(f"Found {len(ticket_list)} tickets matching criteria")
            if ticket_list:
                sample = ticket_list[0]
                logger.info(f"Sample ticket - ID: {sample['id']}, Ticket ID: {sample['ticket_id']}, "
                          f"Subject: {sample.get('subject', 'No subject')}, "
                          f"Created: {sample.get('created_at', 'Unknown')}")
            
            return jsonify(ticket_list)
            
    except Exception as e:
        logger.exception("Error getting tickets")
        return jsonify({'error': str(e)}), 500

@app.route('/api/tickets/<ticket_id>')
def get_ticket(ticket_id):
    ticket = ticketing.get_ticket(ticket_id)
    if ticket:
        return jsonify(ticket)
    return jsonify({'error': 'Ticket not found'}), 404

@app.route('/api/tickets/<ticket_id>/reply', methods=['POST'])
def reply_to_ticket(ticket_id):
    data = request.json
    content = data.get('content', '')
    
    # Get ticket info
    ticket = ticketing.get_ticket(ticket_id)
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404
    
    # Send email
    success = email_processor.send_email(
        ticket['sender_email'],
        ticket['subject'],
        content,
        ticket_id
    )
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to send email'}), 500

@app.route('/api/tickets/<ticket_id>/status', methods=['PUT'])
def update_ticket_status(ticket_id):
    data = request.json
    status = data.get('status')
    
    ticketing.update_ticket_status(ticket_id, status)
    return jsonify({'success': True})

@app.route('/api/attachments/<int:attachment_id>')
def download_attachment(attachment_id):
    """Download an attachment by its ID"""
    try:
        with engine.connect() as conn:
            # Get attachment details from database
            query = select(attachments).where(attachments.c.id == attachment_id)
            attachment = conn.execute(query).fetchone()
            
            if attachment and os.path.exists(attachment.file_path):
                return send_file(attachment.file_path, 
                               as_attachment=True, 
                               download_name=attachment.filename)
            
            return jsonify({'error': 'File not found'}), 404
    except SQLAlchemyError as e:
        logger.error(f"Error downloading attachment {attachment_id}: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/admin/check-emails', methods=['POST'])
def admin_check_emails():
    """Protected admin endpoint to trigger an immediate email check.

    Provide header X-Admin-Token or ?token= in query string. ADMIN_TOKEN must be
    set in environment for this endpoint to accept requests.
    """
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401

    try:
        email_processor.check_emails()
        return jsonify({'success': True})
    except Exception as e:
        logging.exception('Manual email check failed: %s', e)
        return jsonify({'error': str(e)}), 500


if ENABLE_DEV_ENDPOINTS:
    @app.route('/api/admin/check-emails/unprotected', methods=['POST'])
    def admin_check_emails_unprotected():
        """Development-only: Trigger email check without ADMIN_TOKEN protection.

        WARNING: This endpoint is unprotected and should NOT be enabled in production.
        It exists to make local/dev testing from the browser easier.
        """
        try:
            email_processor.check_emails()
            return jsonify({'success': True, 'note': 'unprotected_endpoint'})
        except Exception as e:
            logging.exception('Unprotected manual email check failed: %s', e)
            return jsonify({'error': str(e)}), 500


    @app.route('/api/admin/set-token', methods=['POST'])
    def admin_set_token():
        """Set the ADMIN_TOKEN at runtime for the current dyno process.

        This writes the token into the running process (module-level variable and app.config).
        It does NOT persist across dyno restarts — use Heroku config var ADMIN_TOKEN to persist.
        This endpoint is intentionally unprotected to allow setting via the UI in dev.
        WARNING: Using this in production is insecure. Use only for development/testing.
        """
        data = request.get_json(force=True) or {}
        token = data.get('token')
        if not token:
            return jsonify({'error': 'token_required'}), 400
        global ADMIN_TOKEN
        ADMIN_TOKEN = token
        # also store in Flask config for convenience
        app.config['ADMIN_TOKEN'] = token
        logging.info('ADMIN_TOKEN set at runtime via /api/admin/set-token (dev-only)')
        return jsonify({'success': True, 'note': 'token_set_runtime_only'})
else:
    logging.info('Dev endpoints disabled (ENABLE_DEV_ENDPOINTS not set)')


@app.route('/api/admin/imap-status', methods=['GET'])
def admin_imap_status():
    """Diagnostic endpoint to check IMAP login and unseen message count.

    Returns short list of unseen message subjects (decoded) for quick verification.
    Requires ADMIN_TOKEN.
    """
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401
    try:
        mail = email_processor.connect_imap()
        if not mail:
            return jsonify({'error': 'imap_connection_failed'}), 500

        mail.select('inbox')
        criteria = EMAIL_SEARCH_CRITERIA or 'UNSEEN'
        status, messages = mail.search(None, criteria)

        unseen_count = 0
        subjects = []
        if status == 'OK' and messages and messages[0]:
            email_ids = messages[0].split()
            unseen_count = len(email_ids)
            # fetch up to last 5 unseen subjects for quick sanity
            for email_id in email_ids[-5:]:
                st, msg_data = mail.fetch(email_id, '(RFC822)')
                if st == 'OK' and msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    subj_raw = msg.get('subject', '')
                    try:
                        decoded_parts = decode_header(subj_raw)
                        subj = ''.join([
                            part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                            for part, enc in decoded_parts
                        ])
                    except Exception:
                        subj = subj_raw
                    subjects.append(subj)

        mail.close()
        mail.logout()

        return jsonify({'imap': 'ok', 'unseen_count': unseen_count, 'subjects': subjects})
    except Exception as e:
        logging.exception('IMAP status check failed: %s', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/health')
def admin_health():
    """Return a simple health report and masked email config (no secrets)."""
    masked_pass = None
    if EMAIL_PASS:
        masked_pass = EMAIL_PASS[:2] + '...' + EMAIL_PASS[-2:]

    return jsonify({
        'status': 'ok',
        'email_host': EMAIL_HOST,
        'email_port': EMAIL_PORT,
        'email_user': EMAIL_USER,
        'email_pass_masked': masked_pass,
        'email_search_criteria': EMAIL_SEARCH_CRITERIA
    })

def email_checker():
    """Background email checker"""
    logging.info('email_checker started')
    while True:
        try:
            email_processor.check_emails()
            time.sleep(EMAIL_CHECK_INTERVAL)  # Check interval configurable via EMAIL_CHECK_INTERVAL
        except Exception as e:
            logging.exception('Email checker error: %s', e)
            time.sleep(60)  # Wait longer if there's an error

# Catch-all error handler
@app.errorhandler(Exception)
def handle_error(error):
    logger.exception("Unhandled error occurred: %s", str(error))
    return jsonify({'error': 'Internal Server Error', 'message': str(error)}), 500

def create_app():
    """Application factory for gunicorn/waitress"""
    return app

if __name__ == '__main__':
    import signal
    import sys
    
    def signal_handler(sig, frame):
        logger.info("Received signal %s, shutting down gracefully...", sig)
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logger.info("Starting email ticketing system...")
        # Force stdout to flush immediately
        import functools
        print = functools.partial(print, flush=True)
        
        # Start the appropriate server
        if os.getenv('FLASK_ENV') == 'development':
            logger.info("Starting Flask development server...")
            app.run(debug=True, host='0.0.0.0', port=8000, use_reloader=False)
        else:
            # Use waitress for production on Windows
            from waitress import serve
            logger.info("Starting waitress server on port 8000...")
            serve(app, host='0.0.0.0', port=8000, threads=4, _quiet=True)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.exception("Fatal error occurred during startup: %s", str(e))
        sys.exit(1)
