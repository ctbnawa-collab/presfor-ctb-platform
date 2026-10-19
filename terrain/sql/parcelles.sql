-- ============================================================================
-- EXTRACTION DES PARCELLES LEVÉES  (source : Requete_extraction_Plle_v4.sql)
-- Paramètres attendus : %(date_debut)s , %(date_fin)s  (format YYYY-MM-DD)
--
-- Le fichier peut être modifié librement : la plateforme le relit à chaque
-- exécution. Les colonnes ci-dessous sont celles attendues par le moteur
-- de contrôle qualité — n'en supprimez aucune, vous pouvez en ajouter.
-- ============================================================================
WITH base AS (
    SELECT DISTINCT ON (parcelle.id_parcelle)
        parcelle.id_parcelle,
        parcelle.code_parcelle,
        parcelle.code_unique,
        parcelle.id_machine,
        COALESCE(to_jsonb(parcelle)->>'nom_agent', '') AS nom_agent,
        personne.nom_pers,
        personne.prenom_pers,
        personne.cin,
        parcelle.date_creation,
        parcelle.shape_prcelle AS geom
    FROM parcelle
    JOIN ordre    ON parcelle.id_parcelle = ordre.id_parcelle
    JOIN personne ON personne.id_personne = ordre.id_personne
    WHERE parcelle.date_creation ~ '^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}'
      AND TO_DATE(parcelle.date_creation, 'DD/MM/YYYY')
          BETWEEN %(date_debut)s::date AND %(date_fin)s::date
    ORDER BY parcelle.id_parcelle
)
SELECT
    base.id_parcelle,
    base.code_parcelle,
    base.code_unique,
    base.id_machine,
    base.nom_agent,
    base.nom_pers,
    base.prenom_pers,
    base.cin,
    base.date_creation,
    TO_DATE(base.date_creation, 'DD/MM/YYYY')          AS jour,
    (ST_Area(base.geom) / 10000)::numeric(12,4)        AS surface_ha,
    ST_IsValid(base.geom)                              AS geom_valide,
    ST_NPoints(base.geom)                              AS nb_sommets,
    UPPER(cv.name_villa)                               AS name_villa,
    cv.statut,
    cv.cd_reg,
    cv.cd_dpt,
    cv.cd_sp,
    cv.cd_vil,
    UPPER(cv.depart)                                   AS depart,
    UPPER(cv."sous-pref")                              AS sous_pref
FROM base
LEFT JOIN LATERAL (
    SELECT
        code_village.name_villa,
        code_village.statut,
        code_village.cd_reg,
        code_village.cd_dpt,
        code_village.cd_sp,
        code_village.cd_vil,
        code_village.depart,
        code_village."sous-pref"
    FROM code_village
    WHERE ST_Intersects(
        ST_SetSRID(code_village.geom, 0),
        ST_SetSRID(ST_Centroid(base.geom), 0)
    )
    LIMIT 1
) cv ON TRUE
ORDER BY jour, base.id_machine, base.code_parcelle;
