from django.urls import path

from . import views

urlpatterns = [
    path("businesses", views.businesses, name="api_businesses"),
    path("businesses/<str:business_id>/profile", views.profile, name="api_profile"),
    path("businesses/<str:business_id>/financial-summary", views.financial_summary, name="api_financial_summary"),
    path("businesses/<str:business_id>/verification-summary", views.verification_summary, name="api_verification_summary"),
    path("businesses/<str:business_id>/data-quality", views.data_quality, name="api_data_quality"),
    path("businesses/<str:business_id>/consent-status", views.consent, name="api_consent_status"),
    path("receipts", views.receipts, name="api_receipts"),
]
