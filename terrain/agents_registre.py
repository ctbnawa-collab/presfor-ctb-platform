"""Vue unifiée des identités de rendement et des fiches complémentaires."""
from collections import defaultdict


def cle_agent(departement, code):
    return (str(departement or '').strip().upper(),str(code or '').strip().casefold())


def fusionner_agents(referentiel, fiches):
    index=defaultdict(list)
    for fiche in fiches:index[cle_agent(fiche.get('departement'),fiche.get('id_machine'))].append(fiche)
    rows=[];utilises=set()
    for ref in referentiel:
        matches=index[cle_agent(ref.get('departement'),ref.get('code_agent'))]
        fiche=matches[0] if len(matches)==1 else {}
        if fiche:utilises.add(fiche['id'])
        row=dict(fiche)
        row.update(pk=fiche.get('id'),id_machine=ref['code_agent'],nom_complet=ref['nom_agent'],
                   nom=fiche.get('nom',ref['nom_agent']),prenoms=fiche.get('prenoms',''),
                   departement=ref['departement'],superviseur=fiche.get('superviseur',ref.get('superviseur','')),
                   controleur=fiche.get('controleur',ref.get('controleur','')),referentiel=True,actif=fiche.get('actif'))
        rows.append(row)
    for fiche in fiches:
        if fiche['id'] not in utilises:
            row=dict(fiche)
            row.update(pk=fiche['id'],nom_complet=(fiche.get('nom','')+' '+fiche.get('prenoms','')).strip(),superviseur=fiche.get('superviseur',''),controleur=fiche.get('controleur',''),referentiel=False)
            rows.append(row)
    return sorted(rows,key=lambda r:(r['departement'],r['nom_complet']))
