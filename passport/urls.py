from django.urls import path

from . import views

urlpatterns = [
    path("", views.passport_home, name="passport"),
    path("generate/", views.passport_generate, name="passport_generate"),
    path("<uuid:public_id>/", views.passport_snapshot, name="passport_snapshot"),
    path("<uuid:public_id>/pdf/", views.passport_pdf, name="passport_pdf"),
]
