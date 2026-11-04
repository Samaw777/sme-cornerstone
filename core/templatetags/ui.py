from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.models import EVIDENCE_EXPLANATIONS, Evidence

register = template.Library()

# Path data from Lucide (https://lucide.dev, ISC license).
ICONS = {
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8" /><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />',
    "dashboard": '<rect width="7" height="9" x="3" y="3" rx="1" /><rect width="7" height="5" x="14" y="3" rx="1" /><rect width="7" height="9" x="14" y="12" rx="1" /><rect width="7" height="5" x="3" y="16" rx="1" />',
    "transactions": '<path d="M8 3 4 7l4 4" /><path d="M4 7h16" /><path d="m16 21 4-4-4-4" /><path d="M20 17H4" />',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" />',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" />',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="M10 9H8" /><path d="M16 13H8" /><path d="M16 17H8" />',
    "receipt": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z" /><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8" /><path d="M12 17.5v-11" />',
    "passport": '<path d="M16 10h2" /><path d="M16 14h2" /><path d="M6.17 15a3 3 0 0 1 5.66 0" /><circle cx="9" cy="11" r="2" /><rect x="2" y="5" width="20" height="14" rx="2" />',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" /><path d="m9 12 2 2 4-4" />',
    "landmark": '<line x1="3" x2="21" y1="22" y2="22" /><line x1="6" x2="6" y1="18" y2="11" /><line x1="10" x2="10" y1="18" y2="11" /><line x1="14" x2="14" y1="18" y2="11" /><line x1="18" x2="18" y1="18" y2="11" /><polygon points="12 2 20 7 4 7" />',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" />',
    "info": '<circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />',
    "plus": '<path d="M5 12h14" /><path d="M12 5v14" />',
    "paperclip": '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />',
    "building": '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z" /><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" /><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2" /><path d="M10 6h4" /><path d="M10 10h4" /><path d="M10 14h4" /><path d="M10 18h4" />',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l4 2" />',
    "ban": '<circle cx="12" cy="12" r="10" /><path d="m4.9 4.9 14.2 14.2" />',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" />',
    "trending-up": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" />',
    "trending-down": '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7" /><polyline points="16 17 22 17 22 11" />',
    "wallet": '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1" /><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4" />',
    "gauge": '<path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" />',
    "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" /><circle cx="12" cy="12" r="3" />',
    "flask": '<path d="M10 2v7.527a2 2 0 0 1-.211.896L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.069-10.127A2 2 0 0 1 14 9.527V2" /><path d="M8.5 2h7" /><path d="M7 16h10" />',
    "menu": '<line x1="4" x2="20" y1="12" y2="12" /><line x1="4" x2="20" y1="6" y2="6" /><line x1="4" x2="20" y1="18" y2="18" />',
    "key": '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4" /><path d="m21 2-9.6 9.6" /><circle cx="7.5" cy="15.5" r="5.5" />',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />',
    "plug": '<path d="M12 22v-5" /><path d="M9 8V2" /><path d="M15 8V2" /><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />',
    "log-out": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" x2="9" y1="12" y2="12" />',
    "check": '<path d="M20 6 9 17l-5-5" />',
    "circle-check": '<circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" />',
    "arrow-right": '<path d="M5 12h14" /><path d="m12 5 7 7-7 7" />',
    "arrow-left": '<path d="m12 19-7-7 7-7" /><path d="M19 12H5" />',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />',
    "calendar": '<path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" />',
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />',
}


@register.simple_tag
def icon(name, size=16, css_class=""):
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        'class="icon {1}" aria-hidden="true">{2}</svg>',
        size, css_class, mark_safe(ICONS[name]),
    )


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


@register.filter
def etb(value):
    """ETB 12,345.00 — or a clearly marked 'Unknown' when there's no data."""
    amount = _decimal(value)
    if amount is None:
        return mark_safe('<span class="unknown" title="No records — not zero">Unknown</span>')
    sign = "−" if amount < 0 else ""
    return format_html('<span class="money">{}ETB&nbsp;{}</span>', sign, f"{abs(amount):,.2f}")


@register.filter
def etb_plain(value):
    amount = _decimal(value)
    return "Unknown" if amount is None else f"{'-' if amount < 0 else ''}ETB {abs(amount):,.2f}"


@register.filter
def compact(value):
    amount = _decimal(value)
    if amount is None:
        return "—"
    a = abs(amount)
    sign = "−" if amount < 0 else ""
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.0f}K"
    return f"{sign}{a:.0f}"


