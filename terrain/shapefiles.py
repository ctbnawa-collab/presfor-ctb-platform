"""
Import des couches shapefile dans la base locale.

Deux couches sont attendues :
  - les parcelles publiées      → ParcellePublicite
  - les parcelles à supprimer   → ParcelleExclue

Les noms de champs des shapefiles sont tronqués à 10 caractères par le format
DBF (« DEPARTEMEN », « code_parce », « Surface (h »…). La correspondance se fait
donc par préfixe normalisé, pas par égalité stricte.
"""
import json
import re

from django.db import transaction

from .geo import ShapefileIllisible, enveloppe, lire_archive
from .imports import en_date, en_float
from .models import ParcelleExclue, ParcellePublicite, normaliser_code


def _norm(nom):
    s = str(nom).strip().lower()
    for a, b in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c'), ('°', '')]:
        s = s.replace(a, b)
    return re.sub(r'[^a-z0-9]', '', s)


def champ(attributs, candidats, index=None):
    """Première valeur non vide parmi les candidats, en tolérant la troncature DBF."""
    index = index or {_norm(k): k for k in attributs}
    for cand in candidats:
        cn = _norm(cand)
        cle = index.get(cn)
        if cle is None:
            # Le DBF tronque à 10 caractères : on accepte le préfixe.
            for kn, k in index.items():
                if kn and (cn.startswith(kn) or kn.startswith(cn)) and len(kn) >= 4:
                    cle = k
                    break
        if cle is None:
            continue
        v = attributs.get(cle)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ('nan', 'none', 'null'):
            return s
    return ''


def importer_publicite(fichier, nom_fichier='', remplacer=False, precision=6):
    """Importe la couche des parcelles publiées. Renvoie un résumé."""
    couche = lire_archive(fichier)
    index = None
    cree = sans_geometrie = ignore = 0
    objets = []

    for attributs, geo in couche.entites():
        if index is None:
            index = {_norm(k): k for k in attributs}
        code = champ(attributs, ['code_parcelle', 'code_parce', 'code de parcelle'], index)
        if not code:
            ignore += 1
            continue

        obj = ParcellePublicite(
            id_parcelle=champ(attributs, ['id_parcelle', 'id_parcell',
                                          'code_unique', 'code_uniqu'], index)[:80],
            code_parcelle=code[:60],
            code_norm=normaliser_code(code),
            num_demande=champ(attributs, ['num_demande', 'n de demande', 'numdemand'], index)[:60],
            departement=champ(attributs, ['departement'], index).upper()[:40],
            sous_prefecture=champ(attributs, ['sous_prefecture', 'sousprefe'], index).upper()[:100],
            village=champ(attributs, ['nom_village', 'village'], index).upper()[:100],
            titulaire=champ(attributs, ['nom digifor', 'nom', 'nom_group'], index)[:160],
            prenoms=champ(attributs, ['prenom digifor', 'prenom'], index)[:160],
            telephone=champ(attributs, ['tel', 'telephone'], index)[:40],
            cni=champ(attributs, ['cni'], index)[:60],
            type_certificat=champ(attributs, ['type', 'type_certificat'], index)[:20],
            commissaire=champ(attributs, ['ce', 'commissaire'], index)[:160],
            id_machine=champ(attributs, ['id_machine'], index)[:40],
            code_unique=champ(attributs, ['code_unique', 'code_mix'], index)[:80],
            surface_ha=en_float(champ(attributs, ['surface (ha)', 'surface_ha', 'surface'], index)),
            date_ouverture=en_date(champ(attributs, ['date_ouverture', 'date_added'], index)),
            observation=champ(attributs, ['observation', 'obs', 'remarques'], index)[:2000],
            statut='publiée',
            fichier_source=nom_fichier[:200],
        )

        if geo:
            obj.geojson = json.dumps(geo, separators=(',', ':'))
            env = enveloppe(geo)
            if env:
                obj.lon_min, obj.lat_min, obj.lon_max, obj.lat_max, obj.lon, obj.lat = env
        else:
            sans_geometrie += 1

        objets.append(obj)
        cree += 1

    # Identifiants déjà présents : sans « Remplacer », l'import ferait doublon.
    deja = set()
    if not remplacer:
        ids = {o.id_parcelle for o in objets if o.id_parcelle}
        deja = set(ParcellePublicite.objects
                   .filter(id_parcelle__in=ids)
                   .values_list('id_parcelle', flat=True))

    with transaction.atomic():
        if remplacer:
            ParcellePublicite.objects.all().delete()
        ParcellePublicite.objects.bulk_create(objets, batch_size=500)

    return {
        'total_couche': couche.total,
        'importees': cree,
        'sans_geometrie': sans_geometrie,
        'ignorees': ignore,
        'doublons': len(deja),
        'crs': couche.crs['nom'],
        'champs': couche.champs,
    }


