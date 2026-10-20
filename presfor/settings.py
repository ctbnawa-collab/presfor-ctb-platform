"""
Configuration Django — PLATEFORME PRESFOR CTB
Projet PRESFOR / CTB — Région NAWA, Côte d'Ivoire

Deux bases de données :
  - default : SQLite locale (sessions d'analyse, agents, oppositions, référentiels)
  - postgis : base terrain PostgreSQL/PostGIS (parcelles, bornes) — LECTURE SEULE
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ── Chargement du fichier .env (sans dépendance externe) ──────────────────────
def _load_env(path):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env(BASE_DIR / '.env')


def env(key, default=''):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return env(key, str(default)).lower() in ('1', 'true', 'yes', 'oui', 'on')


# ── Identité de l'application ────────────────────────────────────────────────
APP_NOM        = env('APP_NOM', 'PRESFOR CTB')
APP_SOUS_TITRE = env('APP_SOUS_TITRE', 'Suivi du foncier rural — NAWA')
APP_PROJET     = env('APP_PROJET', 'Projet PRESFOR / CTB')

SECRET_KEY = env('DJANGO_SECRET_KEY', 'django-presfor-ctb-dev-key-a-changer-en-production')
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = [h.strip() for h in env('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'analyse',
    'terrain',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'presfor.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
            'terrain.context_processors.identite',
        ],
    },
}]

WSGI_APPLICATION = 'presfor.wsgi.application'

# ── Bases de données ─────────────────────────────────────────────────────────
# Une seule base gérée par Django : la base locale.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    },
}

# ── Base terrain PostgreSQL / PostGIS ────────────────────────────────────────
# Elle N'EST PAS déclarée dans DATABASES : Django 4.2 exige PostgreSQL 12 ou
# plus, alors que le serveur terrain tourne en 10.x. La plateforme s'y connecte
# donc directement avec psycopg2, en lecture seule (voir terrain/db.py).
POSTGIS = {
    'HOST':     env('PG_HOST', 'localhost'),
    'PORT':     env('PG_PORT', '5432'),
    'NAME':     env('PG_NAME', 'nawa'),
    'USER':     env('PG_USER', 'postgres'),
    'PASSWORD': env('PG_PASSWORD', ''),
    'TIMEOUT':  int(env('PG_TIMEOUT', '10')),
    'STATEMENT_TIMEOUT_MS': env('PG_STATEMENT_TIMEOUT_MS', '90000'),
}

# Colonne de date utilisée pour filtrer la couche point (startleve / endleve)
PG_COLONNE_DATE_BORNE = env('PG_COLONNE_DATE_BORNE', 'startleve')

# Couches PostGIS proposées sur la carte : table:colonne_geometrie:libellé:couleur
# Laisser vide pour que la plateforme détecte automatiquement les couches
# disponibles dans le catalogue PostGIS.
PG_COUCHES_CARTE = [
    c.strip() for c in env(
        'PG_COUCHES_CARTE',
        'parcelle:shape_prcelle:Parcelles levées:#E0A800,'
        'code_village:geom:Territoires villageois:#7A5AA8,'
        'douar:geom:Douars:#0F766E,'
        'point:shape_pt:Bornes:#C0392B'
    ).split(',') if c.strip()
]

# Objectif de rendement journalier par défaut (parcelles levées / agent / jour)
OBJECTIF_JOURNALIER_DEFAUT = int(env('OBJECTIF_JOURNALIER_DEFAUT', '8'))

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

DATABASES['default']['NAME'] = env('APP_DATABASE_PATH', str(BASE_DIR / 'application.sqlite3'))
GEOSERVER_WMS_URL = env('GEOSERVER_WMS_URL', '')
GEOSERVER_LAYERS = env('GEOSERVER_LAYERS', 'presfor:parcelles')

PRESFOR_AUTH_REQUIRED = env_bool('PRESFOR_AUTH_REQUIRED', True)
LOGIN_URL = '/connexion/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/connexion/'
MIDDLEWARE.insert(6, 'terrain.access.AccesPlateforme')
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
CSRF_TRUSTED_ORIGINS = [s for s in env('CSRF_TRUSTED_ORIGINS','').split(',') if s]
SESSION_COOKIE_SECURE = env_bool('DJANGO_HTTPS', False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# Render application database is separate from the read-only terrain source.
if env('DATABASE_URL'):
    import dj_database_url
    DATABASES['default'] = dj_database_url.parse(env('DATABASE_URL'), conn_max_age=60)
if env('RENDER_EXTERNAL_HOSTNAME'):
    _render_host = env('RENDER_EXTERNAL_HOSTNAME')
    if _render_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_render_host)
    _render_origin = 'https://' + _render_host
    if _render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_render_origin)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

PRESFOR_POSTGRES_SURFACES_ONLY = env_bool('PRESFOR_POSTGRES_SURFACES_ONLY', True)
