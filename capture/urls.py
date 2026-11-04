from django.urls import path

from . import views

urlpatterns = [
    path("quick/", views.quick_add, name="quick_add"),
    path("quick/parse/", views.quick_parse, name="quick_parse"),
    path("quick/favourites/<int:pk>/delete/", views.template_delete, name="template_delete"),
    path("quick/sms/", views.sms, name="sms"),
    path("bills/<int:pk>/paid/", views.bill_paid, name="bill_paid"),
    path("approvals/", views.approvals, name="approvals"),
    path("telegram/", views.telegram_settings, name="telegram_settings"),
    path("telegram/webhook/", views.telegram_webhook, name="telegram_webhook"),
    path("sw.js", views.service_worker, name="service_worker"),
]
