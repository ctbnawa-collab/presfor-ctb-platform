import unicodedata
"""
DIGIFOR - Outil de traitement des dossiers fonciers
Application GUI Windows - Traitement multi-CSV
"""

import threading
import os
import sys
import re, io, os, sys, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ─── CONSTANTES ───────────────────────────────
DETENTEUR_CODES = {1, 10}
REQUIRED_QUAL_RDC = [
    ({1, 10}, 'Détenteur de droit / Gestionnaire'),
    ({4, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 3},
     'Personne ressource (Sachant/Notable/Famille/Témoin/Autres)'),
    ({5, 8, 9}, 'Membre CVGFR / Secrétaire'),
]
WATER_KW  = ['riviere', 'rivière', 'fleuve', "cours d'eau"]
ROAD_KW   = ['route', 'axe soubre', 'axe ', 'axe-', '/axe', 'piste', 'chemin', 'voie ']
ELEC_KW   = ['ligne haute tension', 'ligne moyenne tension', 'hta', 'htb',
              'haute tension', 'moyenne tension']
NATURE_KW = ['forêt classée', 'foret classée', 'foret classee', 'forêt classee',
              'parc national', 'parc ', 'réserve', 'reserve']
ALL_KW    = WATER_KW + ROAD_KW + ELEC_KW + NATURE_KW

# ─── UTILITAIRES ──────────────────────────────
def is_empty(v): return pd.isna(v) or str(v).strip() in ['', 'nan']
def has_remark(*values):
    """Retourne True si au moins une valeur n'est pas vide ou 'RAS'."""
    return any(str(v).strip() not in ('', 'RAS') for v in values)
def parse_date_any(v):
    """Convertit une date (jj-mm-aaaa, jj/mm/aaaa, ISO...) en Timestamp ; NaT si vide/invalide."""
    if v is None or pd.isna(v): return pd.NaT
    s = str(v).strip()
    if s in ('', 'nan'): return pd.NaT
    if re.match(r'^\d{4}-\d{2}-\d{2}', s): return pd.to_datetime(s[:10], format='%Y-%m-%d', errors='coerce')
    return pd.to_datetime(s.replace('/', '-')[:10], format='%d-%m-%Y', errors='coerce')

def parse_creation_obs(v, ref=pd.NaT):
    """creationDate de 'observation' : jj-mm-aaaa, ou format compact sans séparateur (ex. 2462025 = 24-6-2025).
    Si le compact est ambigu, on retient la date la plus proche de 'ref' (observationDate)."""
    if v is None or pd.isna(v): return pd.NaT
    s = str(v).strip()
    if not re.fullmatch(r'\d{6,7}', s): return parse_date_any(s)
    y = s[-4:]; head = s[:-4]; cands = []
    for dd, mm in ([(head[:1], head[1:])] + ([(head[:2], head[2:])] if len(head) >= 2 else [])):
        if dd and mm:
            d = pd.to_datetime(f"{dd.zfill(2)}-{mm.zfill(2)}-{y}", format='%d-%m-%Y', errors='coerce')
            if not pd.isna(d) and d not in cands: cands.append(d)
    if not cands: return pd.NaT
    if len(cands) == 1 or pd.isna(ref): return cands[0]
    return min(cands, key=lambda d: abs((d - ref).days))

def fmt_date(d):
    return '' if pd.isna(d) else d.strftime('%d-%m-%Y')

def extract_suffix(v): return re.sub(r'^[A-Z]+', '', str(v)) if not pd.isna(v) else None

def _remove_accents(s):
    """Supprime les accents pour comparaison souple (gère aussi œ, æ)."""
    s = str(s).replace('œ','oe').replace('Œ','oe').replace('æ','ae').replace('Æ','ae')
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    ).lower()

def match_souple(a, b, min_common=2):
    """Match souple sur les noms : au moins min_common tokens significatifs en commun.
    Permet de gérer les fautes de frappe mineures (Felix vs Felx)."""
    ta = {t for t in re.sub(r'\s+', ' ', str(a)).strip().lower().split() if len(t) > 2}
    tb = {t for t in re.sub(r'\s+', ' ', str(b)).strip().lower().split() if len(t) > 2}
    return len(ta & tb) >= min_common

def normalize_name(s):
    """Normalise espaces multiples et casse pour comparaison souple de noms."""
    return re.sub(r'\s+', ' ', str(s)).strip().lower()

def safe_int_code(c):
    if pd.isna(c): return None
    # Gérer les floats directement (ex: 10.0, 2.0 venant de colonnes float64)
    try:
        return int(float(c))
    except (ValueError, TypeError):
        pass
    s = str(c).strip().lstrip('0') or '0'
    try: return int(s)
    except: return None
def get_village_code(n):
    p = re.sub(r'^[A-Z]+', '', str(n)).split('-')
    return f"{p[0]}-{p[1]}" if len(p) >= 2 else None
def get_subpref_code(n):
    p = re.sub(r'^[A-Z]+', '', str(n)).split('-')
    try: return int(p[0])
    except: return None

# ─── EXTRACTION TABLE ─────────────────────────
def extract_table(lines, table_name):
    start = end = None
    for i, line in enumerate(lines):
        if f"### TABLE: {table_name} " in line or f"### TABLE: {table_name}\n" in line:
            j = i + 1
            while j < len(lines) and lines[j].startswith('#'): j += 1
            start = j
        elif start is not None and lines[i].startswith('###') and i > start:
            end = i; break
    if start is None: return None
    if end is None: end = len(lines)
    tl = [l for l in lines[start:end] if l.strip() and not l.startswith('#')]
    if not tl: return None
    try: return pd.read_csv(io.StringIO(''.join(tl)), sep=';', low_memory=False)
    except: return None

# ─── SERVITUDES ───────────────────────────────
def _classify_easement(name):
    n = str(name).lower().strip()
    if any(kw in n for kw in WATER_KW):   return "Limite naturelle",  str(name).strip()
    if any(kw in n for kw in NATURE_KW):  return "Limite naturelle",  str(name).strip()
    if any(kw in n for kw in ROAD_KW):    return "Domaine public",    str(name).strip()
    if any(kw in n for kw in ELEC_KW):    return "Domaine public",    str(name).strip()
    # Détection souple sans accents : "Route entre/liant/reliant/vers X et Y", "Axe X-Y"
    _n_na = _remove_accents(n)  # version sans accents
    _liaison_kw = ['entre', 'liant', 'reliant', 'vers', 'passant']
    _route_kw   = ['route', 'axe', 'piste', 'chemin', 'voie']
    _has_route   = any(kw in _n_na for kw in _route_kw)
    _has_liaison = any(kw in _n_na.split() or (' ' + kw + ' ') in _n_na for kw in _liaison_kw) or '-' in _n_na or '/' in _n_na
    if _has_route and _has_liaison:
        return "Domaine public", str(name).strip()
    return None, None

def get_easement_remark(apt_num, neigh_for_apt, list_easements, pres_for_apt=None):
    if neigh_for_apt is None or (hasattr(neigh_for_apt, 'empty') and neigh_for_apt.empty):
        return ''
    detected = []
    seen = set()
    for _, nr in neigh_for_apt.iterrows():
        name = str(nr.get('nameOfNeighbor', '')).strip()
        if not name or name.lower() == 'nan': continue
        cat, val = _classify_easement(name)
        if cat is None: continue
        key = val.lower()
        if key not in seen:
            seen.add(key)
            detected.append({'cat': cat, 'val': val})
    if not detected:
        return ''
    # Noms présents dans observationPresence
    pres_names = set()
    if pres_for_apt is not None and not pres_for_apt.empty:
        for v in pres_for_apt['nameOfPerson'].dropna():
            pres_names.add(str(v).lower().strip())
    if list_easements is None or list_easements.empty:
        return '\n'.join(f"{e['cat']} : {e['val']}" for e in detected)
    # Construire les sets sans accents pour comparaison souple
    full_names_na = set()
    if 'fullName' in list_easements.columns:
        for v in list_easements['fullName'].dropna():
            full_names_na.add(_remove_accents(str(v)))
    trans_numbers = set()
    if 'transactionNumber' in list_easements.columns:
        for v in list_easements['transactionNumber'].dropna():
            trans_numbers.add(str(v).lower().strip())

    def _ease_match(val_na, fn):
        """Comparaison souple : contenu OU mots-clés significatifs communs (gère ordre inversé)."""
        if val_na in fn or fn in val_na:
            return True
        # Mots significatifs (> 3 lettres) en commun
        w_val = {w for w in val_na.split() if len(w) > 3}
        w_fn  = {w for w in fn.split()    if len(w) > 3}
        return len(w_val & w_fn) >= 2

    missing = []
    for e in detected:
        val_na = _remove_accents(e['val'])
        found = (
            any(_ease_match(val_na, fn) for fn in full_names_na) or
            any(val_na in tn or tn in val_na for tn in trans_numbers)
        )
        if not found:
            missing.append(f"{e['cat']} : {e['val']}")
    return '\n'.join(missing)

# ─────────────────────────────────────────────
#  CODES QUALITÉS PAR CAS D'ACQUISITION
# ─────────────────────────────────────────────
# Codes member_type par catégorie
CODES_DET_GEST   = {1, 10}          # Détenteur de droit, Gestionnaire
CODES_CVGFR      = {5, 8, 9, 23}    # Membre, Président, Secrétaire, Trésorier CVGFR
CODES_CHEFFERIE  = {3, 12, 15}      # Chef de village, Notable, Chef de terre
CODES_FAMILLE    = {11, 14, 16, 17, 22}  # Ayant droit, Chef de famille, Frère, Sœur, Autres (lien parenté)
CODES_DONATEUR   = {19}             # Donateur
CODES_CEDANT     = {18}             # Cédant
CODES_VENDEUR    = {20}             # Vendeur
CODES_TEMOIN     = {21}             # Témoin
CODES_SACHANT    = {4, 13}          # Sachant, Représentant

