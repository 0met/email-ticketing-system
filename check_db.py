import os
import logging
from sqlalchemy import create_engine, MetaData, text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get database URL
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def check_db():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        # Check if tables exist
        result = conn.execute(text("""
            SELECT tablename 
            FROM pg_tables 
            WHERE schemaname = 'public'
        """))
        tables = [row[0] for row in result]
        print("Tables found:", tables)

        # Check tickets table content
        if 'tickets' in tables:
            result = conn.execute(text("SELECT COUNT(*) FROM tickets"))
            count = result.scalar()
            print(f"Found {count} tickets")

            if count > 0:
                result = conn.execute(text("SELECT * FROM tickets ORDER BY created_at DESC LIMIT 1"))
                latest = dict(result.first())
                print("Latest ticket:", latest)

if __name__ == '__main__':
    check_db()