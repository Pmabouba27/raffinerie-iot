from django.urls import path
from . import views

urlpatterns = [
    path('',                          views.dashboard,   name='dashboard'),
    path('simulation/',               views.simulation,  name='simulation'),
    path('alertes/',                  views.alertes,     name='alertes'),
    path('alertes/<int:alerte_id>/acquitter/', views.acquitter, name='acquitter'),
    path('pipeline/',                 views.pipeline,    name='pipeline'),
    path('api/status/',               views.api_status,  name='api_status'),
    path('api/mesures/<str:type_nom>/', views.api_mesures, name='api_mesures'),
]
