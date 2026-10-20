import re

from django.conf import settings
from django.db import models

DEPARTEMENTS = ['SOUBRE', 'MEAGUI', 'BUYO']
REGION = 'NAWA'

DEPT_CHOICES = [(d, d) for d in DEPARTEMENTS] + [('SOUBRE / MEAGUI', 'SOUBRE / MEAGUI')]


class Agent(models.Model):
    """Agent de terrain — remplace l'AGENT_MAP codé en dur.

    La clé métier est le couple (id_machine, departement) : plusieurs agents
    de départements différents peuvent porter le même numéro de machine.
    """

    FONCTIONS = [
        ('topographe',   'Opérateur topographe'),
        ('enqueteur',    'Commissaire enquêteur'),
        ('animateur',    'Animateur / sensibilisation'),
        ('opposition',   'Agent opposition / litige'),
        ('superviseur',  'Superviseur'),
        ('saisie',       'Agent de saisie'),
        ('autre',        'Autre'),
    ]

    id_machine   = models.CharField('Identifiant machine', max_length=40,
                                    help_text="Tel qu'il apparaît dans la base terrain (ex : agent42).")
    nom          = models.CharField('Nom', max_length=80)
    prenoms      = models.CharField('Prénoms', max_length=120, blank=True)
    fonction     = models.CharField('Fonction', max_length=20, choices=FONCTIONS, default='topographe')
    departement  = models.CharField('Département', max_length=40, choices=DEPT_CHOICES, default='SOUBRE')
    sous_prefecture = models.CharField('Sous-préfecture', max_length=100, blank=True)
    telephone    = models.CharField('Téléphone', max_length=30, blank=True)
    email        = models.EmailField('Email', blank=True)
    matricule    = models.CharField('Matricule', max_length=40, blank=True)
    objectif_journalier = models.PositiveIntegerField(
        'Objectif journalier (parcelles)', default=settings.OBJECTIF_JOURNALIER_DEFAUT)
    actif        = models.BooleanField('En activité', default=True)
    date_prise_service = models.DateField('Prise de service', null=True, blank=True)
    observation  = models.TextField('Observation', blank=True)
    cree_le      = models.DateTimeField(auto_now_add=True)
    modifie_le   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['departement', 'nom', 'prenoms']
        unique_together = ('id_machine', 'departement')
        verbose_name = 'Agent'
        verbose_name_plural = 'Agents'

    def __str__(self):
        return f'{self.nom_complet} ({self.id_machine} — {self.departement})'

    @property
    def nom_complet(self):
        return f'{self.nom} {self.prenoms}'.strip()

    @property
    def initiales(self):
        parties = [p for p in self.nom_complet.split() if p]
        return ''.join(p[0] for p in parties[:2]).upper() or '?'


class Opposition(models.Model):
    """Opposition ou litige foncier suivi jusqu'à son règlement."""

    STATUTS = [
        ('non_renseigne', 'Statut non renseigné'),
        ('levee', 'Opposition levée'),
        ('enregistree', 'Enregistrée'),
        ('instruction', 'En instruction'),
        ('conciliation', 'En conciliation'),
        ('reglee',      'Réglée à l\'amiable'),
        ('rejetee',     'Rejetée'),
        ('transmise',   'Transmise au tribunal'),
    ]
    STATUTS_CLOS = ('reglee', 'rejetee', 'transmise', 'levee')

    TYPES = [
        ('limite',      'Contestation de limite'),
        ('propriete',   'Contestation de propriété'),
        ('heritage',    'Litige successoral'),
        ('vente',       'Contestation de vente'),
        ('exploitation', 'Conflit d\'exploitation'),
        ('autre',       'Autre'),
    ]

    code_parcelle   = models.CharField('Code parcelle', max_length=60, blank=True)
    num_demande     = models.CharField('N° demande', max_length=60, blank=True)
    departement     = models.CharField('Département', max_length=40, choices=DEPT_CHOICES, default='SOUBRE')
    sous_prefecture = models.CharField('Sous-préfecture', max_length=100, blank=True)
    village         = models.CharField('Village', max_length=100, blank=True)

    demandeur   = models.CharField('Demandeur (titulaire)', max_length=160, blank=True)
    opposant    = models.CharField('Opposant', max_length=160, blank=True)
    type_litige = models.CharField('Type de litige', max_length=20, choices=TYPES, default='limite')
    motif       = models.TextField('Motif', blank=True)

    date_opposition = models.DateField('Date de l\'opposition', null=True, blank=True)
    date_reglement  = models.DateField('Date de règlement', null=True, blank=True)
    statut          = models.CharField('Statut', max_length=20, choices=STATUTS, default='enregistree')
    issue           = models.TextField('Issue / décision', blank=True)

    agent      = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='oppositions', verbose_name='Agent en charge')
    source_reference = models.CharField(max_length=64, unique=True, null=True, blank=True)
    source_metadata = models.JSONField(default=dict, blank=True)
    source     = models.CharField(max_length=20, default='saisie',
                                  choices=[('saisie', 'Saisie manuelle'),
                                           ('import', 'Import CSV'),
                                           ('postgis', 'Base terrain')])
    cree_le    = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_opposition']
        verbose_name = 'Opposition / litige'
        verbose_name_plural = 'Oppositions / litiges'

    def __str__(self):
        return f'{self.code_parcelle or self.num_demande or "?"} — {self.get_statut_display()}'

    @property
    def est_close(self):
        return self.statut in self.STATUTS_CLOS

    @property
    def delai_jours(self):
        """Délai de traitement en jours (jusqu'au règlement, ou jusqu'à aujourd'hui)."""
        from datetime import date as _d
        if self.est_close and not self.date_reglement:
            return None
        fin = self.date_reglement or _d.today()
        if not self.date_opposition:
            return None
        return (fin - self.date_opposition).days


