"""Django settings for SME Cornerstone.

Configuration comes from environment variables (or a local `.env` file).
See `.env.example` for the full list. No secrets live in source code.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)
TESTING = sys.argv[1:2] == ["test"]

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not (DEBUG or TESTING):
        raise RuntimeError("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Render sets this automatically for every web service.
if render_host := os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(render_host)
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_host}")

# Vercel sets VERCEL=1 plus the production, branch and per-deployment hostnames.
ON_VERCEL = bool(os.environ.get("VERCEL"))
for name in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL", "VERCEL_URL"):
    if vercel_host := os.environ.get(name):
        ALLOWED_HOSTS.append(vercel_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{vercel_host}")

# Shows the "demo environment" ribbon and the demo sign-in hints on the login page.
DEMO_MODE = env_bool("DEMO_MODE", DEBUG)


INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Order matters only for template lookups; `core` owns the shared pieces.
    "core",
    "accounts",
    "merchants",
    "ledger",
    "passport",
    "sharing",
    "partner_api",
    "pilot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "merchants.middleware.CurrentBusinessMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.workspace",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database: a Postgres connection string in DATABASE_URL (Supabase's session pooler works).
# Falls back to a local SQLite file when it isn't set, for quick local hacking and tests.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            # Serverless functions can't hold connections between requests.
            conn_max_age=0 if ON_VERCEL else 60,
            conn_health_checks=not ON_VERCEL,
            ssl_require=DATABASE_URL.startswith("postgres") and "sslmode=" not in DATABASE_URL,
        )
    }
    if ON_VERCEL:
        # Vercel connects through Supabase's transaction pooler (port 6543), which hands each
        # transaction to any server connection: no prepared statements or server-side cursors.
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
        DATABASES["default"].setdefault("OPTIONS", {})["prepare_threshold"] = None
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Financial data: sessions last two weeks and the clock restarts on every visit,
# so an installed home-screen app stays signed in with regular use.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"


# English now; the labels are wrapped for translation so Amharic can be added
# with `makemessages -l am` without code changes.
LANGUAGE_CODE = "en"
# Plain strings, not gettext_lazy: Vercel's build serialises settings before Django's apps
# load, and a lazy translation can't be evaluated that early.
LANGUAGES = [("en", "English"), ("am", "Amharic")]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = os.environ.get("TIME_ZONE", "Africa/Addis_Ababa")

# The only currency the MVP records and aggregates.
DEFAULT_CURRENCY = "ETB"


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Vercel has no collectstatic step, so WhiteNoise serves straight from static/.
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG or TESTING or ON_VERCEL
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
# Serve straight from static/ on Vercel and in local development (no stale collectstatic copy).
WHITENOISE_USE_FINDERS = ON_VERCEL or DEBUG

# Supporting documents are stored in the database (see ledger.DocumentBlob), never
# under a public URL, and are only streamed back through an authorised view.
# Vercel rejects request bodies over 4.5 MB, so uploads there default to 4 MB.
DOCUMENT_MAX_BYTES = int(os.environ.get("DOCUMENT_MAX_BYTES", str((4 if ON_VERCEL else 5) * 1024 * 1024)))
CSV_IMPORT_MAX_BYTES = int(os.environ.get("CSV_IMPORT_MAX_BYTES", str(1024 * 1024)))
DATA_UPLOAD_MAX_MEMORY_SIZE = DOCUMENT_MAX_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = DOCUMENT_MAX_BYTES + 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Partner API rate limits (requests per minute). The default cache is per-process;
# with several workers, point CACHE_URL-style config at a shared cache (see docs/SECURITY.md).
PARTNER_API_RATE_LIMIT = int(os.environ.get("PARTNER_API_RATE_LIMIT", "60"))
PARTNER_API_AUTH_FAILURE_LIMIT = int(os.environ.get("PARTNER_API_AUTH_FAILURE_LIMIT", "20"))
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# How long a platform administrator's audited support access to one business lasts.
SUPPORT_ACCESS_MINUTES = int(os.environ.get("SUPPORT_ACCESS_MINUTES", "30"))


DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "SME Cornerstone <no-reply@localhost>")
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    # Audit events go to the database; this log carries only ids and outcomes.
    "loggers": {"cornerstone": {"handlers": ["console"], "level": "INFO"}},
}


if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["loggers"]["cornerstone"]["level"] = "WARNING"

if not (DEBUG or TESTING):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
