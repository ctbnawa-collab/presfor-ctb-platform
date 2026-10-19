-- ============================================================================
-- OPPOSITIONS / RÈGLEMENT DE LITIGE  —  REQUÊTE À ADAPTER
--
-- Le schéma exact des tables d'opposition n'est pas encore connu de la
-- plateforme. Tant que ce fichier n'est pas adapté, la section « Opposition /
-- Règlement de litige » fonctionne sur la saisie manuelle et l'import CSV
-- (base locale), et bascule automatiquement sur cette requête dès qu'elle
-- s'exécute sans erreur.
--
-- Pour l'adapter : ouvrez Terrain ▸ Explorateur de base pour lister les tables
-- et colonnes réelles, puis réécrivez le SELECT ci-dessous.
--
-- Paramètres disponibles : %(date_debut)s , %(date_fin)s  (format YYYY-MM-DD)
--
-- Colonnes attendues par la plateforme (alias obligatoires) :
--   code_parcelle, departement, sous_pref, village, opposant,
--   motif, jour_opposition, jour_reglement, statut, id_machine
-- ============================================================================

SELECT
    o.code_parcelle                       AS code_parcelle,
    UPPER(o.departement)                  AS departement,
    UPPER(o.sous_pref)                    AS sous_pref,
    UPPER(o.village)                      AS village,
    o.opposant                            AS opposant,
    o.motif                               AS motif,
    o.date_opposition::date               AS jour_opposition,
    o.date_reglement::date                AS jour_reglement,
    o.statut                              AS statut,
    o.id_machine                          AS id_machine
FROM opposition o
WHERE o.date_opposition::date BETWEEN %(date_debut)s::date AND %(date_fin)s::date
ORDER BY o.date_opposition;
