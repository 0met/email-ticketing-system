"""Run the email checker loop as a separate process.

Use this as the Heroku worker: `worker: python email_worker.py` in Procfile
"""
import logging
import sys
import os
import time

from app import check_emails

# Configure logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s'
)
logger = logging.getLogger('email-worker')
logger.setLevel(logging.INFO)

# Get check interval from environment
EMAIL_CHECK_INTERVAL = int(os.getenv('EMAIL_CHECK_INTERVAL', 30))

def main():
    logger.info('Email worker starting')
    logger.info('Using database at %s', os.getenv('SQLITE_PATH', 'tickets.db'))
    logger.info('Check interval: %d seconds', EMAIL_CHECK_INTERVAL)
    
    while True:
        try:
            logger.info('Checking for new emails...')
            check_emails()
            logger.info('Email check complete')
        except Exception as e:
            logger.exception('Error in email worker loop: %s', e)
            # Still wait on error to avoid rapid retries
        time.sleep(EMAIL_CHECK_INTERVAL)

if __name__ == '__main__':
    main()
