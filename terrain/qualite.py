"""
Moteur de contrôle qualité sur les données terrain (PostGIS).

Chaque règle inspecte une ligne (parcelle ou borne) et renvoie un constat
lorsqu'elle est en défaut. Deux niveaux de gravité :

  ERREUR  — la donnée est inexploitable en l'état, elle bloque la suite
            de la chaîne (demande, PVCL, RDC, publicité).
  ALERTE  — la donnée passe, mais elle mérite une vérification terrain.

Le score qualité d'un lot part du taux de lignes sans erreur, dont on retranche
un quart du taux de lignes en alerte :

    score = 100 − %lignes en erreur − 0,25 × %lignes en alerte

Il se lit donc comme un taux de conformité légèrement pénalisé par les points
à vérifier, et reste borné à [0, 100].
"""
import re
from collections import defaultdict
from datetime import date

ERREUR = 'erreur'
ALERTE = 'alerte'

# Seuils métier — ajustables ici, un seul endroit.
SURFACE_MIN_HA = 0.05      # en dessous : levé douteux
SURFACE_MAX_HA = 60.0      # au dessus : à vérifier (grande plantation)
SOMMETS_MIN = 4            # un polygone de parcelle a au moins 3 sommets + fermeture
TEL_CI = re.compile(r'^(\+?225)?\s*0?[0-9]{8,10}$')


def _vide(v):
    return v is None or str(v).strip() == '' or str(v).strip().lower() in ('nan', 'none', 'null')


# ── Règles parcelles ─────────────────────────────────────────────────────────
def controler_parcelles(lignes, agents_connus=None):
    """
    lignes : liste de dicts issus de terrain/sql/parcelles.sql
    agents_connus : set des id_machine enregistrés dans la plateforme

    Renvoie (lignes_annotees, constats) où constats est une liste de dicts.
    """
    agents_connus = agents_connus or set()
    constats = []

    # Pré-calcul des doublons
    compte_code = defaultdict(int)
    compte_unique = defaultdict(int)
    for l in lignes:
        if not _vide(l.get('code_parcelle')):
            compte_code[str(l['code_parcelle']).strip()] += 1
        if not _vide(l.get('code_unique')):
            compte_unique[str(l['code_unique']).strip()] += 1

    for l in lignes:
        defauts = []
        ref = l.get('code_parcelle') or f"id {l.get('id_parcelle')}"

        # 1. Identification
        if _vide(l.get('code_parcelle')):
            defauts.append((ERREUR, 'Code parcelle absent',
                            'La parcelle ne peut pas être rattachée à une demande.'))
        elif compte_code[str(l['code_parcelle']).strip()] > 1:
            defauts.append((ERREUR, 'Code parcelle en doublon',
                            f"Le code {l['code_parcelle']} apparaît "
                            f"{compte_code[str(l['code_parcelle']).strip()]} fois sur la période."))

        if _vide(l.get('code_unique')):
            defauts.append((ALERTE, 'Code unique absent',
                            'Le rapprochement avec le CSV Digifor sera moins fiable.'))
        elif compte_unique[str(l['code_unique']).strip()] > 1:
            defauts.append((ALERTE, 'Code unique en doublon', 'Vérifier le levé.'))

        # 2. Titulaire
        if _vide(l.get('nom_pers')) and _vide(l.get('prenom_pers')):
            defauts.append((ERREUR, 'Titulaire non renseigné',
                            'Nom et prénoms absents dans la table personne.'))
        elif _vide(l.get('nom_pers')) or _vide(l.get('prenom_pers')):
            defauts.append((ALERTE, 'Identité incomplète', 'Nom ou prénoms manquant.'))
        if _vide(l.get('cin')):
            defauts.append((ALERTE, 'Pièce d\'identité absente',
                            'Le CIN/CNI du titulaire n\'est pas saisi.'))

        # 3. Géométrie et surface
        surface = l.get('surface_ha')
        try:
            surface = float(surface) if surface is not None else None
        except (TypeError, ValueError):
            surface = None
        if surface is None or surface <= 0:
            defauts.append((ERREUR, 'Surface nulle ou incalculable',
                            'Géométrie absente ou dégénérée.'))
        elif surface < SURFACE_MIN_HA:
            defauts.append((ALERTE, 'Surface anormalement petite',
                            f'{surface:.4f} ha — seuil {SURFACE_MIN_HA} ha.'))
        elif surface > SURFACE_MAX_HA:
            defauts.append((ALERTE, 'Surface anormalement grande',
                            f'{surface:.2f} ha — seuil {SURFACE_MAX_HA} ha.'))

        if l.get('geom_valide') is False:
            defauts.append((ERREUR, 'Géométrie invalide',
                            'Polygone auto-intersectant ou non fermé (ST_IsValid = faux).'))
        try:
            if l.get('nb_sommets') is not None and int(l['nb_sommets']) < SOMMETS_MIN:
                defauts.append((ERREUR, 'Polygone incomplet',
                                f"{l['nb_sommets']} sommets seulement."))
        except (TypeError, ValueError):
            pass

        # 4. Localisation administrative
        if _vide(l.get('name_villa')):
            defauts.append((ERREUR, 'Parcelle hors territoire villageois',
                            'Le centroïde ne tombe dans aucun polygone de code_village.'))
        if _vide(l.get('depart')):
            defauts.append((ERREUR, 'Département non déterminé',
                            'Rattachement administratif impossible.'))
        elif str(l['depart']).strip().upper() not in ('SOUBRE', 'MEAGUI', 'BUYO'):
            defauts.append((ALERTE, 'Département hors NAWA',
                            f"Valeur lue : {l['depart']}."))
        if _vide(l.get('statut')):
            defauts.append((ALERTE, 'Statut du village non renseigné', ''))

        # 5. Agent et date
        machine = str(l.get('id_machine') or '').strip()
        if _vide(machine):
            defauts.append((ERREUR, 'Agent non identifié',
                            'id_machine absent : rendement non imputable.'))
        elif agents_connus and machine not in agents_connus:
            defauts.append((ALERTE, 'Agent non enregistré',
                            f'{machine} ne figure pas dans la fiche des agents.'))

        jour = l.get('jour')
        if jour is None:
            defauts.append((ERREUR, 'Date de levé illisible',
                            f"date_creation = {l.get('date_creation')!r}."))
        elif isinstance(jour, date) and jour > date.today():
            defauts.append((ALERTE, 'Date de levé dans le futur', str(jour)))

        l['defauts'] = defauts
        l['nb_erreurs_ligne'] = sum(1 for g, _, _ in defauts if g == ERREUR)
        l['nb_alertes_ligne'] = sum(1 for g, _, _ in defauts if g == ALERTE)
        l['conforme'] = l['nb_erreurs_ligne'] == 0

        for gravite, libelle, detail in defauts:
            constats.append({
                'objet': 'parcelle',
                'reference': ref,
                'gravite': gravite,
                'regle': libelle,
                'detail': detail,
                'agent': machine,
                'departement': l.get('depart') or '',
                'sous_pref': l.get('sous_pref') or '',
                'village': l.get('name_villa') or '',
                'jour': jour,
            })

    return lignes, constats


