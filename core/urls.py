from django.urls import path
from . import views

urlpatterns = [
    path('inscription/',               views.inscription, name='inscription'),
    path('',                          views.dashboard,   name='dashboard'),
    path('simulation/',               views.simulation,  name='simulation'),
    path('alertes/',                  views.alertes,     name='alertes'),
    path('alertes/<int:alerte_id>/',           views.alerte_detail, name='alerte_detail'),
    path('alertes/<int:alerte_id>/acquitter/', views.acquitter,      name='acquitter'),
    path('pipeline/',                 views.pipeline,    name='pipeline'),
    path('api/status/',               views.api_status,  name='api_status'),
    path('api/mesures/<str:type_nom>/', views.api_mesures, name='api_mesures'),

    # ── Gestion (CRUD dans l'interface) ──────────────────────────────────────
    path('gestion/',                                  views.gestion_index,            name='gestion_index'),
    # Capteurs
    path('gestion/capteurs/',                         views.gestion_capteurs,         name='gestion_capteurs'),
    path('gestion/capteurs/<int:pk>/edit/',           views.gestion_capteur_edit,     name='gestion_capteur_edit'),
    path('gestion/capteurs/<int:pk>/supprimer/',      views.gestion_capteur_delete,   name='gestion_capteur_delete'),
    # Zones
    path('gestion/zones/',                            views.gestion_zones,            name='gestion_zones'),
    path('gestion/zones/<int:pk>/edit/',              views.gestion_zone_edit,        name='gestion_zone_edit'),
    path('gestion/zones/<int:pk>/supprimer/',         views.gestion_zone_delete,      name='gestion_zone_delete'),
    # Règles
    path('gestion/regles/',                           views.gestion_regles,           name='gestion_regles'),
    path('gestion/regles/<int:pk>/toggle/',           views.gestion_regle_toggle,     name='gestion_regle_toggle'),
    path('gestion/regles/<int:pk>/supprimer/',        views.gestion_regle_delete,     name='gestion_regle_delete'),
    # Utilisateurs
    path('gestion/utilisateurs/',                     views.gestion_utilisateurs,     name='gestion_utilisateurs'),
    path('gestion/utilisateurs/<int:pk>/edit/',       views.gestion_utilisateur_edit, name='gestion_utilisateur_edit'),
    path('gestion/utilisateurs/<int:pk>/supprimer/',  views.gestion_utilisateur_delete, name='gestion_utilisateur_delete'),
]