# Qualités obligatoires par cas
REQUIRED_DON = [
    (CODES_DET_GEST,              'Détenteur de droit / Gestionnaire'),
    (CODES_DONATEUR | CODES_FAMILLE, 'Donateur ou membre de la famille'),
    (CODES_CVGFR,                 'Membre CVGFR (Membre/Secrétaire/Trésorier/Président)'),
    (CODES_CHEFFERIE,             'Membre de la chefferie (Notable/Chef de terre/Chef de village)'),
]
REQUIRED_HERITAGE = [
    (CODES_DET_GEST,              'Détenteur de droit / Gestionnaire'),
    (CODES_FAMILLE,               'Ayant droit / Membre de la famille'),
    (CODES_CVGFR,                 'Membre CVGFR (Membre/Secrétaire/Trésorier/Président)'),
    (CODES_CHEFFERIE,             'Membre de la chefferie (Notable/Chef de terre/Chef de village)'),
]
REQUIRED_ACHAT = [
    (CODES_DET_GEST,              'Détenteur de droit / Gestionnaire'),
    (CODES_CEDANT | CODES_VENDEUR | CODES_TEMOIN | CODES_FAMILLE,
                                  'Cédant / Témoin / Membre de la famille / Vendeur'),
    (CODES_CVGFR,                 'Membre CVGFR (Membre/Secrétaire/Trésorier/Président)'),
    (CODES_CHEFFERIE,             'Membre de la chefferie (Notable/Chef de terre/Chef de village)'),
]

# Qualités importantes (toutes les autres sont "à supprimer" si non auditionnées)
CODES_IMPORTANTS = (CODES_DET_GEST | CODES_CVGFR | CODES_CHEFFERIE |
                    CODES_FAMILLE | CODES_DONATEUR | CODES_CEDANT |
                    CODES_VENDEUR | CODES_TEMOIN | {11})

# ─────────────────────────────────────────────
#  DOUBLON NOM FICHE DÉMOGRAPHIQUE
# ─────────────────────────────────────────────
def get_doublon_fiche(apt_num, ip_table, get_qual_fn=None):
    """Retourne (fiche_complete, doublons_str)."""
    if ip_table is None or ip_table.empty:
        return '', 'RAS'
    sfx = extract_suffix(apt_num)
    prefix = f'TEMRDC{sfx}'
    rows = ip_table[ip_table['interviewedpersonNumber'].str.startswith(prefix, na=False)]
    if rows.empty:
        return '', 'RAS'
    names_lower = rows['nameInterviewedPerson'].dropna().str.strip().str.lower().tolist()
    count = {}
    for n in names_lower:
        count[n] = count.get(n, 0) + 1
    fiche_lines = []
    doublons = []
    seen_dbl = set()
    for _, p in rows.iterrows():
        name = str(p.get('nameInterviewedPerson', '')).strip()
        qual = get_qual_fn(p.get('codeMemberType', '')) if get_qual_fn else str(p.get('codeMemberType', ''))
        fiche_lines.append(f"{name} ({qual})")
        key = name.lower()
        if count.get(key, 0) > 1 and key not in seen_dbl:
            seen_dbl.add(key)
            doublons.append(name)
    fiche_str    = '\n'.join(fiche_lines)
    doublons_str = '\n'.join(doublons) if doublons else 'RAS'
    return fiche_str, doublons_str

def get_audition_fiche(apt_num, ip_table, inter_table, get_qual_fn=None):
    """
    Pour chaque personne de la fiche démographique,
    vérifie si son intercalaire (question + réponse) est renseigné.
    Retourne la liste des personnes avec statut :
      ✓ auditionné  ou  à auditionner
    Si tout le monde est auditionné → 'RAS'
    """
    if ip_table is None or ip_table.empty:
        return 'RAS'
    sfx = extract_suffix(apt_num)
    prefix = f'TEMRDC{sfx}'
    rows = ip_table[ip_table['interviewedpersonNumber'].str.startswith(prefix, na=False)]
    if rows.empty:
        return 'RAS'

    # Construire index des intercalaires pour ce dossier
    # Utiliser contains(sfx) pour capturer INTER, AUTREQ et tout autre préfixe
    inter_rows = pd.DataFrame()
    if inter_table is not None and not inter_table.empty:
        inter_rows = inter_table[
            inter_table['codeIntercalaire'].str.contains(sfx, na=False)
        ]

    a_auditionner = []
    for _, p in rows.iterrows():
        name = str(p.get('nameInterviewedPerson', '')).strip()
        if not name or name.lower() == 'nan':
            continue
        qual = get_qual_fn(p.get('codeMemberType', '')) if get_qual_fn else ''
        nom_norm = normalize_name(name)

        # Chercher dans l'intercalaire une ligne avec question ET réponse renseignées
        auditionne = False
        if not inter_rows.empty:
            match = inter_rows[inter_rows['nomTemoin'].apply(
                lambda t: not is_empty(t) and (
                    nom_norm in normalize_name(t) or normalize_name(t) in nom_norm
                )
            )]
            if not match.empty:
                # Vérifier que question ET réponse sont renseignées
                for _, ir in match.iterrows():
                    q = ir.get('question', np.nan)
                    r = ir.get('reponse', np.nan)
                    if not is_empty(q) and not is_empty(r):
                        auditionne = True
                        break

        if not auditionne:
            a_auditionner.append(f"{name} ({qual})")

    if not a_auditionner:
        return 'RAS'
    return 'à auditionner :\n' + '\n'.join(f"  • {p}" for p in a_auditionner)

