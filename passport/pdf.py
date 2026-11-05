"""PDF rendering of a frozen passport snapshot (reportlab, no external services)."""

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#101828")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#e4e7ec")
ACCENT = colors.HexColor("#163a6b")
SOFT = colors.HexColor("#eef3fb")


def _money(value):
    if value is None:
        return "Unknown"
    amount = Decimal(value)
    return f"{'-' if amount < 0 else ''}ETB {abs(amount):,.2f}"


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=19, textColor=INK, alignment=TA_LEFT, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=INK),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.6, textColor=MUTED),
        "notice": ParagraphStyle("notice", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=INK),
    }


def _table(rows, widths, header=True, align_right_from=1):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"))
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.6), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    table.setStyle(TableStyle(style))
    return table


def render(snapshot):
    data = snapshot.data
    s = _styles()
    buffer = io.BytesIO()
    business_name = data.get("profile", {}).get("name") or snapshot.business.name

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"Business Financial Passport · {business_name} · report v{snapshot.version} · method {data['calculation_version']}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        if data.get("includes_demo_data"):
            canvas.setFillColor(colors.HexColor("#c0392b"))
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.drawString(18 * mm, A4[1] - 10 * mm, "CONTAINS DEMO DATA — NOT REAL VERIFICATION")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Business Financial Passport — {business_name}", author="SME Cornerstone")
    width = A4[0] - 36 * mm
    story = [
        Paragraph("Business Financial Passport", s["title"]),
        Paragraph(f"{business_name} · {data['period']['label']}", s["body"]),
        Spacer(1, 6),
    ]
    notice = Table([[Paragraph(data["disclaimer"], s["notice"])]], colWidths=[width])
    notice.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.8, ACCENT),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(notice)

    meta = [
        ["Report version", f"v{snapshot.version} (calculation method {data['calculation_version']})"],
        ["Generated", snapshot.generated_at.strftime("%d %b %Y %H:%M %Z")],
        ["Reporting period", f"{data['period']['label']} ({data['period']['months_with_records']} of {data['period']['months']} months with records)"],
        ["Recorded history", f"{data['history']['available_months']} month(s) since first record {data['history']['first_record'] or '—'}"
         + (" — LIMITED HISTORY" if data["history"]["limited"] else "")],
        ["Currency", "Ethiopian birr (ETB)"],
    ]
    sharing = data.get("sharing")
    if sharing:
        meta += [["Authorised recipient", sharing["recipient"]], ["Sharing purpose", sharing["purpose"]],
                 ["Consent reference", sharing["consent_reference"]]]
    story += [Spacer(1, 6), _table(meta, [42 * mm, width - 42 * mm], header=False, align_right_from=None)]

    if "profile" in data:
        p = data["profile"]
        rows = [["Field", "Value", "Source"],
                ["Business name", p["name"], "Merchant-provided"],
                ["Sector", p["sector"], "Merchant-provided"],
                ["Location", p["location"], "Merchant-provided"],
                ["Operating since", p["operating_since"], p["operating_since_status"]],
                ["TIN", p["tin"]["value"] or "Not provided", p["tin"]["status_label"]],
                ["Trade licence", p["trade_license"]["value"] or "Not provided", p["trade_license"]["status_label"]],
                ["Authorised users", str(p["authorized_users"]), "Platform record"]]
        story += [Paragraph("Business profile", s["h2"]), _table(rows, [40 * mm, 70 * mm, width - 110 * mm], align_right_from=None)]

    if "monthly" in data:
        rows = [["Month", "Revenue", "Operating exp.", "Cash in", "Cash out", "Net cash movement"]]
        for m in data["monthly"]:
            if m["state"] != "recorded":
                note = "No records (unknown)" if m["state"] == "no_records" else "Before recorded history"
                rows.append([m["label"], note, "", "", "", ""])
            else:
                rows.append([m["label"] + (" *" if m["in_progress"] else ""), _money(m["revenue"]), _money(m["operating_expenses"]),
                             _money(m["inflows"]), _money(m["outflows"]), _money(m["net_cash_movement"])])
        t = data["totals"]
        rows.append(["Total (recorded months)", _money(t["revenue"]), _money(t["operating_expenses"]), _money(t["inflows"]),
                     _money(t["outflows"]), _money(t["net_cash_movement"])])
        table = _table(rows, [30 * mm] + [(width - 30 * mm) / 5] * 5)
        table.setStyle(TableStyle([("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8)]))
        story += [Paragraph("Monthly revenue, expenses and cash movement", s["h2"]), table,
                  Paragraph(t["basis"] + " * = month in progress. Revenue less operating expenses "
                            f"for the period: {_money(t['revenue_less_operating_expenses'])} (cash basis; not accounting profit).", s["small"])]
        tr, se, va = data["trend"], data["seasonality"], data["variability"]
        lines = [
            "Revenue trend: " + (f"{tr['change_percent']}% ({tr['recent_months']} average {_money(tr['recent_average'])} vs {tr['prior_months']} {_money(tr['prior_average'])})" if tr["available"] else f"not shown — {tr['reason']}"),
            "Seasonality: " + (f"highest {se['peak']} ({_money(se['peak_revenue'])}), lowest {se['trough']} ({_money(se['trough_revenue'])}). {se['note']}" if se["available"] else f"not shown — {se['reason']}"),
            "Revenue variability (coefficient of variation): " + (f"{va['coefficient_of_variation']} over {va['months']} months" if va["available"] else f"not shown — {va['reason']}"),
        ]
        story += [Spacer(1, 4)] + [Paragraph(line + " [Derived metric]", s["body"]) for line in lines]

    if "credit_metrics" in data:
        cm = data["credit_metrics"]

        def shown(m):
            return f"{m['value']} {m['unit']}".strip() if m["available"] else f"Not calculated — {m['reason']}"

        def inputs(m):
            return " · ".join(f"{i['key'].replace('_', ' ').capitalize()}: {_money(i['value']) if i['unit'] == 'ETB' else i['value']}"
                              for i in m["inputs"]) + (f" · {m['basis']}" if m["basis"] else "")

        rows = [["Metric [Derived]", "Value", "Inputs"]] + [
            [Paragraph(m["label"], s["body"]), Paragraph(shown(m), s["body"]), Paragraph(inputs(m) if m["available"] else "", s["small"])]
            for m in cm["scores"] + cm["metrics"]]
        story += [Paragraph("Credit-relevant metrics", s["h2"]),
                  _table(rows, [45 * mm, 45 * mm, width - 90 * mm], align_right_from=None),
                  Paragraph(cm["note"], s["small"])]

    if "evidence" in data:
        e = data["evidence"]
        rows = [["Evidence class", "Amount", "Share", "Records", "Of which demo"]]
        for row in e["by_class"]:
            rows.append([row["label"], _money(row["amount"]), f"{row['percent']}%" if row["percent"] else "—", str(row["count"]), _money(row["demo_amount"])])
        story += [Paragraph("Evidence breakdown", s["h2"]), _table(rows, [50 * mm, 35 * mm, 25 * mm, 25 * mm, width - 135 * mm]),
                  Paragraph(f"Denominator: {e['denominator']} = {_money(e['denominator_amount'])} across {e['record_count']} records. "
                            f"Linked documents: {e['documents']['linked_documents']} ({e['documents']['reviewed']} reviewed by platform staff, "
                            f"{e['documents']['checks_only']} with file checks only). Derived metrics are not counted as evidence.", s["small"])]
        for source in e["sources"]:
            story.append(Paragraph(f"Connected source: {source['name']} {source['account']}" + (" — DEMO SOURCE, not a real bank connection." if source["is_demo"] else ""), s["small"]))

    if "receivables" in data:
        r, o = data["receivables"], data["obligations"]
        rows = [["Receivables aging", "Amount"]] + [[a["bucket"], _money(a["amount"])] for a in r["aging"]]
        rows.append([f"Total outstanding ({r['open_invoices']} open invoices)", _money(r["outstanding_total"])])
        block = [Paragraph("Recorded receivables and obligations", s["h2"]), _table(rows, [100 * mm, width - 100 * mm]), Paragraph(r["note"], s["small"])]
        if o["items"]:
            orows = [["Obligation", "Type", "Amount", "Frequency", "Monthly equiv."]] + [
                [i["name"], i["type"], _money(i["amount"]), i["frequency"], _money(i["monthly_equivalent"])] for i in o["items"]]
            block += [Spacer(1, 4), _table(orows, [50 * mm, 30 * mm, 32 * mm, 25 * mm, width - 137 * mm], align_right_from=2)]
        block.append(Paragraph(f"{o['note']} Monthly equivalent of current obligations: {_money(o['monthly_equivalent_total'])}.", s["small"]))
        story.append(KeepTogether(block))

    if "data_quality" in data:
        q = data["data_quality"]
        story += [Paragraph("Data-quality indicators", s["h2"]), Paragraph(q["note"], s["small"]),
                  Paragraph(f"Reconciliation: {q['reconciliation']}", s["body"]),
                  Paragraph("Months without records: " + (", ".join(q["missing_months"]) or "none"), s["body"])]
        if q["findings"]:
            rows = [["Indicator", "Detail", "Merchant note"]] + [
                [Paragraph(f["title"], s["body"]), Paragraph(f["detail"], s["small"]), Paragraph(f["merchant_note"] or "—", s["small"])]
                for f in q["findings"]]
            story.append(_table(rows, [55 * mm, 75 * mm, width - 130 * mm], align_right_from=None))

    story += [Paragraph("Calculation methods", s["h2"])]
    story += [Paragraph(f"<b>{m['metric']}.</b> {m['method']}", s["small"]) for m in data["methods"]]
    story += [Paragraph("Material limitations", s["h2"])]
    story += [Paragraph(f"• {line}", s["body"]) for line in data["limitations"]]
    story += [Spacer(1, 8), Paragraph(data["disclaimer"] + " The receiving institution remains responsible for its own credit policy, KYC/AML checks, underwriting and decision.", s["notice"])]
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()
