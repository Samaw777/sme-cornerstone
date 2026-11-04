from django.urls import path

from . import views

urlpatterns = [
    path("onboarding/", views.onboarding, name="onboarding"),
    path("switch/<uuid:public_id>/", views.switch_business, name="switch_business"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("business/", views.business_profile, name="business_profile"),
    path("business/staff/", views.staff, name="staff"),
    path("business/staff/<int:pk>/", views.staff_update, name="staff_update"),
    path("more/", views.more, name="more"),
]
