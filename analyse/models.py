from datetime import datetime

from django.db import models

# Étapes de la chaîne CF, dans l'ordre d'avancement.
# Libellés alignés sur detection_etape_cf.py.
ETAPES_CF = [
    'Demande',
    'Constat de limite',
    'Recensement de DC',
    'Annonce de publicité',
    'Ouverture de publicité',
    'Cloture de publicité',
    "Constat d'existence",
    'Validation',
]

COULEURS_ETAPES = {
    'Demande':                 '#FFF2CC',
    'Constat de limite':       '#FCE4D6',
    'Recensement de DC':       '#E2EFDA',
    'Annonce de publicité':    '#DDEBF7',
    'Ouverture de publicité':  '#BDD7EE',
    'Cloture de publicité':    '#9DC3E6',
    "Constat d'existence":     '#2E75B6',
    'Validation':              '#375623',
}

# Bases de date proposées dans le filtre par date
BASES_DATE = [
    ('toutes',    'Toutes les dates (logique OU)'),
    ('creation',  'Date de création de la demande'),
    ('pvcl_fin',  'Date de fin du PVCL'),
    ('rdc_fin',   'Date de fin du RDC'),
    ('pvcl',      'Date de création du PVCL'),
    ('rdc',       'Date de création du RDC'),
]


def en_date(valeur):
    """Convertit une date JJ-MM-AAAA (format d'affichage) en objet date."""
    s = str(valeur or '').strip()
    if not s or s.lower() in ('nan', 'none', ''):
        return None
    for f in ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


DEPARTEMENTS = ['SOUBRE', 'MEAGUI', 'BUYO']
REGION = 'NAWA'

class AnalyseSession(models.Model):
    nom = models.CharField(max_length=200, verbose_name="Nom de l'analyse")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_debut_filtre = models.DateField(null=True, blank=True)
    date_fin_filtre   = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=20, default='en_cours',
                              choices=[('en_cours','En cours'),('termine','Terminé'),('erreur','Erreur')])
    nb_csv = models.IntegerField(default=0)
    message_erreur = models.TextField(blank=True)
    rattachement = models.CharField(
        max_length=12, default='parcelle',
        choices=[('parcelle', 'Parcelles levées'), ('demande', 'Dates de la demande')])
    # Qualité du rapprochement demande ↔ parcelle (clé code + village + sous-préfecture)
    rapprochement = models.JSONField(default=dict, blank=True)
    # Bilan du moteur de contrôle qualité v8 sur cette analyse
    qualite_v8 = models.JSONField(default=dict, blank=True)

    NIVEAUX_RAPPROCHEMENT = [
        ('exact',     'Code, village et sous-préfecture'),
        ('village',   'Code et village'),
        ('sous_pref', 'Code et sous-préfecture'),
        ('code',      'Code seul, sans ambiguïté'),
        ('ambigu',    'Code porté par plusieurs parcelles — non rapproché'),
        ('absent',    'Aucune parcelle ne porte ce code'),
    ]

    @property
    def qualite_rapprochement(self):
        """Lignes prêtes à afficher : niveau, libellé, nombre, pourcentage."""
        detail = (self.rapprochement or {}).get('detail') or {}
        total = sum(detail.values()) or 1
        return [{'code': c, 'libelle': lib, 'nb': detail.get(c, 0),
                 'pct': round(detail.get(c, 0) / total * 100, 1)}
                for c, lib in self.NIVEAUX_RAPPROCHEMENT]

    class Meta:
        ordering = ['-date_creation']
        verbose_name = "Session d'analyse"

    def __str__(self):
        return f"{self.nom} – {self.date_creation.strftime('%d/%m/%Y %H:%M')}"

    @property
    def periode(self):
        if self.date_debut_filtre and self.date_fin_filtre:
            return f"{self.date_debut_filtre.strftime('%d/%m/%Y')} → {self.date_fin_filtre.strftime('%d/%m/%Y')}"
        return "Toutes dates"


