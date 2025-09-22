"""Check database tables script"""
import os
from sqlalchemy import create_engine, MetaData, inspect

# Get database URL and convert to postgresql:// format if needed
url = os.getenv('DATABASE_URL')
if url and url.startswith('postgres://'):
    url = url.replace('postgres://', 'postgresql://')

# Create engine and inspect tables
engine = create_engine(url)
inspector = inspect(engine)
print('Tables:', inspector.get_table_names())