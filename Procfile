web: gunicorn 'app:create_app()' --workers 2 --threads 2 --bind 0.0.0.0:$PORT --log-level debug --access-logfile - --error-logfile -
worker: python email_worker.py
