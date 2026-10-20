"""
Accès à la base terrain PostgreSQL / PostGIS.

La connexion est établie **directement avec psycopg2**, sans passer par le
backend Django : Django 4.2 refuse tout serveur antérieur à PostgreSQL 12,
alors que la base terrain tourne en 10.x. Cette limite vient de l'ORM, pas du
serveur — et comme la plateforme ne fait ici que du SQL brut en lecture seule,
l'ORM n'apporte rien.

Les requêtes vivent dans terrain/sql/*.sql et sont relues à chaque appel :
on peut donc les corriger sans redémarrer le serveur.
"""
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from django.conf import settings

SQL_DIR = Path(__file__).resolve().parent / 'sql'

# Garde-fou anti-injection pour les identifiants construits dynamiquement.
_IDENT_OK = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')


class PostgisIndisponible(Exception):
    """La base terrain n'est pas joignable ou la requête a échoué."""


# ── Connexion ────────────────────────────────────────────────────────────────
@contextmanager
def connexion():
    """Ouvre une connexion psycopg2 en lecture seule, puis la referme."""
    try:
        import psycopg2
    except ImportError as exc:
        raise PostgisIndisponible(
            "Le paquet psycopg2-binary n'est pas installé "
            "(pip install psycopg2-binary).") from exc

    cfg = settings.POSTGIS
    conn = None
    try:
        conn = psycopg2.connect(
            host=cfg['HOST'], port=cfg['PORT'], dbname=cfg['NAME'],
            user=cfg['USER'], password=cfg['PASSWORD'],
            connect_timeout=cfg['TIMEOUT'],
            options=f"-c statement_timeout={cfg['STATEMENT_TIMEOUT_MS']}",
        )
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    except PostgisIndisponible:
        raise
    except Exception as exc:
        raise PostgisIndisponible(str(exc).strip()) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def executer(sql, params=None, limite=None):
    """Exécute une requête sur la base terrain et renvoie une liste de dicts."""
    import psycopg2.extras

    if limite:
        sql = f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) _q LIMIT {int(limite)}"
    with connexion() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or {})
                return [dict(l) for l in cur.fetchall()]
        except Exception as exc:
            raise PostgisIndisponible(str(exc).strip()) from exc


# ── Utilitaires ──────────────────────────────────────────────────────────────
def charger_sql(nom):
    """Lit un fichier SQL de terrain/sql/ et injecte les jetons de configuration."""
    chemin = SQL_DIR / f'{nom}.sql'
    if not chemin.exists():
        raise PostgisIndisponible(f"Requête introuvable : {chemin.name}")
    texte = chemin.read_text(encoding='utf-8')
    col_date = settings.PG_COLONNE_DATE_BORNE
    if not set(col_date) <= _IDENT_OK:
        raise PostgisIndisponible(f"PG_COLONNE_DATE_BORNE invalide : {col_date!r}")
    return texte.replace('{COL_DATE}', col_date)


def _normaliser_date(v, defaut):
    if v is None or v == '':
        return defaut
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def executer_fichier(nom, date_debut=None, date_fin=None, limite=None):
    params = {
        'date_debut': _normaliser_date(date_debut, '1900-01-01'),
        'date_fin':   _normaliser_date(date_fin, '2100-12-31'),
    }
    return executer(charger_sql(nom), params, limite=limite)


# ── Diagnostic ───────────────────────────────────────────────────────────────
def tester_connexion():
    """Renvoie (ok: bool, message: str, details: dict)."""
    cfg = settings.POSTGIS
    cible = f"{cfg['USER']}@{cfg['HOST']}:{cfg['PORT']}/{cfg['NAME']}"
    try:
        lignes = executer(
            "SELECT version() AS v, current_database() AS db, now() AS maintenant")
        details = lignes[0] if lignes else {}
        try:
            details['postgis'] = executer("SELECT PostGIS_Lib_Version() AS v")[0]['v']
        except PostgisIndisponible:
            details['postgis'] = 'extension postgis non détectée'
        details['cible'] = cible
        return True, f"Connecté à {cible}", details
    except PostgisIndisponible as exc:
        return False, str(exc), {'cible': cible}


def lister_tables(schema='public'):
    return executer(
        """
        SELECT table_name,
               (SELECT count(*) FROM information_schema.columns c
                 WHERE c.table_schema = t.table_schema
                   AND c.table_name = t.table_name) AS nb_colonnes
        FROM information_schema.tables t
        WHERE table_schema = %(schema)s AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        {'schema': schema},
    )


def lister_colonnes(table, schema='public'):
    return executer(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %(schema)s AND table_name = %(table)s
        ORDER BY ordinal_position
        """,
        {'schema': schema, 'table': table},
    )


def apercu_table(table, limite=20, schema='public'):
    if not set(table) <= _IDENT_OK or not set(schema) <= _IDENT_OK:
        raise PostgisIndisponible("Nom de table invalide.")
    return executer(f'SELECT * FROM "{schema}"."{table}" LIMIT {int(limite)}')


def lister_couches_geo(schema='public'):
    """Couches géométriques de la base terrain, via le catalogue PostGIS."""
    try:
        return executer(
            """
            SELECT f_table_name  AS table_name,
                   f_geometry_column AS colonne_geom,
                   type AS type_geom,
                   srid,
                   coord_dimension
            FROM geometry_columns
            WHERE f_table_schema = %(schema)s
            ORDER BY f_table_name
            """,
            {'schema': schema},
        )
    except PostgisIndisponible:
        # Base sans vue geometry_columns : on retombe sur information_schema.
        return executer(
            """
            SELECT table_name, column_name AS colonne_geom,
                   'GEOMETRY' AS type_geom, 0 AS srid, 2 AS coord_dimension
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND udt_name = 'geometry'
            ORDER BY table_name
            """,
            {'schema': schema},
        )


