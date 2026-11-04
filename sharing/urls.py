from django.urls import path

from . import views

urlpatterns = [
    path("sharing/", views.sharing_home, name="sharing"),
    path("sharing/new/", views.consent_new, name="consent_new"),
    path("sharing/<uuid:public_id>/", views.consent_detail, name="consent_detail"),
    path("sharing/<uuid:public_id>/revoke/", views.consent_revoke, name="consent_revoke"),
    path("bank/", views.bank_home, name="bank_home"),
    path("bank/receipts/", views.bank_receipts, name="bank_receipts"),
    path("bank/businesses/<uuid:public_id>/", views.bank_business, name="bank_business"),
    path("bank/businesses/<uuid:public_id>/pdf/", views.bank_pdf, name="bank_pdf"),
]
