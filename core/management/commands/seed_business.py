"""Fill one existing business with 12 months of sample transport-and-logistics
records, so its dashboard, passport and sharing screens can be explored with
realistic activity.

    python manage.py seed_business "CornerstonePLC"

Every record, document, invoice and obligation it creates is flagged is_demo
and labelled Demo throughout the app. It refuses to run twice on the same
business; `--remove` deletes only the demo records it created."""

import datetime as dt
import random
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from core.periods import add_months, month_end, month_start, today
from ledger import importing, quality, services
from ledger.connectors import registry
from ledger.models import (
    ConnectedSource, Correction, Document, ImportBatch, Invoice, InvoicePayment, RecurringObligation, SourceEvent,
    Transaction,
)
from merchants.models import Business

from .seed_demo import demo_pdf

MONTHS = 12


class Command(BaseCommand):
    help = "Add 12 months of demo transport-and-logistics records to an existing business."

    def add_arguments(self, parser):
        parser.add_argument("business", help="The business name, exactly as on its profile (case doesn't matter).")
        parser.add_argument("--remove", action="store_true", help="Delete the demo records this command created.")
        parser.add_argument("--no-bank", action="store_true", help="Don't connect the demo bank source.")

    def handle(self, *args, **options):
        matches = list(Business.objects.filter(name__iexact=options["business"].strip()))
        if not matches:
            raise CommandError(f"No business named “{options['business']}”. Check the name on its Profile page.")
        if len(matches) > 1:
            raise CommandError("More than one business has that name; rename one first.")
        business = matches[0]
        owner = business.memberships.filter(role="owner", is_active=True).select_related("user").first()
        if owner is None:
            raise CommandError("That business has no owner.")
        if options["remove"]:
            self.remove(business)
            return
        if Transaction.objects.filter(business=business, is_demo=True).exists():
            raise CommandError("This business already has demo records. Run with --remove first to start over.")
        self.rng = random.Random(f"{business.public_id}")
        with db_transaction.atomic():
            counts = self.seed(business, owner.user, connect_bank=not options["no_bank"])
            quality.refresh(business)
        self.stdout.write(self.style.SUCCESS(f"Added demo data to {business.name}:"))
        for label, n in counts.items():
            self.stdout.write(f"  {n:>4} {label}")
        self.stdout.write("Everything is labelled Demo. Remove it any time with --remove.")

    # ---------- helpers ----------

    def record(self, business, user, day, direction, category, amount, description, counterparty="", reference=""):
        if direction == "in":
            method = self.rng.choice(["bank", "bank", "telebirr", "cash"])
        else:
            method = "cash" if category in ("transport", "maintenance") else self.rng.choice(["bank", "telebirr"])
        return services.record_manual(business, user, {
            "direction": direction, "category": category, "amount": Decimal(amount).quantize(Decimal("0.01")),
            "currency": "ETB", "occurred_on": day, "description": description, "counterparty": counterparty,
            "reference": reference, "payment_method": method, "is_demo": True,
        })

    def day_in(self, month, low=0, high=27):
        last = min(month_end(month), today())
        return month + dt.timedelta(days=min(self.rng.randint(low, high), (last - month).days))

    def receipt(self, business, user, txn, number, issuer):
        pdf = demo_pdf(f"Receipt {number}", [issuer, f"Date: {txn.occurred_on:%d/%m/%Y}", f"Amount: ETB {txn.amount:,.2f}",
                                             txn.description])
        upload = SimpleUploadedFile(f"{number}.pdf", pdf, content_type="application/pdf")
        document = services.store_document(business, user, upload, "receipt", f"{issuer} — {number}", is_demo=True)
        services.link_document(document, user, transaction=txn)

    # ---------- the story ----------

    def seed(self, business, user, connect_bank):
        rng = self.rng
        months = [add_months(month_start(today()), -i) for i in range(MONTHS - 1, -1, -1)]
        shippers = ["Merkato wholesale traders", "Kality warehouse cooperative", "Adama flour mill",
                    "Modjo dry port agent", "Bole construction supplier"]
        receipt_no = 0
        counts = {}

        for i, month in enumerate(months):
            # Freight income: busier from October to January (harvest and import season).
            busy = 1.3 if month.month in (10, 11, 12, 1) else 1.0
            for _ in range(rng.randint(7, 10)):
                shipper = rng.choice(shippers)
                amount = Decimal(rng.randint(90, 220) * 100) * Decimal(str(busy))
                self.record(business, user, self.day_in(month), "in", "service_income", amount.quantize(Decimal("1")),
                            "Freight delivery — Addis to regions", shipper)
            # Monthly running costs.
            fuel = self.record(business, user, self.day_in(month, 2, 8), "out", "transport", rng.randint(250, 420) * 100,
                               "Diesel for 3 trucks", "Fuel station, Kality")
            if rng.random() < 0.6:
                receipt_no += 1
                self.receipt(business, user, fuel, f"FUEL-{receipt_no:04d}", "Fuel station, Kality")
            self.record(business, user, self.day_in(month, 25, 27), "out", "wages", "36000", "Driver and loader wages", "Staff")
            self.record(business, user, self.day_in(month, 0, 3), "out", "rent", "15000", "Truck yard and office rent", "Merkato landlord")
            self.record(business, user, self.day_in(month, 10, 20), "out", "telecom", rng.randint(12, 20) * 100,
                        "Airtime and GPS trackers", "Mobile operator")
            if i % 3 == 0:
                repair = self.record(business, user, self.day_in(month, 5, 20), "out", "maintenance", rng.randint(80, 220) * 100,
                                     "Truck service and tyres", "Kality garage")
                receipt_no += 1
                self.receipt(business, user, repair, f"GAR-{receipt_no:04d}", "Kality garage")
            if i % 3 == 1:
                self.record(business, user, self.day_in(month, 1, 5), "out", "loan_repayment", "45000",
                            "Truck loan instalment", "Equipment lender")
            if month.month == 7:
                self.record(business, user, self.day_in(month, 5, 10), "out", "taxes_fees", rng.randint(150, 250) * 100,
                            "Annual road fund, licence and inspection", "Transport authority")
        # One large undocumented purchase, so the data-quality check has something to show.
        self.record(business, user, self.day_in(months[-4], 12, 14), "out", "maintenance", "64000",
                    "Engine overhaul — paid cash", "Independent mechanic")
        # Owner put money in once to cover the engine overhaul.
        self.record(business, user, self.day_in(months[-4], 10, 11), "in", "owner_capital", "50000",
                    "Owner top-up for engine repair", "Owner")

        # A typo fixed with a correction: the original stays, marked superseded.
        typo_day = self.day_in(months[-2], 6, 8)
        typo = self.record(business, user, typo_day, "in", "service_income", "95000", "Freight — Adama round trip", "Adama flour mill")
        services.amend(typo, user, {"direction": "in", "category": "service_income", "amount": Decimal("9500"), "currency": "ETB",
                                    "occurred_on": typo_day, "description": "Freight — Adama round trip",
                                    "counterparty": "Adama flour mill", "reference": ""},
                       "Extra zero typed; delivery note shows ETB 9,500")

        # A dispatcher's spreadsheet of small local deliveries, imported as CSV (self-reported).
        rows = ["date,type,amount,category,customer,description,reference"]
        for month in months[-3:]:
            for j in range(4):
                day = self.day_in(month, 3 + j * 6, 4 + j * 6)
                rows.append(f"{day:%d/%m/%Y},in,\"{rng.randint(15, 40) * 100:,}.00\",Service income,Merkato shops,"
                            f"Local delivery run,DSP-{day:%y%m%d}-{j + 1}")
        text, digest = importing.read_upload(SimpleUploadedFile("dispatch-log.csv", ("\n".join(rows) + "\n").encode()))
        headers, _ = importing.parse(text)
        mapping = {**importing.guess_mapping(headers), "date_format": "dmy", "direction": "type", "counterparty": "customer"}
        batch = ImportBatch.objects.create(business=business, uploaded_by=user, filename="dispatch-log.csv (demo)",
                                           file_sha256=digest, raw_text=text, mapping=mapping, status=ImportBatch.Status.MAPPED)
        importing.commit(batch, user, is_demo=True)

        # Invoices for shippers on credit: one paid, one part-paid, one overdue.
        on = today()
        for number, customer, days_ago, amount, paid in (
            ("CPLC-INV-101", "Modjo dry port agent", 75, "84000", None),
            ("CPLC-INV-108", "Kality warehouse cooperative", 18, "42500", "20000"),
            ("CPLC-INV-097", "Bole construction supplier", 100, "56000", "56000"),
        ):
            issued = on - dt.timedelta(days=days_ago)
            invoice = Invoice.objects.create(business=business, number=number, customer=customer, issue_date=issued,
                                             due_date=issued + dt.timedelta(days=30), amount=Decimal(amount),
                                             created_by=user, is_demo=True)
            if paid:
                InvoicePayment.objects.create(invoice=invoice, amount=Decimal(paid), paid_on=issued + dt.timedelta(days=28),
                                              recorded_by=user)
                if invoice.outstanding <= 0:
                    invoice.status = Invoice.Status.PAID
                    invoice.save()

        for name, kind, party, amount, freq, start in (
            ("Truck loan", "loan", "Equipment lender", "45000", "quarterly", months[1] + dt.timedelta(days=2)),
            ("Yard and office rent", "rent", "Merkato landlord", "15000", "monthly", dt.date(2022, 1, 1)),
            ("Fleet insurance", "other", "Insurance company", "60000", "annual", dt.date(2024, 7, 1)),
        ):
            RecurringObligation.objects.create(business=business, name=name, kind=kind, counterparty=party,
                                               amount=Decimal(amount), frequency=freq, start_date=start,
                                               created_by=user, is_demo=True, notes="Demo")

        if connect_bank:
            source = registry.connect(business, user, "mock_bank")
            registry.sync(source, user)

        counts["transactions"] = Transaction.objects.filter(business=business, is_demo=True).count()
        counts["documents"] = Document.objects.filter(business=business, is_demo=True).count()
        counts["invoices"] = Invoice.objects.filter(business=business, is_demo=True).count()
        counts["obligations"] = RecurringObligation.objects.filter(business=business, is_demo=True).count()
        return counts

    def remove(self, business):
        """Delete only demo-flagged data. Append-only tables (source events,
        corrections) are removed with raw deletes because this is test data."""
        with db_transaction.atomic():
            txns = Transaction.objects.filter(business=business, is_demo=True)
            ids = list(txns.values_list("pk", flat=True))
            event_ids = set(txns.exclude(source_event=None).values_list("source_event_id", flat=True))
            batch_ids = set(txns.exclude(import_batch=None).values_list("import_batch_id", flat=True))
            source_ids = set(ConnectedSource.objects.filter(business=business, is_demo=True).values_list("pk", flat=True))
            Correction.objects.filter(original_id__in=ids)._raw_delete(Correction.objects.db)
            Transaction.objects.filter(pk__in=ids).delete()
            SourceEvent.objects.filter(pk__in=event_ids)._raw_delete(SourceEvent.objects.db)
            SourceEvent.objects.filter(connected_source_id__in=source_ids)._raw_delete(SourceEvent.objects.db)
            ImportBatch.objects.filter(pk__in=batch_ids).delete()
            ConnectedSource.objects.filter(pk__in=source_ids).delete()
            Document.objects.filter(business=business, is_demo=True).delete()
            Invoice.objects.filter(business=business, is_demo=True).delete()
            RecurringObligation.objects.filter(business=business, is_demo=True).delete()
            quality.refresh(business)
        self.stdout.write(self.style.SUCCESS(f"Removed demo records from {business.name}."))
