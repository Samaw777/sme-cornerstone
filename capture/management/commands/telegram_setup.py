"""Point the Telegram bot at this site: `python manage.py telegram_setup https://your-site.vercel.app`."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from capture import telegram


class Command(BaseCommand):
    help = "Register the Telegram webhook for this site."

    def add_arguments(self, parser):
        parser.add_argument("site_url", help="Your public site address, e.g. https://sme-cornerstone.vercel.app")

    def handle(self, *args, site_url, **options):
        if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_WEBHOOK_SECRET):
            raise CommandError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET first.")
        if not site_url.startswith("https://"):
            raise CommandError("Telegram needs an https:// address.")
        url = site_url.rstrip("/") + "/telegram/webhook/"
        result = telegram.call("setWebhook", url=url, secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
                               allowed_updates='["message","edited_message"]')
        if not result.get("ok"):
            raise CommandError(f"Telegram refused: {result}")
        me = telegram.call("getMe")["result"]
        self.stdout.write(self.style.SUCCESS(f"Webhook set to {url} for @{me['username']}."))
        self.stdout.write(f"Set TELEGRAM_BOT_USERNAME={me['username']} so the app can show a link to the bot.")
