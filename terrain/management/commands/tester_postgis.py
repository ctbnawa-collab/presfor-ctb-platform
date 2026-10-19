"""
Diagnostic en ligne de commande de la base terrain.

    python manage.py tester_postgis
    python manage.py tester_postgis --tables
    python manage.py tester_postgis --requete parcelles --debut 2026-06-01 --fin 2026-06-30
"""
from django.core.management.base import BaseCommand

from terrain.db import (PostgisIndisponible, bornes_dates_disponibles,
                        executer_fichier, lister_tables, tester_connexion)


class Command(BaseCommand):
    help = "Teste la connexion à la base terrain PostGIS et les requêtes d'extraction."

    def add_arguments(self, parser):
        parser.add_argument('--tables', action='store_true',
                            help='Liste les tables du schéma public.')
        parser.add_argument('--requete', choices=['parcelles', 'bornes', 'oppositions'],
                            help='Exécute une requête et affiche les 5 premières lignes.')
        parser.add_argument('--debut', default='2026-01-01', help='Date de début (AAAA-MM-JJ).')
        parser.add_argument('--fin', default='2026-12-31', help='Date de fin (AAAA-MM-JJ).')

    def handle(self, *args, **options):
        ok, message, details = tester_connexion()
        if not ok:
            self.stderr.write(self.style.ERROR(f'Connexion impossible : {message}'))
            self.stdout.write(f"Cible : {details.get('cible')}")
            self.stdout.write(
                "Vérifiez le fichier .env (PG_HOST, PG_PORT, PG_NAME, PG_USER, PG_PASSWORD), "
                "l'ouverture du port et l'installation de psycopg2-binary.")
            return
        self.stdout.write(self.style.SUCCESS(message))
        self.stdout.write(f"PostGIS : {details.get('postgis')}")

        try:
            plage = bornes_dates_disponibles()
            self.stdout.write(
                f"Parcelles en base : {plage['total']} "
                f"({plage['premiere']} → {plage['derniere']})")
        except PostgisIndisponible as exc:
            self.stderr.write(self.style.WARNING(f'Table parcelle illisible : {exc}'))

        if options['tables']:
            for t in lister_tables():
                self.stdout.write(f"  {t['table_name']:32} {t['nb_colonnes']} colonnes")

        if options['requete']:
            nom = options['requete']
            try:
                lignes = executer_fichier(nom, options['debut'], options['fin'], limite=5)
            except PostgisIndisponible as exc:
                self.stderr.write(self.style.ERROR(f'Requête « {nom} » en échec : {exc}'))
                return
            self.stdout.write(self.style.SUCCESS(
                f"\nRequête « {nom} » : {len(lignes)} ligne(s) d'aperçu"))
            for l in lignes:
                self.stdout.write('  ' + ' | '.join(
                    f'{k}={v}' for k, v in list(l.items())[:8]))
