from django import forms

class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)
    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(MultipleFileField, self).clean(d, initial) for d in data]
        return [super(MultipleFileField, self).clean(data, initial)]

class UploadForm(forms.Form):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from django.conf import settings
        if settings.PRESFOR_POSTGRES_SURFACES_ONLY:
            self.fields['source_parcelles'].choices=[('postgis','Base PostgreSQL terrain — superficie de référence')]
            self.fields['source_parcelles'].initial='postgis'
            self.fields['parcelle_file'].widget=forms.HiddenInput()

    nom = forms.CharField(
        max_length=200, label="Nom de l'analyse",
        widget=forms.TextInput(attrs={'class':'form-control','placeholder':'Ex: Analyse Juillet 2026'}))
    csv_files = MultipleFileField(
        label="Fichiers CSV Digifor", required=True,
        widget=MultipleFileInput(attrs={'class':'form-control','accept':'.csv,.xls,.xlsx','multiple':True,'id':'csv_files'}))
    rattachement = forms.ChoiceField(
        label="Ce que la période désigne",
        choices=[
            ('parcelle', "Les parcelles levées — on compte les demandes créées pour ces parcelles"),
            ('demande',  "Les demandes elles-mêmes — on filtre sur leurs propres dates"),
        ],
        initial='parcelle',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}))
    source_parcelles = forms.ChoiceField(
        label="Source des parcelles",
        choices=[
            ('postgis', "Base terrain PostGIS (recommandé)"),
            ('fichier', "Fichier Parcelle Excel"),
            ('aucune',  "Aucune — CSV seuls, sans surfaces"),
        ],
        initial='postgis',
        widget=forms.RadioSelect(attrs={'class':'form-check-input'}))
    parcelle_file = forms.FileField(
        label="Fichier Parcelle Excel (.xlsx)", required=False,
        widget=forms.FileInput(attrs={'class':'form-control','accept':'.xlsx'}))
    date_debut = forms.DateField(
        label="Date début (filtre)", required=False,
        widget=forms.DateInput(attrs={'class':'form-control','type':'date'}))
    date_fin = forms.DateField(
        label="Date fin (filtre)", required=False,
        widget=forms.DateInput(attrs={'class':'form-control','type':'date'}))

    def clean(self):
        donnees = super().clean()
        if donnees.get('source_parcelles') == 'fichier' and not donnees.get('parcelle_file'):
            self.add_error('parcelle_file',
                           "Choisissez un fichier, ou basculez sur la base terrain PostGIS.")
        return donnees
