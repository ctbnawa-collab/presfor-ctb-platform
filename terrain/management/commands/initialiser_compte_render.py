import os
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help='Create the first administrator from Render secrets, never rotate an existing password.'
    def handle(self,*args,**options):
        User=get_user_model()
        if User.objects.filter(is_active=True,is_superuser=True).exists():
            self.stdout.write('Compte administrateur déjà présent.')
            return
        username=os.environ.get('DJANGO_SUPERUSER_USERNAME','').strip()
        password=os.environ.get('DJANGO_SUPERUSER_PASSWORD','')
        email=os.environ.get('DJANGO_SUPERUSER_EMAIL','').strip()
        if not username or not password:
            raise CommandError('Configurer DJANGO_SUPERUSER_USERNAME et DJANGO_SUPERUSER_PASSWORD dans Render.')
        if User.objects.filter(username=username).exists():
            raise CommandError('Le compte existe sans droits administrateur. Vérifier ce compte avant initialisation.')
        User.objects.create_superuser(username=username,email=email,password=password)
        self.stdout.write('Compte administrateur initialisé.')
