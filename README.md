# email-ticketing-system

## Heroku deployment & debugging

1. Ensure Procfile contains:

```
web: gunicorn app:app
worker: python email_worker.py
```

2. Set required Heroku Config Vars (example):

Use the Heroku CLI or Dashboard to set:

- EMAIL_USER: your Gmail address
- EMAIL_PASS: app password (if you use 2FA) or OAuth token
- ADMIN_TOKEN: a random secret string for admin endpoints

Example CLI:

```powershell
heroku config:set EMAIL_USER=emailticket4@gmail.com EMAIL_PASS=<your-app-password> ADMIN_TOKEN=<token> --app <your-heroku-app-name>
heroku ps:scale worker=1 --app <your-heroku-app-name>
```

3. Debugging & manual checks

- IMAP status endpoint (GET):

	GET https://<your-app>.herokuapp.com/api/admin/imap-status

	Provide header `X-Admin-Token: <ADMIN_TOKEN>`

- Trigger manual check (POST):

	POST https://<your-app>.herokuapp.com/api/admin/check-emails

	Provide header `X-Admin-Token: <ADMIN_TOKEN>`

4. Quick local IMAP test

Set `EMAIL_USER` and `EMAIL_PASS` locally and run:

```powershell
$env:EMAIL_USER='emailticket4@gmail.com'
$env:EMAIL_PASS='<your-app-password>'
python check_imap.py
```

5. Watch logs

```powershell
heroku logs --tail --app <your-heroku-app-name>
```