# ─────────────────────────────────────────────
#  MODE D'ACQUISITION + REMARQUES ADDITIONNELLES
# ─────────────────────────────────────────────
def get_acquisition_info(apt_num, minutes_row, ip_table, inter_table,
                         heir_dict, don_dict, achat_dict, mr3_dict,
                         inv_members, get_qual_fn, is_empty_fn, mt_lookup,
                         signatories=None):
    """
    Retourne (mode_acquisition, remarques_additionnelles, qualite_rdc)
    pour un dossier donné.
    """
    if minutes_row is None:
        return '', '', ''

    c3 = minutes_row.get('codeResponse3', np.nan)
    # Fallback : si codeResponse3 est NaN, chercher MR3{sfx} directement
    if is_empty_fn(c3):
        _sfx_r3 = extract_suffix(apt_num)
        c3_fallback = f'MR3{_sfx_r3}'
        if str(c3_fallback) in {str(k) for k in mr3_dict}:
            c3 = c3_fallback
    if is_empty_fn(c3) or str(c3) not in {str(k) for k in mr3_dict}:
        return '', '', ''

    r3 = mr3_dict.get(c3, mr3_dict.get(str(c3)))
    cod_heir  = r3.get('codeCasHeritage', np.nan)
    cod_don   = r3.get('codeCasDon', np.nan)
    cod_achat = r3.get('codeCasAchat', np.nan)

    mode       = ''
    rem_add    = []
    qual_parts = []

    # ── Données auditionnées (interviewed_person pour ce dossier) ──
    sfx = extract_suffix(apt_num)
    prefix = f'TEMRDC{sfx}'
    ip_rows = pd.DataFrame()
    if ip_table is not None:
        ip_rows = ip_table[ip_table['interviewedpersonNumber'].str.startswith(prefix, na=False)]

    # Codes présents dans la fiche RDC
    codes_auditionnes = set()
    if not ip_rows.empty:
        for _, p in ip_rows.iterrows():
            raw_code = p.get('codeMemberType', '')
            c = safe_int(raw_code)
            if c is None and is_famille_textuel(raw_code): c = 22
            if c is not None: codes_auditionnes.add(c)

    # Codes présents dans investigation_member
    codes_equipe = set()
    if inv_members is not None and not inv_members.empty:
        for _, m in inv_members.iterrows():
            c = safe_int(m.get('codeMemberType', ''))
            if c is not None: codes_equipe.add(c)

    # Codes présents dans intercalaire pour ce dossier
    codes_inter = set()
    inter_rows = pd.DataFrame()
    if inter_table is not None:
        inter_rows = inter_table[inter_table['codeIntercalaire'].str.contains(sfx, na=False)] if inter_table is not None else pd.DataFrame()
        # Associer nomTemoin → codeMemberType via ip_table
        if not inter_rows.empty and not ip_rows.empty:
            for _, ir in inter_rows.iterrows():
                nom = str(ir.get('nomTemoin', '')).strip().lower()
                if not nom or nom == 'nan': continue
                match = ip_rows[ip_rows['nameInterviewedPerson'].str.strip().str.lower() == nom]
                if not match.empty:
                    c = safe_int(match.iloc[0].get('codeMemberType', ''))
                    if c is not None: codes_inter.add(c)

    codes_all = codes_auditionnes | codes_inter | codes_equipe

    # Si des intercalaires contiennent un lien familial textuel dans les réponses
    # → ajouter code 22 (famille) à codes_all pour satisfaire check_qualites
    if not inter_rows.empty:
        for _, _ir in inter_rows.iterrows():
            _rep = _remove_accents(normalize_name(str(_ir.get('reponse', ''))))
            if not is_empty(_ir.get('reponse', '')) and any(kw in _rep for kw in _FAMILLE_TEXTUEL_KW):
                codes_all.add(22)
                break
    CODES_FAMILLE_REP = {11, 14, 16, 17, 22, 19, 20, 21, 4, 13}
    # Codes famille directe — leur présence suffit même sans nom mentionné
    CODES_FAMILLE_DIRECTE = {11, 14, 16, 17, 22}  # Ayant Droit, Chef de famille, Frère, Sœur, Autres

    def _temoin_famille(nomTemoin, reponse, nom_personne):
        """Vérifie si un témoin représente la personne :
        - Soit son code est famille directe (11,14,16,17) ET il a une réponse
        - Soit son code est famille/sachant ET le nom est mentionné dans la réponse."""
        if is_empty_fn(reponse): return False
        nom_p_court = _extract_nom_court(nom_personne)
        if not any(part in normalize_name(str(reponse)) for part in [normalize_name(nom_personne), nom_p_court] if len(part) > 3):
            return False
        # Chercher la qualité de ce témoin dans ip_rows
        t_nom = normalize_name(str(nomTemoin))
        if not ip_rows.empty:
            match = ip_rows[ip_rows['nameInterviewedPerson'].apply(
                lambda n: not is_empty_fn(n) and (
                    t_nom in normalize_name(n) or normalize_name(n) in t_nom))]
            if not match.empty:
                raw_c = match.iloc[0].get('codeMemberType', '')
                code = safe_int(raw_c)
                if code is None and is_famille_textuel(raw_c): code = 22
                if code in CODES_FAMILLE_REP:
                    return True
        return False

    def check_person_audition(name_key, codes_cible, label_cible):
        """Vérifie si une personne est dans la fiche avec audition + signature.
        Si son nom est mentionné dans une réponse intercalaire par un témoin
        dont la qualité indique un lien familial → représenté → RAS."""
        if is_empty_fn(name_key) or str(name_key).strip().lower() in ['nan', '']:
            # Nom inconnu → vérifier si un membre de famille textuel est auditionnné dans ip_rows
            if not ip_rows.empty and not inter_rows.empty:
                for _, _p in ip_rows.iterrows():
                    _raw = _p.get('codeMemberType', '')
                    if not is_famille_textuel(_raw): continue
                    _pnom = normalize_name(str(_p.get('nameInterviewedPerson', '')))
                    has_inter = inter_rows[inter_rows['nomTemoin'].apply(
                        lambda t: not is_empty_fn(t) and (
                            _pnom in normalize_name(str(t)) or normalize_name(str(t)) in _pnom)
                    )]['reponse'].apply(lambda v: not is_empty_fn(v)).any()
                    if has_inter:
                        return ''  # Représenté par famille textuelle → RAS
            return f"Renseigner le nom du {label_cible} et faire l'audition"
        # ARSO ou ETAT → organisme public, pas de remarque
        _name_low = normalize_name(str(name_key))
        if any(kw in _name_low for kw in ['arso', "l'arso", 'etat', 'état']):
            return ''
        # Si le nom contient une qualité entre parenthèses ex: (Oncle), (Chef de village)
        # → vérifier si cette personne ou un membre de même qualité est dans la fiche ET auditionné
        _paren_match = re.search(r'\(([^)]+)\)', str(name_key))
        if _paren_match:
            _qual_paren = _paren_match.group(1)
            _is_fam_paren   = is_famille_textuel(_qual_paren)
            _is_cheff_paren = is_chefferie_textuel(_qual_paren)
            if _is_fam_paren or _is_cheff_paren:
                nom_clean = normalize_name(_extract_nom_court(str(name_key)))
                _codes_ok = set()
                if _is_fam_paren:   _codes_ok |= CODES_FAMILLE_REP | CODES_FAMILLE_DIRECTE | {4}
                if _is_cheff_paren: _codes_ok |= {3, 12, 15}
                # 1. La personne elle-même a une réponse dans l'intercalaire
                #    (même si absente de ip_rows — elle a quand même auditionné)
                if not inter_rows.empty:
                    _m_inter = inter_rows[inter_rows['nomTemoin'].apply(
                        lambda t: not is_empty_fn(t) and (
                            nom_clean in normalize_name(str(t)) or normalize_name(str(t)) in nom_clean))]
                    if not _m_inter.empty and _m_inter['reponse'].apply(lambda v: not is_empty_fn(v)).any():
                        return ''
        # Personne décédée (Feu / Feu.) → vérifier si famille/sachant avec lien auditionné
        _is_feu = bool(re.match(r'^[Ff]eu\.?\s+', str(name_key).strip()))
        if _is_feu:
            # Chercher un membre famille ou sachant auditionné dans inter_rows
            if not inter_rows.empty and not ip_rows.empty:
                for _, _p in ip_rows.iterrows():
                    _c = safe_int(_p.get('codeMemberType', ''))
                    if _c is None and is_famille_textuel(_p.get('codeMemberType', '')):
                        _c = 22
                    if _c not in (CODES_FAMILLE_REP | CODES_FAMILLE_DIRECTE | {4}):
                        continue
                    _pnom = normalize_name(str(_p.get('nameInterviewedPerson', '')))
                    _pm = inter_rows[inter_rows['nomTemoin'].apply(
                        lambda t: not is_empty_fn(t) and (
                            _pnom in normalize_name(str(t)) or normalize_name(str(t)) in _pnom))]
                    for _, _ir in _pm.iterrows():
                        _rep = normalize_name(str(_ir.get('reponse', '')))
                        if _rep and any(kw in _rep for kw in _FAMILLE_TEXTUEL_KW):
                            return ''  # Représenté par famille/sachant → RAS
        nom = normalize_name(str(name_key).strip())
        nom_court = _extract_nom_court(name_key)  # nom sans 'Feu' ni 'représenté par'

        # Vérifier représentation familiale dans l'intercalaire (quelle que soit la présence en fiche)
        represente = False
        if not inter_rows.empty:
            for _, ir in inter_rows.iterrows():
                if _temoin_famille(ir.get('nomTemoin', ''), ir.get('reponse', ''), nom):
                    represente = True
                    break
        if represente:
            return ''  # Représenté par un lien familial → RAS

        # Chercher dans ip_rows
        match = ip_rows[ip_rows['nameInterviewedPerson'].str.strip().str.lower().apply(
            lambda n: not is_empty_fn(n) and (nom in normalize_name(n) or normalize_name(n) in nom)
        )] if not ip_rows.empty else pd.DataFrame()
        if match.empty:
            # Vérifier aussi si famille directe (Ayant Droit 11, Frère 16, Sœur 17, Chef famille 14)
            # est auditionnée dans ip_rows → représentation suffisante
            if not represente and not ip_rows.empty and not inter_rows.empty:
                famille_dir = ip_rows[ip_rows['codeMemberType'].apply(
                    lambda c: safe_int(c) in CODES_FAMILLE_DIRECTE)]
                for _, fd in famille_dir.iterrows():
                    fd_nom = normalize_name(fd.get('nameInterviewedPerson', ''))
                    has_inter = inter_rows[inter_rows['nomTemoin'].apply(
                        lambda t: not is_empty_fn(t) and (
                            fd_nom in normalize_name(t) or normalize_name(t) in fd_nom)
                    )]['reponse'].apply(lambda v: not is_empty_fn(v)).any()
                    if has_inter:
                        represente = True
                        break
            if represente:
                return ''
            return f"{name_key} ({label_cible}) : ajouter à la fiche démographique et faire son audition"

        # Personne dans la fiche — vérifier son intercalaire propre
        inter_ok = not inter_rows.empty and inter_rows[inter_rows['nomTemoin'].str.strip().str.lower().apply(
            lambda n: not is_empty_fn(n) and (nom in normalize_name(n) or normalize_name(n) in nom)
        )]['reponse'].apply(lambda v: not is_empty_fn(v)).any() if not inter_rows.empty else False
        # Vérifier signature dans signatories (SINTER{sfx})
        sig_ok = False
        if signatories is not None and not signatories.empty:
            sig_rows_inter = signatories[
                signatories['numberOfPersonSigning'].str.startswith(f'SINTER{sfx}', na=False)]
            if not sig_rows_inter.empty:
                sig_match = sig_rows_inter[sig_rows_inter['nameSignatory'].str.strip().str.lower().apply(
                    lambda n: not is_empty_fn(n) and (nom in normalize_name(n) or normalize_name(n) in nom))]
                sig_ok = not sig_match.empty and sig_match['signatoryPhoto'].apply(
                    lambda v: not is_empty_fn(v)).any()
        issues = []
        if not inter_ok: issues.append("audition manquante")
        if not sig_ok:   issues.append("signature manquante")
        return (f"{name_key} ({label_cible}) : " + ' / '.join(issues)) if issues else ''

    def _is_arso(name_val):
        if is_empty_fn(name_val): return False
        n = normalize_name(str(name_val))
        return any(kw in n for kw in ['arso', "l'arso", 'etat', 'état'])

    def check_qualites(required_list):
        """Vérifie les qualités obligatoires et celles à supprimer.
        Dédoublonne les lignes 'Auditionné' identiques."""
        parts = []
        # Codes requis pour ce cas d'acquisition
        required_codes = set()
        for codes_set, _ in required_list:
            required_codes |= codes_set

        # Qualités manquantes — dédoublonnées
        # Détecter si le donateur/vendeur/référent est ARSO → ignorer la qualité famille
        _skip_famille = False
        if mode == 'Donation' and not is_empty_fn(cod_don) and cod_don in don_dict:
            _don_p = don_dict.get(cod_don, {}); _skip_famille = _is_arso(_don_p.get('nomPrenomDonateur','') or _don_p.get('donateurParcelle',''))
        elif mode == 'Achat' and not is_empty_fn(cod_achat) and cod_achat in achat_dict:
            _skip_famille = _is_arso(achat_dict.get(cod_achat, {}).get('nameOfSeller', ''))

        seen_labels = set()
        for codes_set, label in required_list:
            # Ignorer la qualité famille si donateur/vendeur est ARSO
            if _skip_famille and any(k in label.lower() for k in ['famille', 'donateur', 'cédant', 'témoin', 'vendeur']):
                continue
            if not codes_all.intersection(codes_set) and label not in seen_labels:
                seen_labels.add(label)
                parts.append(f"Auditionné : {label}")

        # Personnes présentes dans la fiche démographique
        # → à supprimer si non auditionnée ET qualité non requise pour ce cas
        if not ip_rows.empty:
            for _, p in ip_rows.iterrows():
                raw_code = p.get('codeMemberType', '')
                c = safe_int(raw_code)
                # Si code textuel familial → traiter comme CODES_FAMILLE (22)
                if c is None and is_famille_textuel(raw_code):
                    c = 22
                nom = str(p.get('nameInterviewedPerson', '')).strip()
                if c is None: continue
                nom_norm = normalize_name(nom)
                has_inter = (not inter_rows.empty and
                    inter_rows[inter_rows['nomTemoin'].str.strip().str.lower().apply(
                        lambda n: not is_empty_fn(n) and (nom_norm in normalize_name(n) or normalize_name(n) in nom_norm))
                    ]['reponse'].apply(lambda v: not is_empty_fn(v)).any()
                ) if not inter_rows.empty else False
                if not has_inter and c not in required_codes:
                    parts.append(f"{nom} ({get_qual_fn(c)}) : à supprimer")
        return '\n'.join(f"  • {p}" for p in parts)

    # ── CAS HÉRITAGE ──
    if not is_empty_fn(cod_heir) and cod_heir in heir_dict:
        mode = 'Héritage'
        heir = heir_dict.get(cod_heir, heir_dict.get(str(cod_heir)))
        referent = heir.get('nomPrenomReferent', np.nan)
        # Fallback : utiliser detenteurHeritage si nomPrenomReferent est vide
        if is_empty_fn(referent):
            referent = heir.get('detenteurHeritage', np.nan)
        issue = check_person_audition(referent, CODES_FAMILLE | {11}, 'référent/héritier')
        if issue: rem_add.append(issue)
        qual_parts_str = check_qualites(REQUIRED_HERITAGE)
        if qual_parts_str: qual_parts.append(qual_parts_str)

    # ── CAS DON ──
    elif not is_empty_fn(cod_don) and str(cod_don) in {str(k) for k in don_dict}:
        mode = 'Donation'
        don = don_dict.get(cod_don, don_dict.get(str(cod_don)))
        donateur = don.get('nomPrenomDonateur', np.nan)
        # Fallback : utiliser donateurParcelle si nomPrenomDonateur est vide
        if is_empty_fn(donateur):
            donateur = don.get('donateurParcelle', np.nan)
        issue = check_person_audition(donateur, CODES_DONATEUR | CODES_FAMILLE, 'donateur')
        if issue: rem_add.append(issue)
        qual_parts_str = check_qualites(REQUIRED_DON)
        if qual_parts_str: qual_parts.append(qual_parts_str)

    # ── CAS ACHAT ──
    elif not is_empty_fn(cod_achat) and str(cod_achat) in {str(k) for k in achat_dict}:
        mode = 'Achat'
        achat = achat_dict.get(cod_achat, achat_dict.get(str(cod_achat)))
        vendeur = achat.get('nameOfSeller', np.nan)
        temoin  = achat.get('nameOfWitness', np.nan)

        # ── Règles d'exemption pour le vendeur ──
        # Si "Peut-on voir le vendeur?" = NON (0)
        # OU si le nom du vendeur commence par "Feu" (décédé)
        # → la remarque d'audition du vendeur est RAS
        _peut_voir_vendeur = safe_int(achat.get('canWeSeeTheSeller', np.nan))
        _vendeur_nom_norm  = _remove_accents(normalize_name(str(vendeur) if not is_empty_fn(vendeur) else ''))
        _vendeur_decede    = _vendeur_nom_norm.startswith('feu ')

        if _peut_voir_vendeur != 0 and not _vendeur_decede:
            issue_v = check_person_audition(vendeur, CODES_VENDEUR | CODES_CEDANT, 'vendeur/cédant')
            if issue_v: rem_add.append(issue_v)

        # ── Règles d'exemption pour le témoin ──
        # Si "Y a-t-il un témoin?" = NON (0) → la remarque du témoin est RAS
        _il_y_a_temoin = safe_int(achat.get('isThereAWitness', np.nan))
        if _il_y_a_temoin != 0:
            issue_t = check_person_audition(temoin, CODES_TEMOIN, 'témoin de la vente')
            if issue_t: rem_add.append(issue_t)

        qual_parts_str = check_qualites(REQUIRED_ACHAT)
        if qual_parts_str: qual_parts.append(qual_parts_str)

    remarque_add = '\n'.join(rem_add)
    qualite_rdc  = '\n'.join(qual_parts)
    return mode, remarque_add, qualite_rdc


