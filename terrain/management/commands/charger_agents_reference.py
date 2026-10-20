import json,os
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from terrain.models import AgentRendement

class Command(BaseCommand):
    help='Complète le référentiel depuis la configuration privée, sans écraser les fiches existantes.'
    def handle(self,*args,**options):
        raw=os.environ.get('PRESFOR_AGENTS_JSON')
        if not raw:
            self.stdout.write(f'Référentiel agents disponible : {AgentRendement.objects.count()} agents.');return
        try:
            rows=json.loads(raw)
            if not isinstance(rows,list):raise ValueError()
            for row in rows:
                for key,maximum in [('departement',40),('code_agent',40),('nom_agent',180),('superviseur',180)]:
                    if not isinstance(row.get(key),str) or not row[key].strip() or len(row[key])>maximum:raise ValueError()
                if row['departement'] not in ('BUYO','SOUBRE','MEAGUI'):raise ValueError()
                if not isinstance(row.get('controleur',''),str) or len(row.get('controleur',''))>120:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise CommandError('Référentiel privé des agents invalide, aucune modification effectuée.')
        crees=0
        with transaction.atomic():
            for row in rows:
                _,created=AgentRendement.objects.get_or_create(departement=row['departement'],code_agent=row['code_agent'],defaults={'nom_agent':row['nom_agent'],'superviseur':row['superviseur'],'controleur':row.get('controleur','')})
                crees+=int(created)
        self.stdout.write(f'Référentiel agents disponible : {AgentRendement.objects.count()} agents, {crees} ajoutés.')
