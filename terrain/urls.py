from django.urls import path
from django.shortcuts import redirect

from . import views
from . import performance

urlpatterns = [
    path('carte-geoserver/', performance.geoserver, name='carte_geoserver'),
    path('rendements/', performance.page, name='performance'),
    path('rendements/modele/', performance.model_page, name='performance_model'),
    path('rendements/api/', performance.api, name='performance_api'),
    path('rendements/journalier/', performance.periodic, {'mode':'journalier'}, name='performance_daily'),
    path('rendements/mensuel/', performance.periodic, {'mode':'mensuel'}, name='performance_monthly'),
    path('agents/referentiel/', performance.referentiel, name='referentiel_rendement'),

    # Activité globale
    path('global/',            views.global_activite,   name='global_activite'),

    # Base terrain
    path('base/',              views.connexion,         name='terrain_connexion'),
    path('base/explorateur/',  views.explorateur,       name='terrain_explorateur'),

    # Contrôle qualité
    path('qualite/', redirect, {'pattern_name':'upload'}, name='controle_qualite'),
    path('qualite/terrain/', views.controle_qualite, name='qualite_geometrie'),
    path('qualite/historique/', views.historique_qualite, name='qualite_historique'),

    # Rendement des levés
    path('rendement/',         views.rendement_leves,   name='rendement_leves'),
    path('rendement/agent/<int:pk>/', views.rendement_agent, name='rendement_agent'),
    path('api/rendement/',     views.api_rendement,     name='api_rendement'),

    # Agents
    path('agents/',            views.agents,            name='agents'),
    path('agents/nouveau/',    views.agent_form,        name='agent_creer'),
    path('agents/<int:pk>/',   views.agent_form,        name='agent_modifier'),
    path('agents/<int:pk>/supprimer/', views.agent_supprimer, name='agent_supprimer'),
    path('agents/importer/',   views.agents_importer,   name='agents_importer'),

    # Oppositions / litiges
    path('oppositions/',       views.oppositions_liste, name='oppositions'),
    path('oppositions/nouvelle/', views.opposition_form, name='opposition_creer'),
    path('oppositions/<int:pk>/', views.opposition_form, name='opposition_modifier'),
    path('oppositions/<int:pk>/supprimer/', views.opposition_supprimer, name='opposition_supprimer'),
    path('oppositions/rendement/', views.oppositions_rendement, name='oppositions_rendement'),
    path('oppositions/importer/', views.oppositions_importer, name='oppositions_importer'),

    # Publicité
    path('publicite/',         views.publicite,          name='publicite'),
    path('publicite/importer/', views.publicite_importer, name='publicite_importer'),

    # Carte Leaflet
    path('carte/',                        views.carte,               name='carte'),
    path('api/parcelles/',                views.api_parcelles,       name='api_parcelles'),
    path('api/couche/<str:table>/',       views.api_couche,          name='api_couche'),
    path('api/parcelle/<int:pk>/',        views.api_parcelle_maj,    name='api_parcelle_maj'),
    path('api/parcelle/<int:pk>/exclure/', views.api_parcelle_exclure, name='api_parcelle_exclure'),

    # Parcelles à supprimer
    path('exclusions/',                   views.exclusions,          name='exclusions'),
    path('exclusions/nouvelle/',          views.exclusion_form,      name='exclusion_creer'),
    path('exclusions/<int:pk>/',          views.exclusion_form,      name='exclusion_modifier'),
    path('exclusions/<int:pk>/basculer/', views.exclusion_basculer,  name='exclusion_basculer'),
    path('exclusions/<int:pk>/supprimer/', views.exclusion_supprimer, name='exclusion_supprimer'),

    # Import de couches shapefile
    path('couches/importer/',             views.importer_couche,     name='importer_couche'),
]
