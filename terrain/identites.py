import re
import unicodedata
from .models import AgentRendement

def normaliser(value):
    return re.sub(r'[^A-Z0-9]','',unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().upper())

def resolve_agent(departement, identite='', fichier=''):
    """Exact full-name/code lookup; never guess short surnames or cross departments."""
    dep=normaliser(departement)
    if dep not in ('BUYO','SOUBRE','MEAGUI'): return {}
    rows=list(AgentRendement.objects.filter(departement=dep).values('departement','code_agent','nom_agent','superviseur','controleur'))
    token=normaliser(identite)
    found=[r for r in rows if token and token in (normaliser(r['nom_agent']),normaliser(r['code_agent']))]
    if not found:
        # Only explicit AGENT + number in filenames; do not interpret other numbers.
        codes={'AGENT'+str(int(n)) for n in re.findall(r'(?i)(?<![A-Za-z])agent[ _-]*0*(\d+)(?!\d)',str(fichier))}
        found=[r for r in rows if r['code_agent'] in codes]
    return found[0] if len(found)==1 else {}
