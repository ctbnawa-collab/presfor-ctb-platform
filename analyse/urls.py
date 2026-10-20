from django.urls import path
from . import views

urlpatterns = [
    path('',                                    views.dashboard,       name='dashboard'),
    path('upload/progression/', views.progression_analyse, name='progression_analyse'),
    path('upload/',                             views.upload,          name='upload'),
    path('session/<int:pk>/',                   views.session_detail,  name='session_detail'),
    path('session/<int:pk>/departement/<str:nom>/', views.departement, name='departement'),
    path('session/<int:pk>/region/nawa/',       views.region_nawa,     name='region_nawa'),
    path('session/<int:pk>/api/stats/',         views.api_stats,       name='api_stats'),
    path('demande/<int:pk>/',                   views.demande_detail,  name='demande_detail'),
    path('session/<int:pk>/delete/',            views.delete_session,  name='delete_session'),
]
