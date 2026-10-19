"""
Import d'une couche shapefile en ligne de commande.

    python manage.py importer_couche publicite  Pub_Global_nette_.zip --remplacer
    python manage.py importer_couche exclusions Plle_a_supprimer.zip --motif geometrie
"""
from django.core.management.base import BaseCommand, CommandError

from terrain.geo import ShapefileIllisible
from terrain.shapefiles import importer_exclusions, importer_publicite


class Command(BaseCommand):
    help = "Importe une couche shapefile (.zip) dans la base locale."

    def add_arguments(self, parser):
        parser.add_argument('couche', choices=['publicite', 'exclusions'])
        parser.add_argument('archive', help='Chemin de l\'archive .zip')
        parser.add_argument('--remplacer', action='store_true',
                            help='Vide la couche existante avant l\'import.')
        parser.add_argument('--motif', default='geometrie',
                            help="Motif d'exclusion (couche « exclusions » uniquement).")

    def handle(self, *args, **options):
        chemin = options['archive']
        try:
            with open(chemin, 'rb') as f:
                if options['couche'] == 'publicite':
                    resume = importer_publicite(f, chemin, options['remplacer'])
                else:
                    resume = importer_exclusions(f, chemin, options['motif'],
                                                 options['remplacer'])
        except FileNotFoundError:
            raise CommandError(f'Archive introuvable : {chemin}')
        except ShapefileIllisible as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f"{resume['importees']} objet(s) importé(s) sur {resume['total_couche']}."))
        self.stdout.write(f"Système de coordonnées lu : {resume['crs']}")
        if resume.get('sans_geometrie'):
            self.stdout.write(self.style.WARNING(
                f"{resume['sans_geometrie']} objet(s) sans géométrie exploitable."))
        if resume['ignorees']:
            self.stdout.write(self.style.WARNING(
                f"{resume['ignorees']} objet(s) ignoré(s) (code absent ou doublon)."))
