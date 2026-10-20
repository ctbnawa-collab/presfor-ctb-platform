"""Lecture en flux des seules tables utilisées par les contrôles.

Le moteur vérifie uniquement la présence des trois champs de pièces ci-dessous.
Leur contenu binaire n'est jamais interprété : un marqueur conserve ce contrôle
sans recopier les images encodées dans plusieurs DataFrames.
"""
import csv
import io
import re
import tempfile

TABLES = set('applicant observation observationNeighborhood observation_neighborhood observationPresence observation_presence minutes advertisement openAdvertising open_advertising openadvertising closeOfAdvertising close_of_advertising closeadvertising existence village subprefecture certificate_order certificateOrder certificat_order validate department departement investigation_member member_type interviewed_person signatories minutesIntercalaire minutesResponse1 minutesResponse3 CaseOfInheritance CaseOfDonation applicantAudition list_easements'.split())
PIECES = {'photoidentity', 'identityDocumentPhoto', 'signatoryPhoto'}
# Valeurs NA reconnues par read_csv ; leur sens doit rester inchangé.
NA = {'', '#N/A', '#N/A N/A', '#NA', '-1.#IND', '-1.#QNAN', '-NaN', '-nan', '1.#IND', '1.#QNAN', '<NA>', 'N/A', 'NA', 'NULL', 'NaN', 'None', 'n/a', 'nan', 'null'}
csv.field_size_limit(64 * 1024 * 1024)


def compacter_export(source):
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    position = stream.tell()
    for encoding in ('utf-8-sig', 'latin-1'):
        stream.seek(position)
        text = io.TextIOWrapper(stream, encoding=encoding, newline='')
        try:
            with tempfile.SpooledTemporaryFile(mode='w+', encoding='utf-8', newline='', max_size=1024 * 1024) as output:
                writer = csv.writer(output, delimiter=';', lineterminator='\n')
                active = False
                header = True
                pieces = []
                for row in csv.reader(text, delimiter=';'):
                    if not row: continue
                    marker = re.match(r'^### TABLE:\s*([^\s;]+)', row[0]) if len(row) == 1 else None
                    if marker:
                        active = marker.group(1) in TABLES
                        header = True
                        pieces = []
                        writer.writerow(row)  # garde aussi les limites des tables ignorées
                    elif row[0].startswith('#'):
                        if active: writer.writerow(row)
                    elif active:
                        if header:
                            pieces = [i for i, name in enumerate(row) if name.strip() in PIECES]
                            header = False
                        else:
                            for i in pieces:
                                if i < len(row) and row[i].strip().lower() not in ('', 'nan', 'none') and row[i] not in NA:
                                    row[i] = 'PIECE_PRESENTE'
                        writer.writerow(row)
                output.seek(0)
                return output.read().encode('utf-8')
        except UnicodeDecodeError:
            if encoding == 'latin-1': raise
        except csv.Error as exc:
            raise ValueError('Export CSV illisible ou champ dépassant 64 Mo.') from exc
        finally:
            text.detach()  # les fichiers téléversés restent sous le contrôle de Django
    raise ValueError('Encodage CSV non reconnu')