class StatDepartement(models.Model):
    session     = models.ForeignKey(AnalyseSession, on_delete=models.CASCADE, related_name='stats')
    departement = models.CharField(max_length=50)
    nb_parcelles        = models.IntegerField(default=0)
    nb_parcelles_avec_demande = models.IntegerField(default=0)
    superficie_levee    = models.FloatField(default=0)
    nb_demandes         = models.IntegerField(default=0)
    nb_rdc_a_creer      = models.IntegerField(default=0)
    nb_rdc_encours      = models.IntegerField(default=0)
    nb_rdc_termine      = models.IntegerField(default=0)
    superficie_enquetee = models.FloatField(default=0)
    nb_pvcl_a_creer     = models.IntegerField(default=0)
    nb_pvcl_encours     = models.IntegerField(default=0)
    nb_pvcl_termine     = models.IntegerField(default=0)

    class Meta:
        unique_together = ('session', 'departement')

    def nb_parcelles_sans_demande(self):
        return max(0, self.nb_parcelles - self.nb_parcelles_avec_demande)

    def taux_couverture(self):
        """Part des parcelles levées pour lesquelles une demande existe."""
        if not self.nb_parcelles:
            return 0
        return round(self.nb_parcelles_avec_demande / self.nb_parcelles * 100, 1)

    def taux_rdc(self):
        if not self.nb_demandes: return 0
        return round(self.nb_rdc_termine / self.nb_demandes * 100, 1)

    def taux_pvcl(self):
        if not self.nb_demandes: return 0
        return round(self.nb_pvcl_termine / self.nb_demandes * 100, 1)

    def taux_rdc_encours(self):
        if not self.nb_demandes: return 0
        return round((self.nb_rdc_encours + self.nb_rdc_termine) / self.nb_demandes * 100, 1)

    def taux_pvcl_encours(self):
        if not self.nb_demandes: return 0
        return round((self.nb_pvcl_encours + self.nb_pvcl_termine) / self.nb_demandes * 100, 1)

    def taux_rdc_a_creer(self):
        if not self.nb_demandes: return 0
        return round(self.nb_rdc_a_creer / self.nb_demandes * 100, 1)

    def taux_pvcl_a_creer(self):
        if not self.nb_demandes: return 0
        return round(self.nb_pvcl_a_creer / self.nb_demandes * 100, 1)


