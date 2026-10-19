# PRESFOR CTB — version de test

Application Django : contrôle qualité CSV, rendements journalier/hebdomadaire/mensuel et cartographie GeoServer.

La source PostgreSQL terrain est consultée en lecture seule. Les superficies du rendement proviennent de cette source. La base applicative est distincte.

Les identifiants, données terrain et référentiels nominatifs sont fournis par variables d’environnement Render et ne figurent pas dans ce dépôt.

Déploiement : `bash build_render.sh`, puis `bash start_render.sh`.
