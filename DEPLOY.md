# Deploying SME Cornerstone

Two free accounts get this live: **Supabase** (database) and **Render** (hosting). The code lives
on GitHub.

## 1. Get your Supabase connection string

You already have a Supabase project. You don't need to run any SQL by hand — Django creates the
tables itself.

1. Open your project on [supabase.com](https://supabase.com/dashboard) and click **Connect** at the
   top of the page.
2. Under **Connection String**, pick the **Session pooler** option and copy the URI. It looks like:
   ```
   postgresql://postgres.abcdefghijkl:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
   Use the session pooler rather than the "Direct connection": the direct one is IPv6-only, and
   Render (like many hosts) can only reach it over IPv4.
3. Replace `[YOUR-PASSWORD]` with the database password you chose when creating the project
   (forgot it? **Project Settings → Database → Reset database password**). If the password has
   characters like `@`, `#`, `/` or `:`, URL-encode them (e.g. `@` → `%40`), or reset it to
   letters and numbers only.

That full string is your `DATABASE_URL`.

## 2. Create the tables (optional, from your computer)

Render runs migrations on every deploy, so you can skip this. To check your connection first:

```bash
cp .env.example .env    # paste DATABASE_URL in, and set SECRET_KEY
python manage.py migrate
```

Afterwards, Supabase's **Table Editor** shows the app's tables (`accounts_user`, `ledger_transaction`,
`sharing_consentgrant`, …), each marked as having RLS enabled.

## 3. Deploy on Render

1. Go to [render.com](https://render.com) and sign up / log in with GitHub.
2. Click **New → Blueprint**, pick this repository, and confirm. Render reads `render.yaml`, which
   sets up the build (`build.sh`: install, collect static files, migrate) and a generated
   `SECRET_KEY`.
3. When asked for `DATABASE_URL`, paste the connection string from step 1.
4. Click **Apply**. After a few minutes you'll get a URL like `https://sme-cornerstone-xxxx.onrender.com`.
   Open it and create your account.

`render.yaml` sets `DEMO_MODE=0`. For a demo-only deployment you can set `DEMO_MODE=1` and run
`python manage.py seed_demo` once from the Render **Shell**. Never do this on a database with real data.

Uploaded documents are stored in the database, so they survive redeploys (Render's disk is
ephemeral).

Render's free tier sleeps after 15 minutes without visitors, so the first page load after a quiet
spell takes 30–60 seconds. The paid Starter plan stays awake.

## Optional extras

- **Password reset emails**: add `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
  `EMAIL_HOST_PASSWORD` and `DEFAULT_FROM_EMAIL` in Render → your service → **Environment**. Any
  SMTP provider works (Gmail with an app password, Resend, Brevo, …). Without them, reset links
  only appear in Render's logs.
- **Platform administrator**: in Render → your service → **Shell**, run
  `python manage.py createsuperuser` (creates a platform administrator), then visit `/console/`.
- **Custom domain**: Render → your service → **Settings → Custom Domains**. Then add the domain to
  `ALLOWED_HOSTS` and its `https://` origin to `CSRF_TRUSTED_ORIGINS`.
- **Updating the app**: push to `main`; Render redeploys and runs any new migrations.
