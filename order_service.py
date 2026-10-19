"""Order-line registration and warehouse receipt logic.

Pure Python, no Streamlit imports, so it can be unit-tested with a fake client.

Rules:
- Registering an order line NEVER changes physical stock.
- Only receive_line() adds stock, and it does so by calling the database function
  public.receive_order_line (migrations/001), which runs the whole receipt in ONE
  transaction with a row lock: a repeated or concurrent call for the same line
  fails without touching stock, and any failure rolls everything back.
"""
import re
from datetime import date, datetime, timezone

TABLE = "order_lines"

CATEGORIES = {"LED": "לדים", "SANTAF": "סנטף"}
STATUS_LABELS = {
    "ordered": "הוזמן מהספק",
    "received": "התקבל במחסן",
    "cancelled": "בוטל",
}
DEFAULT_SANTAF_TYPE = "סנטף BH שקוף"


class OrderError(Exception):
    """User-facing error (message is shown to the user as-is)."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value):
    value = (value or "").strip()
    return value or None


def _iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def build_line_payload(
    customer_order_number,
    category,
    quantity,
    length_mm=None,
    item_spec=None,
    supplier_order_ref=None,
    supplier_order_date=None,
    ordered_by=None,
    coordination_date=None,
    notes=None,
    record_as_received=False,
    santaf_lengths=(),
):
    """Validate input and build the row for order_lines. Raises OrderError."""
    order_no = _clean(customer_order_number)
    if not order_no:
        raise OrderError("חובה להזין מספר הזמנת פרגולה.")
    if category not in CATEGORIES:
        raise OrderError("חובה לבחור קטגוריה: לדים או סנטף.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise OrderError("הכמות חייבת להיות מספר שלם.")
    if quantity <= 0:
        raise OrderError("הכמות חייבת להיות גדולה מאפס.")
    if length_mm in (None, ""):
        raise OrderError("חובה להזין אורך.")
    try:
        length_mm = int(length_mm)
    except (TypeError, ValueError):
        raise OrderError("האורך חייב להיות מספר.")
    if length_mm <= 0:
        raise OrderError("האורך חייב להיות גדול מאפס.")
    if category == "SANTAF" and santaf_lengths and length_mm not in santaf_lengths:
        raise OrderError(f"אורך סנטף {length_mm} אינו ברשימת המידות הקיימת.")
    spec = _clean(item_spec)
    if category == "SANTAF" and not spec:
        spec = DEFAULT_SANTAF_TYPE
    if category == "LED" and not spec:
        raise OrderError("חובה להזין סוג לד.")

    return {
        "customer_order_number": order_no,
        "category": category,
        "supplier_order_ref": _clean(supplier_order_ref),
        "supplier_order_date": _iso(supplier_order_date),
        "ordered_by": _clean(ordered_by),
        "item_spec": spec,
        "length_mm": length_mm,
        "quantity": quantity,
        # Registering as "received" is a record only: stock_applied stays False
        # so it can never be confused with (or double-counted as) physical stock.
        "status": "received" if record_as_received else "ordered",
        "stock_applied": False,
        "coordination_date": _iso(coordination_date),
        "notes": _clean(notes),
    }


def create_line(client, payload):
    res = client.table(TABLE).insert(payload).execute()
    return (res.data or [None])[0]


def search_lines(client, order_number=None, status=None, category=None,
                 coordination_date=None, limit=500):
    q = client.table(TABLE).select("*")
    if _clean(order_number):
        q = q.ilike("customer_order_number", f"%{_clean(order_number)}%")
    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    if coordination_date:
        q = q.eq("coordination_date", _iso(coordination_date))
    res = q.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


def update_coordination_date(client, line_id, new_date):
    client.table(TABLE).update({
        "coordination_date": _iso(new_date),
        "updated_at": _now(),
    }).eq("id", line_id).execute()


def cancel_line(client, line_id):
    """Cancel an order line that has not been received. Never deletes."""
    res = (
        client.table(TABLE)
        .update({"status": "cancelled", "updated_at": _now()})
        .eq("id", line_id).eq("status", "ordered").eq("stock_applied", False)
        .execute()
    )
    if not res.data:
        raise OrderError("לא ניתן לבטל: השורה כבר התקבלה, בוטלה או אינה קיימת.")


_ORDERERR = re.compile(r"ORDERERR:([^'\"}]*)")
_AUTH_HINTS = ("jwt", "pgrst301", "pgrst302", "401", "permission denied", "42501")


def _rpc_error_message(exc):
    """Map a database/HTTP error to a safe, user-facing Hebrew message."""
    message = str(getattr(exc, "message", "") or "")
    text = f"{message} {exc}"
    found = _ORDERERR.search(message) or _ORDERERR.search(text)
    if found:
        return found.group(1).strip()
    low = text.lower()
    if any(h in low for h in _AUTH_HINTS):
        return "ההתחברות פגה או שאין הרשאה. התחברו מחדש ונסו שוב. לא בוצע שינוי במלאי."
    return "הקליטה נכשלה ולא בוצע שינוי במלאי. נסו שוב."


def receive_line(client, line_id, received_qty, received_date, location,
                 santaf_lengths=()):
    """Confirm a warehouse receipt for an ordered line and add the stock exactly once."""
    try:
        qty = int(received_qty)
    except (TypeError, ValueError):
        raise OrderError("הכמות שהתקבלה חייבת להיות מספר שלם.")
    if qty <= 0:
        raise OrderError("הכמות שהתקבלה חייבת להיות גדולה מאפס.")
    location = _clean(location)
    if not location:
        raise OrderError("חובה להזין מיקום אחסון.")
    received_date = _iso(received_date) or _iso(date.today())

    try:
        res = client.rpc("receive_order_line", {
            "p_line_id": str(line_id), "p_qty": qty,
            "p_date": received_date, "p_location": location,
        }).execute()
    except Exception as exc:
        raise OrderError(_rpc_error_message(exc)) from exc
    data = res.data
    return data[0] if isinstance(data, list) and data else data
