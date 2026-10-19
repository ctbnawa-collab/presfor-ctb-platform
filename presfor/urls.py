from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path('connexion/',auth_views.LoginView.as_view(template_name='connexion.html'),name='login'),
    path('deconnexion/',auth_views.LogoutView.as_view(),name='logout'),
    path('admin/', admin.site.urls),
    path('terrain/', include('terrain.urls')),
    path('', include('analyse.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = f'{settings.APP_NOM} — administration'
admin.site.site_title = settings.APP_NOM
admin.site.index_title = settings.APP_PROJET
