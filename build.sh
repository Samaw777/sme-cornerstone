#!/usr/bin/env bash
# Render build command: install, collect static files, apply database migrations.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
