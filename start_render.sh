#!/usr/bin/env bash
set -o errexit
python manage.py migrate --noinput
python manage.py initialiser_compte_render
python manage.py tester_postgis
exec python -m gunicorn presfor.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 1 --timeout 300 --access-logfile -
