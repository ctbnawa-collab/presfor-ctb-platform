import json
import os
from pathlib import Path
from django.db import migrations

def load(apps, schema_editor):
    Agent = apps.get_model('terrain','AgentRendement')
    source=Path(__file__).resolve().parents[1]/'data'/'agents.json'
    raw = os.environ.get('PRESFOR_AGENTS_JSON')
    rows = json.loads(raw) if raw else (json.loads(source.read_text()) if source.exists() else [])
    for row in rows:
        dep=row.pop('departement');code=row.pop('code_agent')
        Agent.objects.update_or_create(departement=dep,code_agent=code,defaults=row)

class Migration(migrations.Migration):
    dependencies=[('terrain','0004_lignerendement_parametresrendement_agentrendement')]
    operations=[migrations.RunPython(load,migrations.RunPython.noop)]
