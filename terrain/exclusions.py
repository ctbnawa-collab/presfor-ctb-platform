"""
Filtre d'exclusion des parcelles à supprimer.

Les parcelles déclarées ici sont masquées partout : cartes, contrôle qualité,
rendements, statistiques, listes. Rien n'est effacé de la base terrain — c'est
un filtre d'affichage, réversible en désactivant l'exclusion.

**Le rapprochement se fait d'abord sur l'identifiant unique** (`id_parcelle` /
`code_unique`). Le code parcelle seul ne suffit pas : il n'est unique qu'à
l'intérieur d'un village. Dans la couche publiée, P3514 désigne quatre parcelles
distinctes, à GNAGBOYA (deux fois), à KOUDOUYO et dans un village non renseigné.
Exclure sur le code seul en masquerait donc quatre au lieu d'une.

Quand l'identifiant unique manque, on retombe sur le couple (code, village),
qui est nettement plus discriminant que le code isolé.
"""
from .models import ParcelleExclue, normaliser_code


def _village(v):
    return str(v or '').strip().upper()


def index_exclusions():
    """Renvoie (identifiants exclus, couples (code, village) exclus)."""
    ids, couples = set(), set()
    for e in ParcelleExclue.objects.filter(active=True).only(
            'id_parcelle', 'code_norm', 'village'):
        if e.id_parcelle:
            ids.add(e.id_parcelle)
        elif e.code_norm:
            couples.add((e.code_norm, _village(e.village)))
    return ids, couples


def est_exclu(id_parcelle='', code='', village='', index=None):
    ids, couples = index if index is not None else index_exclusions()
    if id_parcelle and id_parcelle in ids:
        return True
    norme = normaliser_code(code)
    return bool(norme) and (norme, _village(village)) in couples


def filtrer_lignes(lignes, champ_id='id_parcelle', champ_code='code_parcelle',
                   champ_village='name_villa', index=None):
    """Retire d'une liste de dicts (lignes PostGIS) les parcelles exclues.

    Renvoie (lignes_conservees, nb_retirees).
    """
    ids, couples = index if index is not None else index_exclusions()
    if not ids and not couples:
        return lignes, 0
    gardees = [
        l for l in lignes
        if not est_exclu(str(l.get(champ_id) or ''), l.get(champ_code),
                         l.get(champ_village), index=(ids, couples))
    ]
    return gardees, len(lignes) - len(gardees)


def filtrer_publiees(queryset, index=None):
    """Retire d'un queryset de ParcellePublicite les parcelles exclues."""
    from django.db.models import Q

    ids, couples = index if index is not None else index_exclusions()
    if not ids and not couples:
        return queryset
    condition = Q(pk__in=[])
    if ids:
        condition |= Q(id_parcelle__in=ids)
    for code, village in couples:
        condition |= Q(code_norm=code, village=village)
    return queryset.exclude(condition)


def codes_exclus():
    """Codes canoniques exclus — conservé pour l'affichage, pas pour filtrer."""
    return {c for c in ParcelleExclue.objects.filter(active=True)
            .values_list('code_norm', flat=True) if c}
