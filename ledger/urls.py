from django.urls import path

from . import views

urlpatterns = [
    path("transactions/", views.transaction_list, name="transactions"),
    path("transactions/new/", views.transaction_new, name="transaction_new"),
    path("transactions/<uuid:public_id>/", views.transaction_detail, name="transaction_detail"),
    path("transactions/<uuid:public_id>/amend/", views.transaction_amend, name="transaction_amend"),
    path("transactions/<uuid:public_id>/reverse/", views.transaction_reverse, name="transaction_reverse"),
    path("transactions/<uuid:public_id>/annotate/", views.transaction_annotate, name="transaction_annotate"),
    path("transactions/<uuid:public_id>/link/", views.transaction_link, name="transaction_link"),
    path("annotations/<int:pk>/resolve/", views.annotation_resolve, name="annotation_resolve"),
    path("links/<int:pk>/remove/", views.link_remove, name="link_remove"),
    path("imports/", views.import_list, name="import_list"),
    path("imports/template.csv", views.import_template, name="import_template"),
    path("imports/<int:pk>/map/", views.import_map, name="import_map"),
    path("imports/<int:pk>/preview/", views.import_preview, name="import_preview"),
    path("imports/<int:pk>/commit/", views.import_commit, name="import_commit"),
    path("imports/<int:pk>/discard/", views.import_discard, name="import_discard"),
    path("sources/", views.sources, name="sources"),
    path("sources/connect/", views.source_connect, name="source_connect"),
    path("sources/<int:pk>/sync/", views.source_sync, name="source_sync"),
    path("documents/", views.documents, name="documents"),
    path("documents/<uuid:public_id>/file/", views.document_download, name="document_download"),
    path("receivables/", views.receivables, name="receivables"),
    path("receivables/invoices/<uuid:public_id>/payment/", views.invoice_payment, name="invoice_payment"),
    path("receivables/invoices/<uuid:public_id>/void/", views.invoice_void, name="invoice_void"),
    path("receivables/obligations/<int:pk>/end/", views.obligation_end, name="obligation_end"),
    path("data-quality/", views.findings, name="findings"),
    path("data-quality/<int:pk>/note/", views.finding_note, name="finding_note"),
]
