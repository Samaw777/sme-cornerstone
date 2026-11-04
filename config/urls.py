from django.urls import include, path

urlpatterns = [
    path("", include("core.urls")),
    path("", include("accounts.urls")),
    path("", include("merchants.urls")),
    path("", include("ledger.urls")),
    path("passport/", include("passport.urls")),
    path("", include("sharing.urls")),
    path("api/v1/", include("partner_api.urls")),
    path("console/", include("pilot.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.not_found"
