"""
Moteur d'analyse adapté de Controle_demande_creee.py
Traitement CSV + Parcelle Excel → statistiques par département
"""
import io, re
import pandas as pd
from datetime import datetime

DEPARTEMENTS = ['SOUBRE', 'MEAGUI', 'BUYO']

# ── Agents (source : numero_agent.xlsx) ──────────────────────────────────────
import json
import os
AGENT_MAP = {(r['machine'], r['departement']): r['nom_agent']
             for r in json.loads(os.environ.get('PRESFOR_LEGACY_AGENTS_JSON', '[]'))}
_ABM = {}
for (_m,_d),_n in AGENT_MAP.items():
    _ABM.setdefault(_m,[]).append(_n)

def get_agent_name(machine, dept=''):
    if not machine: return 'Sans_agent'
    if (machine, dept) in AGENT_MAP: return AGENT_MAP[(machine, dept)]
    noms = _ABM.get(machine, [])
    return noms[0] if len(noms) == 1 else machine

# ── Helpers ───────────────────────────────────────────────────────────────────
def clean(v):
    s = str(v).strip() if v is not None else ''
    return '' if s.lower() in ('nan','none','') else s

def fmt_date(s):
    s = clean(s).split('T')[0]
    for f in ('%Y-%m-%d','%d-%m-%Y','%d/%m/%Y','%Y/%m/%d'):
        try: return datetime.strptime(s, f).strftime('%d-%m-%Y')
        except: pass
    return ''

def fmt_date_parcelle(raw):
    s = clean(raw).split(' ')[0]
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if m:
        d,mo,y = m.groups()
        return f"{int(d):02d}-{int(mo):02d}-{y}"
    return fmt_date(s)

def get_code(v):
    """Code de demande complet : 3-3-6 (ex. 375-001-051076).

    C'est la clé de jointure de TOUTES les tables liées à une demande
    (observation, minutes, publicité, existence, validation, certificate_order).
    Le code village 3-3 ne suffit pas : il confondrait toutes les demandes
    d'un même village.
    """
    m = re.search(r'(\d{3}-\d{3}-\d{6})', str(v))
    return m.group(1) if m else ''


def sans_prefixe(v):
    """Retire le préfixe alphabétique (APT, RDC, PCL…) d'un numéro."""
    s = clean(v)
    code = get_code(s)
    return code if code else re.sub(r'^[A-Za-z]+', '', s)


def extract_parcelle_num(code):
    s = str(code).strip()
    if not s or s.lower() in ('nan','none',''): return ''
    return re.sub(r'\D','', re.sub(r'^[Pp]\s*','',s))

def find_col(d, candidates):
    def norm(s):
        s = str(s).strip().lower()
        for a,b in [('é','e'),('è','e'),('ê','e'),('à','a'),('ç','c')]:
            s=s.replace(a,b)
        return re.sub(r'[^a-z0-9]','',s)
    km = {norm(k):k for k in d.keys()}
    for c in candidates:
        cn = norm(c)
        if cn in km:
            v = clean(d[km[cn]])
            if v: return v
    return ''

def val_col(row, *candidats):
    """Première valeur non vide parmi plusieurs noms de colonnes possibles.

    Les exports Digifor ne nomment pas les colonnes de la même façon d'une
    version à l'autre (codeAdvertisement / advertisementNumber, announceDate /
    advertisementDate…). On accepte donc les deux familles de noms.
    """
    try:
        cles = row.keys()
    except AttributeError:
        return ''
    index = {str(k).strip().lower(): k for k in cles}
    for c in candidats:
        k = index.get(c.strip().lower())
        if k is None:
            continue
        v = clean(row.get(k, ''))
        if v:
            return v
    return ''


def _norm_sp(s):
    s = str(s).strip().lower()
    for a,b in [('é','e'),('è','e'),('ê','e'),('à','a'),('ç','c'),('î','i')]:
        s=s.replace(a,b)
    return re.sub(r'\s+','',s)

def extract_table(content, tname):
    marker = f"### TABLE: {tname}"
    start = content.find(marker)
    if start == -1: return None
    next_m = content.find("### TABLE:", start+len(marker))
    block = content[start:next_m if next_m!=-1 else len(content)]
    lines = block.split('\n')
    data_lines, in_data = [], False
    for line in lines[1:]:
        if line.startswith('#') or line.strip()=='':
            if in_data: break
            continue
        in_data = True
        data_lines.append(line)
    if not data_lines: return None
    try:
        return pd.read_csv(io.StringIO('\n'.join(data_lines)), sep=';', dtype=str)
    except: return None

