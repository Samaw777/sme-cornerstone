# Working on SME Cornerstone

Django 5.2 app with server-rendered templates; Postgres (Supabase works) is the only service. See
README.md for the layout and docs/ for API, security and integration notes.

- Set up: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- Run tests: `python manage.py test` (uses SQLite unless `DATABASE_URL` is set)
- Run locally with demo data: `DEBUG=1 python manage.py migrate && DEBUG=1 python manage.py seed_demo && DEBUG=1 python manage.py runserver`
- After changing models: `python manage.py makemigrations <app>` and commit the migration.
- Financial integrity rules live in `ledger/services.py`, `ledger/importing.py` and `passport/metrics.py`; views call
  them rather than writing records directly. Transactions are never edited in place — use amend/reverse.
- Every bank/MFI read goes through `sharing.access.authorize()` and gets an `AccessReceipt`.
- Keep every table private: `core/apps.py` enables Row Level Security on all tables after each `migrate`.
- Templates are in `templates/`; all styles are in `static/css/app.css`.
