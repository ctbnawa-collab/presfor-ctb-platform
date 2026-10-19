-- ============================================================================
-- EXTRACTION DE LA COUCHE POINT / BORNES
-- (source : Requete_extraction_distinct_borne_type1.sql)
--
-- Paramètres : %(date_debut)s , %(date_fin)s  (format YYYY-MM-DD)
-- Le nom de la colonne de date est injecté par la plateforme via le jeton
-- {COL_DATE} (valeur définie par PG_COLONNE_DATE_BORNE : startleve | endleve).
--
-- Format réel de la colonne de date : YYYYMMDD + suffixe parasite ("20250726--0")
-- d'où le LEFT(...,8) et le filtre par expression régulière.
-- ============================================================================
SELECT DISTINCT ON (pt.id_borne)
    pt.id_borne,
    dl.nom,
    dl.prenom,
    dl.cin,
    dl.phone                                        AS tel,
    LEFT(pt.{COL_DATE}, 8)                          AS date_brute,
    TO_DATE(LEFT(pt.{COL_DATE}, 8), 'YYYYMMDD')     AS jour,
    ST_IsValid(pt.shape_pt)                         AS geom_valide,
    ST_X(ST_Centroid(pt.shape_pt))                  AS x,
    ST_Y(ST_Centroid(pt.shape_pt))                  AS y
FROM point pt
JOIN description_limite dl
    ON dl.id_borne_1 = pt.id_borne
WHERE pt.{COL_DATE} ~ '^[0-9]{8}'
  AND TO_DATE(LEFT(pt.{COL_DATE}, 8), 'YYYYMMDD')
      BETWEEN %(date_debut)s::date AND %(date_fin)s::date
ORDER BY pt.id_borne, dl.qgis_id;
