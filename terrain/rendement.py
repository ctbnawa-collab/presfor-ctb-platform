"""
Calcul des rendements : par agent, par jour, par département.

Les lignes en entrée sont celles de terrain/sql/parcelles.sql, déjà annotées
par terrain.qualite (clés nb_erreurs_ligne / conforme).
"""
from collections import defaultdict
from datetime import date, timedelta

from .qualite import score_qualite


def _jour(v):
    if isinstance(v, date):
        return v
    return None


def par_agent(lignes, agents_par_machine=None, objectif_defaut=8):
    """
    Synthèse par agent sur toute la période.
    agents_par_machine : dict {(id_machine, departement) ou id_machine: Agent}
    """
    agents_par_machine = agents_par_machine or {}
    agg = defaultdict(lambda: {
        'id_machine': '', 'agent': None, 'nom': '', 'fonction': '', 'departement': '',
        'nb_parcelles': 0, 'surface_ha': 0.0, 'jours': set(),
        'nbconformes': 0, 'nb_erreurs': 0, 'nb_alertes': 0,
        'villages': set(), 'sous_prefs': set(),
    })

    for l in lignes:
        machine = str(l.get('id_machine') or '').strip() or 'NON IDENTIFIÉ'
        dept = str(l.get('depart') or '').strip().upper()
        cle = (machine, dept)
        e = agg[cle]
        e['id_machine'] = machine
        e['departement'] = dept or '—'
        e['nb_parcelles'] += 1
        try:
            e['surface_ha'] += float(l.get('surface_ha') or 0)
        except (TypeError, ValueError):
            pass
        j = _jour(l.get('jour'))
        if j:
            e['jours'].add(j)
        if l.get('conforme', True):
            e['nbconformes'] += 1
        e['nb_erreurs'] += l.get('nb_erreurs_ligne', 0)
        e['nb_alertes'] += l.get('nb_alertes_ligne', 0)
        if l.get('name_villa'):
            e['villages'].add(l['name_villa'])
        if l.get('sous_pref'):
            e['sous_prefs'].add(l['sous_pref'])

    resultat = []
    for (machine, dept), e in agg.items():
        agent = agents_par_machine.get((machine, dept)) or agents_par_machine.get(machine)
        nb_jours = len(e['jours']) or 1
        objectif = agent.objectif_journalier if agent else objectif_defaut
        e['agent'] = agent
        e['nom'] = agent.nom_complet if agent else machine
        e['fonction'] = agent.get_fonction_display() if agent else 'Non enregistré'
        e['enregistre'] = agent is not None
        e['nb_jours'] = nb_jours
        e['moyenne_jour'] = round(e['nb_parcelles'] / nb_jours, 1)
        e['surface_ha'] = round(e['surface_ha'], 2)
        e['surface_moy'] = round(e['surface_ha'] / e['nb_parcelles'], 2) if e['nb_parcelles'] else 0
        e['objectif'] = objectif
        e['taux_objectif'] = round(e['moyenne_jour'] / objectif * 100, 1) if objectif else 0
        e['taux_conformite'] = round(e['nbconformes'] / e['nb_parcelles'] * 100, 1) if e['nb_parcelles'] else 0
        e['nb_villages'] = len(e['villages'])
        e['villages'] = sorted(e['villages'])[:12]
        e['sous_prefs'] = sorted(e['sous_prefs'])
        e['jours'] = sorted(e['jours'])
        e['premier_jour'] = e['jours'][0] if e['jours'] else None
        e['dernier_jour'] = e['jours'][-1] if e['jours'] else None
        resultat.append(e)

    return sorted(resultat, key=lambda x: -x['nb_parcelles'])


def par_jour(lignes):
    """Série quotidienne : parcelles, surface, agents actifs, conformité."""
    agg = defaultdict(lambda: {'jour': None, 'nb_parcelles': 0, 'surface_ha': 0.0,
                               'agents': set(), 'nbconformes': 0})
    for l in lignes:
        j = _jour(l.get('jour'))
        if not j:
            continue
        e = agg[j]
        e['jour'] = j
        e['nb_parcelles'] += 1
        try:
            e['surface_ha'] += float(l.get('surface_ha') or 0)
        except (TypeError, ValueError):
            pass
        if l.get('id_machine'):
            e['agents'].add(str(l['id_machine']).strip())
        if l.get('conforme', True):
            e['nbconformes'] += 1

    serie = []
    for j in sorted(agg):
        e = agg[j]
        e['nb_agents'] = len(e['agents'])
        e['agents'] = sorted(e['agents'])
        e['surface_ha'] = round(e['surface_ha'], 2)
        e['moyenne_agent'] = round(e['nb_parcelles'] / e['nb_agents'], 1) if e['nb_agents'] else 0
        e['taux_conformite'] = round(e['nbconformes'] / e['nb_parcelles'] * 100, 1) if e['nb_parcelles'] else 0
        serie.append(e)
    return serie


def par_departement(lignes):
    agg = defaultdict(lambda: {'departement': '', 'nb_parcelles': 0, 'surface_ha': 0.0,
                               'agents': set(), 'villages': set(), 'nbconformes': 0,
                               'nb_erreurs': 0, 'nb_alertes': 0})
    for l in lignes:
        d = (str(l.get('depart') or '').strip().upper()) or 'NON DÉTERMINÉ'
        e = agg[d]
        e['departement'] = d
        e['nb_parcelles'] += 1
        try:
            e['surface_ha'] += float(l.get('surface_ha') or 0)
        except (TypeError, ValueError):
            pass
        if l.get('id_machine'):
            e['agents'].add(str(l['id_machine']).strip())
        if l.get('name_villa'):
            e['villages'].add(l['name_villa'])
        if l.get('conforme', True):
            e['nbconformes'] += 1
        if l.get('nb_erreurs_ligne'):
            e['nb_erreurs'] += 1
        if l.get('nb_alertes_ligne'):
            e['nb_alertes'] += 1

    resultat = []
    for d, e in agg.items():
        e['nb_agents'] = len(e['agents'])
        e['nb_villages'] = len(e['villages'])
        e['agents'] = sorted(e['agents'])
        e['villages'] = sorted(e['villages'])
        e['surface_ha'] = round(e['surface_ha'], 2)
        e['taux_conformite'] = round(e['nbconformes'] / e['nb_parcelles'] * 100, 1) if e['nb_parcelles'] else 0
        e['score'] = score_qualite(e['nb_parcelles'], e['nb_erreurs'], e['nb_alertes'])
        resultat.append(e)
    return sorted(resultat, key=lambda x: -x['nb_parcelles'])


def periode_defaut(nb_jours=30):
    fin = date.today()
    return fin - timedelta(days=nb_jours), fin
