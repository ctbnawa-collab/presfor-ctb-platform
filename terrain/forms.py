from datetime import date, timedelta

from django import forms
from django.db import transaction

from .models import Agent, AgentRendement, Opposition

CTRL = {'class': 'form-control'}
SEL = {'class': 'form-select'}


class PeriodeForm(forms.Form):
    """Filtre de période commun à toutes les vues terrain."""
    date_debut = forms.DateField(
        label='Du', required=True,
        widget=forms.DateInput(attrs={**CTRL, 'type': 'date'}))
    date_fin = forms.DateField(
        label='Au', required=True,
        widget=forms.DateInput(attrs={**CTRL, 'type': 'date'}))
    departement = forms.ChoiceField(
        label='Département', required=False,
        choices=[('', 'Tous'), ('SOUBRE', 'SOUBRE'), ('MEAGUI', 'MEAGUI'), ('BUYO', 'BUYO')],
        widget=forms.Select(attrs=SEL))

    def __init__(self, *args, **kwargs):
        jours = kwargs.pop('jours_defaut', 30)
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            fin = date.today()
            self.fields['date_debut'].initial = fin - timedelta(days=jours)
            self.fields['date_fin'].initial = fin

    def clean(self):
        donnees = super().clean()
        d, f = donnees.get('date_debut'), donnees.get('date_fin')
        if d and f and d > f:
            raise forms.ValidationError("La date de début est postérieure à la date de fin.")
        return donnees


class AgentForm(forms.ModelForm):
    class Meta:
        model = Agent
        fields = ['id_machine', 'nom', 'prenoms', 'fonction', 'departement', 'superviseur', 'controleur',
                  'sous_prefecture', 'telephone', 'email', 'matricule',
                  'objectif_journalier', 'date_prise_service', 'actif', 'observation']
        widgets = {
            'id_machine': forms.TextInput(attrs={**CTRL, 'placeholder': 'agent42'}),
            'nom': forms.TextInput(attrs=CTRL),
            'prenoms': forms.TextInput(attrs=CTRL),
            'fonction': forms.Select(attrs=SEL),
            'superviseur': forms.TextInput(attrs=CTRL),
            'controleur': forms.TextInput(attrs=CTRL),
            'departement': forms.Select(attrs=SEL),
            'sous_prefecture': forms.TextInput(attrs=CTRL),
            'telephone': forms.TextInput(attrs={**CTRL, 'placeholder': '07 00 00 00 00'}),
            'email': forms.EmailInput(attrs=CTRL),
            'matricule': forms.TextInput(attrs=CTRL),
            'objectif_journalier': forms.NumberInput(attrs={**CTRL, 'min': 1}),
            'date_prise_service': forms.DateInput(attrs={**CTRL, 'type': 'date'}),
            'actif': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'observation': forms.Textarea(attrs={**CTRL, 'rows': 2}),
        }

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)
        with transaction.atomic():
            agent = super().save()
            AgentRendement.objects.filter(
                departement=agent.departement, code_agent__iexact=agent.id_machine
            ).update(superviseur=agent.superviseur, controleur=agent.controleur)
        return agent

    def clean_id_machine(self):
        v = (self.cleaned_data['id_machine'] or '').strip().lower().replace(' ', '')
        return v


class OppositionForm(forms.ModelForm):
    class Meta:
        model = Opposition
        fields = ['code_parcelle', 'num_demande', 'departement', 'sous_prefecture', 'village',
                  'demandeur', 'opposant', 'type_litige', 'motif',
                  'date_opposition', 'date_reglement', 'statut', 'issue', 'agent']
        widgets = {
            'code_parcelle': forms.TextInput(attrs=CTRL),
            'num_demande': forms.TextInput(attrs=CTRL),
            'departement': forms.Select(attrs=SEL),
            'sous_prefecture': forms.TextInput(attrs=CTRL),
            'village': forms.TextInput(attrs=CTRL),
            'demandeur': forms.TextInput(attrs=CTRL),
            'opposant': forms.TextInput(attrs=CTRL),
            'type_litige': forms.Select(attrs=SEL),
            'motif': forms.Textarea(attrs={**CTRL, 'rows': 2}),
            'date_opposition': forms.DateInput(attrs={**CTRL, 'type': 'date'}),
            'date_reglement': forms.DateInput(attrs={**CTRL, 'type': 'date'}),
            'statut': forms.Select(attrs=SEL),
            'issue': forms.Textarea(attrs={**CTRL, 'rows': 2}),
            'agent': forms.Select(attrs=SEL),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['agent'].queryset = Agent.objects.filter(actif=True)
        self.fields['agent'].empty_label = '— Non affecté —'

    def clean(self):
        d = super().clean()
        if d.get('date_reglement') and d.get('date_opposition') \
                and d['date_reglement'] < d['date_opposition']:
            raise forms.ValidationError("Le règlement ne peut pas précéder l'opposition.")
        if d.get('statut') in Opposition.STATUTS_CLOS and not d.get('date_reglement'):
            raise forms.ValidationError(
                "Une opposition close doit porter une date de règlement.")
        return d


class ImportForm(forms.Form):
    """Import CSV/Excel générique (oppositions, parcelles en publicité, agents)."""
    fichier = forms.FileField(
        label='Fichier (.csv, .xlsx)',
        widget=forms.ClearableFileInput(attrs={**CTRL, 'accept': '.csv,.xlsx,.xls'}))
    remplacer = forms.BooleanField(
        label='Remplacer les données existantes', required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))


