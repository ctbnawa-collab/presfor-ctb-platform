"""
Rendement des activités d'opposition et de règlement de litige.

Source de vérité : la base locale (saisie manuelle + import CSV).
Si terrain/sql/oppositions.sql s'exécute correctement sur la base terrain,
ces lignes viennent compléter la vue (source « Base terrain »).
"""
from collections import defaultdict
from datetime import date

from .db import PostgisIndisponible, executer_fichier
from .models import Opposition


def depuis_postgis(date_debut, date_fin):
    """Renvoie (lignes, message). lignes vide si la requête n'est pas adaptée."""
    try:
        lignes = executer_fichier('oppositions', date_debut, date_fin)
        return lignes, f'{len(lignes)} enregistrement(s) lus sur la base terrain.'
    except PostgisIndisponible as exc:
        return [], f"Requête oppositions non opérationnelle : {exc}"


def synthese(queryset):
    """Indicateurs de rendement sur un queryset d'oppositions."""
    total = queryset.count()
    closes = queryset.filter(statut__in=Opposition.STATUTS_CLOS)
    reglees = queryset.filter(statut='reglee')

    delais = [o.delai_jours for o in closes if o.delai_jours is not None]
    delais_ouverts = [o.delai_jours for o in queryset.filter(statut__in=['enregistree','instruction','conciliation'])
                      if o.delai_jours is not None]

    return {
        'total': total,
        'nb_closes': closes.count(),
        'nb_reglees': reglees.count(),
        'nb_ouvertes': queryset.filter(statut__in=['enregistree','instruction','conciliation']).count(),
        'nb_sans_statut': queryset.filter(statut='non_renseigne').count(),
        'nb_sans_date': queryset.filter(date_opposition__isnull=True).count(),
        'nb_levees': queryset.filter(statut='levee').count(),
        'taux_reglement': round(reglees.count() / total * 100, 1) if total else 0,
        'taux_traitement': round(closes.count() / total * 100, 1) if total else 0,
        'delai_moyen': round(sum(delais) / len(delais), 1) if delais else 0,
        'delai_max': max(delais) if delais else 0,
        'anciennete_moyenne': round(sum(delais_ouverts) / len(delais_ouverts), 1) if delais_ouverts else 0,
        'nb_plus_30j': sum(1 for d in delais_ouverts if d > 30),
    }


def par_statut(queryset):
    libelles = dict(Opposition.STATUTS)
    compte = defaultdict(int)
    for statut in queryset.values_list('statut', flat=True):
        compte[statut] += 1
    return [{'code': k, 'libelle': libelles.get(k, k), 'nb': v}
            for k, v in sorted(compte.items(), key=lambda x: -x[1])]


def par_type(queryset):
    libelles = dict(Opposition.TYPES)
    compte = defaultdict(int)
    for t in queryset.values_list('type_litige', flat=True):
        compte[t] += 1
    return [{'code': k, 'libelle': libelles.get(k, k), 'nb': v}
            for k, v in sorted(compte.items(), key=lambda x: -x[1])]


def par_agent(queryset):
    agg = defaultdict(lambda: {'nom': '', 'total': 0, 'reglees': 0, 'ouvertes': 0, 'delais': []})
    for o in queryset.select_related('agent'):
        cle = o.agent.nom_complet if o.agent else (o.source_metadata.get('ce') or 'Non affecté')
        e = agg[cle]
        e['nom'] = cle
        e['total'] += 1
        if o.est_close:
            e['reglees'] += 1
            if o.delai_jours is not None:
                e['delais'].append(o.delai_jours)
        else:
            e['ouvertes'] += 1
    resultat = []
    for e in agg.values():
        e['delai_moyen'] = round(sum(e['delais']) / len(e['delais']), 1) if e['delais'] else 0
        e['taux'] = round(e['reglees'] / e['total'] * 100, 1) if e['total'] else 0
        del e['delais']
        resultat.append(e)
    return sorted(resultat, key=lambda x: -x['total'])


def par_departement(queryset):
    agg = defaultdict(lambda: {'departement': '', 'total': 0, 'reglees': 0, 'ouvertes': 0})
    for o in queryset:
        d = o.departement or 'NON RENSEIGNÉ'
        agg[d]['departement'] = d
        agg[d]['total'] += 1
        if o.est_close:
            agg[d]['reglees'] += 1
        else:
            agg[d]['ouvertes'] += 1
    for e in agg.values():
        e['taux'] = round(e['reglees'] / e['total'] * 100, 1) if e['total'] else 0
    return sorted(agg.values(), key=lambda x: -x['total'])


def serie_mensuelle(queryset):
    """Oppositions enregistrées et réglées par mois."""
    ouvertures = defaultdict(int)
    reglements = defaultdict(int)
    for o in queryset:
        if o.date_opposition:
            ouvertures[o.date_opposition.strftime('%Y-%m')] += 1
        if o.date_reglement:
            reglements[o.date_reglement.strftime('%Y-%m')] += 1
    mois = sorted(set(ouvertures) | set(reglements))
    return [{'mois': m, 'ouvertes': ouvertures.get(m, 0), 'reglees': reglements.get(m, 0)}
            for m in mois]
