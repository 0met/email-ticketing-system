"""Run the email checker loop as a separate process.

Use this as the Heroku worker: `worker: python email_worker.py` in Procfile
"""
import logging
import sys

from app import email_checker


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.info('email_worker starting')
    try:
        email_checker()
    except Exception:
        logging.exception('email_worker crashed')
        raise


if __name__ == '__main__':
    main()
