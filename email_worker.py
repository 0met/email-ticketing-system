"""Run the email checker loop as a separate process.

Use this as the Heroku worker: `worker: python email_worker.py` in Procfile
"""
from app import email_checker


if __name__ == '__main__':
    print('Starting email worker...')
    email_checker()