# Mots-clés détectant un lien familial dans les valeurs textuelles de codeMemberType
# (certains agents saisissent "Fils Du Donateur" au lieu du code numérique)
_FAMILLE_TEXTUEL_KW = [
    # Fils / Fille
    'fils', 'fille', 'fils de', 'fils du', 'fille de', 'fille du',
    'fils du village', 'fils de la famille',
    # Frère / Sœur
    'frere', 'frere de', 'frere du', 'freres',
    'beau frere', 'grand frere', 'petit frere',
    'frere aine', 'frere cadet', 'frere de famille',
    'soeur', 'sodeur', 'soeur ainee', 'soeur cadette',
    # Père / Mère
    'pere', 'mere', 'beau pere', 'belle mere', 'veuve',
    # Oncle / Tante
    'oncle', 'oncle du', 'oncle de', 'l oncle', 'tante',
    # Neveu / Nièce
    'neveu', 'neveu du', 'niece', 'neuve',
    # Cousin / Cousine
    'cousin', 'cousine', 'cousin du', 'cousine du', 'cousi',
    # Époux / Épouse
    'epoux', 'epouse',
    # Grand-père / Grand-mère
    'grand pere', 'grand mere', 'grand frere',
    # Parent / Famille
    'parent', 'parents', 'membre de la famille', 'membrasse de la famille',
    'membre de la grande famille', 'grande famille',
    # Chef de famille / quartier
    'chef de famille', 'chef de quartier',
    # Défunt / Feu / référent
    'defunt', 'feu ', 'referent', 'heritier', 'ayant droit',
]

def is_famille_textuel(v):
    """Retourne True si v est une valeur textuelle indiquant un lien familial."""
    if v is None: return False
    s = str(v).lower().strip()
    if s in ('nan', '', 'none'): return False
    # Nettoyer le préfixe numérique type '022|Cousin' → 'cousin'
    if '|' in s:
        s = s.split('|', 1)[-1].strip()
    # Supprimer les accents pour comparaison souple
    s = _remove_accents(s)
    try:
        float(s); return False  # valeur numérique pure → pas textuel
    except ValueError:
        return any(kw in s for kw in _FAMILLE_TEXTUEL_KW)


def _extract_nom_court(nom):
    """Extrait le nom court : supprime Feu/Feu., parenthèses, 'représenté par'."""
    s = str(nom).strip()
    # Supprimer qualité entre parenthèses : "Zille Abo(Frère)" → "Zille Abo"
    s = re.sub(r'\s*\(.*?\)', '', s).strip()
    # Supprimer "Feu." ou "Feu " en début
    s = re.sub(r'^[Ff]eu\.?\s+', '', s).strip()
    # Tronquer avant "représenté par" / "représentant"
    s = re.split(r'\s+représenté\s+', s, flags=re.IGNORECASE)[0].strip()
    s = re.split(r'\s+représentant\s+', s, flags=re.IGNORECASE)[0].strip()
    return s.lower()


# Mots-clés pour les qualités légitimes entre parenthèses (chefferie, sachant, CVGFR…)
_QUALITES_LEGIT_KW = [
    'chef de village', 'chef du village', 'chef de terre', 'chef de tribu',
    'chef de communaute', 'chef du campement', 'chef du quartier',
    'notable', 'doyen du village', 'chef de kipiri',
    'cvgfr', 'membre cvgfr', 'president cvgfr', 'secretaire cvgfr',
    'sachant', 'temoin',
]

def is_qualite_legit(v):
    """Retourne True si v est une qualité légitime de représentation (chefferie, sachant…)."""
    if v is None: return False
    s = _remove_accents(str(v).strip())
    if s in ('nan', '', 'none'): return False
    if '|' in s: s = s.split('|', 1)[-1].strip()
    try:
        float(s); return False
    except ValueError:
        return any(kw in s for kw in _QUALITES_LEGIT_KW)


_CHEFFERIE_KW = ['chef de village', 'chef de terre', 'chef du village', 'notable',
                 'chef de tribu', 'chef de communaute', 'chef de kipiri',
                 'chef du campement', 'chef de quartier']

