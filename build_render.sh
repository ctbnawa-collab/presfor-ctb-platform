#!/usr/bin/env bash
set -o errexit
python -m pip install -r requirements.txt
python -m unittest terrain.tests_parcelles_count
python -m py_compile terrain/performance.py
python manage.py collectstatic --noinput