class ImportShapefileForm(forms.Form):
    """Import d'une couche shapefile compressée en .zip."""
    COUCHES = [
        ('publicite',  'Parcelles publiées'),
        ('exclusions', 'Parcelles à supprimer (liste d\'exclusion)'),
    ]
    couche = forms.ChoiceField(
        label='Nature de la couche', choices=COUCHES,
        widget=forms.Select(attrs=SEL))
    archive = forms.FileField(
        label='Archive .zip du shapefile',
        help_text='Compressez ensemble les fichiers .shp, .shx, .dbf et .prj.',
        widget=forms.ClearableFileInput(attrs={**CTRL, 'accept': '.zip'}))
    motif = forms.ChoiceField(
        label="Motif d'exclusion", required=False,
        choices=Opposition.STATUTS[:0] or [],
        widget=forms.Select(attrs=SEL))
    remplacer = forms.BooleanField(
        label='Remplacer la couche existante', required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import ParcelleExclue
        self.fields['motif'].choices = ParcelleExclue.MOTIFS
        self.fields['motif'].initial = 'geometrie'

    def clean_archive(self):
        f = self.cleaned_data['archive']
        if not f.name.lower().endswith('.zip'):
            raise forms.ValidationError(
                "Attendu : une archive .zip contenant le shapefile.")
        return f


class ParcelleEditionForm(forms.ModelForm):
    """Édition des attributs d'une parcelle depuis la carte."""

    class Meta:
        from .models import ParcellePublicite as _PP
        model = _PP
        fields = ['code_parcelle', 'num_demande', 'departement', 'sous_prefecture',
                  'village', 'titulaire', 'prenoms', 'telephone', 'cni',
                  'type_certificat', 'commissaire', 'surface_ha', 'statut',
                  'date_ouverture', 'date_cloture', 'observation']
        widgets = {
            'code_parcelle': forms.TextInput(attrs=CTRL),
            'num_demande': forms.TextInput(attrs=CTRL),
            'departement': forms.TextInput(attrs=CTRL),
            'sous_prefecture': forms.TextInput(attrs=CTRL),
            'village': forms.TextInput(attrs=CTRL),
            'titulaire': forms.TextInput(attrs=CTRL),
            'prenoms': forms.TextInput(attrs=CTRL),
            'telephone': forms.TextInput(attrs=CTRL),
            'cni': forms.TextInput(attrs=CTRL),
            'type_certificat': forms.TextInput(attrs=CTRL),
            'commissaire': forms.TextInput(attrs=CTRL),
            'surface_ha': forms.NumberInput(attrs={**CTRL, 'step': '0.0001'}),
            'statut': forms.TextInput(attrs=CTRL),
            'date_ouverture': forms.DateInput(attrs={**CTRL, 'type': 'date'}),
            'date_cloture': forms.DateInput(attrs={**CTRL, 'type': 'date'}),
            'observation': forms.Textarea(attrs={**CTRL, 'rows': 2}),
        }


class ExclusionForm(forms.ModelForm):
    class Meta:
        from .models import ParcelleExclue as _PE
        model = _PE
        fields = ['code_parcelle', 'motif', 'departement', 'village',
                  'commentaire', 'active']
        widgets = {
            'code_parcelle': forms.TextInput(attrs=CTRL),
            'motif': forms.Select(attrs=SEL),
            'departement': forms.TextInput(attrs=CTRL),
            'village': forms.TextInput(attrs=CTRL),
            'commentaire': forms.Textarea(attrs={**CTRL, 'rows': 2}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