def is_chefferie_textuel(v):
    """Retourne True si v indique une qualité de chefferie/notable."""
    if v is None: return False
    s = _remove_accents(str(v)).lower().strip()
    if s in ('nan', '', 'none'): return False
    if '|' in s: s = s.split('|', 1)[-1].strip()
    return any(kw in s for kw in _CHEFFERIE_KW)

def safe_int(v):
    """Convertit une valeur en int ou retourne None."""
    if v is None or (isinstance(v, float) and np.isnan(v)): return None
    try: return int(float(str(v).strip()))
    except: return None


def process_csv(filepath, log_fn=None):
    def log(msg):
        if log_fn: log_fn(msg)
    log(f"  Lecture : {os.path.basename(filepath)}")
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    tables = {}
    for name in ['applicant','observation','observationNeighborhood','observationPresence',
                 'investigation_member','member_type','minutes','village','subprefecture',
                 'interviewed_person','signatories','minutesIntercalaire',
                 'minutesResponse1','minutesResponse3','certificate_order',
                 'CaseOfInheritance','CaseOfDonation','applicantAudition','list_easements']:
        t = extract_table(lines, name)
        tables[name] = t
        if t is None: log(f"    ⚠ Table manquante : {name}")

    def tbl(n): return tables.get(n)
    required = ['applicant','village','subprefecture','member_type','minutes']
    for r in required:
        if tbl(r) is None:
            log(f"  ✗ Table obligatoire manquante : {r}"); return None

    village_lookup  = dict(zip(tbl('village')['codeVillage'], tbl('village')['label']))
    subpref_lookup  = dict(zip(tbl('subprefecture')['codeSubPrefecture'], tbl('subprefecture')['label']))
    mt_lookup = {}
    for _, r in tbl('member_type').iterrows():
        s = str(r['codeMemberType']).strip()
        mt_lookup[s] = r['label']; mt_lookup[s.lstrip('0')] = r['label']
        try: mt_lookup[int(float(s))] = r['label']
        except: pass

    def get_qual(code):
        if pd.isna(code): return ''
        s = str(code).strip()
        return mt_lookup.get(s) or mt_lookup.get(s.lstrip('0')) or s

    mr1 = tbl('minutesResponse1')
    mr1_set = set(mr1['codeResponse1'].tolist()) if mr1 is not None else set()
    mr3 = tbl('minutesResponse3')
    mr3_dict = {r['codeResponse3']: r for _, r in mr3.iterrows()} if mr3 is not None else {}

    def make_dict(t, key): return {r[key]: r for _, r in t.iterrows()} if t is not None else {}
    heir_dict  = make_dict(tbl('CaseOfInheritance'), 'codeCasHeritage')
    don_dict   = make_dict(tbl('CaseOfDonation'),    'codeCasDon')
    achat_dict = make_dict(tbl('applicantAudition'), 'codeCasAchat')

    # ── Date création demande (certificate_order.creationDate, clé = requestNumber) ──
    co = tbl('certificate_order')
    co_date_dict = {}
    if co is not None and 'requestNumber' in co.columns and 'creationDate' in co.columns:
        for _, r in co.iterrows():
            if not is_empty(r['requestNumber']):
                co_date_dict[str(r['requestNumber']).strip()] = r['creationDate']

    obs = tbl('observation')
    obs_dict = {}
    if obs is not None:
        for _, r in obs.iterrows():
            sfx = extract_suffix(r['codeObservation'])
            obs_dict[sfx] = {'pcl': r['presences'], 'neicl': r['neighborhood'],
                             'expertSurveyor': r.get('expertSurveyor', np.nan),
                             'creationDate': r.get('creationDate', np.nan),
                             'observationDate': r.get('observationDate', np.nan)}

    obs_pres  = tbl('observationPresence')
    obs_neigh = tbl('observationNeighborhood')
    list_easements = tbl('list_easements')

    def get_pvcl_status(apt_num):
        sfx = extract_suffix(apt_num); info = obs_dict.get(sfx)
        if info is None: return 'Pvcl à créer', ''
        pres  = obs_pres[obs_pres['code'] == info['pcl']] if obs_pres is not None else pd.DataFrame()
        neigh = obs_neigh[obs_neigh['code'] == info['neicl']] if obs_neigh is not None else pd.DataFrame()
        missing = []

        # Normaliser memberType en entier pour comparaison robuste ('001'→1, '1'→1, 1→1)
        if not pres.empty:
            pres = pres.copy()
            pres['memberType'] = pres['memberType'].apply(
                lambda v: safe_int_code(v) if not (v is None or (isinstance(v, float) and pd.isna(v))) else v
            )

        # ── Dédoublonnage des voisins : un même voisin peut apparaître sur
        #    plusieurs positions (Nord, Sud…) → ne compter qu'une seule présence requise
        #    Exclure les domaines publics et limites naturelles (routes, rivières, forêts…) ──
        neigh_unique_names = []
        seen_neigh = set()
        for _, nr in neigh.iterrows():
            raw_name = str(nr.get('nameOfNeighbor', '')).strip()
            nn = normalize_name(raw_name)
            if not nn or nn in seen_neigh:
                continue
            # Exclure si c'est un domaine public ou une limite naturelle
            n_low = _remove_accents(nn)
            if any(kw in n_low for kw in WATER_KW + ROAD_KW + ELEC_KW + NATURE_KW +
                   ['cimetiere', 'lac ', 'lac du', 'fleuve', 'marigot']):
                continue
            seen_neigh.add(nn)
            neigh_unique_names.append(raw_name)

        # ── Fonction helper : vrai si un Représentant (code 13) couvre un rôle ──
        def _rep_covers(kw_list):
            """Vrai si un représentant (code 13) a 'representant' contenant un des mots-clés."""
            reps = pres[(pres['memberType'] == 13) &
                pres['representant'].apply(lambda v: not is_empty(v) and
                    any(kw in _remove_accents(str(v)).lower() for kw in kw_list))]
            return reps.shape[0] > 0

        # ── Noms des Détenteurs/Gestionnaires connus dans la fiche RDC ──
        # (pour détecter un Représentant PVCL dont le champ 'representant'
        #  contient uniquement le nom sans mot-clé de rôle)
        _det_names_rdc   = set()
        _cheff_names_rdc = set()
        _cvgfr_names_rdc = set()
        if ip_table is not None:
            rdc_num = f"RDC{sfx}"
            min_row = minutes_tbl[minutes_tbl['minutesNumber'] == rdc_num] if minutes_tbl is not None else pd.DataFrame()
            if not min_row.empty:
                _ip_code_rdc = min_row.iloc[0].get('interviewedpersonNumber', np.nan)
                if not is_empty(_ip_code_rdc):
                    _ip_rdc = ip_table[ip_table['interviewedpersonNumber'] == _ip_code_rdc]
                else:
                    _ip_rdc = ip_table[ip_table['interviewedpersonNumber'].str.startswith(f'TEMRDC{sfx}', na=False)]
                for _, _r in _ip_rdc.iterrows():
                    _c = safe_int_code(_r['codeMemberType'])
                    _nm = normalize_name(str(_r['nameInterviewedPerson']))
                    if _c in DETENTEUR_CODES:             _det_names_rdc.add(_nm)
                    if _c in {3, 12, 15}:                 _cheff_names_rdc.add(_nm)
                    if _c in {5, 8, 9}:                   _cvgfr_names_rdc.add(_nm)

        def _rep_covers_by_name(known_names, kw_list):
            """Vrai si un représentant PVCL (code 13) :
            - a un mot-clé de rôle explicite dans 'representant', OU
            - son champ 'representant' (nom seul) correspond à quelqu'un de connu dans la fiche RDC."""
            for _, r in pres[pres['memberType'] == 13].iterrows():
                v = str(r.get('representant', ''))
                if is_empty(v): continue
                v_norm = _remove_accents(v).lower()
                if any(kw in v_norm for kw in kw_list):
                    return True
                v_name = normalize_name(v.split(',')[0].strip())
                if known_names and any(v_name in kn or kn in v_name or match_souple(v_name, kn) for kn in known_names):
                    return True
            return False

        def _rep_covers_det():
            return _rep_covers_by_name(_det_names_rdc,
                ['detenteur', 'gestionnaire', 'demandeur', 'droit coutumier', 'droits'])

        # ── Qualités manquantes ──
        # Détenteur de droit/Gestionnaire (1,10) OU représentant du détenteur/gestionnaire
        has_det = (pres[pres['memberType'].isin([1, 10])].shape[0] > 0 or
                   _rep_covers_det())
        if not has_det:
            missing.append('Ajouter (Détenteur de droit/Gestionnaire)')

        # Voisins : comparer noms uniques de neigh avec observationPresence
        pres_voisin_names = set(
            normalize_name(str(n)) for n in pres[pres['memberType'] == 7]['nameOfPerson'].tolist()
            if not pd.isna(n)
        )
        has_rep = pres[pres['representant'].notna() &
                       (pres['representant'].astype(str).str.strip() != '') &
                       (pres['representant'].astype(str).str.lower() != 'nan')].shape[0] > 0
        mv = []
        for nn in neigh_unique_names:
            nn_norm = normalize_name(nn)
            # Ignorer si c'est une servitude
            cat, _ = _classify_easement(nn)
            if cat is not None: continue
            # Présent si son nom normalisé correspond à un voisin dans presences
            # (correspondance directe OU souple pour gérer fautes de frappe : Nahe/Nahi)
            present = has_rep or any(
                nn_norm in pn or pn in nn_norm or match_souple(nn_norm, pn) for pn in pres_voisin_names
            )
            if not present:
                mv.append(nn)
        if mv: missing.append(f"Voisins manquants: {', '.join(mv)}")

        # Représentant d'un voisin → étendre has_rep
        has_rep = has_rep or _rep_covers(['voisin', 'limitrophe', 'famille'])

        # Chefferie (3,12,15) OU représentant d'un notable/chef de village
        # (code 13 = Représentant générique, ne doit PAS valider la chefferie directement —
        #  seul un représentant dont le champ 'representant' mentionne explicitement
        #  un rôle de chefferie, OU dont le nom correspond à un Notable/Chef dans la fiche RDC)
        has_cheff = (pres[pres['memberType'].isin([3, 12, 15])].shape[0] > 0 or
                     _rep_covers_by_name(_cheff_names_rdc,
                         ['notable', 'chef', 'chefferie', 'village', 'campement', 'communaute', 'tribu']))
        if not has_cheff:
            missing.append('Ajouter (Rep. Chefferie/Chef de village)')

        # CVGFR (5,8,9) OU représentant d'un membre CVGFR
        has_cvgfr = (pres[pres['memberType'].isin([5, 8, 9])].shape[0] > 0 or
                     _rep_covers_by_name(_cvgfr_names_rdc,
                         ['cvgfr', 'comite', 'villageois', 'membre cvgfr']))
        if not has_cvgfr:
            missing.append('Ajouter (Rep. CVGFR/Membre CVGFR/Secrétaire)')

        if pres[pres['memberType'] == 2].shape[0] == 0:
            missing.append('Ajouter (Commissaire Enquêteur)')

        # Géomètre direct (code 6) OU représentant du géomètre
        has_geo = (pres[pres['memberType'] == 6].shape[0] > 0 or
                   _rep_covers(['geometre', 'expert']))
        if not has_geo and is_empty(info['expertSurveyor']):
            missing.append('Ajouter (Rep. Géomètre)')

        # ── Personnes sans signature + servitudes à supprimer dans observationPresence ──
        if not pres.empty:
            unsigned  = []
            to_delete = []
            seen_serv     = set()
            seen_unsigned = set()
            for _, p in pres.iterrows():
                name = '' if pd.isna(p.get('nameOfPerson', np.nan)) else str(p.get('nameOfPerson', '')).strip()
                cat, _ = _classify_easement(name)
                if cat is not None:
                    key = normalize_name(name)
                    if key not in seen_serv:
                        seen_serv.add(key)
                        to_delete.append(f"{name} ({cat})")
                    continue
                if is_empty(p.get('signatoryPhoto', np.nan)):
                    key = normalize_name(name)
                    if key not in seen_unsigned:
                        seen_unsigned.add(key)
                        qual = get_qual(p.get('memberType', ''))
                        unsigned.append(f"{name} ({qual})")
            if to_delete:
                missing.append("à supprimer de la liste de présence :\n" + '\n'.join(f"  • {str(u)}" for u in to_delete))
            if unsigned:
                missing.append("Signature manquante :\n" + '\n'.join(f"  • {u}" for u in unsigned))

        return ('Pvcl en cours', '\n'.join(str(m) for m in missing)) if missing else ('Pvcl terminé', '')

    def check_r3(code_r3, sfx_override=None):
        issues = []
        r3 = None
        # Tentative directe via code_r3
        if not is_empty(code_r3) and str(code_r3) in {str(k) for k in mr3_dict}:
            r3 = mr3_dict[code_r3]
        # Fallback : chercher MR3{sfx} directement dans la table
        if r3 is None and sfx_override:
            fb_key = f'MR3{sfx_override}'
            _fb = mr3[mr3['codeResponse3'] == fb_key] if mr3 is not None else pd.DataFrame()
            if not _fb.empty: r3 = _fb.iloc[0].to_dict()
        if r3 is None:
            issues.append("R3 (Récit historique) : absente"); return issues
        if is_empty(r3.get('qResponse', np.nan)): issues.append("R3 : récit historique non renseigné")
        cod_heir = r3.get('codeCasHeritage', np.nan); cod_achat = r3.get('codeCasAchat', np.nan)
        cod_don  = r3.get('codeCasDon', np.nan)
        if not is_empty(cod_heir):
            if cod_heir in heir_dict:
                _heir_nom = heir_dict[cod_heir].get('nomPrenomReferent', np.nan)
                if is_empty(_heir_nom): _heir_nom = heir_dict[cod_heir].get('detenteurHeritage', np.nan)
                if is_empty(_heir_nom): issues.append("R3/Héritage : référent manquant")
            else: issues.append("R3/Héritage : fiche introuvable")
        elif not is_empty(cod_don):
            if cod_don in don_dict:
                _don_nom = don_dict[cod_don].get('nomPrenomDonateur', np.nan)
                if is_empty(_don_nom): _don_nom = don_dict[cod_don].get('donateurParcelle', np.nan)
                if is_empty(_don_nom): issues.append("R3/Don : nom du donateur manquant")
            else: issues.append("R3/Don : fiche introuvable")
        elif not is_empty(cod_achat):
            if cod_achat in achat_dict:
                _ac = achat_dict[cod_achat]
                _peut_voir = safe_int(_ac.get('canWeSeeTheSeller', np.nan))
                _nom_v = str(_ac.get('nameOfSeller', '')) if not is_empty(_ac.get('nameOfSeller', np.nan)) else ''
                _decede = _remove_accents(_nom_v).lower().startswith('feu ')
                # Si on ne peut pas voir le vendeur (NON) ou s'il est décédé → RAS
                if _peut_voir != 0 and not _decede:
                    if is_empty(_ac.get('nameOfSeller', np.nan)): issues.append("R3/Achat : nom du vendeur manquant")
            else: issues.append("R3/Achat : fiche introuvable")
        else: issues.append("R3 : type de cas non renseigné")
        return issues

    minutes_tbl  = tbl('minutes'); ip_table = tbl('interviewed_person')
    signatories  = tbl('signatories'); intercalaire = tbl('minutesIntercalaire')
    inv_member   = tbl('investigation_member')

    def get_equipe(sfx):
        if inv_member is None: return 'RAS'
        members = inv_member[inv_member['memberNumber'].str.startswith(f"IMRDC{sfx}", na=False)]
        labels_present = []; missing_sig = []
        for _, m in members.iterrows():
            label = get_qual(m['codeMemberType']); labels_present.append(label)
            if is_empty(m.get('signatoryPhoto', np.nan)):
                missing_sig.append(f"{str(m['firstname']).strip()} ({label})")
        mq = []
        if not any('Commissaire' in l for l in labels_present): mq.append('Commissaire Enquêteur')
        if not any(l in ['Détenteur de droit','Gestionnaire'] for l in labels_present): mq.append('Détenteur de droit/Gestionnaire')
        if not any(l in ['Membre CVGFR','Président CVGFR','Secrétaire'] for l in labels_present): mq.append('Rep. CVGFR/Secrétaire')
        if not any(l in ['Chef de village','Notable','Répresentant'] for l in labels_present): mq.append('Rep. Chefferie/Chef de village/Notable')
        if not any(l == 'Voisin limitrophe' for l in labels_present): mq.append('Au moins un voisin')
        eq_parts = []
        if mq:          eq_parts.append("Qualités manquantes :\n" + '\n'.join(f"  • {q}" for q in mq))
        if missing_sig: eq_parts.append("Signatures manquantes :\n" + '\n'.join(f"  • {s}" for s in missing_sig))
        return '\n'.join(eq_parts) if eq_parts else 'RAS'

    def get_rdc_et_equipe(apt_num):
        sfx = extract_suffix(apt_num); rdc_num = f"RDC{sfx}"
        min_rows = minutes_tbl[minutes_tbl['minutesNumber'] == rdc_num] if minutes_tbl is not None else pd.DataFrame()
        if min_rows.empty: return 'RDC à créer', '', get_equipe(sfx)
        row = min_rows.iloc[0]
        ip_code = row.get('interviewedpersonNumber', np.nan); sig_code = row.get('signatoryNumber', np.nan)
        inter_code = row.get('codeIntercalaire', np.nan)
        # Fallback : si ip_code est NaN, chercher via TEMRDC{sfx} directement
        if is_empty(ip_code) and ip_table is not None:
            ip_rows = ip_table[ip_table['interviewedpersonNumber'].str.startswith(f'TEMRDC{sfx}', na=False)].copy()
        else:
            ip_rows = ip_table[ip_table['interviewedpersonNumber'] == ip_code].copy() if (ip_table is not None and not is_empty(ip_code)) else pd.DataFrame()
        sig_rows   = signatories[signatories['signatoryNumber'] == sig_code].copy() if (signatories is not None and not is_empty(sig_code)) else pd.DataFrame()
        # Fallback signatures : si signatoryNumber NaN, chercher via SQ2RDC{sfx} et SINTER{sfx}
        if sig_rows.empty and signatories is not None:
            sig_rows = signatories[
                signatories['numberOfPersonSigning'].str.startswith(f'SQ2RDC{sfx}', na=False) |
                signatories['numberOfPersonSigning'].str.startswith(f'SINTER{sfx}', na=False)
            ].copy()
        inter_rows = intercalaire[intercalaire['codeIntercalaire'].str.contains(sfx, na=False)].copy() if intercalaire is not None else pd.DataFrame()
        rdc_parts = []
        if ip_rows.empty:
            rdc_parts.append("Aucune personne auditionnée")
        else:
            present = {safe_int_code(c) for c in ip_rows['codeMemberType'] if safe_int_code(c) is not None}
            for codes_set, label in REQUIRED_QUAL_RDC:
                if not present.intersection(codes_set):
                    rdc_parts.append(f"Qualité manquante : {label}")
        det_lines = []; no_audit = []; no_sig = []
        if not ip_rows.empty:
            # Construire la liste effective des Détenteurs/Gestionnaires à vérifier :
            # soit directement dans ip_rows (code 1/10),
            # soit représentés par un code 13 dont le champ 'representant' mentionne leur rôle
            # OU dont le nom dans 'representant' correspond à un Détenteur connu (fiche RDC)
            _det_rows_direct = ip_rows[ip_rows['codeMemberType'].apply(
                lambda c: safe_int_code(c) in DETENTEUR_CODES)]
            # Détenteurs représentés (code 13) non encore couverts par une entrée directe
            _direct_det_names = {normalize_name(str(r['nameInterviewedPerson'])) for _, r in _det_rows_direct.iterrows()}
            for _, person in ip_rows.iterrows():
                c = safe_int_code(person['codeMemberType'])
                if c != 13: continue
                rep_val = str(person.get('representant', ''))
                if is_empty(rep_val): continue
                v_norm = _remove_accents(rep_val).lower()
                is_det_rep = False
                # Cas 1 : mot-clé de rôle dans le champ representant
                if any(kw in v_norm for kw in ['detenteur', 'gestionnaire', 'demandeur', 'droit coutumier', 'droits']):
                    is_det_rep = True
                else:
                    # Cas 2 : nom du représenté correspond à un Détenteur/Gestionnaire dans ip_rows
                    v_name = normalize_name(rep_val)
                    if any(v_name in dn or dn in v_name or match_souple(v_name, dn) for dn in _direct_det_names):
                        is_det_rep = True
                if not is_det_rep: continue
                # Extraire le nom du gestionnaire représenté depuis le champ 'representant'
                # Format courant : "NOM Prenom, gestionnaire" ou "NOM Prenom"
                _rep_name_raw = rep_val.split(',')[0].strip() if ',' in rep_val else rep_val.split('(')[0].strip()
                _rep_name_norm = normalize_name(_rep_name_raw)
                # Ne pas dupliquer si déjà dans ip_rows direct
                if any(_rep_name_norm in dn or dn in _rep_name_norm for dn in _direct_det_names):
                    continue
                # Créer une pseudo-entrée pour le gestionnaire représenté
                _pseudo = person.copy()
                _pseudo['nameInterviewedPerson'] = _rep_name_raw
                # Deviner le code : 10 (Gestionnaire) si 'gestionnaire' dans le texte, sinon 1 (Détenteur)
                _pseudo['codeMemberType'] = 10 if 'gestionnaire' in v_norm else 1
                _det_rows_direct = pd.concat([_det_rows_direct, _pseudo.to_frame().T], ignore_index=True)
            for _, person in _det_rows_direct.iterrows():
                name = str(person['nameInterviewedPerson']).strip()
                qual = get_qual(person['codeMemberType'])
                code = safe_int_code(person['codeMemberType'])
                is_det = code in DETENTEUR_CODES if code is not None else False
                if is_det:
                    # Simplifier la qualité : "Détenteur de droit" → "Détenteur", "Gestionnaire" → "Gestionnaire"
                    qual_simple = 'Gestionnaire' if 'gestionnaire' in qual.lower() else 'Détenteur'
                    r_issues = []
                    c1 = row.get('codeResponse1', np.nan)
                    # Fallback : si codeResponse1 est vide, chercher MR1{sfx} directement
                    if is_empty(c1):
                        c1_fallback = f'MR1{sfx}'
                        if c1_fallback in mr1_set: c1 = c1_fallback
                    if is_empty(c1) or c1 not in mr1_set: r_issues.append("R1 (Motif/Exploitation) : réponse absente")
                    c3 = row.get('codeResponse3', np.nan); r_issues.extend(check_r3(c3, sfx))
                    if r_issues:
                        block = f"{name} ({qual_simple}) :"
                        for iss in r_issues: block += f"\n      – {iss}"
                        det_lines.append(block)
                    det_sig = sig_rows[
                        sig_rows['description'].str.contains('demandeur|gestionnaire', case=False, na=False) &
                        sig_rows['nameSignatory'].apply(lambda n: not is_empty(n) and (
                            match_souple(name, n) or
                            normalize_name(name) in normalize_name(n) or normalize_name(n) in normalize_name(name)))
                    ] if not sig_rows.empty else pd.DataFrame()
                    signed = (not det_sig.empty) and det_sig['signatoryPhoto'].apply(lambda v: not is_empty(v)).any()
                    # Si pas signé directement : vérifier si un Représentant (code 13) a signé
                    # (il représente le détenteur/gestionnaire dans la liste de présence PVCL)
                    if not signed and not sig_rows.empty:
                        # Chercher toute signature présente dans les témoins (SINTER)
                        sinter = sig_rows[sig_rows['numberOfPersonSigning'].str.startswith(f'SINTER{sfx}', na=False)]
                        if not sinter.empty and sinter['signatoryPhoto'].apply(lambda v: not is_empty(v)).any():
                            # Vérifier si l'un de ces signataires est représentant du détenteur
                            # On accepte si au moins un représentant a signé
                            signed = True
                    if not signed: no_sig.append(f"{name} ({qual_simple})")
                else:
                    audition_ok = False
                    if not inter_rows.empty:
                        m = inter_rows[inter_rows['nomTemoin'].apply(
                            lambda t: not is_empty(t) and (normalize_name(name) in normalize_name(t) or normalize_name(t) in normalize_name(name)))]
                        audition_ok = (not m.empty) and m['reponse'].apply(lambda v: not is_empty(v)).any()
                    # Si pas d'audition propre, vérifier représentation familiale
                    # via les intercalaires des autres témoins
                    if not audition_ok and not inter_rows.empty:
                        for _, ir in inter_rows.iterrows():
                            t_nom = normalize_name(str(ir.get('nomTemoin', '')))
                            reponse = str(ir.get('reponse', ''))
                            if is_empty(reponse): continue
                            # Le nom est mentionné dans la réponse
                            if normalize_name(name) not in normalize_name(reponse): continue
                            # Vérifier la qualité du témoin dans ip_rows
                            t_match = ip_rows[ip_rows['nameInterviewedPerson'].apply(
                                lambda n: not is_empty(n) and (
                                    t_nom in normalize_name(str(n)) or normalize_name(str(n)) in t_nom))]
                            if not t_match.empty:
                                t_code = safe_int_code(t_match.iloc[0].get('codeMemberType', ''))
                                if t_code in {4, 11, 13, 14, 16, 17, 19, 20, 21}:
                                    audition_ok = True
                                    break
                            # Aussi accepter si famille directe présente et auditionnée
                            famille_dir = ip_rows[ip_rows['codeMemberType'].apply(
                                lambda c: safe_int_code(c) in {11, 14, 16, 17})]
                            if not famille_dir.empty:
                                for _, fd in famille_dir.iterrows():
                                    fd_nom = normalize_name(str(fd.get('nameInterviewedPerson', '')))
                                    fd_inter = inter_rows[inter_rows['nomTemoin'].apply(
                                        lambda t: not is_empty(t) and (
                                            fd_nom in normalize_name(str(t)) or normalize_name(str(t)) in fd_nom))]
                                    if not fd_inter.empty and fd_inter['reponse'].apply(lambda v: not is_empty(v)).any():
                                        audition_ok = True
                                        break
                            if audition_ok: break
                    if not audition_ok:
                        if code in DETENTEUR_CODES | CODES_CVGFR | CODES_CHEFFERIE | CODES_FAMILLE | {11, 19, 18, 20, 21}:
                            no_audit.append(f"{name} ({qual})")
                    else:
                        sachant_famille = False
                        if code == 4 and not inter_rows.empty:
                            nom_norm = normalize_name(name)
                            m_sach = inter_rows[inter_rows['nomTemoin'].apply(
                                lambda t: not is_empty(t) and (
                                    nom_norm in normalize_name(str(t)) or normalize_name(str(t)) in nom_norm))]
                            for _, ir_s in m_sach.iterrows():
                                rep_s = normalize_name(str(ir_s.get('reponse', '')))
                                if any(kw in rep_s for kw in _FAMILLE_TEXTUEL_KW):
                                    sachant_famille = True
                                    break
                        if not sachant_famille:
                            sm = sig_rows[sig_rows['nameSignatory'].apply(
                                lambda n: not is_empty(n) and (
                                    match_souple(name, n) or
                                    normalize_name(name) in normalize_name(n) or normalize_name(n) in normalize_name(name))
                            )] if not sig_rows.empty else pd.DataFrame()
                            if sm.empty or not sm['signatoryPhoto'].apply(lambda v: not is_empty(v)).any():
                                no_sig.append(f"{name} ({qual})")
            if det_lines: rdc_parts.append("Completer les reponses :\n" + '\n'.join(f"  • {x}" for x in det_lines))
            if no_audit:  rdc_parts.append("Audition manquante ou non renseignée :\n" + '\n'.join(f"  • {x}" for x in no_audit))
            if no_sig:    rdc_parts.append("Signature manquante :\n" + '\n'.join(f"  • {x}" for x in no_sig))
        remarque_rdc = '\n'.join(rdc_parts); remarque_equipe = get_equipe(sfx)
        statut = 'RDC terminé' if (not remarque_rdc and remarque_equipe == 'RAS') else 'RDC en cours'
        return statut, remarque_rdc, remarque_equipe

    rows = []
    for _, row in tbl('applicant').iterrows():
        apt = row['applicantNumber']
        vc  = get_village_code(apt); sc = get_subpref_code(apt)
        pvcl_s, pvcl_r       = get_pvcl_status(apt)
        rdc_s, rdc_r, equipe = get_rdc_et_equipe(apt)
        _manques_dem = []
        if is_empty(row.get('photoidentity', np.nan)):         _manques_dem.append("- Pièce d'identité manquante")
        if is_empty(row.get('identityDocumentPhoto', np.nan)): _manques_dem.append("- Demande à signer")
        photo_ok = '\n'.join(_manques_dem) if _manques_dem else 'RAS'
        sfx  = extract_suffix(apt); info = obs_dict.get(sfx)
        neigh_for_apt = pd.DataFrame()
        pres_for_apt  = pd.DataFrame()
        if info is not None:
            neigh_code = info.get('neicl')
            pres_code  = info.get('pcl')
            if obs_neigh is not None and not obs_neigh.empty and neigh_code is not None and not pd.isna(neigh_code):
                neigh_for_apt = obs_neigh[obs_neigh['code'] == neigh_code]
            if obs_pres is not None and not obs_pres.empty and pres_code is not None and not pd.isna(pres_code):
                pres_for_apt = obs_pres[obs_pres['code'] == pres_code]
        remarque_serv = get_easement_remark(apt, neigh_for_apt, list_easements, pres_for_apt)

        # ── Doublon fiche démographique ──
        _, doublon_fiche = get_doublon_fiche(apt, ip_table, get_qual)

        # ── Audition fiche démographique ──

        # ── Mode d'acquisition + remarques additionnelles + qualité RDC ──
        min_rows = minutes_tbl[minutes_tbl['minutesNumber'] == f"RDC{sfx}"] if minutes_tbl is not None else pd.DataFrame()
        min_row = min_rows.iloc[0] if not min_rows.empty else None
        inv_members_apt = inv_member[inv_member['memberNumber'].str.startswith(f"IMRDC{sfx}", na=False)] if inv_member is not None else pd.DataFrame()

        mode_acq, rem_add, qual_rdc = get_acquisition_info(
            apt, min_row, ip_table, intercalaire,
            heir_dict, don_dict, achat_dict, mr3_dict,
            inv_members_apt, get_qual, is_empty, mt_lookup,
            signatories=signatories
        )

        # Fusionner rem_add + qual_rdc dans rdc_r
        rdc_r_final = '\n'.join(p for p in [rdc_r, rem_add, qual_rdc] if p.strip())

                # ── Calcul des statuts ──
        # PVCL terminé si remarque_pvcl = RAS
        if pvcl_s == 'Pvcl à créer':
            pvcl_final = 'Pvcl à créer'
        elif has_remark(pvcl_r):
            pvcl_final = 'Pvcl en cours'
        else:
            pvcl_final = 'Pvcl terminé'

        # RDC terminé si remarque_rdc + remarque_servitude + remarque_equipe = tous RAS
        if rdc_s == 'RDC à créer':
            rdc_final = 'RDC à créer'
        elif has_remark(rdc_r_final, remarque_serv, equipe, doublon_fiche):
            rdc_final = 'RDC en cours'
        else:
            rdc_final = 'RDC terminé'

        # ── Dates PVCL (observation.creationDate / observationDate) et RDC (minutes.creationDate/updateDate) ──
        _obs_ref = parse_date_any(info.get('observationDate')) if info is not None else pd.NaT
        _d_pvcl_creation = fmt_date(parse_creation_obs(info.get('creationDate'), _obs_ref)) if info is not None else ''
        _d_pvcl_fin      = fmt_date(_obs_ref) if (pvcl_final == 'Pvcl terminé' and info is not None) else ''
        _d_rdc_creation  = fmt_date(parse_date_any(min_row.get('creationDate'))) if min_row is not None else ''
        _d_rdc_fin       = fmt_date(parse_date_any(min_row.get('updateDate'))) if (min_row is not None and rdc_final == 'RDC terminé') else ''

        rows.append({
            'N°DEMANDE'            : apt.replace('APT', ''),
            'Date création demande': co_date_dict.get(apt.replace('APT', '').strip(), ''),
            'NOM'                  : row.get('lastname', ''),
            'PRENOM'               : row.get('firstname', ''),
            'TYPE_CERTIFICAT'      : row.get('typeOfIndividualCertificate', ''),
            'NUM_PARCELLE'         : row.get('numParcelleOF', ''),
            'Village'              : village_lookup.get(vc, ''),
            'Sous-prefecture'      : subpref_lookup.get(sc, ''),
            'REMARQUE_DEMANDE'     : photo_ok,
            'Date création PVCL'   : _d_pvcl_creation,
            'STATUT_PVCL'          : pvcl_final,
            'Date PVCL terminé'    : _d_pvcl_fin,
            'REMARQUE_PVCL'        : pvcl_r,
            'Date création RDC'    : _d_rdc_creation,
            'STATUT_RDC'           : rdc_final,
            'Date RDC terminé'     : _d_rdc_fin,
            'REMARQUE_SERVITUDE'   : remarque_serv,
            'MODE_ACQUISITION'     : mode_acq,
            'DOUBLON_FICHE_DEMO'   : doublon_fiche,
            'REMARQUE_RDC'         : rdc_r_final,
            'REMARQUE_EQUIPE'      : equipe,
            'SOURCE_FICHIER'       : os.path.basename(filepath),
        })
    log(f"  ✓ {len(rows)} dossiers traités")
    return pd.DataFrame(rows)