@register.filter
def percent(value):
    amount = _decimal(value)
    return "—" if amount is None else f"{amount:.1f}%"


@register.filter
def get(mapping, key):
    return (mapping or {}).get(key)


EVIDENCE_CSS = {
    Evidence.SYSTEM_VERIFIED: "ev-verified",
    Evidence.EVIDENCE_SUPPORTED: "ev-supported",
    Evidence.SELF_REPORTED: "ev-self",
    Evidence.DERIVED: "ev-derived",
}


@register.simple_tag
def evidence_badge(value, demo=False):
    cls = Evidence(value)
    demo_html = mark_safe(' <span class="badge badge-demo" title="Synthetic or mock-connector data">Demo</span>') if demo else ""
    return format_html('<span class="badge {}" title="{}">{}</span>{}', EVIDENCE_CSS[cls], EVIDENCE_EXPLANATIONS[cls], cls.label, demo_html)


@register.filter
def evidence_css(value):
    return EVIDENCE_CSS.get(value, "")


@register.inclusion_tag("_chart.html")
def monthly_chart(monthly, kind="revenue_expenses"):
    """Server-rendered SVG chart. Months without records are drawn as hatched
    'no records' columns rather than zero-height bars."""
    width, height, pad_left, pad_bottom, pad_top = 640, 220, 44, 28, 12
    plot_h = height - pad_bottom - pad_top
    n = max(len(monthly), 1)
    slot = (width - pad_left) / n

    if kind == "net":
        values = [_decimal(m.get("net_cash_movement")) for m in monthly]
        known = [abs(v) for v in values if v is not None] or [Decimal(1)]
        top = max(known) or Decimal(1)
        zero_y = pad_top + plot_h / 2
        bars = []
        for i, (m, v) in enumerate(zip(monthly, values)):
            x = pad_left + i * slot + slot * 0.2
            if v is None:
                if m.get("state") == "no_records":
                    bars.append({"x": x, "y": pad_top, "w": slot * 0.6, "h": plot_h, "cls": "bar-missing", "title": f"{m['label']}: no records"})
                continue
            h = float(abs(v) / top) * (plot_h / 2)
            y = zero_y - h if v >= 0 else zero_y
            bars.append({"x": x, "y": y, "w": slot * 0.6, "h": max(h, 1), "cls": "bar-pos" if v >= 0 else "bar-neg",
                         "title": f"{m['label']}: net {'+' if v >= 0 else '−'}ETB {abs(v):,.0f}"})
        grid = [{"y": zero_y, "label": "0"}, {"y": pad_top, "label": compact(top)}, {"y": pad_top + plot_h, "label": compact(-top)}]
    else:
        rev = [_decimal(m.get("revenue")) for m in monthly]
        exp = [_decimal(m.get("operating_expenses")) for m in monthly]
        top = max([v for v in rev + exp if v is not None] or [Decimal(1)]) or Decimal(1)
        base = pad_top + plot_h
        bars = []
        for i, m in enumerate(monthly):
            x = pad_left + i * slot + slot * 0.14
            w = slot * 0.34
            if rev[i] is None:
                if m.get("state") != "no_records":
                    continue  # before recorded history: nothing to draw
                bars.append({"x": x, "y": pad_top, "w": w * 2 + 2, "h": plot_h, "cls": "bar-missing", "title": f"{m['label']}: no records"})
                continue
            for j, (v, cls, name) in enumerate(((rev[i], "bar-rev", "Revenue"), (exp[i], "bar-exp", "Operating expenses"))):
                h = float(v / top) * plot_h
                bars.append({"x": x + j * (w + 2), "y": base - h, "w": w, "h": max(h, 1), "cls": cls,
                             "title": f"{m['label']}: {name} ETB {v:,.0f}"})
        grid = [{"y": pad_top + plot_h * (1 - f), "label": compact(top * Decimal(str(f)))} for f in (0, 0.5, 1)]

    labels = []
    step = 1 if n <= 12 else 2
    for i, m in enumerate(monthly):
        if i % step == 0:
            labels.append({"x": pad_left + i * slot + slot / 2, "text": m["label"].split()[0][:3],
                           "year": m["label"].split()[-1][-2:]})
    return {"width": width, "height": height, "bars": bars, "grid": grid, "labels": labels, "pad_left": pad_left,
            "label_y": height - 8, "kind": kind}
