from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_migrate


def enable_row_level_security(using, **kwargs):
    """Supabase publishes the `public` schema through its REST API using the anon
    key. Turning on RLS with no policies makes every Django table invisible there,
    while Django itself (connecting as the table owner) is unaffected."""
    connection = connections[using]
    if connection.vendor != "postgresql":
        return
    tables = set(connection.introspection.django_table_names(only_existing=True, include_views=False))
    tables.add("django_migrations")
    with connection.cursor() as cursor:
        for table in sorted(tables):
            cursor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} ENABLE ROW LEVEL SECURITY")


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Fired once at the end of every `migrate`, after all apps' tables exist.
        post_migrate.connect(enable_row_level_security, sender=self)
