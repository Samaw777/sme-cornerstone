from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("glossary/", views.glossary, name="glossary"),
]