def compter_doublons():
    """Parcelles présentes plusieurs fois, au sens de l'identifiant unique.

    Le comptage porte sur id_parcelle, jamais sur code_parcelle : ce dernier
    n'est unique qu'au sein d'un village, et 986 codes de la couche publiée
    désignent légitimement plusieurs parcelles distinctes.
    """
    from django.db.models import Count
    groupes = (ParcellePublicite.objects
               .exclude(id_parcelle='')
               .values('id_parcelle')
               .annotate(n=Count('id'))
               .filter(n__gt=1))
    return {'codes': groupes.count(),
            'lignes_en_trop': sum(g['n'] - 1 for g in groupes)}


def nettoyer_doublons():
    """Ne garde qu'un enregistrement par identifiant unique : le plus récent."""
    from django.db.models import Count
    groupes = (ParcellePublicite.objects
               .exclude(id_parcelle='')
               .values('id_parcelle')
               .annotate(n=Count('id'))
               .filter(n__gt=1))
    supprimes = 0
    with transaction.atomic():
        for g in groupes:
            ids = list(ParcellePublicite.objects
                       .filter(id_parcelle=g['id_parcelle'])
                       .order_by('-importee_le', '-id')
                       .values_list('id', flat=True))
            if ids[1:]:
                ParcellePublicite.objects.filter(id__in=ids[1:]).delete()
                supprimes += len(ids[1:])
    return supprimes


def importer_exclusions(fichier, nom_fichier='', motif='geometrie', remplacer=False):
    """Importe la couche des parcelles à supprimer vers la liste d'exclusion."""
    couche = lire_archive(fichier)
    index = None
    cree = ignore = 0
    objets = []
    vus = set()

    for attributs, geo in couche.entites():
        if index is None:
            index = {_norm(k): k for k in attributs}
        code = champ(attributs, ['code_parcelle', 'code_parce'], index)
        identifiant = champ(attributs, ['id_parcelle', 'id_parcell',
                                        'code_unique', 'code_uniqu'], index)
        norme = normaliser_code(code)
        # Dédoublonnage sur l'identifiant unique quand il existe.
        cle = identifiant or f'code:{norme}'
        if (not code and not identifiant) or cle in vus:
            ignore += 1
            continue
        vus.add(cle)

        obj = ParcelleExclue(
            id_parcelle=identifiant[:80],
            code_parcelle=code[:60],
            code_norm=norme,
            motif=motif,
            departement=champ(attributs, ['departement'], index).upper()[:40],
            village=champ(attributs, ['nom_village', 'village'], index).upper()[:100],
            id_machine=champ(attributs, ['id_machine'], index)[:40],
            date_creation_levee=champ(attributs, ['date_creation', 'date_creat'], index)[:40],
            commentaire=champ(attributs, ['observation', 'obs'], index)[:2000],
            fichier_source=nom_fichier[:200],
        )
        if geo:
            obj.geojson = json.dumps(geo, separators=(',', ':'))
            env = enveloppe(geo)
            if env:
                obj.lon, obj.lat = env[4], env[5]
        objets.append(obj)
        cree += 1

    with transaction.atomic():
        if remplacer:
            ParcelleExclue.objects.filter(fichier_source__isnull=False).delete()
        ParcelleExclue.objects.bulk_create(objets, batch_size=500)

    return {
        'total_couche': couche.total,
        'importees': cree,
        'ignorees': ignore,
        'crs': couche.crs['nom'],
        'champs': couche.champs,
    }