# ── Traitement d'un CSV ────────────────────────────────────────────────────────
def process_csv(content_str):
    """
    Traite le contenu d'un CSV export Digifor.
    Retourne (df_demandes, village_map, subpref_map)
    """
    name_map = {
        'applicant':'applicant','observation':'observation',
        'obs_n':'observationNeighborhood','obs_pres':'observationPresence',
        'minutes':'minutes','advertisement':'advertisement',
        'open_adv':'openAdvertising','close_adv':'closeOfAdvertising',
        'existence':'existence','village':'village','subprefecture':'subprefecture',
        'cert_order':'certificate_order','validate':'validate',
        'department':'department',
    }
    # Les exports ne nomment pas toujours les tables de la même façon
    # (department / departement, certificate_order / certificateOrder…).
    # On essaie chaque variante avant de renoncer.
    ALIAS = {
        'department': ['department', 'departement'],
        'cert_order': ['certificate_order', 'certificateOrder', 'certificat_order'],
        'open_adv':   ['openAdvertising', 'open_advertising', 'openadvertising'],
        'close_adv':  ['closeOfAdvertising', 'close_of_advertising', 'closeadvertising'],
        'obs_n':      ['observationNeighborhood', 'observation_neighborhood'],
        'obs_pres':   ['observationPresence', 'observation_presence'],
    }
    tables = {}
    for k, v in name_map.items():
        resultat = None
        for nom in ALIAS.get(k, [v]):
            resultat = extract_table(content_str, nom)
            if resultat is not None:
                break
        tables[k] = resultat if resultat is not None else pd.DataFrame()

    # ── village_map / subpref_map ─────────────────────────────────────────────
    village_map  = {}
    subpref_map  = {}
    if not tables['village'].empty:
        for _,r in tables['village'].iterrows():
            code = clean(r.get('codeVillage',''))
            if code:
                village_map[code] = (clean(r.get('label','')), clean(r.get('codeSubPrefecture','')))
    if not tables['subprefecture'].empty:
        for _,r in tables['subprefecture'].iterrows():
            code = clean(r.get('codeSubPrefecture',''))
            if code: subpref_map[code] = clean(r.get('label',''))

    # ── dept_map : codeDepartement → label ───────────────────────────────────
    dept_map = {}
    if not tables['department'].empty:
        for _,r in tables['department'].iterrows():
            code = clean(r.get('codeDepartement',''))
            if code: dept_map[code] = clean(r.get('label',''))

    # ── sp_to_dept : sous_pref label → département label ─────────────────────
    # Construit depuis subprefecture.codeDepartement → department.label
    # Permet d'assigner le département directement depuis le CSV
    sp_to_dept = {}
    if not tables['subprefecture'].empty:
        for _,r in tables['subprefecture'].iterrows():
            sp_label   = clean(r.get('label',''))
            code_dept  = clean(r.get('codeDepartement',''))
            dept_label = dept_map.get(code_dept, '')
            if sp_label and dept_label:
                sp_to_dept[sp_label.upper()] = dept_label

    def get_village_code(s):
        m = re.search(r'(\d{3}-\d{3})', str(s))
        return m.group(1) if m else ''

    def safe_int_code(v):
        try: return int(float(str(v)))
        except: return None

    # ── PVCL : observation → presences et voisins ─────────────────────────────
    obs_map = {}; obs_neigh_map = {}; obs_date_map = {}; obs_geometre_map = {}
    if not tables['observation'].empty:
        for _,r in tables['observation'].iterrows():
            nc = get_code(val_col(r, 'codeObservation', 'observationNumber'))
            if nc:
                obs_map[nc]       = clean(r.get('presences',''))
                obs_neigh_map[nc] = clean(r.get('neighborhood',''))
                d_obs = clean(r.get('observationDate','')) or clean(r.get('creationDate',''))
                obs_date_map[nc]  = fmt_date(str(d_obs))
                # Un géomètre nommé sur la fiche dispense de sa présence à la
                # liste — même règle que Controle_qualite_v8.py.
                obs_geometre_map[nc] = clean(r.get('expertSurveyor',''))

    # obs_n_map : code_neicl → [nameOfNeighbor]
    obs_n_map = {}
    if not tables['obs_n'].empty:
        for _,r in tables['obs_n'].iterrows():
            code = clean(r.get('code',''))
            name = clean(r.get('nameOfNeighbor',''))
            if code and name:
                obs_n_map.setdefault(code,[]).append(name)

    # obs_pres_map : code_presences → [{name, memberType, signatoryPhoto, representant}]
    obs_pres_map = {}
    if not tables['obs_pres'].empty:
        for _,r in tables['obs_pres'].iterrows():
            pc = clean(r.get('code',''))
            if pc:
                obs_pres_map.setdefault(pc,[]).append({
                    'name':          clean(r.get('nameOfPerson','')),
                    'memberType':    safe_int_code(r.get('memberType','')),
                    'signatoryPhoto':bool(clean(r.get('signatoryPhoto',''))),
                    'representant':  clean(r.get('representant','')),
                })

    # Qualités requises au PVCL (cf. Controle_qualite_v8.py)
    PVCL_DET   = {1, 10}      # Détenteur de droit, Gestionnaire
    PVCL_CHEFF = {3, 12, 15}  # Chef de village, Notable, Chef de terre
    PVCL_CVGFR = {5, 8, 9}    # Membre, Président, Secrétaire CVGFR
    PVCL_CE    = {2}           # Commissaire Enquêteur
    PVCL_GEO   = {6}           # Géomètre

    _SERV_KW = ['route','piste','riviere','fleuve','rivière','foret','forêt',
                'chemin','axe','ligne','haute tension','parc','reserve','réserve',
                'marigot','lac ','cimetiere']

    def _rep_kw(entries, kw_list):
        for e in entries:
            if e['memberType'] == 13:
                v = (e.get('representant','') or '').lower()
                if any(kw in v for kw in kw_list):
                    return True
        return False

    # Libellés repris textuellement de Controle_qualite_v8.py, pour que les
    # remarques affichées ici soient exactement celles du rapport Excel.
    PVCL_MANQUE = {
        'chefferie': 'Ajouter (Rep. Chefferie/Chef de village)',
        'cvgfr':     'Ajouter (Rep. CVGFR/Membre CVGFR/Secrétaire)',
        'ce':        'Ajouter (Commissaire Enquêteur)',
        'geometre':  'Ajouter (Rep. Géomètre)',
    }
    # Qualités obligatoires du RDC — REQUIRED_QUAL_RDC de Controle_qualite_v8.py
    REQUIRED_QUAL_RDC = [
        ({1, 10}, 'Détenteur de droit / Gestionnaire'),
        ({4, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 3},
         'Personne ressource (Sachant/Notable/Famille/Témoin/Autres)'),
        ({5, 8, 9}, 'Membre CVGFR / Secrétaire'),
    ]

    def compute_statut_pvcl(nc):
        """Statut PVCL et motifs du blocage.

        Renvoie (statut, date_fin, remarque). La remarque énumère ce qui manque
        pour que le PVCL soit considéré terminé — c'est elle qui s'affiche dans
        la colonne « Remarque » des demandes non terminées.
        """
        if nc not in obs_map:
            return 'Pvcl à créer', '', 'Pvcl à créer : aucune fiche de constat de limite.'

        presences_code = obs_map[nc]
        neigh_code     = obs_neigh_map.get(nc, '')
        entries        = obs_pres_map.get(presences_code, [])
        neighbors      = obs_n_map.get(neigh_code, []) if neigh_code else []

        if not entries:
            return 'Pvcl en cours', '', 'Aucune personne présente sur la liste de présence.'

        member_types = {e['memberType'] for e in entries if e['memberType'] is not None}
        pres_names   = [e['name'].lower() for e in entries if e['name']]
        pres_reps    = [(e.get('representant', '') or '').lower() for e in entries]

        # 1. Voisins présents (hors servitudes routières/naturelles)
        neigh_valid = [nb for nb in neighbors if nb and
                       not any(kw in nb.lower() for kw in _SERV_KW)]
        voisins_absents = [
            nb for nb in neigh_valid
            if nb.lower() not in pres_names
            and not any(nb.lower() in rep for rep in pres_reps if rep)
        ]

        # 2. Qualités obligatoires (directes ou via représentant code 13)
        manquantes = []
        if not (member_types & PVCL_CHEFF or _rep_kw(
                entries, ['notable','chef','chefferie','village','campement','communaute','tribu'])):
            manquantes.append(PVCL_MANQUE['chefferie'])
        if not (member_types & PVCL_CVGFR or _rep_kw(
                entries, ['cvgfr','comite','villageois','membre cvgfr'])):
            manquantes.append(PVCL_MANQUE['cvgfr'])
        if not member_types & PVCL_CE:
            manquantes.append(PVCL_MANQUE['ce'])
        if not (PVCL_GEO & member_types or _rep_kw(entries, ['geometre','expert'])
                or obs_geometre_map.get(nc)):
            manquantes.append(PVCL_MANQUE['geometre'])

        # 3. Signatures photographiées
        non_signes = [e['name'] for e in entries if not e['signatoryPhoto'] and e['name']]

        # Les messages suivent mot pour mot ceux de Controle_qualite_v8.py.
        motifs = list(manquantes)
        if voisins_absents:
            motifs.insert(0, 'Voisins manquants: ' + ', '.join(voisins_absents))
        if non_signes:
            motifs.append('Signature manquante :\n'
                          + '\n'.join(f'  • {n}' for n in non_signes))

        if motifs:
            return 'Pvcl en cours', '', '\n'.join(motifs)
        return 'Pvcl terminé', obs_date_map.get(nc, ''), ''

    # ── RDC : minutes ──────────────────────────────────────────────────────────
    minutes_map = {}
    if not tables['minutes'].empty:
        for _,r in tables['minutes'].iterrows():
            nc = get_code(val_col(r, 'minutesNumber', 'codeMinutes'))
            if nc: minutes_map[nc] = r.to_dict()

    # ── Dates de la chaîne CF ────────────────────────────────────────────────
    # Toutes ces tables se joignent sur le code de demande complet (3-3-6).
    # Les noms de colonnes varient selon la version de l'export : on accepte
    # les deux familles (codeAdvertisement / advertisementNumber, etc.).
    def map_dates(table, cles_code, cles_date):
        out = {}
        if tables[table].empty:
            return out
        for _, r in tables[table].iterrows():
            nc = get_code(val_col(r, *cles_code))
            if nc:
                out[nc] = fmt_date(val_col(r, *cles_date))
        return out

    adv_map   = map_dates('advertisement',
                          ('codeAdvertisement', 'advertisementNumber'),
                          ('announceDate', 'advertisementDate', 'creationDate'))
    open_map  = map_dates('open_adv',
                          ('codeOpenAdvertising', 'advertisementNumber', 'codeAdvertisement'),
                          ('openDate', 'openingDate', 'creationDate'))
    close_map = map_dates('close_adv',
                          ('codeCloseOfAdvertising', 'advertisementNumber', 'codeAdvertisement'),
                          ('closeDate', 'closingDate', 'creationDate'))
    exist_map = map_dates('existence',
                          ('codeExistence', 'existenceNumber'),
                          ('dateExistence', 'existenceDate', 'creationDate'))
    validate_map = map_dates('validate',
                             ('codeValidate', 'validateNumber'),
                             ('creationDate', 'validateDate', 'date'))
    cert_date_map = map_dates('cert_order',
                              ('orderNumber', 'certificateOrderNumber', 'codeCertificateOrder'),
                              ('creationDate', 'orderDate'))

    def compute_etape(p, r, ann, ouv, clo, exi, val):
        """Étape de la chaîne CF, priorité décroissante.

        Libellés et logique repris de detection_etape_cf.py :
          1. date de validation  → Validation
          2. dates publicité / existence → étape la plus avancée
          3. sinon, croisement PVCL × RDC
        """
        if val: return 'Validation'
        if exi: return "Constat d'existence"
        if clo: return 'Cloture de publicité'
        if ouv: return 'Ouverture de publicité'
        if ann: return 'Annonce de publicité'

        if p == 'Pvcl à créer' and r == 'RDC à créer':
            return 'Demande'
        if (p == 'Pvcl en cours' and r == 'RDC à créer') or \
           (p == 'Pvcl à créer'  and r == 'RDC encours') or \
           (p == 'Pvcl à créer'  and r == 'RDC terminé') or \
           (p == 'Pvcl terminé'  and r == 'RDC à créer'):
            return 'Constat de limite'
        return 'Recensement de DC'

    rows = []
    if tables['applicant'].empty:
        return pd.DataFrame(), village_map, subpref_map, sp_to_dept

    for _,apt in tables['applicant'].iterrows():
        apt_num = clean(apt.get('applicantNumber',''))
        nc = get_code(apt_num)          # clé de demande : 3-3-6
        vc = get_village_code(apt_num)  # clé de village : 3-3
        vill_info = village_map.get(vc,('',''))
        vill_label = vill_info[0]
        sp_code    = vill_info[1]
        sp_label   = subpref_map.get(sp_code,'')

        # PVCL — qualités, signatures et voisins
        if nc:
            statut_pvcl, date_pvcl_fin, remarque_pvcl = compute_statut_pvcl(nc)
        else:
            statut_pvcl, date_pvcl_fin = 'Pvcl à créer', ''
            remarque_pvcl = 'Numéro de demande illisible : rapprochement impossible.'

        # RDC — champs obligatoires de la fiche minutes
        RDC_LIB = {
            'interviewedpersonNumber': 'Aucune personne auditionnée',
            'signatoryNumber':         'Signature manquante',
            'codeResponse1':           'Completer les reponses : question 1',
            'codeResponse3':           'Completer les reponses : question 3',
        }
        if nc and nc in minutes_map:
            mr = minutes_map[nc]
            # RDC terminé : 4 champs obligatoires seulement.
            # codeResponse4 est conditionnel (vide dans la majorité des fiches) et
            # codeIntercalaire/codeResponse2 ne sont pas systématiquement servis :
            # les exiger créait une forte discordance avec Controle_qualite_v8.py.
            manquants = [lib for champ, lib in RDC_LIB.items() if not clean(mr.get(champ, ''))]
            rdc_ok = not manquants
            statut_rdc   = 'RDC terminé' if rdc_ok else 'RDC encours'
            date_rdc     = fmt_date(clean(mr.get('creationDate','')))
            date_rdc_fin = fmt_date(clean(mr.get('updateDate',''))) if rdc_ok else ''
            remarque_rdc = '' if rdc_ok else '\n'.join(manquants)
        else:
            statut_rdc = 'RDC à créer'; date_rdc = ''; date_rdc_fin = ''
            remarque_rdc = 'RDC à créer : aucune fiche RDC enregistrée.'

        d_ann=adv_map.get(nc,'') if nc else ''
        d_ouv=open_map.get(nc,'') if nc else ''
        d_clo=close_map.get(nc,'') if nc else ''
        d_exi=exist_map.get(nc,'') if nc else ''
        d_val=validate_map.get(nc,'') if nc else ''
        etape=compute_etape(statut_pvcl,statut_rdc,d_ann,d_ouv,d_clo,d_exi,d_val)

        # Département depuis la chaîne CSV : village → sous-pref → département
        dept_label = sp_to_dept.get(sp_label.upper(), '') if sp_label else ''

        remarque = '\n'.join(m for m in (remarque_pvcl, remarque_rdc) if m)

        rows.append({
            'num_demande':    sans_prefixe(apt_num),
            'identite_agent': clean(apt.get('nom_agent', '') or apt.get('code_agent','') or apt.get('id_machine','')),
            'num_demande_brut': apt_num,
            'departement':    dept_label,
            'sous_prefecture':sp_label,
            'village':        vill_label,
            'nom':            clean(apt.get('firstname','')),
            'prenom':         clean(apt.get('lastname','')),
            'num_parcelle':   clean(apt.get('numParcelleOF','')),
            'type_certificat':clean(apt.get('typeOfIndividualCertificate','')),
            'statut_pvcl':    statut_pvcl,
            'statut_rdc':     statut_rdc,
            'etape':          etape,
            'date_creation':  cert_date_map.get(nc,'') if nc else '',
            'date_pvcl':      obs_date_map.get(nc,'') if nc else '',
            'date_pvcl_fin':  date_pvcl_fin,
            'date_rdc':       date_rdc,
            'date_rdc_fin':   date_rdc_fin,
            'date_annonce':   d_ann,
            'date_ouverture': d_ouv,
            'date_cloture':   d_clo,
            'date_epc':       d_exi,
            'date_validation': d_val,
            'remarque_pvcl':  remarque_pvcl,
            'remarque_rdc':   remarque_rdc,
            'remarque':       remarque,
        })
    return pd.DataFrame(rows), village_map, subpref_map, sp_to_dept


