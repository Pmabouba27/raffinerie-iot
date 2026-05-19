from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

admin.site.site_header   = "RaffineryWatch — Administration"
admin.site.site_title    = "RaffineryWatch Admin"
admin.site.index_title   = "Gestion de la plateforme IoT"

urlpatterns = [
    path('admin/',  admin.site.urls),
    path('login/',  auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('',        include('core.urls')),
]