# ─── EXPORT EXCEL ─────────────────────────────
def export_excel(df, path, sheet_name="Rapport", title="RAPPORT DOSSIERS FONCIERS", wb=None):
    create_new = (wb is None)
    if create_new:
        wb = Workbook(); ws = wb.active; ws.title = sheet_name
    else:
        ws = wb.create_sheet(title=sheet_name)
    CH="1F4E79"; CV="C6EFCE"; CEC="FFEB9C"; CAC="FFC7CE"; CSH="2E75B6"
    thin = Side(style='thin'); bdr = Border(left=thin, right=thin, top=thin, bottom=thin)
    n_cols = len(df.columns)
    ws.merge_cells(f'A1:{get_column_letter(n_cols)}1')
    ws['A1'] = title
    ws['A1'].font      = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    ws['A1'].fill      = PatternFill('solid', start_color=CH)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 28
    col_config = {
        'N°DEMANDE': 22, 'Date création demande': 16, 'Date création PVCL': 16, 'Date PVCL terminé': 16,
        'Date création RDC': 16, 'Date RDC terminé': 16, 'NOM': 20, 'PRENOM': 18, 'TYPE_CERTIFICAT': 12,
        'NUM_PARCELLE': 12, 'Village': 15, 'Sous-prefecture': 15,
        'REMARQUE_DEMANDE': 30, 'STATUT_PVCL': 14, 'REMARQUE_PVCL': 50,
        'STATUT_RDC': 14, 'REMARQUE_SERVITUDE': 55,
        'MODE_ACQUISITION': 14, 'DOUBLON_FICHE_DEMO': 40,
        'REMARQUE_RDC': 90,
        'REMARQUE_EQUIPE': 55, 'SOURCE_FICHIER': 30,
    }
    for ci, col in enumerate(df.columns, 1):
        c = ws.cell(row=2, column=ci, value=col.upper().replace('_', '\n'))
        c.font      = Font(name='Arial', bold=True, size=9, color='FFFFFF')
        c.fill      = PatternFill('solid', start_color=CSH)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.column_dimensions[get_column_letter(ci)].width = col_config.get(col, 15)
    ws.row_dimensions[2].height = 32
    for ri, (_, row_data) in enumerate(df.iterrows(), 3):
        n_lines = max(str(row_data.get('REMARQUE_RDC','')).count('\n')+1,
                      str(row_data.get('REMARQUE_EQUIPE','')).count('\n')+1,
                      str(row_data.get('REMARQUE_DEMANDE','')).count('\n')+1, 1)
        for ci, col in enumerate(df.columns, 1):
            val = row_data[col]; c = ws.cell(row=ri, column=ci, value=val)
            c.font = Font(name='Arial', size=9)
            c.alignment = Alignment(wrap_text=True, vertical='top'); c.border = bdr
            if col == 'REMARQUE_DEMANDE':
                c.fill = PatternFill('solid', start_color=CV if val=='RAS' else CAC)
            elif col in ['STATUT_PVCL','STATUT_RDC']:
                if   'terminé'  in str(val): c.fill = PatternFill('solid', start_color=CV)
                elif 'en cours' in str(val): c.fill = PatternFill('solid', start_color=CEC)
                elif 'créer'    in str(val): c.fill = PatternFill('solid', start_color=CAC)
            elif col == 'REMARQUE_SERVITUDE':
                if not val or str(val).strip()=='': c.fill = PatternFill('solid', start_color=CV)
                else:                                c.fill = PatternFill('solid', start_color=CAC)
            elif col == 'DOUBLON_FICHE_DEMO':
                if str(val).strip() == 'RAS': c.fill = PatternFill('solid', start_color=CV)
                else:                          c.fill = PatternFill('solid', start_color=CAC)
            elif col == 'REMARQUE_EQUIPE':
                if val == 'RAS': c.fill = PatternFill('solid', start_color=CV)
        ws.row_dimensions[ri].height = max(15, min(160, n_lines*20))
    ws.freeze_panes = 'A3'
    if create_new:
        wb.save(path)

# ─── MAIN ─────────────────────────────────────

# ─────────────────────────────────────────────