def lire_couche(table, colonne_geom, colonnes=None, bbox=None, limite=2000,
                schema='public'):
    """Lit une couche géométrique et renvoie ses objets avec GeoJSON brut.

    `bbox` est exprimée dans les coordonnées de la base (TMCI), pas en degrés :
    la conversion est faite par l'appelant. Le filtre utilise l'opérateur `&&`,
    qui s'appuie sur l'index spatial.
    """
    for ident in (table, colonne_geom, schema):
        if not set(str(ident)) <= _IDENT_OK:
            raise PostgisIndisponible(f"Identifiant invalide : {ident!r}")

    champs = ', '.join(f'"{c}"' for c in (colonnes or []) if set(c) <= _IDENT_OK)
    select = f'{champs}, ' if champs else ''
    where, params = '', {}
    if bbox:
        where = (f'WHERE "{colonne_geom}" && '
                 'ST_MakeEnvelope(%(x1)s, %(y1)s, %(x2)s, %(y2)s, 0)')
        params = {'x1': bbox[0], 'y1': bbox[1], 'x2': bbox[2], 'y2': bbox[3]}

    sql = (f'SELECT {select}ST_AsGeoJSON("{colonne_geom}") AS geojson '
           f'FROM "{schema}"."{table}" '
           f'{where} '
           f'LIMIT {int(limite)}')
    return executer(sql, params)


def compter_couche(table, colonne_geom, bbox=None, schema='public'):
    for ident in (table, colonne_geom, schema):
        if not set(str(ident)) <= _IDENT_OK:
            raise PostgisIndisponible(f"Identifiant invalide : {ident!r}")
    where, params = '', {}
    if bbox:
        where = (f'WHERE "{colonne_geom}" && '
                 'ST_MakeEnvelope(%(x1)s, %(y1)s, %(x2)s, %(y2)s, 0)')
        params = {'x1': bbox[0], 'y1': bbox[1], 'x2': bbox[2], 'y2': bbox[3]}
    return executer(f'SELECT count(*) AS n FROM "{schema}"."{table}" {where}',
                    params)[0]['n']


def bornes_dates_disponibles():
    """Première et dernière date de levé présentes dans la table parcelle."""
    return executer(
        """
        SELECT MIN(TO_DATE(date_creation, 'DD/MM/YYYY')) AS premiere,
               MAX(TO_DATE(date_creation, 'DD/MM/YYYY')) AS derniere,
               COUNT(*) AS total
        FROM parcelle
        WHERE date_creation ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}'
        """
    )[0]


def lire_parcelles_carte(bbox=None, limite=1500):
    """Identités et localisation spatiale, sans perdre les parcelles sans personne."""
    params={'limite':max(1,min(int(limite),4000))}
    where=''
    if bbox:
        where='WHERE p.shape_prcelle && ST_MakeEnvelope(%(x1)s,%(y1)s,%(x2)s,%(y2)s,0)'
        params.update(zip(('x1','y1','x2','y2'),bbox))
    sql="""
    WITH selection AS (
        SELECT p.* FROM parcelle p
        {where}
        ORDER BY p.id_parcelle LIMIT %(limite)s
    )
    SELECT p.id_parcelle,p.code_parcelle,p.code_unique,
           COALESCE(to_jsonb(p)->>'indice_unique','') AS indice_unique,
           COALESCE(to_jsonb(p)->>'num_demande',to_jsonb(p)->>'num_demand','') AS num_demande,
           identite.nom_pers AS nom,identite.prenom_pers AS prenoms,
           concat_ws(' ',identite.nom_pers,identite.prenom_pers) AS nom_prenom,
           identite.telephone,
           cv.sous_prefecture,cv.village,cv.departement,
           ST_AsGeoJSON(p.shape_prcelle) AS geojson
    FROM selection p
    LEFT JOIN LATERAL (
        SELECT personne.nom_pers,personne.prenom_pers,
               COALESCE(NULLIF(to_jsonb(personne)->>'telephone',''),
                        NULLIF(to_jsonb(personne)->>'tel_pers',''),
                        NULLIF(to_jsonb(personne)->>'tel',''),
                        to_jsonb(personne)->>'telephone_pers','') AS telephone
        FROM ordre JOIN personne ON personne.id_personne=ordre.id_personne
        WHERE ordre.id_parcelle=p.id_parcelle
        ORDER BY personne.id_personne LIMIT 1
    ) identite ON TRUE
    LEFT JOIN LATERAL (
        SELECT UPPER(code_village."sous-pref") AS sous_prefecture,
               UPPER(code_village.name_villa) AS village,
               UPPER(code_village.depart) AS departement
        FROM code_village
        WHERE ST_Intersects(ST_SetSRID(code_village.geom,0),
              ST_SetSRID(ST_PointOnSurface(p.shape_prcelle),0))
        ORDER BY code_village.name_villa,code_village."sous-pref" LIMIT 1
    ) cv ON TRUE
    ORDER BY p.id_parcelle
    """.format(where=where)
    return executer(sql,params)
