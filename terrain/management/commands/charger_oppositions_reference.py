"""Charge une référence privée fournie par l'administrateur Render, une fois par version."""
import os,json,gzip,base64,hashlib
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from terrain.models import ChargementReference
from terrain.imports import en_date
from terrain.oppositions_classeur import enregistrer_dossiers

class Command(BaseCommand):
    def handle(self,*args,**options):
        count=int(os.environ.get('PRESFOR_OPPOSITIONS_BLOCS','0'))
        if not count:return
        if not 1<=count<=100:raise CommandError('Nombre de blocs de référence invalide')
        value=''.join(os.environ.get('PRESFOR_OPPOSITIONS_'+str(i),'') for i in range(count))
        revision=hashlib.sha256(value.encode()).hexdigest()
        if revision!=os.environ.get('PRESFOR_OPPOSITIONS_SHA256'):raise CommandError('Référence privée incomplète')
        if ChargementReference.objects.filter(cle='oppositions',revision=revision).exists():
            self.stdout.write('Référence oppositions déjà chargée.');return
        try:
            rows=json.loads(gzip.decompress(base64.b64decode(value,validate=True)))
            if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise ValueError('Nombre de dossiers invalide')
            for row in rows:row['date_opposition']=en_date(row.get('date_opposition'))
        except Exception as exc:raise CommandError('Référence oppositions illisible') from exc
        with transaction.atomic():
            result=enregistrer_dossiers(rows)
            ChargementReference.objects.update_or_create(cle='oppositions',defaults={'revision':revision})
        self.stdout.write(f"Référence oppositions chargée : {len(rows)} dossiers, {result['cree']} créés, {result['actualise']} actualisés.")