# ── Chargement fichier Parcelle ────────────────────────────────────────────────
def load_parcelles_from_file(file_obj):
    df = pd.read_excel(file_obj, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    col_code = next((c for c in df.columns if c.strip().lower().replace(' ','_')
                     in ('code_parcelle','codeparcelle')), None)
    if not col_code: raise ValueError("Colonne 'code_parcelle' introuvable")
    parcelle_map = {}
    for _,row in df.iterrows():
        num = extract_parcelle_num(row.get(col_code,''))
        if num: parcelle_map.setdefault(num,[]).append(row.to_dict())
    return parcelle_map


def date_leve(entree):
    """Date de levé d'une parcelle, quelle que soit la source."""
    return fmt_date_parcelle(
        find_col(entree, ['date_creation', 'date_creat', 'date_levee',
                          'date_leve', 'dateCreation']))


# ── Chargement des parcelles depuis la base terrain PostGIS ───────────────────
def load_parcelles_from_postgis(date_debut=None, date_fin=None):
    """Construit le même parcelle_map que le fichier Excel, mais depuis PostGIS.

    Le fichier Parcelle n'est alors plus nécessaire : surface, village,
    sous-préfecture et département viennent directement de la base terrain.
    """
    from terrain.db import executer_fichier   # import différé : Django doit être prêt

    lignes = executer_fichier('parcelles', date_debut, date_fin)
    parcelle_map = {}
    for l in lignes:
        num = extract_parcelle_num(l.get('code_parcelle', ''))
        if not num:
            continue
        parcelle_map.setdefault(num, []).append({
            'code_parcelle': l.get('code_parcelle', ''),
            'surface_ha':    l.get('surface_ha'),
            'depart':        l.get('depart') or '',
            'sous_pref':     l.get('sous_pref') or '',
            'village':       l.get('name_villa') or '',
            'id_machine':    l.get('id_machine') or '',
            'id_parcelle': l.get('id_parcelle') or '',
            'nom_agent': l.get('nom_agent') or '',
            'date_creation': l.get('date_creation') or '',
            'nom_pers':      l.get('nom_pers') or '',
            'prenom_pers':   l.get('prenom_pers') or '',
        })
    return parcelle_map



# ── Observations du moteur de contrôle qualité v8 ─────────────────────────────
def analyser_v8(contenu, nom_fichier):
    """Exécute le moteur v8 sur un export. Voir analyse/qualite_v8.py."""
    from .qualite_v8 import analyser
    return analyser(contenu, nom_fichier)


def appliquer_observations_v8(df, observations):
    """Remplace les remarques de la plateforme par celles du moteur v8.

    Les statuts sont repris eux aussi : c'est la condition pour que la
    plateforme et le rapport Excel de référence disent la même chose.
    """
    from .qualite_v8 import STATUTS_PVCL, STATUTS_RDC

    correspondances = {
        'nom': 'NOM',
        'prenom': 'PRENOM',
        'remarque_pvcl':      'REMARQUE_PVCL',
        'remarque_rdc':       'REMARQUE_RDC',
        'remarque_servitude': 'REMARQUE_SERVITUDE',
        'remarque_equipe':    'REMARQUE_EQUIPE',
        'doublon_fiche':      'DOUBLON_FICHE_DEMO',
        'mode_acquisition':   'MODE_ACQUISITION',
        'photo_id':           'REMARQUE_DEMANDE',
        'date_creation': 'Date création demande',
        'date_pvcl': 'Date création PVCL',
        'date_pvcl_fin': 'Date PVCL terminé',
        'date_rdc': 'Date création RDC',
        'date_rdc_fin': 'Date RDC terminé',
    }
    for champ in correspondances:
        if champ not in df.columns:
            df[champ] = ''
    df['source_qualite'] = 'plateforme'

    for i, num in df['num_demande'].items():
        obs = observations.get(str(num))
        if not obs:
            continue
        for champ, colonne in correspondances.items():
            df.at[i, champ] = obs.get(colonne, '')
        pvcl = STATUTS_PVCL.get(obs.get('STATUT_PVCL', ''))
        rdc = STATUTS_RDC.get(obs.get('STATUT_RDC', ''))
        if pvcl:
            df.at[i, 'statut_pvcl'] = pvcl
        if rdc:
            df.at[i, 'statut_rdc'] = rdc
        df.at[i, 'source_qualite'] = 'v8'

    # L'étape dépend des statuts : la recalculer après leur mise à jour.
    if 'etape' in df.columns:
        df['etape'] = df.apply(lambda r: recalculer_etape(r), axis=1)
    return df


def recalculer_etape(row):
    """Étape de la chaîne CF à partir des statuts et des dates de la ligne."""
    if row.get('date_validation'):   return 'Validation'
    if row.get('date_epc'):          return "Constat d'existence"
    if row.get('date_cloture'):      return 'Cloture de publicité'
    if row.get('date_ouverture'):    return 'Ouverture de publicité'
    if row.get('date_annonce'):      return 'Annonce de publicité'
    p, r = row.get('statut_pvcl', ''), row.get('statut_rdc', '')
    if p == 'Pvcl à créer' and r == 'RDC à créer':
        return 'Demande'
    if (p == 'Pvcl en cours' and r == 'RDC à créer') or \
       (p == 'Pvcl à créer'  and r == 'RDC encours') or \
       (p == 'Pvcl à créer'  and r == 'RDC terminé') or \
       (p == 'Pvcl terminé'  and r == 'RDC à créer'):
        return 'Constat de limite'
    return 'Recensement de DC'


# ── Rapprochement demande ↔ parcelle ──────────────────────────────────────────
def _txt(v):
    """Normalise un libellé administratif pour la comparaison."""
    s = str(v or '').strip().lower()
    for a, b in [('é','e'),('è','e'),('ê','e'),('ë','e'),('à','a'),('â','a'),
                 ('ç','c'),('ô','o'),('ö','o'),('î','i'),('ï','i'),('û','u'),('ù','u')]:
        s = s.replace(a, b)
    return re.sub(r'[^a-z0-9]', '', s)


class IndexParcelles:
    """Rapprochement d'une demande avec sa parcelle.

    Le code parcelle seul ne suffit pas : il n'est unique qu'à l'intérieur d'un
    village. Dans la couche publiée, P3514 désigne quatre parcelles distinctes,
    à GNAGBOYA (deux fois), à KOUDOUYO et dans un village non renseigné. La clé
    de rapprochement est donc **(code parcelle, village, sous-préfecture)**.

    La résolution est en cascade, du plus précis au plus large. Chaque niveau
    est compté, ce qui permet d'afficher la qualité du rapprochement plutôt que
    de la supposer :

      exact      (code, village, sous-préfecture)
      village    (code, village)
      sous_pref  (code, sous-préfecture)
      code       code seul, et une seule parcelle porte ce code
      ambigu     code seul, mais plusieurs parcelles le portent → non rapproché
      absent     aucune parcelle ne porte ce code
    """

    NIVEAUX = ['exact', 'village', 'sous_pref', 'code', 'ambigu', 'absent']

    def __init__(self, parcelle_map):
        self.par_cle = {}        # (num, village, sous_pref)
        self.par_num_vill = {}   # (num, village)
        self.par_num_sp = {}     # (num, sous_pref)
        self.par_num = {}        # num
        self.compteurs = {n: 0 for n in self.NIVEAUX}

        for num, entrees in (parcelle_map or {}).items():
            for e in entrees:
                vill = _txt(find_col(e, ['name_villa', 'village', 'nom_village', 'localite']))
                sp   = _txt(find_col(e, ['sous_pref', 'sous_prefecture', 'souspref', 'sous-pref']))
                self.par_num.setdefault(num, []).append(e)
                self.par_cle.setdefault((num, vill, sp), []).append(e)
                if vill:
                    self.par_num_vill.setdefault((num, vill), []).append(e)
                if sp:
                    self.par_num_sp.setdefault((num, sp), []).append(e)

    def resoudre(self, code_parcelle, village='', sous_pref=''):
        """Renvoie (entree | None, niveau)."""
        num = extract_parcelle_num(code_parcelle)
        if not num:
            return None, 'absent'
        v, sp = _txt(village), _txt(sous_pref)

        if v and sp:
            trouve = self.par_cle.get((num, v, sp))
            if trouve:
                return trouve[0], 'exact'
        if v:
            trouve = self.par_num_vill.get((num, v))
            if trouve:
                return trouve[0], 'village'
        if sp:
            trouve = self.par_num_sp.get((num, sp))
            if trouve:
                return trouve[0], 'sous_pref'

        candidats = self.par_num.get(num)
        if not candidats:
            return None, 'absent'
        if len(candidats) == 1:
            return candidats[0], 'code'
        # Plusieurs parcelles portent ce code et rien ne permet de trancher :
        # mieux vaut ne pas rapprocher que rapprocher au hasard.
        return None, 'ambigu'

    def resoudre_et_compter(self, code_parcelle, village='', sous_pref=''):
        entree, niveau = self.resoudre(code_parcelle, village, sous_pref)
        self.compteurs[niveau] += 1
        return entree, niveau

    def cle_de(self, entree, num):
        vill = _txt(find_col(entree, ['name_villa', 'village', 'nom_village', 'localite']))
        sp   = _txt(find_col(entree, ['sous_pref', 'sous_prefecture', 'souspref', 'sous-pref']))
        return (num, vill, sp)

    @property
    def qualite(self):
        total = sum(self.compteurs.values()) or 1
        return {
            'total': sum(self.compteurs.values()),
            'detail': dict(self.compteurs),
            'taux_exact': round(self.compteurs['exact'] / total * 100, 1),
            'taux_rapproche': round(
                sum(self.compteurs[n] for n in ('exact', 'village', 'sous_pref', 'code'))
                / total * 100, 1),
        }


# ── Pipeline principal ─────────────────────────────────────────────────────────
def run_analyse(csv_files, parcelle_file=None, date_debut=None, date_fin=None,
                source_parcelles='fichier', rattachement='parcelle', progression=None):
    """
    csv_files       : liste de (nom, contenu_bytes ou fichier ouvert)
    parcelle_file   : contenu_bytes ou None (utilisé si source_parcelles='fichier')
    source_parcelles: 'postgis' | 'fichier' | 'aucune'
    rattachement    : 'parcelle' — la période porte sur la date de levé, et seules
                      les demandes rattachées à ces parcelles sont comptées ;
                      'demande'  — la période porte sur les dates de la demande.
    Retourne dict avec demandes DataFrame, stats par dept, etc.
    """
    from django.conf import settings
    if settings.PRESFOR_POSTGRES_SURFACES_ONLY and source_parcelles != 'postgis':
        raise ValueError('La superficie des parcelles doit provenir exclusivement de PostgreSQL. Sélectionnez la base terrain.')
    all_demandes = []
    global_sp_to_dept = {}   # sous_pref label → département (agrégé de tous les CSV)
    avertissements_qualite = []
    nb_dossiers_v8 = 0

    for index_csv, (nom, content_bytes) in enumerate(csv_files):
        if progression: progression(index_csv, len(csv_files), "Contrôle qualité des CSV")
        if hasattr(content_bytes, "read"):
            content_bytes = content_bytes.read()
        try:
            for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
                try:
                    content = content_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                content = content_bytes.decode('utf-8', errors='replace')
        except Exception:
            content = str(content_bytes)

        df, _, _, sp_to_dept = process_csv(content)
        global_sp_to_dept.update(sp_to_dept)

        # Moteur de contrôle qualité v8 : ses observations, mot pour mot,
        # remplacent celles calculées par la plateforme quand il aboutit.
        observations, message = analyser_v8(content_bytes, nom)
        if message:
            avertissements_qualite.append(message)
        if observations and not df.empty:
            df = appliquer_observations_v8(df, observations)
            nb_dossiers_v8 += sum(
                1 for v in df['num_demande'] if str(v) in observations)

        if not df.empty:
            # 'departement' est déjà dans df (assigné dans process_csv via village→sous-pref→dept)
            df['fichier_source'] = nom
            all_demandes.append(df)

    if not all_demandes:
        raise ValueError(
            "Aucune demande trouvée dans les CSV fournis. "
            "Vérifiez que les fichiers sont bien des exports Digifor complets "
            "(format ### TABLE: applicant attendu)."
        )

    if progression: progression(len(csv_files), len(csv_files), 'Rapprochement avec les parcelles PostgreSQL')
    df_all = pd.concat(all_demandes, ignore_index=True)

    # ── Jointure Parcelle (enrichissement surface + complétion département) ────
    parcelle_map = {}
    index_parcelles = None
    if source_parcelles == 'postgis':
        parcelle_map = load_parcelles_from_postgis(date_debut, date_fin)
    elif parcelle_file:
        parcelle_map = load_parcelles_from_file(parcelle_file)

    if parcelle_map:

        # Compléter sp_to_dept depuis le fichier Parcelle
        sp_to_dept_parc = {}
        for num, entries in parcelle_map.items():
            dept = find_col(entries[0], ['depart','departement','dept'])
            sp   = find_col(entries[0], ['sous_pref','sous_prefecture'])
            if dept and sp:
                sp_to_dept_parc[sp.strip().upper()] = dept
        global_sp_to_dept.update(sp_to_dept_parc)

        index_parcelles = IndexParcelles(parcelle_map)

        def entree_de(row):
            """Parcelle correspondant à une demande, par clé composite."""
            e, _ = index_parcelles.resoudre(
                row.get('num_parcelle', ''), row.get('village', ''),
                row.get('sous_prefecture', ''))
            return e

        def get_pfield_row(row, candidates):
            e = entree_de(row)
            return find_col(e, candidates) if e else ''

        # Niveau de rapprochement de chaque demande, conservé pour diagnostic
        niveaux = df_all.apply(
            lambda r: index_parcelles.resoudre_et_compter(
                r.get('num_parcelle', ''), r.get('village', ''),
                r.get('sous_prefecture', ''))[1], axis=1)
        df_all['rapprochement'] = niveaux

        df_all['surface'] = pd.to_numeric(
            df_all.apply(lambda r: get_pfield_row(r, ['surface_ha','surface','superficie']), axis=1),
            errors='coerce')

        df_all['identite_agent'] = df_all.apply(lambda r: get_pfield_row(r, ['nom_agent','nom agents','ce','id_machine','code_agent']) or r.get('identite_agent',''), axis=1)

        if source_parcelles == 'postgis':
            df_all['surface_postgres'] = df_all['surface']
            df_all['id_parcelle_source'] = df_all.apply(lambda r: get_pfield_row(r, ['id_parcelle']), axis=1)

        # Enrichir département si la parcelle a une info plus précise
        dept_from_parc = df_all.apply(
            lambda r: get_pfield_row(r, ['depart','departement','dept']), axis=1)
        mask_upgrade = dept_from_parc != ''
        df_all.loc[mask_upgrade, 'departement'] = dept_from_parc[mask_upgrade]

    # Fallback final : si encore vide, essayer via sous_prefecture → global_sp_to_dept
    if 'departement' in df_all.columns:
        empty_dept = df_all['departement'].isin(['', None]) | df_all['departement'].isna()
        if empty_dept.any() and global_sp_to_dept:
            df_all.loc[empty_dept, 'departement'] = df_all.loc[empty_dept, 'sous_prefecture'].apply(
                lambda sp: global_sp_to_dept.get(str(sp).strip().upper(), '') if sp else ''
            )

    # ── Cohorte : les parcelles levées sur la période ────────────────────────
    # Une demande n'existe que pour une parcelle levée. Compter les demandes sur
    # leur propre date de création, indépendamment des parcelles, produit deux
    # populations sans rapport : 414 demandes en face de 198 parcelles, et zéro
    # demande en face de 72 parcelles ailleurs.
    #
    # En mode « parcelle » (par défaut), on part donc des parcelles levées dans
    # la période et on ne retient que les demandes qui s'y rattachent. La
    # question posée devient : « pour ces N parcelles levées, combien de
    # demandes ont été créées ? »
    dt_debut = datetime.combine(date_debut, datetime.min.time()) if date_debut else None
    dt_fin   = datetime.combine(date_fin,   datetime.max.time()) if date_fin   else None

    def _dans_periode(d_str, defaut=True):
        """True si la date tombe dans la plage ; `defaut` si elle est illisible."""
        s = str(d_str or '').strip()
        if not s or s.lower() in ('nan', 'none', ''):
            return defaut
        try:
            d = datetime.strptime(s, '%d-%m-%Y')
        except ValueError:
            return defaut
        if dt_debut and d < dt_debut:
            return False
        if dt_fin and d > dt_fin:
            return False
        return True

    # La cohorte est indexée par la clé composite (code, village, sous-préfecture),
    # jamais par le code seul : deux parcelles de villages différents peuvent
    # porter le même code et n'ont rien à voir l'une avec l'autre.
    cohorte = {}
    if parcelle_map:
        if index_parcelles is None:
            index_parcelles = IndexParcelles(parcelle_map)
        for num, entries in parcelle_map.items():
            for e in entries:
                if _dans_periode(date_leve(e)):
                    cohorte.setdefault(index_parcelles.cle_de(e, num), []).append(e)

    nb_hors_cohorte = 0
    if rattachement == 'parcelle' and cohorte:
        def _dans_cohorte(row):
            e, _ = index_parcelles.resoudre(
                row.get('num_parcelle', ''), row.get('village', ''),
                row.get('sous_prefecture', ''))
            if e is None:
                return False
            num = extract_parcelle_num(row.get('num_parcelle', ''))
            return index_parcelles.cle_de(e, num) in cohorte

        avant = len(df_all)
        df_all = df_all[df_all.apply(_dans_cohorte, axis=1)].copy()
        nb_hors_cohorte = avant - len(df_all)

    # ── Filtre de date ──────────────────────────────────────────────────────────
    # Mode « demande » uniquement : on filtre alors sur les dates de la demande
    # elle-même (création, PVCL, RDC), en logique OU, en conservant les lignes
    # dépourvues de toute date.
    if rattachement == 'demande' and (date_debut or date_fin):

        def _in_range(d_str):
            """True si la date est dans la plage, None si vide/illisible."""
            s = str(d_str or '').strip()
            if not s or s.lower() in ('nan','none',''):
                return None
            try:
                d = datetime.strptime(s, '%d-%m-%Y')
                if dt_debut and d < dt_debut: return False
                if dt_fin   and d > dt_fin:   return False
                return True
            except Exception:
                return None

        def _keep(row):
            results = [
                _in_range(row.get('date_creation', '')),   # demande créée
                _in_range(row.get('date_pvcl',     '')),   # PVCL créé
                _in_range(row.get('date_pvcl_fin', '')),   # PVCL terminé
                _in_range(row.get('date_rdc',      '')),   # RDC créé
                _in_range(row.get('date_rdc_fin',  '')),   # RDC terminé
            ]
            valid = [r for r in results if r is not None]
            if not valid:
                return True   # aucune date disponible → on conserve
            return any(valid)  # au moins une date dans la plage → on conserve

        mask = df_all.apply(_keep, axis=1)
        df_all = df_all[mask].copy()

    # ── Stats des parcelles levées, sur la cohorte de la période ─────────────
    if cohorte:
        source_parcelles_stats = cohorte
    elif parcelle_map and index_parcelles is not None:
        source_parcelles_stats = {}
        for num, entries in parcelle_map.items():
            for e in entries:
                source_parcelles_stats.setdefault(
                    index_parcelles.cle_de(e, num), []).append(e)
    else:
        source_parcelles_stats = {}

    dept_parcelles  = {}
    dept_superficie = {}
    cles_par_dept   = {}
    for cle, entries in source_parcelles_stats.items():
        for e in entries:
            d = find_col(e, ['depart','departement','dept'])
            if not d:
                continue
            dept_parcelles[d] = dept_parcelles.get(d, 0) + 1
            cles_par_dept.setdefault(d, set()).add(cle)
            try:
                surf_str = find_col(e, ['surface_ha','surface','superficie'])
                dept_superficie[d] = dept_superficie.get(d, 0.0) + (float(surf_str) if surf_str else 0.0)
            except (ValueError, TypeError):
                pass

    # Parcelles effectivement couvertes par une demande, à la clé composite
    cles_avec_demande = set()
    if index_parcelles is not None and 'num_parcelle' in df_all.columns:
        for _, r in df_all.iterrows():
            e, _niv = index_parcelles.resoudre(
                r.get('num_parcelle', ''), r.get('village', ''), r.get('sous_prefecture', ''))
            if e is not None:
                cles_avec_demande.add(
                    index_parcelles.cle_de(e, extract_parcelle_num(r.get('num_parcelle', ''))))

    # ── Stats par département ──────────────────────────────────────────────────
    has_dept = 'departement' in df_all.columns
    has_rdc  = 'statut_rdc'  in df_all.columns
    has_pvcl = 'statut_pvcl' in df_all.columns
    has_surf = 'surface'     in df_all.columns

    stats = {}
    for dept in DEPARTEMENTS:
        if has_dept:
            df_d = df_all[df_all['departement'] == dept].copy()
        else:
            df_d = pd.DataFrame()

        n = len(df_d)
        sup_enq = 0.0
        if has_rdc and has_surf and n > 0:
            rdc_mask = df_d['statut_rdc'].isin(['RDC encours', 'RDC terminé'])
            sup_enq  = float(
                pd.to_numeric(df_d.loc[rdc_mask, 'surface'], errors='coerce')
                .fillna(0).sum()
            )

        nb_couvertes = len(cles_par_dept.get(dept, set()) & cles_avec_demande)

        stats[dept] = {
            'nb_parcelles':        dept_parcelles.get(dept, 0),
            'nb_parcelles_avec_demande': nb_couvertes,
            'superficie_levee':    round(dept_superficie.get(dept, 0.0), 2),
            'nb_demandes':         n,
            'nb_rdc_a_creer':      int((df_d['statut_rdc'] == 'RDC à créer').sum())   if has_rdc  and n > 0 else 0,
            'nb_rdc_encours':      int((df_d['statut_rdc'] == 'RDC encours').sum())   if has_rdc  and n > 0 else 0,
            'nb_rdc_termine':      int((df_d['statut_rdc'] == 'RDC terminé').sum())   if has_rdc  and n > 0 else 0,
            'superficie_enquetee': round(sup_enq, 2),
            'nb_pvcl_a_creer':     int((df_d['statut_pvcl'] == 'Pvcl à créer').sum()) if has_pvcl and n > 0 else 0,
            'nb_pvcl_encours':     int((df_d['statut_pvcl'] == 'Pvcl en cours').sum()) if has_pvcl and n > 0 else 0,
            'nb_pvcl_termine':     int((df_d['statut_pvcl'] == 'Pvcl terminé').sum())  if has_pvcl and n > 0 else 0,
            'etapes':              df_d['etape'].value_counts().to_dict() if 'etape' in df_d.columns and n > 0 else {},
        }

    # ── NAWA = agrégation des 3 départements + total réel ─────────────────────
    # On utilise df_all entier pour NAWA (inclut les demandes sans département)
    n_all = len(df_all)
    sup_enq_all = 0.0
    if has_rdc and has_surf and n_all > 0:
        rdc_mask_all = df_all['statut_rdc'].isin(['RDC encours', 'RDC terminé'])
        sup_enq_all  = float(
            pd.to_numeric(df_all.loc[rdc_mask_all, 'surface'], errors='coerce')
            .fillna(0).sum()
        )

    stats['NAWA'] = {
        'nb_parcelles':        sum(dept_parcelles.get(d, 0) for d in DEPARTEMENTS),
        'nb_parcelles_avec_demande': len(
            set().union(*(cles_par_dept.get(d, set()) for d in DEPARTEMENTS))
            & cles_avec_demande) if cles_par_dept else 0,
        'superficie_levee':    round(sum(dept_superficie.get(d, 0.0) for d in DEPARTEMENTS), 2),
        'nb_demandes':         n_all,
        'nb_rdc_a_creer':      int((df_all['statut_rdc'] == 'RDC à créer').sum())   if has_rdc  and n_all > 0 else 0,
        'nb_rdc_encours':      int((df_all['statut_rdc'] == 'RDC encours').sum())   if has_rdc  and n_all > 0 else 0,
        'nb_rdc_termine':      int((df_all['statut_rdc'] == 'RDC terminé').sum())   if has_rdc  and n_all > 0 else 0,
        'superficie_enquetee': round(sup_enq_all, 2),
        'nb_pvcl_a_creer':     int((df_all['statut_pvcl'] == 'Pvcl à créer').sum()) if has_pvcl and n_all > 0 else 0,
        'nb_pvcl_encours':     int((df_all['statut_pvcl'] == 'Pvcl en cours').sum()) if has_pvcl and n_all > 0 else 0,
        'nb_pvcl_termine':     int((df_all['statut_pvcl'] == 'Pvcl terminé').sum())  if has_pvcl and n_all > 0 else 0,
        'etapes':              df_all['etape'].value_counts().to_dict() if 'etape' in df_all.columns and n_all > 0 else {},
    }

    return {
        'demandes': df_all,
        'stats': stats,
        'nb_csv': len(csv_files),
        'rattachement': rattachement,
        'nb_parcelles_cohorte': sum(len(v) for v in cohorte.values()),
        'nb_hors_cohorte': nb_hors_cohorte,
        'rapprochement': index_parcelles.qualite if index_parcelles else None,
        'qualite_v8': {
            'version': '8.3',
            'dossiers': nb_dossiers_v8,
            'avertissements': avertissements_qualite,
        },
    }
