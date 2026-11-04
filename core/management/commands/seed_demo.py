"""Create synthetic Ethiopian demo data: three businesses, two institutions,
demo accounts, consents in each state, a demo API credential and a pilot.

Every business, institution, document and connector record it creates is
flagged as demo. Run it only against a demo database."""

import datetime as dt
import io
import random
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from django.utils import timezone
from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas

from accounts.models import User
from core.demo import DEMO_PASSWORD
from core.periods import add_months, month_end, month_start, today
from ledger import importing, quality, services
from ledger.connectors import registry
from ledger.models import ImportBatch, Invoice, InvoicePayment, RecurringObligation, Transaction
from merchants.models import Business, BusinessMembership, IdentityStatus, Permission
from passport import services as passport_services
from pilot.models import PilotProgram
from sharing.models import ConsentGrant, DataCategory, FinancialInstitution, InstitutionMembership, PartnerCredential

DEMO_API_KEY_ID = "pk_demo_pilotmfi"
DEMO_API_SECRET = "demo-secret-not-for-production"


def demo_pdf(title, lines):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A6)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20, 380, title)
    c.setFont("Helvetica", 8)
    y = 360
    for line in lines + ["", "SYNTHETIC DEMO DOCUMENT — NOT A REAL RECEIPT"]:
        c.drawString(20, y, line)
        y -= 12
    c.save()
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Load synthetic demo data (businesses, institutions, consents, demo accounts)."

    def handle(self, *args, **options):
        if User.objects.filter(email="admin@demo.cornerstone.et").exists():
            raise CommandError("Demo data already exists. Use a fresh database to reseed.")
        self.rng = random.Random(2026)
        with db_transaction.atomic():
            self.seed()
        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write(f"Sign in with any demo account; password: {DEMO_PASSWORD}")
        self.stdout.write(f"Demo partner API token (Pilot Partner MFI): {DEMO_API_KEY_ID}.{DEMO_API_SECRET}")

    def user(self, email, name, **extra):
        return User.objects.create_user(email=email, password=DEMO_PASSWORD, name=name, **extra)

    def seed(self):
        admin = self.user("admin@demo.cornerstone.et", "Platform Admin", is_platform_admin=True)
        mfi = FinancialInstitution.objects.create(name="Pilot Partner MFI (Demo)", kind="mfi", is_pilot_partner=True, is_demo=True)
        bank = FinancialInstitution.objects.create(name="Second Demo Bank", kind="bank", is_demo=True)
        InstitutionMembership.objects.create(user=self.user("analyst@pilot-mfi.demo", "Hana Tesfaye"), institution=mfi)
        InstitutionMembership.objects.create(user=self.user("analyst@other-bank.demo", "Yonas Bekele"), institution=bank)
        PartnerCredential.issue(mfi, "Demo integration", [s.value for s in PartnerCredential.Scope], admin, days=365,
                                secret=DEMO_API_SECRET, key_id=DEMO_API_KEY_ID)
        PilotProgram.objects.create(
            name="SME passport pilot (demo)", institution=mfi, start_date=add_months(month_start(today()), -1),
            target_merchants=30,
            evaluation_design="Compare analyst review time and follow-up document requests for applications with and "
                              "without a passport, using the MFI's existing review log. (Demo configuration.)",
        )

        abebe = self.abebe_coffee(admin)
        selam = self.selam_tailoring()
        hawassa = self.hawassa_produce()

        now = timezone.now()
        start, end = add_months(month_start(today()), -11), month_end(today())
        ConsentGrant.objects.create(
            business=abebe, institution=mfi, purpose=ConsentGrant.Purpose.FINANCING_APPLICATION,
            purpose_detail="Working-capital application (demo)", categories=[c.value for c in DataCategory],
            period_start=start, period_end=end, expires_at=now + dt.timedelta(days=60),
            granted_by=abebe.memberships.get(role="owner").user, is_demo=True,
        )
        revoked = ConsentGrant.objects.create(
            business=selam, institution=mfi, purpose=ConsentGrant.Purpose.PILOT_EVALUATION,
            categories=[DataCategory.PROFILE, DataCategory.FINANCIAL_SUMMARY], period_start=add_months(start, 6), period_end=end,
            expires_at=now + dt.timedelta(days=30), granted_by=selam.memberships.get(role="owner").user, is_demo=True,
        )
        revoked.revoked_at = now - dt.timedelta(days=2)
        revoked.revoked_by = revoked.granted_by
        revoked.revocation_reason = "Changed my mind for now (demo)"
        revoked.save()
        ConsentGrant.objects.create(
            business=hawassa, institution=bank, purpose=ConsentGrant.Purpose.ACCOUNT_REVIEW,
            categories=[DataCategory.PROFILE, DataCategory.FINANCIAL_SUMMARY, DataCategory.DATA_QUALITY],
            period_start=add_months(start, 3), period_end=add_months(month_end(today()), -1),
            expires_at=now - dt.timedelta(days=5), granted_by=hawassa.memberships.get(role="owner").user, is_demo=True,
        )
        for business in (abebe, selam, hawassa):
            quality.refresh(business)
        passport_services.generate(abebe, start, end, user=abebe.memberships.get(role="owner").user)

    def business(self, owner, **fields):
        business = Business.objects.create(is_demo=True, created_by=owner, **fields)
        business.refresh_identity_statuses()
        business.save()
        BusinessMembership.objects.create(user=owner, business=business, role=BusinessMembership.Role.OWNER)
        return business

    def manual(self, business, user, day, direction, category, amount, description, counterparty="", payment_method=None):
        method = payment_method or (self.rng.choice(["cash", "cash", "telebirr", "bank"]) if direction == "in" else
                                    self.rng.choice(["cash", "telebirr", "bank"]))
        return services.record_manual(business, user, {
            "direction": direction, "category": category, "amount": Decimal(amount).quantize(Decimal("0.01")),
            "currency": "ETB", "occurred_on": day, "description": description, "counterparty": counterparty,
            "payment_method": method, "is_demo": True,
        })

    def abebe_coffee(self, admin):
        rng = self.rng
        owner = self.user("abebe@demo.cornerstone.et", "Abebe Kebede")
        staff = self.user("meron@demo.cornerstone.et", "Meron Alemu")
        business = self.business(
            owner, name="Abebe Coffee Roasters", legal_name="Abebe Kebede Coffee Roasting (Sole Proprietorship)",
            sector="coffee", operating_start_date=dt.date(2019, 3, 1), region="addis_ababa", city="Addis Ababa",
            sub_city="Bole, Woreda 03", phone="+251 911 000 101", tin="0012345678", trade_license_number="AA/BL/14/2019-0457",
        )
        business.tin_status = IdentityStatus.PLATFORM_VALIDATED
        business.identity_validated_by = admin
        business.identity_validated_at = timezone.now()
        business.identity_validation_note = "Checked against TIN certificate copy (demo)"
        business.save()
        BusinessMembership.objects.create(
            user=staff, business=business, role=BusinessMembership.Role.STAFF, added_by=owner,
            permissions=[Permission.RECORD, Permission.DOCUMENTS, Permission.IMPORT, Permission.REPORTS],
        )

        # Bank account via the mock connector: system-verified structure, demo-labelled.
        source = registry.connect(business, owner, "mock_bank")
        registry.sync(source, owner)

        # Cash shop sales and expenses entered by hand, with receipts for some.
        first = add_months(month_start(today()), -12)
        receipt_no = 1000
        for month in [add_months(first, i) for i in range(13)]:
            last = min(month_end(month), today())
            for _ in range(rng.randint(3, 5)):
                day = month + dt.timedelta(days=rng.randint(0, (last - month).days))
                self.manual(business, owner, day, "in", "sales", rng.randint(40, 95) * 100, "Café counter cash sales (week)", "Walk-in customers")
            for desc, cat, low, high, party in (("Packaging and bags", "stock", 25, 60, "Merkato packaging supplier"),
                                                ("Fuel for delivery van", "transport", 15, 35, "Fuel station"),
                                                ("Airtime and data", "telecom", 4, 9, "Mobile operator")):
                day = month + dt.timedelta(days=rng.randint(0, (last - month).days))
                txn = self.manual(business, owner, day, "out", cat, rng.randint(low, high) * 100, desc, party)
                if cat == "stock" and rng.random() < 0.7:
                    receipt_no += 1
                    pdf = demo_pdf(f"Receipt #{receipt_no}", [party, f"Date: {day:%d/%m/%Y}", f"Amount: ETB {txn.amount:,.2f}", desc])
                    upload = SimpleUploadedFile(f"receipt-{receipt_no}.pdf", pdf, content_type="application/pdf")
                    document = services.store_document(business, staff, upload, "receipt", f"Supplier receipt #{receipt_no}", is_demo=True)
                    services.link_document(document, staff, transaction=txn)
            # A large undocumented purchase every few months, so the missing-document check has something to say.
            if month.month % 4 == 0:
                day = month + dt.timedelta(days=min(9, (last - month).days))
                self.manual(business, owner, day, "out", "stock", rng.randint(120, 180) * 100, "Green coffee — cash purchase", "Jimma cooperative agent")

        # A weekend market stall export, imported as CSV (self-reported).
        rows = ["date,type,amount,category,customer,description,reference"]
        for i, month in enumerate([add_months(month_start(today()), -k) for k in (3, 2, 1)]):
            for j in range(4):
                day = month + dt.timedelta(days=5 + j * 7)
                rows.append(f"{day:%d/%m/%Y},in,\"{rng.randint(20, 45) * 100:,}.00\",Sales,Bole Sunday market,Market stall sales,MKT-{month:%y%m}-{j + 1}")
        text = "\n".join(rows) + "\n"
        upload = SimpleUploadedFile("market-stall.csv", text.encode(), content_type="text/csv")
        text, digest = importing.read_upload(upload)
        headers, _ = importing.parse(text)
        mapping = importing.guess_mapping(headers)
        mapping.update({"date_format": "dmy", "direction": "type", "counterparty": "customer"})
        batch = ImportBatch.objects.create(business=business, uploaded_by=owner, filename="market-stall.csv", file_sha256=digest,
                                           raw_text=text, mapping=mapping, status=ImportBatch.Status.MAPPED)
        importing.commit(batch, owner, is_demo=True)

        # A typo corrected with an amendment: the original stays, superseded.
        typo_day = add_months(month_start(today()), -2) + dt.timedelta(days=3)
        typo = self.manual(business, owner, typo_day, "out", "maintenance", "45000", "Grinder repair", "Bole workshop")
        services.amend(typo, owner, {"direction": "out", "category": "maintenance", "amount": Decimal("4500.00"),
                                     "currency": "ETB", "occurred_on": typo_day, "description": "Grinder repair",
                                     "counterparty": "Bole workshop", "reference": ""},
                       "Extra zero typed; workshop invoice shows ETB 4,500")

        # Staff entries waiting for the owner's approval (not counted until approved).
        for days_back, amount, desc in ((1, "6200", "Café counter cash sales (today)"), (2, "1800", "Milk and sugar"),
                                        (3, "950", "Taxi for delivery")):
            day = today() - dt.timedelta(days=days_back)
            self.manual(business, staff, day, "in" if "sales" in desc else "out",
                        "sales" if "sales" in desc else ("stock" if "Milk" in desc else "transport"), amount, desc,
                        payment_method="cash")

        # A dispute on a connector record (can't be amended — only annotated).
        disputed = Transaction.objects.filter(business=business, source_kind="connector", direction="out").order_by("-occurred_on")[3]
        services.annotate(disputed, owner, "dispute", "This supplier payment was refunded the next day; refund not shown in the feed.")

        on = today()
        for number, customer, issued, days, amount, paid in (
            ("INV-2026-031", "Sheraton-area hotel (demo)", on - dt.timedelta(days=70), 30, "38500", None),
            ("INV-2026-034", "Bole office café (demo)", on - dt.timedelta(days=20), 30, "16200", "6000"),
            ("INV-2026-029", "Airport lounge supplier (demo)", on - dt.timedelta(days=95), 30, "22000", "22000"),
        ):
            invoice = Invoice.objects.create(business=business, number=number, customer=customer, issue_date=issued,
                                             due_date=issued + dt.timedelta(days=days), amount=Decimal(amount), created_by=owner, is_demo=True)
            if paid:
                InvoicePayment.objects.create(invoice=invoice, amount=Decimal(paid), paid_on=issued + dt.timedelta(days=25), recorded_by=owner)
                if invoice.outstanding <= 0:
                    invoice.status = Invoice.Status.PAID
                    invoice.save()
        RecurringObligation.objects.create(business=business, name="Roastery rent", counterparty="Bole landlord (demo)", kind="rent",
                                           amount=Decimal("18000"), frequency="monthly", start_date=dt.date(2023, 1, 1), created_by=owner, is_demo=True)
        RecurringObligation.objects.create(business=business, name="Roaster equipment loan", counterparty="Equipment lessor (demo)", kind="loan",
                                           amount=Decimal("19500"), frequency="quarterly", start_date=dt.date(2025, 7, 1),
                                           end_date=dt.date(2027, 6, 30), created_by=owner, is_demo=True)
        return business

    def selam_tailoring(self):
        rng = self.rng
        owner = self.user("selam@demo.cornerstone.et", "Selam Getachew")
        business = self.business(owner, name="Selam Tailoring", sector="textiles", operating_start_date=dt.date(2024, 6, 1),
                                 region="amhara", city="Bahir Dar", sub_city="Kebele 14")
        this_month = month_start(today())
        # Four months of history with a gap: limited history, one missing month.
        for offset in (-4, -3, -1, 0):
            month = add_months(this_month, offset)
            last = min(month_end(month), today())
            for _ in range(rng.randint(4, 7)):
                day = month + dt.timedelta(days=rng.randint(0, (last - month).days))
                self.manual(business, owner, day, "in", "service_income", rng.randint(8, 30) * 100, "Tailoring orders", "Customers")
            self.manual(business, owner, month + dt.timedelta(days=min(2, (last - month).days)), "out", "rent", "3500", "Shop rent", "Landlord")
            self.manual(business, owner, month + dt.timedelta(days=min(10, (last - month).days)), "out", "stock", rng.randint(15, 40) * 100, "Fabric and thread", "Bahir Dar market")
        return business

    def hawassa_produce(self):
        rng = self.rng
        owner = self.user("dawit@demo.cornerstone.et", "Dawit Lemma")
        business = self.business(owner, name="Hawassa Fresh Produce", sector="agriculture", operating_start_date=dt.date(2021, 9, 1),
                                 region="sidama", city="Hawassa", tin="0098765432")
        for offset in range(-8, 0):
            month = add_months(month_start(today()), offset)
            for _ in range(rng.randint(5, 8)):
                day = month + dt.timedelta(days=rng.randint(0, 27))
                self.manual(business, owner, day, "in", "sales", rng.randint(30, 120) * 100, "Vegetable sales to hotels", "Hawassa hotels")
            self.manual(business, owner, month + dt.timedelta(days=4), "out", "stock", rng.randint(80, 200) * 100, "Farm-gate purchases", "Farmers' group")
            self.manual(business, owner, month + dt.timedelta(days=12), "out", "wages", "9000", "Casual labour", "Workers")
        return business
