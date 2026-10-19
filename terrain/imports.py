"""
Lecture tolérante des fichiers d'import (CSV ou Excel) et rapprochement
des colonnes par nom normalisé.
"""
import io
import re
from datetime import date, datetime

import pandas as pd


def normaliser(nom):
    s = str(nom).strip().lower()
    for a, b in [('é', 'e'), ('è', 'e'), ('ê', 'e'), ('à', 'a'), ('ç', 'c'), ('ô', 'o'), ('î', 'i')]:
        s = s.replace(a, b)
    return re.sub(r'[^a-z0-9]', '', s)


def lire_tableau(fichier):
    """Renvoie un DataFrame à partir d'un .csv, .xlsx ou .xls."""
    nom = getattr(fichier, 'name', '') or ''
    contenu = fichier.read()
    if nom.lower().endswith(('.xlsx', '.xls')):
        return pd.read_excel(io.BytesIO(contenu), dtype=str)
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        for sep in (';', ',', '\t'):
            try:
                df = pd.read_csv(io.BytesIO(contenu), dtype=str, encoding=enc, sep=sep)
                if df.shape[1] > 1:
                    return df
            except Exception:
                continue
    raise ValueError("Format de fichier non reconnu : attendu .csv, .xlsx ou .xls.")


def colonne(ligne, candidats, index_normalise=None):
    """Récupère la première colonne existante parmi les candidats."""
    idx = index_normalise or {normaliser(k): k for k in ligne.keys()}
    for c in candidats:
        cle = idx.get(normaliser(c))
        if cle is None:
            continue
        v = ligne.get(cle)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ('nan', 'none', 'null'):
            return s
    return ''


def en_date(valeur):
    s = str(valeur or '').strip().split(' ')[0].split('T')[0]
    if not s or s.lower() in ('nan', 'none', 'null'):
        return None
    for f in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%Y/%m/%d', '%d/%m/%y'):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def en_float(valeur):
    s = str(valeur or '').strip().replace(' ', '').replace(',', '.')
    if not s or s.lower() in ('nan', 'none', 'null'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def serialiser(valeur):
    """Rend une valeur JSON-compatible (dates PostgreSQL notamment)."""
    if isinstance(valeur, (datetime, date)):
        return valeur.isoformat()
    if valeur is None or isinstance(valeur, (str, int, float, bool)):
        return valeur
    return str(valeur)
