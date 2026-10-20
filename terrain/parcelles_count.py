"""Comptage des parcelles physiques, indépendamment du nombre de demandes."""
def compter_parcelles(rows):
    identifiants=set()
    codes_sans_identifiant=set()
    codes_avec_identifiant=set()
    for row in rows:
        identifiant=str(row.get('id_parcelle') or '').strip()
        code=str(row.get('code_parcelle') or '').strip().upper()
        village=str(row.get('village') or '').strip().upper()
        departement=str(row.get('d') or '').strip().upper()
        cle=(departement,village,code) if code and village and departement else None
        if identifiant:
            identifiants.add(identifiant)
            if cle:codes_avec_identifiant.add(cle)
        elif cle:
            codes_sans_identifiant.add(cle)
    return len(identifiants)+len(codes_sans_identifiant-codes_avec_identifiant)
