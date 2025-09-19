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
from datetime import datetime, timedelta
import hashlib
import base64
import json
from threading import Thread
import time
import schedule
import atexit

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

# Global flag for email checker
email_checker_running = False

class TicketingSystem:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        # Create tickets table with enhanced fields
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
                category TEXT,
                first_response_at TIMESTAMP,
                closed_at TIMESTAMP,
                response_time_hours REAL
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
        
        # Create analytics table for tracking metrics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analytics_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE UNIQUE,
                tickets_created INTEGER DEFAULT 0,
                tickets_closed INTEGER DEFAULT 0,
                avg_response_time REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add indexes for better performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tickets_sender_email ON tickets(sender_email)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_responses_ticket_id ON responses(ticket_id)')
        
        conn.commit()
        conn.close()
    
    def generate_ticket_id(self, email_content):
        """Generate unique ticket ID"""
        hash_object = hashlib.md5(f"{email_content}{datetime.now()}".encode())
        return f"TKT-{hash_object.hexdigest()[:8].upper()}"
    
    def create_ticket(self, subject, sender_email, sender_name, content, attachments=None):
        """Create new ticket with priority assignment"""
        ticket_id = self.generate_ticket_id(f"{subject}{sender_email}")
        priority = self.assign_priority(subject, content)
        
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO tickets (ticket_id, subject, sender_email, sender_name, content, priority)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ticket_id, subject, sender_email, sender_name, content, priority))
        
        if attachments:
            for attachment in attachments:
                cursor.execute('''
                    INSERT INTO attachments (ticket_id, filename, file_path, file_size)
                    VALUES (?, ?, ?, ?)
                ''', (ticket_id, attachment['filename'], attachment['path'], attachment['size']))
        
        # Update daily analytics
        today = datetime.now().date()
        cursor.execute('''
            INSERT OR IGNORE INTO analytics_daily (date, tickets_created)
            VALUES (?, 1)
        ''', (today,))
        
        cursor.execute('''
            UPDATE analytics_daily 
            SET tickets_created = tickets_created + 1 
            WHERE date = ?
        ''', (today,))
        
        conn.commit()
        conn.close()
        
        return ticket_id
    
    def assign_priority(self, subject, content):
        """Smart priority assignment based on keywords"""
        text = f"{subject} {content}".lower()
        
        urgent_keywords = [
            'urgent', 'emergency', 'critical', 'down', 'broken', 'error', 
            'crash', 'failure', 'outage', 'bug', 'issue', 'problem'
        ]
        
        high_keywords = [
            'important', 'asap', 'soon', 'quickly', 'help', 'support',
            'question', 'inquiry'
        ]
        
        if any(keyword in text for keyword in urgent_keywords):
            return 'urgent'
        elif any(keyword in text for keyword in high_keywords):
            return 'high'
        else:
            return 'normal'
    
    def add_response(self, ticket_id, response_type, sender, content):
        """Add response to ticket and update metrics"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        # Insert response
        cursor.execute('''
            INSERT INTO responses (ticket_id, response_type, sender, content)
            VALUES (?, ?, ?, ?)
        ''', (ticket_id, response_type, sender, content))
        
        # Update ticket timestamp
        cursor.execute('''
            UPDATE tickets SET updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?
        ''', (ticket_id,))
        
        # If this is the first response, calculate response time
        if response_type == 'outgoing':
            cursor.execute('''
                SELECT created_at, first_response_at FROM tickets WHERE ticket_id = ?
            ''', (ticket_id,))
            
            ticket_data = cursor.fetchone()
            if ticket_data and not ticket_data[1]:  # No first response yet
                created_at = datetime.fromisoformat(ticket_data[0])
                response_time = (datetime.now() - created_at).total_seconds() / 3600  # hours
                
                cursor.execute('''
                    UPDATE tickets 
                    SET first_response_at = CURRENT_TIMESTAMP, response_time_hours = ?
                    WHERE ticket_id = ?
                ''', (response_time, ticket_id))
        
        response_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return response_id
    
    def get_tickets(self, status=None, search_query=None, date_from=None, date_to=None):
        """Get tickets with advanced filtering"""
        conn = sqlite3.connect('tickets.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = 'SELECT * FROM tickets WHERE 1=1'
        params = []
        
        if status:
            query += ' AND status = ?'
            params.append(status)
        
        if search_query:
            query += ' AND (subject LIKE ? OR content LIKE ? OR sender_email LIKE ? OR sender_name LIKE ?)'
            search_param = f'%{search_query}%'
            params.extend([search_param] * 4)
        
        if date_from:
            query += ' AND DATE(created_at) >= ?'
            params.append(date_from)
        
        if date_to:
            query += ' AND DATE(created_at) <= ?'
            params.append(date_to)
        
        query += ' ORDER BY updated_at DESC'
        
        cursor.execute(query, params)
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
        """Update ticket status and track closure metrics"""
        conn = sqlite3.connect('tickets.db')
        cursor = conn.cursor()
        
        # Update status
        cursor.execute('''
            UPDATE tickets SET status = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE ticket_id = ?
        ''', (status, ticket_id))
        
        # If closing ticket, update closure metrics
        if status == 'closed':
            cursor.execute('''
                UPDATE tickets SET closed_at = CURRENT_TIMESTAMP WHERE ticket_id = ?
            ''', (ticket_id,))
            
            # Update daily analytics
            today = datetime.now().date()
            cursor.execute('''
                INSERT OR IGNORE INTO analytics_daily (date, tickets_closed)
                VALUES (?, 1)
            ''', (today,))
            
            cursor.execute('''
                UPDATE analytics_daily 
                SET tickets_closed = tickets_closed + 1 
                WHERE date = ?
            ''', (today,))
        
        conn.commit()
        conn.close()
    
    def get_analytics_data(self):
        """Get analytics data for dashboard"""
        conn = sqlite3.connect('tickets.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Basic stats
        cursor.execute('SELECT COUNT(*) FROM tickets')
        total_tickets = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "open"')
        open_tickets = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "closed"')
        closed_tickets = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "pending"')
        pending_tickets = cursor.fetchone()[0]
        
        # Average response time
        cursor.execute('SELECT AVG(response_time_hours) FROM tickets WHERE response_time_hours IS NOT NULL')
        avg_response_result = cursor.fetchone()
        avg_response_hours = avg_response_result[0] if avg_response_result[0] else 0
        
        # Recent trends (last 7 days)
        cursor.execute('''
            SELECT DATE(created_at) as date, COUNT(*) as count 
            FROM tickets 
            WHERE created_at >= date('now', '-7 days')
            GROUP BY DATE(created_at)
            ORDER BY date
        ''')
        daily_trends = [dict(row) for row in cursor.fetchall()]
        
        # Priority distribution
        cursor.execute('''
            SELECT priority, COUNT(*) as count 
            FROM tickets 
            GROUP BY priority
        ''')
        priority_distribution = [dict(row) for row in cursor.fetchall()]
        
        # Top customers by ticket count
        cursor.execute('''
            SELECT sender_email, sender_name, COUNT(*) as ticket_count
            FROM tickets 
            GROUP BY sender_email 
            ORDER BY ticket_count DESC 
            LIMIT 5
        ''')
        top_customers = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            'total_tickets': total_tickets,
            'open_tickets': open_tickets,
            'closed_tickets': closed_tickets,
            'pending_tickets': pending_tickets,
            'avg_response_hours': round(avg_response_hours, 2) if avg_response_hours else 0,
            'daily_trends': daily_trends,
            'priority_distribution': priority_distribution,
            'top_customers': top_customers
        }

# Initialize ticketing system
ticketing = TicketingSystem()

class EmailProcessor:
    def __init__(self):
        self.processed_emails = set()
        print(f"EmailProcessor initialized with user: {EMAIL_USER}")
    
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            print(f"Attempting IMAP connection to {EMAIL_HOST}:{EMAIL_PORT}")
            mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
            print(f"Logging in with user: {EMAIL_USER}")
            mail.login(EMAIL_USER, EMAIL_PASS)
            print("IMAP connection successful!")
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
            
            print(f"Processing email from {sender_email}, subject: {subject}")
            
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
        print("Checking for new emails...")
        mail = self.connect_imap()
        if not mail:
            print("Failed to connect to IMAP server")
            return
        
        try:
            mail.select('inbox')
            status, messages = mail.search(None, 'UNSEEN')
            
            if status == 'OK':
                email_ids = messages[0].split()
                print(f"Found {len(email_ids)} unread emails")
                
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
            print("Email check completed")
            
        except Exception as e:
            print(f"Error checking emails: {e}")
    
    def send_email(self, to_email, subject, content, ticket_id):
        """Send email response"""
        try:
            print(f"Sending email to {to_email} for ticket {ticket_id}")
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
            print(f"Email sent successfully to {to_email}")
            
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
    search_query = request.args.get('search')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    tickets = ticketing.get_tickets(status, search_query, date_from, date_to)
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

@app.route('/api/analytics')
def get_analytics():
    """Get comprehensive analytics data"""
    analytics_data = ticketing.get_analytics_data()
    return jsonify(analytics_data)

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

@app.route('/api/check-emails', methods=['POST'])
def manual_email_check():
    """Manual email check for testing"""
    try:
        email_processor.check_emails()
        return jsonify({'success': True, 'message': 'Email check completed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def email_checker_background():
    """Background email checker function"""
    global email_checker_running
    email_checker_running = True
    print("Email checker background thread started")
    
    while email_checker_running:
        try:
            email_processor.check_emails()
            time.sleep(30)  # Check every 30 seconds
        except Exception as e:
            print(f"Email checker background error: {e}")
            time.sleep(60)  # Wait longer if there's an error

def stop_email_checker():
    """Stop email checker on app shutdown"""
    global email_checker_running
    email_checker_running = False
    print("Email checker stopped")

# Register cleanup function
atexit.register(stop_email_checker)

# Start email checker thread when app starts
def start_email_checker():
    if not email_checker_running:
        email_thread = Thread(target=email_checker_background, daemon=True)
        email_thread.start()
        print("Started email checker thread")

if __name__ == '__main__':
    # Start email checker thread
    start_email_checker()
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
else:
    # For production (Gunicorn), start the email checker
    start_email_checker()