class Demande(models.Model):
    session         = models.ForeignKey(AnalyseSession, on_delete=models.CASCADE, related_name='demandes')
    departement     = models.CharField(max_length=50,  blank=True)
    sous_prefecture = models.CharField(max_length=100, blank=True)
    village         = models.CharField(max_length=100, blank=True)
    nom             = models.CharField(max_length=100, blank=True)
    prenom          = models.CharField(max_length=100, blank=True)
    num_demande     = models.CharField(max_length=60,  blank=True)
    num_parcelle    = models.CharField(max_length=30,  blank=True)
    type_certificat = models.CharField(max_length=10,  blank=True)
    statut_pvcl     = models.CharField(max_length=30,  blank=True)
    statut_rdc      = models.CharField(max_length=30,  blank=True)
    etape           = models.CharField(max_length=100, blank=True)
    date_creation   = models.CharField(max_length=20,  blank=True)
    date_pvcl       = models.CharField(max_length=20,  blank=True)
    date_rdc        = models.CharField(max_length=20,  blank=True)
    date_pvcl_fin   = models.CharField(max_length=20,  blank=True)
    date_rdc_fin    = models.CharField(max_length=20,  blank=True)

    # Dates de la suite de la chaîne CF (affichage)
    date_annonce    = models.CharField(max_length=20,  blank=True)
    date_ouverture  = models.CharField(max_length=20,  blank=True)
    date_cloture    = models.CharField(max_length=20,  blank=True)
    date_epc        = models.CharField(max_length=20,  blank=True)
    date_validation = models.CharField(max_length=20,  blank=True)

    # Mêmes dates en type date, pour filtrer et trier
    d_creation      = models.DateField(null=True, blank=True, db_index=True)
    d_pvcl          = models.DateField(null=True, blank=True, db_index=True)
    d_pvcl_fin      = models.DateField(null=True, blank=True, db_index=True)
    d_rdc           = models.DateField(null=True, blank=True, db_index=True)
    d_rdc_fin       = models.DateField(null=True, blank=True, db_index=True)
    d_validation    = models.DateField(null=True, blank=True, db_index=True)

    # Observations du contrôle qualité. Quand le moteur v8 a pu s'exécuter,
    # ces champs contiennent ses remarques mot pour mot ; sinon, celles
    # calculées par la plateforme.
    remarque_pvcl      = models.TextField('Remarque PVCL', blank=True)
    remarque_rdc       = models.TextField('Remarque RDC', blank=True)
    remarque_servitude = models.TextField('Servitudes', blank=True)
    remarque_equipe    = models.TextField('Équipe et pièces', blank=True)
    doublon_fiche      = models.TextField('Doublon fiche démographique', blank=True)
    mode_acquisition   = models.CharField('Mode d\'acquisition', max_length=120, blank=True)
    photo_id = models.TextField('Remarque demande et pièce identité', blank=True)
    source_qualite     = models.CharField(
        max_length=12, default='plateforme',
        choices=[('v8', 'Contrôle qualité v8'), ('plateforme', 'Calcul plateforme')])

    surface_postgres = models.FloatField(null=True, blank=True)
    id_parcelle_source = models.CharField(max_length=100, blank=True)
    surface         = models.FloatField(null=True, blank=True)
    nom_agent = models.CharField(max_length=180, blank=True)
    code_agent = models.CharField(max_length=40, blank=True)
    superviseur = models.CharField(max_length=180, blank=True)
    controleur = models.CharField(max_length=120, blank=True)
    fichier_source  = models.CharField(max_length=200, blank=True)

    CHAMPS_REMARQUE = ('photo_id', 'remarque_pvcl', 'remarque_rdc', 'remarque_servitude',
                       'doublon_fiche', 'remarque_equipe')

    @property
    def remarque(self):
        """Toutes les observations du dossier, une par ligne."""
        return '\n'.join(
            t for t in (getattr(self, c, '') for c in self.CHAMPS_REMARQUE)
            if t and t.strip().upper() != 'RAS')

    @property
    def remarque_courte(self):
        """Version d'une seule ligne, pour les tableaux et les infobulles."""
        return ' | '.join(l.strip() for l in self.remarque.split('\n') if l.strip())

    @property
    def motifs(self):
        """Liste des motifs, détails de deuxième niveau rattachés à leur entête.

        « Signature manquante : » suivi de lignes « • NOM » forme un seul motif.
        """
        blocs = []
        for ligne in self.remarque.split('\n'):
            texte = ligne.strip()
            if not texte:
                continue
            if texte.startswith('•'):
                if blocs:
                    blocs[-1] += ' ' + texte.lstrip('• ').strip()
                continue
            if texte.upper() == 'RAS':
                continue
            blocs.append(texte)
        return blocs

    @property
    def motifs_entetes(self):
        """Intitulés seuls, sans les listes de noms — pour les regroupements."""
        return [m.split(' :')[0].split(':')[0].strip() for m in self.motifs]

    @property
    def remarques_detaillees(self):
        """Toutes les observations, groupées par volet, pour la fiche du dossier."""
        from .qualite_v8 import motifs as decouper

        volets = [
            ('Demande et pièce identité', self.photo_id, ''),
            ('Constat de limite (PVCL)', self.remarque_pvcl, self.statut_pvcl),
            ('Recensement des droits coutumiers (RDC)', self.remarque_rdc, self.statut_rdc),
            ('Servitudes et limites naturelles', self.remarque_servitude, ''),
            ('Doublon de fiche démographique', self.doublon_fiche, ''),
            ('Équipe et pièces du dossier', self.remarque_equipe, ''),
        ]
        blocs = []
        for libelle, texte, statut in volets:
            decoupes = decouper(texte)
            if decoupes:
                blocs.append({'volet': libelle, 'statut': statut, 'motifs': decoupes})
        return blocs

    @property
    def terminee(self):
        return self.statut_pvcl == 'Pvcl terminé' and self.statut_rdc == 'RDC terminé'

    @property
    def couleur_etape(self):
        return COULEURS_ETAPES.get(self.etape, '#EEF2F6')

    class Meta:
        ordering = ['departement', 'sous_prefecture', 'nom']
