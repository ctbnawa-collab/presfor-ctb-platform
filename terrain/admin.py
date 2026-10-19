from django.contrib import admin

from .models import (Agent, ControleQualite, Opposition, ParcelleExclue,
                     ParcellePublicite)


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('id_machine', 'nom', 'prenoms', 'fonction', 'departement',
                    'objectif_journalier', 'actif')
    list_filter = ('departement', 'fonction', 'actif')
    search_fields = ('nom', 'prenoms', 'id_machine', 'matricule')


@admin.register(Opposition)
class OppositionAdmin(admin.ModelAdmin):
    list_display = ('code_parcelle', 'departement', 'opposant', 'type_litige',
                    'date_opposition', 'statut', 'agent')
    list_filter = ('departement', 'statut', 'type_litige')
    search_fields = ('code_parcelle', 'opposant', 'demandeur', 'village')
    date_hierarchy = 'date_opposition'


@admin.register(ParcellePublicite)
class ParcellePubliciteAdmin(admin.ModelAdmin):
    list_display = ('code_parcelle', 'departement', 'village', 'titulaire',
                    'surface_ha', 'a_geometrie')
    list_filter = ('departement', 'statut', 'type_certificat')
    search_fields = ('code_parcelle', 'titulaire', 'prenoms', 'village', 'num_demande')


@admin.register(ParcelleExclue)
class ParcelleExclueAdmin(admin.ModelAdmin):
    list_display = ('code_parcelle', 'motif', 'departement', 'village',
                    'id_machine', 'active')
    list_filter = ('motif', 'active', 'departement')
    search_fields = ('code_parcelle', 'commentaire')


@admin.register(ControleQualite)
class ControleQualiteAdmin(admin.ModelAdmin):
    list_display = ('libelle', 'lance_le', 'nb_parcelles', 'nb_bornes',
                    'nb_erreurs', 'nb_alertes', 'score')
    readonly_fields = ('resultat',)