# ── Règles bornes ────────────────────────────────────────────────────────────
def controler_bornes(lignes):
    constats = []
    for l in lignes:
        defauts = []
        ref = str(l.get('id_borne'))

        if _vide(l.get('nom')) and _vide(l.get('prenom')):
            defauts.append((ERREUR, 'Riverain non identifié',
                            'Aucun nom rattaché à la borne dans description_limite.'))
        elif _vide(l.get('nom')) or _vide(l.get('prenom')):
            defauts.append((ALERTE, 'Identité de riverain incomplète', ''))

        if _vide(l.get('cin')):
            defauts.append((ALERTE, 'Pièce d\'identité du riverain absente', ''))

        tel = str(l.get('tel') or '').strip()
        if _vide(tel):
            defauts.append((ALERTE, 'Téléphone du riverain absent',
                            'Le contact est nécessaire pour la convocation au PVCL.'))
        elif not TEL_CI.match(tel.replace(' ', '').replace('-', '')):
            defauts.append((ALERTE, 'Téléphone au format douteux', tel))

        if l.get('geom_valide') is False:
            defauts.append((ERREUR, 'Géométrie de borne invalide', ''))
        if l.get('x') is None or l.get('y') is None:
            defauts.append((ERREUR, 'Borne sans coordonnées', ''))

        l['defauts'] = defauts
        l['nb_erreurs_ligne'] = sum(1 for g, _, _ in defauts if g == ERREUR)
        l['nb_alertes_ligne'] = sum(1 for g, _, _ in defauts if g == ALERTE)
        l['conforme'] = l['nb_erreurs_ligne'] == 0

        for gravite, libelle, detail in defauts:
            constats.append({
                'objet': 'borne', 'reference': ref, 'gravite': gravite,
                'regle': libelle, 'detail': detail, 'agent': '',
                'departement': '', 'sous_pref': '', 'village': '',
                'jour': l.get('jour'),
            })
    return lignes, constats


# ── Synthèses ────────────────────────────────────────────────────────────────
def score_qualite(nb_lignes, nb_lignes_erreur, nb_lignes_alerte):
    if not nb_lignes:
        return 100.0
    pct_err = nb_lignes_erreur / nb_lignes * 100
    pct_ale = nb_lignes_alerte / nb_lignes * 100
    return round(max(0.0, min(100.0, 100 - pct_err - 0.25 * pct_ale)), 1)


def synthese_regles(constats):
    """Agrège les constats par règle, triés du plus fréquent au moins fréquent."""
    agg = defaultdict(lambda: {'regle': '', 'gravite': '', 'objet': '', 'nb': 0, 'exemples': []})
    for c in constats:
        cle = (c['objet'], c['regle'])
        e = agg[cle]
        e['regle'], e['gravite'], e['objet'] = c['regle'], c['gravite'], c['objet']
        e['nb'] += 1
        if len(e['exemples']) < 5:
            e['exemples'].append(c['reference'])
    return sorted(agg.values(), key=lambda x: (-x['nb'], x['regle']))


def synthese_par_departement(constats):
    agg = defaultdict(lambda: {'departement': '', 'erreurs': 0, 'alertes': 0})
    for c in constats:
        d = c.get('departement') or 'NON DÉTERMINÉ'
        agg[d]['departement'] = d
        if c['gravite'] == ERREUR:
            agg[d]['erreurs'] += 1
        else:
            agg[d]['alertes'] += 1
    return sorted(agg.values(), key=lambda x: -(x['erreurs'] + x['alertes']))
