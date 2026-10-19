from django.contrib import admin
from .models import AnalyseSession, StatDepartement, Demande

class StatInline(admin.TabularInline):
    model = StatDepartement
    extra = 0
    readonly_fields = ('departement','nb_parcelles','nb_demandes','nb_rdc_encours','nb_rdc_termine','nb_pvcl_encours','nb_pvcl_termine')

@admin.register(AnalyseSession)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('nom','date_creation','statut','nb_csv')
    inlines = [StatInline]

@admin.register(Demande)
class DemandeAdmin(admin.ModelAdmin):
    list_display = ('num_parcelle','nom','prenom','departement','statut_pvcl','statut_rdc')
    list_filter  = ('departement','statut_pvcl','statut_rdc','session')
    search_fields = ('nom','prenom','num_parcelle')
