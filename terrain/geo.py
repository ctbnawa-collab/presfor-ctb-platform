"""
Lecture de shapefiles et reprojection vers WGS84 (le seul système que Leaflet
comprend).

Les couches du projet sont en **TMCI-5.5** : une Transverse Mercator centrée sur
le méridien -5,5°, ellipsoïde GRS80 (ITRF2014), facteur d'échelle 0,9996, faux
est 500 000 m. Ce système n'a pas de code EPSG standard, d'où la transformation
implémentée ici plutôt qu'un appel à pyproj.

Les paramètres sont lus dans le fichier .prj : la même fonction traite donc
n'importe quelle Transverse Mercator, ainsi que les couches déjà en degrés.
"""
import io
import re
import zipfile
from math import atan, cos, degrees, radians, sin, sqrt, tan


# ── Lecture du .prj ──────────────────────────────────────────────────────────
def _param(prj, nom, defaut=0.0):
    m = re.search(rf'PARAMETER\s*\[\s*"{nom}"\s*,\s*(-?[\d.]+)', prj, re.I)
    return float(m.group(1)) if m else defaut


def lire_projection(prj):
    """Renvoie un descripteur du système de coordonnées à partir du .prj."""
    if not prj or 'PROJCS' not in prj.upper():
        # Déjà en degrés : rien à reprojeter.
        return {'type': 'geographique', 'nom': 'WGS84 / géographique'}

    sph = re.search(r'SPHEROID\s*\[\s*"[^"]*"\s*,\s*([\d.]+)\s*,\s*([\d.]+)', prj, re.I)
    a = float(sph.group(1)) if sph else 6378137.0
    inv_f = float(sph.group(2)) if sph else 298.257223563
    nom = re.search(r'PROJCS\s*\[\s*"([^"]*)"', prj, re.I)

    if not re.search(r'Transverse_Mercator', prj, re.I):
        return {'type': 'inconnu', 'nom': nom.group(1) if nom else 'projection inconnue'}

    return {
        'type': 'transverse_mercator',
        'nom': nom.group(1) if nom else 'Transverse Mercator',
        'a': a,
        'f': 1.0 / inv_f if inv_f else 0.0,
        'lon0': _param(prj, 'Central_Meridian', -5.5),
        'lat0': _param(prj, 'Latitude_Of_Origin', 0.0),
        'k0':   _param(prj, 'Scale_Factor', 0.9996),
        'fe':   _param(prj, 'False_Easting', 500000.0),
        'fn':   _param(prj, 'False_Northing', 0.0),
    }


