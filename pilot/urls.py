from django.urls import path

from . import views

urlpatterns = [
    path("", views.console_home, name="console_home"),
    path("businesses/", views.console_businesses, name="console_businesses"),
    path("businesses/<uuid:public_id>/", views.console_business, name="console_business"),
    path("institutions/", views.console_institutions, name="console_institutions"),
    path("institutions/<uuid:public_id>/", views.console_institution, name="console_institution"),
    path("pilot/", views.console_pilot, name="console_pilot"),
    path("issues/", views.console_issues, name="console_issues"),
    path("issues/<int:pk>/resolve/", views.console_issue_resolve, name="console_issue_resolve"),
    path("audit/", views.console_audit, name="console_audit"),
]
