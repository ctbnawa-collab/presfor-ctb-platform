from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.contrib.auth import get_user_model
class Command(BaseCommand):
    help='Créer le premier compte administrateur si nécessaire'
    def handle(self,*args,**options):
        if not get_user_model().objects.filter(is_active=True,is_superuser=True).exists():
            self.stdout.write('Créez votre compte administrateur pour la plateforme.')
            call_command('createsuperuser',interactive=True)
        else:self.stdout.write('Compte administrateur déjà configuré.')
