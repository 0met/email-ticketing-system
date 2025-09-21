import os
import sqlite3
import smtplib
import imaplib
import email
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
from threading import Thread
import time
import schedule

app = Flask(__name__)
CORS(app)

# Configuration - Set these as environment variables
EMAIL_HOST = os.getenv('EMAIL_HOST', 'imap.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER', 'your-email@gmail.com')
EMAIL_PASS = os.getenv('EMAIL_PASS', 'your-app-password')
UPLOAD_FOLDER = 'attachments'

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

class TicketingSystem:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        # Create tickets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE,
                subject TEXT,
                sender_email TEXT,
                sender_name TEXT,
                content TEXT,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'medium',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigned_to TEXT,
                category TEXT
            )
        ''')
        
        # Create responses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT,
                response_type TEXT,
                sender TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets (ticket_id)
            )
        ''')
        
        # Create attachments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT,
                response_id INTEGER,
                filename TEXT,
                file_path TEXT,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets (ticket_id),
                FOREIGN KEY (response_id) REFERENCES responses (id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def generate_ticket_id(self, email_content):
        """Generate unique ticket ID"""
        hash_object = hashlib.md5(f"{email_content}{datetime.now()}".encode())
        return f"TKT-{hash_object.hexdigest()[:8].upper()}"
    
    def create_ticket(self, subject, sender_email, sender_name, content, attachments=None):
        """Create new ticket"""
        ticket_id = self.generate_ticket_id(f"{subject}{sender_email}")
        
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO tickets (ticket_id, subject, sender_email, sender_name, content)
            VALUES (?, ?, ?, ?, ?)
        ''', (ticket_id, subject, sender_email, sender_name, content))
        
        if attachments:
            for attachment in attachments:
                cursor.execute('''
                    INSERT INTO attachments (ticket_id, filename, file_path, file_size)
                    VALUES (?, ?, ?, ?)
                ''', (ticket_id, attachment['filename'], attachment['path'], attachment['size']))
        
        conn.commit()
        conn.close()
        
        return ticket_id
    
    def add_response(self, ticket_id, response_type, sender, content):
        """Add response to ticket"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO responses (ticket_id, response_type, sender, content)
            VALUES (?, ?, ?, ?)
        ''', (ticket_id, response_type, sender, content))
        
        # Update ticket timestamp
        cursor.execute('''
            UPDATE tickets SET updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?
        ''', (ticket_id,))
        
        response_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return response_id
    
    def get_tickets(self, status=None):
        """Get all tickets or filter by status"""
        conn = sqlite3.connect('tickets.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if status:
            cursor.execute('SELECT * FROM tickets WHERE status = ? ORDER BY updated_at DESC', (status,))
        else:
            cursor.execute('SELECT * FROM tickets ORDER BY updated_at DESC')
        
        tickets = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return tickets
    
    def get_ticket(self, ticket_id):
        """Get specific ticket with responses"""
        conn = sqlite3.connect('tickets.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get ticket
        cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
        ticket = cursor.fetchone()
        
        if ticket:
            ticket = dict(ticket)
            
            # Get responses
            cursor.execute('SELECT * FROM responses WHERE ticket_id = ? ORDER BY created_at ASC', (ticket_id,))
            ticket['responses'] = [dict(row) for row in cursor.fetchall()]
            
            # Get attachments
            cursor.execute('SELECT * FROM attachments WHERE ticket_id = ? ORDER BY created_at ASC', (ticket_id,))
            ticket['attachments'] = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return ticket
    
    def update_ticket_status(self, ticket_id, status):
        """Update ticket status"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE tickets SET status = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE ticket_id = ?
        ''', (status, ticket_id))
        
        conn.commit()
        conn.close()

# Initialize ticketing system
ticketing = TicketingSystem()

class EmailProcessor:
    def __init__(self):
        self.processed_emails = set()
    
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
            mail.login(EMAIL_USER, EMAIL_PASS)
            return mail
        except Exception as e:
            print(f"IMAP connection failed: {e}")
            return None
    
    def process_email(self, msg):
        """Process incoming email and create ticket"""
        try:
            subject = msg.get('subject', 'No Subject')
            sender = msg.get('from', '')
            sender_email = sender.split('<')[-1].replace('>', '') if '<' in sender else sender
            sender_name = sender.split('<')[0].strip() if '<' in sender else sender_email
            
            # Get email content
            content = ""
            attachments = []
            
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    elif part.get_content_type() == "text/html":
                        if not content:  # Use HTML if no plain text
                            content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    
                    # Handle attachments
                    filename = part.get_filename()
                    if filename:
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        with open(file_path, 'wb') as f:
                            f.write(part.get_payload(decode=True))
                        
                        attachments.append({
                            'filename': filename,
                            'path': file_path,
                            'size': os.path.getsize(file_path)
                        })
            else:
                content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            
            # Create ticket
            ticket_id = ticketing.create_ticket(subject, sender_email, sender_name, content, attachments)
            print(f"Created ticket {ticket_id} for {sender_email}")
            
            return ticket_id
            
        except Exception as e:
            print(f"Error processing email: {e}")
            return None
    
    def check_emails(self):
        """Check for new emails and process them"""
        mail = self.connect_imap()
        if not mail:
            return
        
        try:
            mail.select('inbox')
            status, messages = mail.search(None, 'UNSEEN')
            
            if status == 'OK':
                email_ids = messages[0].split()
                
                for email_id in email_ids:
                    status, msg_data = mail.fetch(email_id, '(RFC822)')
                    
                    if status == 'OK':
                        email_body = msg_data[0][1]
                        email_message = email.message_from_bytes(email_body)
                        
                        # Check if we've already processed this email
                        email_hash = hashlib.md5(email_body).hexdigest()
                        if email_hash not in self.processed_emails:
                            self.process_email(email_message)
                            self.processed_emails.add(email_hash)
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            print(f"Error checking emails: {e}")
    
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

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/tickets')
def get_tickets():
    status = request.args.get('status')
    tickets = ticketing.get_tickets(status)
    return jsonify(tickets)

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
    conn = sqlite3.connect('tickets.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM attachments WHERE id = ?', (attachment_id,))
    attachment = cursor.fetchone()
    conn.close()
    
    if attachment and os.path.exists(attachment['file_path']):
        return send_file(attachment['file_path'], as_attachment=True, download_name=attachment['filename'])
    
    return jsonify({'error': 'File not found'}), 404

def email_checker():
    """Background email checker"""
    while True:
        try:
            email_processor.check_emails()
            time.sleep(30)  # Check every 30 seconds
        except Exception as e:
            print(f"Email checker error: {e}")
            time.sleep(60)  # Wait longer if there's an error

# Start email checker in background
if __name__ == '__main__':
    # Start email checker thread
    email_thread = Thread(target=email_checker, daemon=True)
    email_thread.start()
    
    app.run(debug=True, host='0.0.0.0', port=5000)
