#!/usr/bin/env bash
set -o errexit
python -m pip install -r requirements.txt
python -m unittest terrain.tests_parcelles_count terrain.tests_agents_registre terrain.tests_equipes_regression
python manage.py test terrain.tests_agents_affichage terrain.tests_agent_form terrain.tests_rendement_vue --noinput
python manage.py makemigrations --check --dry-run
python -m py_compile terrain/performance.py terrain/views.py terrain/management/commands/charger_agents_reference.py
python manage.py collectstatic --noinput
