"""Run the email checker loop as a separate process.

Use this as the Heroku worker: `worker: python email_worker.py` in Procfile
"""
import logging
import sys
import os
import time

from app import email_processor, ticketing, EMAIL_CHECK_INTERVAL

def main():
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )
    logger = logging.getLogger('email-worker')
    logger.setLevel(logging.INFO)
    
    logger.info('Email worker starting')
    logger.info(f'Using database at {os.getenv("SQLITE_PATH")}')
    
    while True:
        try:
            logger.info('Checking for new emails...')
            email_processor.check_emails()
            logger.info('Email check complete')
        except Exception as e:
            logger.exception('Error in email worker loop: %s', e)
        time.sleep(EMAIL_CHECK_INTERVAL)


if __name__ == '__main__':
    main()
