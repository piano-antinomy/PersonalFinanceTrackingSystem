import re
import datetime as dt
from typing import List, Dict, Tuple

from dateutil import parser as dateparser


_AMOUNT_RE = re.compile(r"(?P<sign>-)?\$?(?P<val>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})")
_DATE_LINE_RE = re.compile(r"^(?P<date>\d{1,2}[\-/]\d{1,2}(?:[\-/]\d{2,4})?)\s+(?P<rest>.+)$")


def _parse_amount(s: str) -> float | None:
    m = _AMOUNT_RE.search(s)
    if not m:
        return None
    val = float(m.group("val").replace(",", ""))
    if m.group("sign"):
        val = -val
    # Heuristic: amounts with trailing '-' also indicate negative
    if s.strip().endswith("-"):
        val = -abs(val)
    return val


def _parse_date_from_fragment(s: str, default_year: int) -> dt.date | None:
    try:
        dtm = dateparser.parse(s, default=dt.datetime(default_year, 1, 1))
        return dt.date(dtm.year, dtm.month, dtm.day)
    except Exception:
        return None


def extract_transactions_from_text(pages_text: List[str]) -> List[Dict]:
    # Very naive heuristic: look for lines that start with a date, then a description, and somewhere an amount
    # This will not perfectly parse all providers but gets us started.
    lines: List[str] = []
    for page in pages_text:
        for ln in page.splitlines():
            if ln.strip():
                lines.append(ln.strip())

    # Infer a default year from any 4-digit year in the doc
    default_year = dt.date.today().year
    year_match = re.search(r"\b(20\d{2})\b", "\n".join(lines))
    if year_match:
        default_year = int(year_match.group(1))

    txns: List[Dict] = []
    for ln in lines:
        m = _DATE_LINE_RE.match(ln)
        if not m:
            continue
        date_part = m.group("date")
        rest = m.group("rest")
        amount = _parse_amount(rest)
        if amount is None:
            amount = _parse_amount(ln)
        if amount is None:
            continue
        posted_at = _parse_date_from_fragment(date_part, default_year)
        if not posted_at:
            continue

        description = re.sub(_AMOUNT_RE, "", rest).strip()
        description = re.sub(r"\s{2,}", " ", description)
        txn = {
            "posted_at": posted_at,
            "description": description or "Transaction",
            "amount": amount,
        }
        txns.append(txn)

    return txns


def categorize_transaction(db, txn: Dict) -> Tuple[int | None, Dict[str, bool]]:
    desc = (txn.get("description") or "").lower()
    amount = float(txn.get("amount") or 0)

    is_income = amount > 0
    is_expense = amount < 0

    # Simple keyword-based rules
    mapping = [
        ("amazon", ("Shopping", "expense")),
        ("target", ("Shopping", "expense")),
        ("walmart", ("Shopping", "expense")),
        ("whole foods", ("Groceries", "expense")),
        ("trader joe", ("Groceries", "expense")),
        ("costco", ("Groceries", "expense")),
        ("uber", ("Transport", "expense")),
        ("lyft", ("Transport", "expense")),
        ("shell", ("Transport", "expense")),
        ("exxon", ("Transport", "expense")),
        ("starbucks", ("Dining", "expense")),
        ("restaurant", ("Dining", "expense")),
        ("mcdonald", ("Dining", "expense")),
        ("mortgage", ("Mortgage", "expense")),
        ("payroll", ("Income:Salary", "income")),
        ("salary", ("Income:Salary", "income")),
        ("dividend", ("Income:Dividend", "income")),
        ("interest", ("Income:Dividend", "income")),
    ]

    chosen: Tuple[str, str] | None = None
    for key, cat in mapping:
        if key in desc:
            chosen = cat
            break

    if chosen is None:
        chosen_name, chosen_type = ("Uncategorized", "expense" if is_expense else "income")
    else:
        chosen_name, chosen_type = chosen

    try:
        category_id = db.get_or_create_category(chosen_name, chosen_type)
    except Exception:
        category_id = None

    return category_id, {"is_income": is_income, "is_expense": is_expense}
