import csv
from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html
from django.db import connection
from .models import Zone, TypeCapteur, Equipement, Capteur, RegleAlerte, Alerte, Maintenance


# ── CSS personnalisé injecté dans chaque page admin ────────────────────────────
class RWAdminSite(admin.AdminSite):
    site_header  = "RaffineryWatch — Administration"
    site_title   = "RaffineryWatch"
    index_title  = "Tableau de bord — Gestion IoT"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Actions réutilisables ──────────────────────────────────────────────────────
def export_csv(modeladmin, request, queryset):
    """Export CSV générique pour n'importe quel modèle."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{modeladmin.model._meta.db_table}.csv"'
    response.write('﻿')  # BOM UTF-8 pour Excel
    writer = csv.writer(response)
    fields = [f.name for f in modeladmin.model._meta.fields]
    writer.writerow(fields)
    for obj in queryset:
        writer.writerow([getattr(obj, f) for f in fields])
    return response
export_csv.short_description = "📥 Exporter en CSV"


# ── Zone ──────────────────────────────────────────────────────────────────────
@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display  = ('id', 'nom', 'description', 'nb_equipements', 'nb_capteurs')
    search_fields = ('nom',)
    actions       = [export_csv]

    def nb_equipements(self, obj):
        return Equipement.objects.filter(zone=obj).count()
    nb_equipements.short_description = "Équipements"

    def nb_capteurs(self, obj):
        count = Capteur.objects.filter(zone=obj).count()
        actifs = Capteur.objects.filter(zone=obj, statut='actif').count()
        return format_html('<span style="color:#00e676">{}</span> / {}', actifs, count)
    nb_capteurs.short_description = "Capteurs actifs / total"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── TypeCapteur ───────────────────────────────────────────────────────────────
@admin.register(TypeCapteur)
class TypeCapteurAdmin(admin.ModelAdmin):
    list_display = ('id', 'nom', 'unite_mesure', 'valeur_min_physique', 'valeur_max_physique', 'nb_capteurs')
    actions      = [export_csv]

    def nb_capteurs(self, obj):
        return Capteur.objects.filter(type_capteur=obj, statut='actif').count()
    nb_capteurs.short_description = "Capteurs actifs"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Equipement ────────────────────────────────────────────────────────────────
@admin.register(Equipement)
class EquipementAdmin(admin.ModelAdmin):
    list_display   = ('id', 'nom', 'zone', 'statut_badge', 'nb_capteurs_actifs')
    list_filter    = ('statut', 'zone')
    search_fields  = ('nom',)
    list_per_page  = 25
    actions        = [export_csv]

    def statut_badge(self, obj):
        colors = {'operationnel': '#00e676', 'maintenance': '#f57c00', 'hors_service': '#ef4444'}
        color = colors.get(obj.statut, '#888')
        return format_html('<span style="color:{};font-weight:600">{}</span>', color, obj.get_statut_display())
    statut_badge.short_description = "Statut"

    def nb_capteurs_actifs(self, obj):
        return Capteur.objects.filter(equipement=obj, statut='actif').count()
    nb_capteurs_actifs.short_description = "Capteurs actifs"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Capteur ───────────────────────────────────────────────────────────────────
def activer_capteurs(modeladmin, request, queryset):
    queryset.update(statut='actif')
activer_capteurs.short_description = "✅ Activer les capteurs sélectionnés"

def desactiver_capteurs(modeladmin, request, queryset):
    queryset.update(statut='inactif')
desactiver_capteurs.short_description = "⏸️ Désactiver les capteurs sélectionnés"

def marquer_en_panne(modeladmin, request, queryset):
    queryset.update(statut='en_panne')
marquer_en_panne.short_description = "🔴 Marquer comme en panne"


@admin.register(Capteur)
class CapteurAdmin(admin.ModelAdmin):
    list_display   = ('code', 'nom', 'type_capteur', 'zone', 'statut_badge', 'seuil_min', 'seuil_max', 'derniere_mesure')
    list_filter    = ('statut', 'type_capteur', 'zone')
    search_fields  = ('code', 'nom')
    list_editable  = ('seuil_min', 'seuil_max')
    list_per_page  = 25
    actions        = [activer_capteurs, desactiver_capteurs, marquer_en_panne, export_csv]
    readonly_fields = ('code',)

    fieldsets = (
        ('Identification', {
            'fields': ('code', 'nom', 'type_capteur', 'equipement', 'zone')
        }),
        ('Statut & Seuils', {
            'fields': ('statut', 'seuil_min', 'seuil_max'),
            'description': 'Modifiez les seuils d\'alerte ici. Le simulateur respectera ces valeurs.'
        }),
    )

    def statut_badge(self, obj):
        colors = {'actif': '#00e676', 'inactif': '#5a8fb0', 'en_panne': '#ef4444'}
        color  = colors.get(obj.statut, '#888')
        return format_html('<span style="color:{};font-weight:600">● {}</span>', color, obj.get_statut_display())
    statut_badge.short_description = "Statut"

    def derniere_mesure(self, obj):
        try:
            with connection.cursor() as cur:
                cur.execute("""
                    SELECT valeur, timestamp FROM mesures
                    WHERE capteur_id = %s ORDER BY timestamp DESC LIMIT 1
                """, [obj.id])
                row = cur.fetchone()
                if row:
                    return format_html(
                        '<span style="color:#00e676">{:.2f}</span> <small class="text-muted">{}</small>',
                        row[0], str(row[1])[:16]
                    )
        except Exception:
            pass
        return format_html('<span style="color:#334e68">—</span>')
    derniere_mesure.short_description = "Dernière mesure"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Règle d'alerte ────────────────────────────────────────────────────────────
def activer_regles(modeladmin, request, queryset):
    queryset.update(actif=True)
activer_regles.short_description = "✅ Activer les règles sélectionnées"

def desactiver_regles(modeladmin, request, queryset):
    queryset.update(actif=False)
desactiver_regles.short_description = "⏸️ Désactiver les règles sélectionnées"


@admin.register(RegleAlerte)
class RegleAlerteAdmin(admin.ModelAdmin):
    list_display  = ('id', 'capteur', 'type_regle', 'valeur_seuil', 'priorite_badge', 'actif_badge', 'description')
    list_filter   = ('priorite', 'actif', 'type_regle')
    list_editable = ('valeur_seuil',)
    search_fields = ('capteur__code', 'description', 'nom')
    list_per_page = 25
    actions       = [activer_regles, desactiver_regles, export_csv]

    def priorite_badge(self, obj):
        colors = {'critique': '#ef4444', 'haute': '#f97316', 'moyenne': '#eab308', 'basse': '#1565c0'}
        color  = colors.get(obj.priorite, '#888')
        return format_html('<span style="color:{};font-weight:600">{}</span>', color, obj.priorite.upper())
    priorite_badge.short_description = "Priorité"

    def actif_badge(self, obj):
        if obj.actif:
            return format_html('<span style="color:#00e676">● Actif</span>')
        return format_html('<span style="color:#5a8fb0">○ Inactif</span>')
    actif_badge.short_description = "Active"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Alertes ───────────────────────────────────────────────────────────────────
def acquitter_alertes(modeladmin, request, queryset):
    """Acquittement en masse via SQL brut (noms de colonnes corrects)."""
    ids = list(queryset.filter(statut='ouverte').values_list('id', flat=True))
    if ids:
        placeholders = ','.join(['%s'] * len(ids))
        with connection.cursor() as cur:
            cur.execute(
                f"UPDATE alertes SET statut='acquittee', timestamp_acquittement=NOW() "
                f"WHERE id IN ({placeholders}) AND statut='ouverte'",
                ids,
            )
acquitter_alertes.short_description = "✔️ Acquitter les alertes sélectionnées"

def exporter_alertes_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="alertes.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['ID', 'Capteur', 'Priorité', 'Statut', 'Message', 'Date'])
    for a in queryset:
        writer.writerow([a.id, str(a.capteur), a.priorite, a.statut, a.message, a.created_at])
    return response
exporter_alertes_csv.short_description = "📥 Exporter alertes en CSV"


@admin.register(Alerte)
class AlerteAdmin(admin.ModelAdmin):
    list_display    = ('id', 'capteur_code', 'priorite_badge', 'statut_badge', 'message_court', 'created_at')
    list_filter     = ('statut', 'priorite', 'capteur__zone')
    search_fields   = ('message', 'capteur__code')
    readonly_fields = ('created_at', 'capteur', 'regle', 'message', 'priorite')
    list_per_page   = 30
    actions         = [acquitter_alertes, exporter_alertes_csv]
    date_hierarchy  = 'created_at'

    def capteur_code(self, obj):
        return format_html('<code style="color:#00e676">{}</code>', obj.capteur.code)
    capteur_code.short_description = "Capteur"

    def priorite_badge(self, obj):
        colors = {'critique': '#ef4444', 'haute': '#f97316', 'moyenne': '#eab308', 'basse': '#1565c0'}
        color  = colors.get(obj.priorite, '#888')
        return format_html('<span style="color:{};font-weight:700">{}</span>', color, obj.priorite.upper())
    priorite_badge.short_description = "Priorité"

    def statut_badge(self, obj):
        if obj.statut == 'ouverte':
            return format_html('<span style="color:#ef4444;font-weight:600">● OUVERTE</span>')
        return format_html('<span style="color:#00e676">✔ Acquittée</span>')
    statut_badge.short_description = "Statut"

    def message_court(self, obj):
        return obj.message[:80] + '…' if len(obj.message) > 80 else obj.message
    message_court.short_description = "Message"

    class Media:
        css = {'all': ('admin/css/custom.css',)}


# ── Maintenance ───────────────────────────────────────────────────────────────
@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display  = ('id', 'equipement', 'type_maintenance', 'statut_badge', 'date_debut', 'date_fin_prevue')
    list_filter   = ('statut', 'type_maintenance')
    search_fields = ('equipement__nom', 'description')
    actions       = [export_csv]

    def statut_badge(self, obj):
        colors = {'planifiee': '#1565c0', 'en_cours': '#f97316', 'terminee': '#00e676', 'annulee': '#ef4444'}
        color  = colors.get(obj.statut, '#888')
        return format_html('<span style="color:{};font-weight:600">{}</span>', color, obj.statut)
    statut_badge.short_description = "Statut"

    class Media:
        css = {'all': ('admin/css/custom.css',)}
