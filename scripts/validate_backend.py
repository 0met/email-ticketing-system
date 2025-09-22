#!/usr/bin/env python3
"""
Validation script for the email-ticketing-system backend.

Usage:
  python scripts/validate_backend.py --url http://localhost:5000
  python scripts/validate_backend.py --url https://your-app.herokuapp.com --db tickets.db

What it checks:
  - GET /api/tickets returns 200 and JSON array
  - GET /api/tickets/<id> for the first ticket (if any) returns 200
  - POST /api/tickets/<id>/responses accepts JSON (requires admin token if enabled)
  - PUT /api/tickets/<id> accepts JSON status updates
  - Local SQLite DB file exists and contains expected tables (`tickets`, optional `processed_messages`)

This is a lightweight smoke-test you can run before pushing changes.
"""
import argparse
import json
import os
import sqlite3
import sys
from urllib.parse import urljoin

try:
    import requests
except Exception:
    print('The requests library is required. Install with: pip install requests')
    raise


def check_url(url):
    if not url.startswith('http'):
        raise ValueError('url must start with http:// or https://')


def maybe_print(v):
    print(v)


def api_get(url, path):
    full = urljoin(url, path)
    r = requests.get(full, timeout=10)
    return r


def api_post(url, path, payload, headers=None):
    full = urljoin(url, path)
    r = requests.post(full, json=payload, headers=headers or {}, timeout=10)
    return r


def api_put(url, path, payload, headers=None):
    full = urljoin(url, path)
    r = requests.put(full, json=payload, headers=headers or {}, timeout=10)
    return r


def check_db(dbpath):
    out = {'exists': False, 'tables': []}
    if not os.path.isfile(dbpath):
        return out
    out['exists'] = True
    try:
        conn = sqlite3.connect(dbpath)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        rows = cur.fetchall()
        out['tables'] = [r[0] for r in rows]
    except Exception as e:
        out['error'] = str(e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True, help='Base URL of the app, e.g. http://localhost:5000')
    p.add_argument('--db', default='tickets.db', help='Path to local sqlite DB file to inspect')
    p.add_argument('--admin-token', default=None, help='Admin token (if server requires X-Admin-Token header)')
    args = p.parse_args()

    base = args.url.rstrip('/') + '/'
    check_url(base)
    headers = {}
    if args.admin_token:
        headers['X-Admin-Token'] = args.admin_token

    print('\n== Basic endpoint checks ==')
    try:
        r = api_get(base, '/api/tickets')
        print('GET /api/tickets ->', r.status_code)
        if r.status_code == 200:
            try:
                j = r.json()
                print('  returned JSON type:', type(j).__name__, 'len=', len(j) if isinstance(j, list) else 'n/a')
            except Exception as e:
                print('  failed to parse JSON:', e)
        else:
            print('  body:', r.text[:400])
    except Exception as e:
        print('  request failed:', e)

    first_id = None
    try:
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, list) and len(j) > 0:
                first = j[0]
                # try common fields
                first_id = first.get('ticket_id') or first.get('id') or first.get('ticketId')
                print('  sample ticket id:', first_id)
    except Exception:
        pass

    if first_id:
        print('\n== Ticket detail and mutation checks for id:', first_id, '==')
        try:
            r2 = api_get(base, f'/api/tickets/{first_id}')
            print('GET /api/tickets/{id} ->', r2.status_code)
            if r2.status_code == 200:
                try:
                    print('  sample:', r2.json())
                except Exception:
                    print('  cannot parse JSON body')
            else:
                print('  body:', r2.text[:400])
        except Exception as e:
            print('  request failed:', e)

        # try posting a small response (may require admin token)
        try:
            rp = api_post(base, f'/api/tickets/{first_id}/responses', {'content':'smoke-test from validate_backend.py'}, headers=headers)
            print('POST /api/tickets/{id}/responses ->', rp.status_code)
            print('  body:', (rp.text or '')[:400])
        except Exception as e:
            print('  post failed:', e)

        try:
            ru = api_put(base, f'/api/tickets/{first_id}', {'status':'pending'}, headers=headers)
            print('PUT /api/tickets/{id} ->', ru.status_code)
            print('  body:', (ru.text or '')[:400])
        except Exception as e:
            print('  put failed:', e)
    else:
        print('\nNo sample ticket id found; skipping detail/mutation checks')

    print('\n== Local DB checks ==')
    d = check_db(args.db)
    print('DB path:', args.db, 'exists=', d['exists'])
    if d.get('tables'):
        print('  tables:', d['tables'])
        if 'processed_messages' in d['tables']:
            print('  dedup table found: processed_messages')
        else:
            print('  dedup table NOT found (optional: processed_messages)')
    if d.get('error'):
        print('  DB error:', d['error'])

    print('\nValidation complete.')


if __name__ == '__main__':
    main()