def normaliser_code(code):
    """Forme canonique d'un code parcelle, pour rapprocher P0345, p345 et 345."""
    s = str(code or '').strip().upper()
    if not s or s in ('NAN', 'NONE', 'NULL'):
        return ''
    s = re.sub(r'^P\s*', '', s)
    chiffres = re.sub(r'\D', '', s)
    return chiffres.lstrip('0') or chiffres or s


class ParcellePublicite(models.Model):
    """Parcelle déjà passée en publicité — liste de référence, avec géométrie."""

    # ATTENTION : code_parcelle n'est PAS unique. Le même code existe dans
    # plusieurs villages (P3514 se trouve à GNAGBOYA et à KOUDOUYO), et parfois
    # deux fois dans le même village. Le seul identifiant fiable est
    # id_parcelle / code_unique.
    id_parcelle     = models.CharField('Identifiant parcelle', max_length=80,
                                       blank=True, db_index=True)
    code_parcelle   = models.CharField('Code parcelle', max_length=60, db_index=True)
    code_norm       = models.CharField(max_length=60, blank=True, db_index=True,
                                       help_text='Forme canonique, calculée automatiquement.')
    num_demande     = models.CharField('N° demande', max_length=60, blank=True)
    departement     = models.CharField('Département', max_length=40, blank=True, db_index=True)
    sous_prefecture = models.CharField('Sous-préfecture', max_length=100, blank=True)
    village         = models.CharField('Village', max_length=100, blank=True)
    titulaire       = models.CharField('Titulaire', max_length=160, blank=True)
    prenoms         = models.CharField('Prénoms', max_length=160, blank=True)
    telephone       = models.CharField('Téléphone', max_length=40, blank=True)
    cni             = models.CharField('CNI', max_length=60, blank=True)
    type_certificat = models.CharField('Type de certificat', max_length=20, blank=True)
    commissaire     = models.CharField('Commissaire enquêteur', max_length=160, blank=True)
    id_machine      = models.CharField('Identifiant machine', max_length=40, blank=True)
    code_unique     = models.CharField('Code unique', max_length=80, blank=True)
    indice_unique   = models.CharField('Indice unique', max_length=80, blank=True)
    surface_ha      = models.FloatField('Surface (ha)', null=True, blank=True)
    date_ouverture  = models.DateField('Ouverture de publicité', null=True, blank=True)
    date_cloture    = models.DateField('Clôture de publicité', null=True, blank=True)
    statut          = models.CharField('Statut', max_length=40, blank=True)
    observation     = models.TextField('Observation', blank=True)

    # ── Géométrie (WGS84, prête pour Leaflet) ────────────────────────────────
    geojson  = models.TextField(blank=True, help_text='Géométrie WGS84 sérialisée.')
    lat      = models.FloatField(null=True, blank=True, db_index=True)
    lon      = models.FloatField(null=True, blank=True, db_index=True)
    lat_min  = models.FloatField(null=True, blank=True)
    lat_max  = models.FloatField(null=True, blank=True)
    lon_min  = models.FloatField(null=True, blank=True)
    lon_max  = models.FloatField(null=True, blank=True)

    fichier_source = models.CharField(max_length=200, blank=True)
    importee_le    = models.DateTimeField(auto_now_add=True)
    modifiee_le    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['departement', 'code_parcelle']
        verbose_name = 'Parcelle en publicité'
        verbose_name_plural = 'Parcelles en publicité'

    def __str__(self):
        return self.code_parcelle

    def save(self, *args, **kwargs):
        self.code_norm = normaliser_code(self.code_parcelle)
        super().save(*args, **kwargs)

    @property
    def nom_complet(self):
        return f'{self.titulaire} {self.prenoms}'.strip()

    @property
    def a_geometrie(self):
        return bool(self.geojson)


