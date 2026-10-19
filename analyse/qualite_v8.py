"""
Pont entre la plateforme et le moteur de contrôle qualité v8.

`analyse/controle_qualite_v8.py` est le script de bureau repris tel quel. Ce
module l'exécute sur un export Digifor et renvoie, pour chaque dossier, la
totalité des observations qu'il produit :

    STATUT_PVCL, REMARQUE_PVCL, STATUT_RDC, REMARQUE_SERVITUDE,
    MODE_ACQUISITION, DOUBLON_FICHE_DEMO, REMARQUE_RDC, REMARQUE_EQUIPE,
    PHOTO_ID

Le moteur exige un export complet : sans les tables `member_type`, `minutes`,
`village` ou `subprefecture`, il renonce. Dans ce cas la plateforme retombe sur
son propre calcul, plus tolérant mais moins détaillé, et le signale.
"""
import os
import re
import tempfile

# Vocabulaire : le script écrit « RDC en cours », la plateforme « RDC encours ».
# On aligne pour que filtres, statistiques et graphiques restent cohérents.
STATUTS_RDC = {
    'RDC en cours': 'RDC encours',
    'RDC encours':  'RDC encours',
    'RDC terminé':  'RDC terminé',
    'RDC à créer':  'RDC à créer',
}
STATUTS_PVCL = {
    'Pvcl en cours': 'Pvcl en cours',
    'Pvcl terminé':  'Pvcl terminé',
    'Pvcl à créer':  'Pvcl à créer',
}

# Volets d'observation, dans l'ordre où ils apparaissent sur la fiche du dossier
VOLETS = [
    ('REMARQUE_DEMANDE', 'Demande et pièce identité'),
    ('REMARQUE_PVCL',      'Constat de limite (PVCL)'),
    ('REMARQUE_RDC',       'Recensement des droits coutumiers (RDC)'),
    ('REMARQUE_SERVITUDE', 'Servitudes et limites naturelles'),
    ('DOUBLON_FICHE_DEMO', 'Doublon de fiche démographique'),
    ('REMARQUE_EQUIPE',    'Équipe et pièces du dossier'),
]


def _vide(v):
    s = str(v or '').strip()
    return not s or s.upper() in ('RAS', 'NAN', 'NONE', 'NULL')


def _norm_num(v):
    """Code de demande canonique : 3-3-6, sans préfixe alphabétique."""
    m = re.search(r'(\d{3}-\d{3}-\d{6})', str(v or ''))
    return m.group(1) if m else str(v or '').strip().lstrip('APTapt')


def analyser(contenu, nom_fichier='export.csv'):
    """Exécute le moteur v8 sur un export. Renvoie (observations, message).

    observations : dict {code_demande : dict de colonnes}
    message      : '' si tout s'est bien passé, sinon la raison de l'abandon.
    """
    from .controle_qualite_v8 import process_csv

    journal = []
    chemin = None
    try:
        with tempfile.NamedTemporaryFile('wb', suffix='.csv', delete=False) as f:
            f.write(contenu if isinstance(contenu, bytes) else contenu.encode('utf-8'))
            chemin = f.name
        df = process_csv(chemin, log_fn=journal.append)
    except Exception as exc:
        return {}, f'{nom_fichier} : moteur v8 interrompu ({exc}).'
    finally:
        if chemin and os.path.exists(chemin):
            try:
                os.unlink(chemin)
            except OSError:
                pass

    if df is None or df.empty:
        manquantes = [l.split(':')[-1].strip() for l in journal if 'obligatoire' in l]
        detail = (f" — table(s) absente(s) : {', '.join(manquantes)}"
                  if manquantes else '')
        return {}, f'{nom_fichier} : export incomplet pour le moteur v8{detail}.'

    observations = {}
    for _, r in df.iterrows():
        cle = _norm_num(r.get('N°DEMANDE', ''))
        if cle:
            observations[cle] = {c: ('' if _vide(r.get(c)) else str(r.get(c)).strip())
                                 for c in df.columns}
    return observations, ''


def motifs(texte):
    """Découpe une remarque v8 en motifs, en rattachant les puces à leur entête."""
    blocs = []
    for ligne in str(texte or '').split('\n'):
        t = ligne.strip()
        if not t or t.upper() == 'RAS':
            continue
        if t.startswith('•'):
            if blocs:
                blocs[-1]['details'].append(t.lstrip('• ').strip())
            continue
        blocs.append({'intitule': t.rstrip(':').strip(), 'details': []})
    return blocs


def resume(observations_dossier):
    """Toutes les observations d'un dossier, groupées par volet."""
    resultat = []
    for cle, libelle in VOLETS:
        texte = observations_dossier.get(cle, '')
        blocs = motifs(texte)
        if blocs:
            resultat.append({'volet': libelle, 'cle': cle, 'motifs': blocs})
    return resultat
