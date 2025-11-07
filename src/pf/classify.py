import re
import datetime as dt
from typing import Optional, Tuple

_DATE_RE = re.compile(r"\b(\d{1,2})[\-/](\d{1,2})[\-/](\d{2,4})\b")
_RANGE_RE = re.compile(r"(\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4})\s*[\u2013\-to]+\s*(\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4})", re.IGNORECASE)


def _parse_date(s: str) -> Optional[dt.date]:
    try:
        parts = re.split(r"[\-/]", s)
        if len(parts) != 3:
            return None
        m, d, y = parts
        y = int(y)
        if y < 100:
            y += 2000
        return dt.date(int(y), int(m), int(d))
    except Exception:
        return None


def classify_statement(text: str) -> tuple[str, float]:
    t = text.lower()
    scores = {
        "mortgage": 0,
        "credit_card": 0,
        "brokerage": 0,
        "bank": 0,
    }
    mortgage_cues = ["escrow", "principal", "interest", "mortgage", "loan number"]
    cc_cues = ["minimum payment", "payment due date", "new balance", "credit card"]
    brkg_cues = ["positions", "dividends", "trade date", "gain", "brokerage"]
    bank_cues = ["checking", "savings", "withdrawal", "deposit", "account summary"]

    for cue in mortgage_cues:
        scores["mortgage"] += 1 if cue in t else 0
    for cue in cc_cues:
        scores["credit_card"] += 1 if cue in t else 0
    for cue in brkg_cues:
        scores["brokerage"] += 1 if cue in t else 0
    for cue in bank_cues:
        scores["bank"] += 1 if cue in t else 0

    best_type = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = 0.0 if total == 0 else (scores[best_type] / max(1, max(scores.values(), default=1)))
    return best_type, float(confidence)


def extract_institution_and_account(text: str) -> tuple[Optional[str], Optional[str]]:
    # Simple institution dictionary match
    institutions = [
        "chase", "wells fargo", "bank of america", "american express", "amex", "citi", "fidelity", "vanguard", "charles schwab",
    ]
    low = text.lower()
    inst = None
    for name in institutions:
        if name in low:
            inst = name.title()
            break

    # Masked account patterns
    patterns = [
        r"account ending in\s*(\d{3,4})",
        r"account\s*no\.?\s*\*+\s*(\d{3,4})",
        r"\*{2,}(\d{3,4})",
        r"xxxx\s*(\d{3,4})",
    ]
    last4 = None
    for p in patterns:
        m = re.search(p, low)
        if m:
            last4 = m.group(1)
            break

    masked = f"****{last4}" if last4 else None
    return inst, masked


def infer_statement_period(text: str) -> tuple[dt.date, dt.date]:
    # Try explicit range first
    m = _RANGE_RE.search(text)
    if m:
        d1 = _parse_date(m.group(1))
        d2 = _parse_date(m.group(2))
        if d1 and d2 and d1 <= d2:
            return d1, d2

    # Fallback: pick any date and use its month boundaries
    dates = []
    for m in _DATE_RE.finditer(text):
        d = _parse_date(m.group(0))
        if d:
            dates.append(d)
    if dates:
        ref = max(dates)
    else:
        ref = dt.date.today()
    start = dt.date(ref.year, ref.month, 1)
    if ref.month == 12:
        end = dt.date(ref.year, 12, 31)
    else:
        end = dt.date(ref.year, ref.month + 1, 1) - dt.timedelta(days=1)
    return start, end