class ParcelleExclue(models.Model):
    """Parcelle à supprimer : masquée partout dans la plateforme.

    Les codes listés ici sont retirés des cartes, des tableaux de contrôle
    qualité, des rendements et des statistiques. Rien n'est effacé de la base
    terrain : c'est un filtre d'affichage, réversible à tout moment.
    """

    MOTIFS = [
        ('doublon',     'Doublon de levé'),
        ('geometrie',   'Géométrie aberrante'),
        ('hors_zone',   'Hors zone de projet'),
        ('annulee',     'Demande annulée'),
        ('test',        'Levé de test'),
        ('autre',       'Autre'),
    ]

    # L'identifiant unique prime toujours sur le code : deux parcelles de
    # villages différents peuvent porter le même code_parcelle.
    id_parcelle   = models.CharField('Identifiant parcelle', max_length=80,
                                     blank=True, db_index=True)
    code_parcelle = models.CharField('Code parcelle', max_length=60, db_index=True)
    code_norm     = models.CharField(max_length=60, blank=True, db_index=True)
    motif         = models.CharField('Motif', max_length=20, choices=MOTIFS, default='autre')
    commentaire   = models.TextField('Commentaire', blank=True)
    departement   = models.CharField('Département', max_length=40, blank=True)
    village       = models.CharField('Village', max_length=100, blank=True)
    id_machine    = models.CharField('Identifiant machine', max_length=40, blank=True)
    date_creation_levee = models.CharField('Date du levé', max_length=40, blank=True)

    geojson = models.TextField(blank=True)
    lat     = models.FloatField(null=True, blank=True)
    lon     = models.FloatField(null=True, blank=True)

    active         = models.BooleanField('Exclusion active', default=True)
    fichier_source = models.CharField(max_length=200, blank=True)
    ajoutee_le     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code_parcelle']
        verbose_name = 'Parcelle exclue'
        verbose_name_plural = 'Parcelles exclues'

    def __str__(self):
        return f'{self.code_parcelle} ({self.get_motif_display()})'

    @property
    def cle(self):
        """Clé de rapprochement : identifiant unique, sinon code + village."""
        if self.id_parcelle:
            return ('id', self.id_parcelle)
        return ('code', self.code_norm, (self.village or '').strip().upper())

    def save(self, *args, **kwargs):
        self.code_norm = normaliser_code(self.code_parcelle)
        super().save(*args, **kwargs)


class ControleQualite(models.Model):
    """Trace d'une exécution du contrôle qualité sur la base terrain."""

    libelle      = models.CharField(max_length=200)
    date_debut   = models.DateField()
    date_fin     = models.DateField()
    lance_le     = models.DateTimeField(auto_now_add=True)
    nb_parcelles = models.IntegerField(default=0)
    nb_bornes    = models.IntegerField(default=0)
    nb_erreurs   = models.IntegerField(default=0)
    nb_alertes   = models.IntegerField(default=0)
    score        = models.FloatField(default=0)
    duree_ms     = models.IntegerField(default=0)
    resultat     = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-lance_le']
        verbose_name = 'Contrôle qualité'
        verbose_name_plural = 'Contrôles qualité'

    def __str__(self):
        return f'{self.libelle} — {self.lance_le:%d/%m/%Y %H:%M}'


class AgentRendement(models.Model):
    departement = models.CharField(max_length=40)
    code_agent = models.CharField(max_length=40)
    nom_agent = models.CharField(max_length=180)
    superviseur = models.CharField(max_length=180)
    controleur = models.CharField(max_length=120, blank=True)
    class Meta:
        unique_together = ('departement','code_agent')
        ordering = ['departement','superviseur','nom_agent']

class LigneRendement(models.Model):
    num_demande = models.CharField(max_length=60, unique=True)
    donnees = models.JSONField(default=dict)
    modifie_le = models.DateTimeField(auto_now=True)

class ParametresRendement(models.Model):
    donnees = models.JSONField(default=dict)


class CoucheServitude(models.Model):
    cle = models.CharField(max_length=100, unique=True)
    libelle = models.CharField(max_length=150)
    role = models.CharField(max_length=20)  # deduction, statut, reference
    geometries = models.JSONField(default=list)  # TMCI-5.5, mètres, sans attributs personnels
    revision = models.CharField(max_length=64)
    modifiee_le = models.DateTimeField(auto_now=True)



class ChargementReference(models.Model):
    cle = models.CharField(max_length=60, unique=True)
    revision = models.CharField(max_length=64)
    importe_le = models.DateTimeField(auto_now=True)