# ── Transverse Mercator inverse (Snyder / USGS 1395) ─────────────────────────
def transformateur(crs):
    """Renvoie une fonction (x, y) → (longitude, latitude) en degrés."""
    if crs['type'] == 'geographique':
        return lambda x, y: (x, y)
    if crs['type'] != 'transverse_mercator':
        raise ValueError(
            f"Projection non prise en charge : {crs['nom']}. "
            "Reprojetez la couche en WGS84 ou en Transverse Mercator dans QGIS.")

    a, f = crs['a'], crs['f']
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    e1 = (1 - sqrt(1 - e2)) / (1 + sqrt(1 - e2))
    k0, fe, fn = crs['k0'], crs['fe'], crs['fn']
    lon0 = radians(crs['lon0'])

    # Arc de méridien jusqu'à la latitude d'origine
    lat0 = radians(crs['lat0'])
    c0 = 1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256

    def m_arc(phi):
        return a * (c0 * phi
                    - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * sin(2 * phi)
                    + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * sin(4 * phi)
                    - (35 * e2**3 / 3072) * sin(6 * phi))

    m0 = m_arc(lat0)

    def inverse(x, y):
        m = m0 + (y - fn) / k0
        mu = m / (a * c0)
        phi1 = (mu
                + (3 * e1 / 2 - 27 * e1**3 / 32) * sin(2 * mu)
                + (21 * e1**2 / 16 - 55 * e1**4 / 32) * sin(4 * mu)
                + (151 * e1**3 / 96) * sin(6 * mu)
                + (1097 * e1**4 / 512) * sin(8 * mu))
        s1, c1 = sin(phi1), cos(phi1)
        t1 = tan(phi1) ** 2
        cc1 = ep2 * c1 ** 2
        n1 = a / sqrt(1 - e2 * s1 ** 2)
        r1 = a * (1 - e2) / (1 - e2 * s1 ** 2) ** 1.5
        d = (x - fe) / (n1 * k0)
        lat = phi1 - (n1 * tan(phi1) / r1) * (
            d**2 / 2
            - (5 + 3 * t1 + 10 * cc1 - 4 * cc1**2 - 9 * ep2) * d**4 / 24
            + (61 + 90 * t1 + 298 * cc1 + 45 * t1**2 - 252 * ep2 - 3 * cc1**2) * d**6 / 720)
        lon = lon0 + (
            d
            - (1 + 2 * t1 + cc1) * d**3 / 6
            + (5 - 2 * cc1 + 28 * t1 - 3 * cc1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / c1
        return degrees(lon), degrees(lat)

    return inverse


def transformateur_direct(crs):
    """Renvoie une fonction (longitude, latitude) → (x, y) dans la projection.

    Sert à convertir l'emprise demandée par Leaflet (en degrés) vers les
    coordonnées de la base terrain, pour filtrer côté serveur au lieu de
    rapatrier toute la couche.
    """
    if crs['type'] == 'geographique':
        return lambda lon, lat: (lon, lat)
    if crs['type'] != 'transverse_mercator':
        raise ValueError(f"Projection non prise en charge : {crs['nom']}")

    a, f = crs['a'], crs['f']
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0, fe, fn = crs['k0'], crs['fe'], crs['fn']
    lon0, lat0 = radians(crs['lon0']), radians(crs['lat0'])
    c0 = 1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256

    def m_arc(phi):
        return a * (c0 * phi
                    - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * sin(2 * phi)
                    + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * sin(4 * phi)
                    - (35 * e2**3 / 3072) * sin(6 * phi))

    m0 = m_arc(lat0)

    def direct(lon, lat):
        phi, lam = radians(lat), radians(lon)
        s_phi, c_phi = sin(phi), cos(phi)
        t = tan(phi) ** 2
        c = ep2 * c_phi ** 2
        n = a / sqrt(1 - e2 * s_phi ** 2)
        A = (lam - lon0) * c_phi
        x = fe + k0 * n * (A + (1 - t + c) * A**3 / 6
                           + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * A**5 / 120)
        y = fn + k0 * (m_arc(phi) - m0 + n * tan(phi) * (
            A**2 / 2 + (5 - t + 9 * c + 4 * c**2) * A**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * A**6 / 720))
        return x, y

    return direct


# Projection de la base terrain : les couches PostGIS sont en TMCI-5.5, sans
# SRID déclaré (les requêtes fournies utilisent ST_SetSRID(..., 0)).
CRS_TERRAIN = {
    'type': 'transverse_mercator', 'nom': 'TMCI-5.5',
    'a': 6378137.0, 'f': 1 / 298.257222101,
    'lon0': -5.5, 'lat0': 0.0, 'k0': 0.9996, 'fe': 500000.0, 'fn': 0.0,
}


def reprojeter_geojson(geometrie, projeter, precision=6):
    """Reprojette une géométrie GeoJSON en place, quel que soit son type."""
    def parcourir(coords):
        if coords and isinstance(coords[0], (int, float)):
            return [round(v, precision) for v in projeter(coords[0], coords[1])]
        return [parcourir(c) for c in coords]

    if not geometrie or 'coordinates' not in geometrie:
        return geometrie
    return {'type': geometrie['type'], 'coordinates': parcourir(geometrie['coordinates'])}


# ── Géométries ───────────────────────────────────────────────────────────────
def _aire_signee(anneau):
    total = 0.0
    for i in range(len(anneau) - 1):
        x1, y1 = anneau[i]
        x2, y2 = anneau[i + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygone_geojson(shape, projeter, precision=6):
    """Convertit une géométrie pyshp en GeoJSON WGS84.

    Convention shapefile : anneau horaire = contour extérieur, anneau
    anti-horaire = trou. On regroupe donc chaque trou avec le contour qui le
    précède, ce qui donne un MultiPolygon correct.
    """
    points = shape.points
    if not points:
        return None
    parts = list(shape.parts) + [len(points)]

    anneaux = []
    for i in range(len(parts) - 1):
        brut = points[parts[i]:parts[i + 1]]
        if len(brut) < 4:
            continue
        anneau = [[round(v, precision) for v in projeter(x, y)] for x, y in brut]
        if anneau[0] != anneau[-1]:
            anneau.append(anneau[0])
        anneaux.append(anneau)

    if not anneaux:
        return None

    polygones = []
    for anneau in anneaux:
        # En WGS84 l'axe Y est inversé par rapport au plan projeté :
        # une aire signée négative correspond au contour extérieur.
        exterieur = _aire_signee(anneau) < 0
        if exterieur or not polygones:
            polygones.append([anneau])
        else:
            polygones[-1].append(anneau)

    if len(polygones) == 1:
        return {'type': 'Polygon', 'coordinates': polygones[0]}
    return {'type': 'MultiPolygon', 'coordinates': polygones}


def enveloppe(geojson):
    """(lon_min, lat_min, lon_max, lat_max, lon_centre, lat_centre)."""
    lons, lats = [], []

    def parcourir(coords, niveau):
        if niveau == 0:
            lons.append(coords[0])
            lats.append(coords[1])
        else:
            for c in coords:
                parcourir(c, niveau - 1)

    niveau = 2 if geojson['type'] == 'Polygon' else 3
    parcourir(geojson['coordinates'], niveau)
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats),
            (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)


# ── Lecture d'une archive shapefile ──────────────────────────────────────────
class ShapefileIllisible(Exception):
    pass


class Couche:
    """Une couche shapefile lue en mémoire, reprojetée à la volée en WGS84."""

    def __init__(self, lecteur, champs, crs, projeter):
        self._lecteur = lecteur
        self.champs = champs
        self.crs = crs
        self._projeter = projeter
        self.total = len(lecteur)

    def entites(self):
        """Produit (attributs: dict, geojson: dict | None) pour chaque objet."""
        for enr in self._lecteur.iterShapeRecords():
            attributs = {c: enr.record[i] for i, c in enumerate(self.champs)}
            yield attributs, polygone_geojson(enr.shape, self._projeter)


def lire_archive(fichier):
    """Lit un .zip contenant un shapefile et renvoie une Couche."""
    try:
        import shapefile as pyshp
    except ImportError as exc:
        raise ShapefileIllisible(
            "Le paquet pyshp n'est pas installé (pip install pyshp).") from exc

    contenu = fichier.read() if hasattr(fichier, 'read') else fichier
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile as exc:
        raise ShapefileIllisible(
            "Le fichier n'est pas une archive .zip valide. Compressez ensemble "
            "les fichiers .shp, .shx, .dbf et .prj.") from exc

    noms = archive.namelist()

    def trouver(ext):
        for n in noms:
            if n.lower().endswith(ext) and not n.startswith('__MACOSX'):
                return n
        return None

    n_shp, n_dbf, n_shx = trouver('.shp'), trouver('.dbf'), trouver('.shx')
    if not (n_shp and n_dbf and n_shx):
        manquants = [e for e, n in (('.shp', n_shp), ('.dbf', n_dbf), ('.shx', n_shx)) if not n]
        raise ShapefileIllisible(
            f"Archive incomplète : il manque {', '.join(manquants)}. "
            "Un shapefile a besoin d'au moins .shp, .shx et .dbf.")

    n_prj = trouver('.prj')
    prj = archive.read(n_prj).decode('utf-8', 'replace') if n_prj else ''
    crs = lire_projection(prj)
    if not n_prj:
        crs = {'type': 'transverse_mercator', 'nom': 'TMCI-5.5 (supposé, .prj absent)',
               'a': 6378137.0, 'f': 1 / 298.257222101, 'lon0': -5.5, 'lat0': 0.0,
               'k0': 0.9996, 'fe': 500000.0, 'fn': 0.0}
    projeter = transformateur(crs)

    n_cpg = trouver('.cpg')
    encodage = 'cp1252'
    if n_cpg:
        brut = archive.read(n_cpg).decode('ascii', 'replace').strip().lower()
        if 'utf' in brut:
            encodage = 'utf-8'
        elif brut.isdigit():
            encodage = f'cp{brut}'

    lecteur = pyshp.Reader(
        shp=io.BytesIO(archive.read(n_shp)),
        dbf=io.BytesIO(archive.read(n_dbf)),
        shx=io.BytesIO(archive.read(n_shx)),
        encoding=encodage, encodingErrors='replace')

    champs = [f[0] for f in lecteur.fields[1:]]
    return Couche(lecteur, champs, crs, projeter)
