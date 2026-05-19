"""
Modèles Django mappés sur les tables TimescaleDB existantes (Phase 1).
managed=False : Django ne crée/supprime pas ces tables.
"""
from django.db import models


class Zone(models.Model):
    nom         = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        managed  = False
        db_table = 'zones'
        verbose_name        = 'Zone'
        verbose_name_plural = 'Zones'

    def __str__(self):
        return self.nom


class TypeCapteur(models.Model):
    nom                 = models.CharField(max_length=100)
    unite_mesure        = models.CharField(max_length=20)
    valeur_min_physique = models.FloatField()
    valeur_max_physique = models.FloatField()

    class Meta:
        managed  = False
        db_table = 'types_capteurs'
        verbose_name        = 'Type de capteur'
        verbose_name_plural = 'Types de capteurs'

    def __str__(self):
        return f"{self.nom} ({self.unite_mesure})"


class Equipement(models.Model):
    STATUT = [('operationnel','Opérationnel'),('maintenance','Maintenance'),('hors_service','Hors service')]
    nom    = models.CharField(max_length=150)
    zone   = models.ForeignKey(Zone, on_delete=models.CASCADE, db_column='zone_id')
    statut = models.CharField(max_length=20, choices=STATUT, default='operationnel')

    class Meta:
        managed  = False
        db_table = 'equipements'
        verbose_name        = 'Équipement'
        verbose_name_plural = 'Équipements'

    def __str__(self):
        return self.nom


class Capteur(models.Model):
    STATUT = [('actif','Actif'),('inactif','Inactif'),('en_panne','En panne')]
    code         = models.CharField(max_length=30, unique=True)
    nom          = models.CharField(max_length=150)
    type_capteur = models.ForeignKey(TypeCapteur, on_delete=models.CASCADE, db_column='type_capteur_id')
    equipement   = models.ForeignKey(Equipement, on_delete=models.SET_NULL, null=True, db_column='equipement_id')
    zone         = models.ForeignKey(Zone, on_delete=models.SET_NULL, null=True, db_column='zone_id')
    statut       = models.CharField(max_length=20, choices=STATUT, default='actif')
    seuil_min    = models.FloatField(null=True, blank=True)
    seuil_max    = models.FloatField(null=True, blank=True)

    class Meta:
        managed  = False
        db_table = 'capteurs'
        verbose_name        = 'Capteur'
        verbose_name_plural = 'Capteurs'

    def __str__(self):
        return f"{self.code} — {self.nom}"


class RegleAlerte(models.Model):
    PRIORITE = [('critique','Critique'),('haute','Haute'),('moyenne','Moyenne'),('basse','Basse')]
    capteur   = models.ForeignKey(Capteur, on_delete=models.CASCADE, db_column='capteur_id')
    condition = models.CharField(max_length=20)
    seuil     = models.FloatField()
    priorite  = models.CharField(max_length=20, choices=PRIORITE)
    message   = models.TextField()
    active    = models.BooleanField(default=True)

    class Meta:
        managed  = False
        db_table = 'regles_alerte'
        verbose_name        = "Règle d'alerte"
        verbose_name_plural = "Règles d'alerte"

    def __str__(self):
        return f"[{self.priorite}] {self.capteur}"


class Alerte(models.Model):
    STATUT   = [('ouverte','Ouverte'),('acquittee','Acquittée'),('fermee','Fermée')]
    PRIORITE = [('critique','Critique'),('haute','Haute'),('moyenne','Moyenne'),('basse','Basse')]

    capteur    = models.ForeignKey(Capteur, on_delete=models.CASCADE, db_column='capteur_id')
    regle      = models.ForeignKey(RegleAlerte, on_delete=models.SET_NULL, null=True, db_column='regle_id')
    valeur     = models.FloatField(null=True)
    message    = models.TextField()
    priorite   = models.CharField(max_length=20, choices=PRIORITE)
    statut     = models.CharField(max_length=20, choices=STATUT, default='ouverte')
    created_at = models.DateTimeField()
    acquitte_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed  = False
        db_table = 'alertes'
        verbose_name        = 'Alerte'
        verbose_name_plural = 'Alertes'
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.priorite}] {self.capteur} — {self.statut}"


class Maintenance(models.Model):
    equipement       = models.ForeignKey(Equipement, on_delete=models.CASCADE, db_column='equipement_id')
    type_maintenance = models.CharField(max_length=50)
    statut           = models.CharField(max_length=20)
    date_debut       = models.DateTimeField()
    date_fin_prevue  = models.DateTimeField(null=True, blank=True)
    description      = models.TextField(blank=True)

    class Meta:
        managed  = False
        db_table = 'maintenances'
        verbose_name        = 'Maintenance'
        verbose_name_plural = 'Maintenances'

    def __str__(self):
        return f"Maintenance {self.equipement}"
