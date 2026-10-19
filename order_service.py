"""Order-line registration and warehouse receipt logic.

Pure Python, no Streamlit imports, so it can be unit-tested with a fake client.

Rules:
- Registering an order line NEVER changes physical stock.
- Only receive_line() adds stock, once per line (claimed atomically via a
  conditional UPDATE), and it rolls the claim back if the stock write fails.
"""
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


def _get_line(client, line_id):
    res = client.table(TABLE).select("*").eq("id", line_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        raise OrderError("שורת ההזמנה לא נמצאה.")
    return rows[0]


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


def _santaf_adjust(client, length, delta):
    """Compare-and-swap add to santaf_inventory.quantity (guards against races)."""
    for _ in range(5):
        res = (
            client.table("santaf_inventory").select("id,quantity")
            .eq("length", length).limit(1).execute()
        )
        rows = res.data or []
        if not rows:
            raise OrderError(f"לא נמצאה שורת מלאי לאורך {length}.")
        row = rows[0]
        raw = row.get("quantity")
        current = int(raw or 0)
        new_qty = current + delta
        if new_qty < 0:
            raise OrderError(f"העדכון יוריד את המלאי מתחת לאפס (אורך {length}).")
        upd = client.table("santaf_inventory").update({"quantity": new_qty}).eq("id", row["id"])
        upd = upd.is_("quantity", "null") if raw is None else upd.eq("quantity", current)
        if upd.execute().data:
            return new_qty
    raise OrderError("המלאי עודכן במקביל על ידי משתמש אחר. נסו שוב.")


def _led_type_label(line):
    meters = (line.get("length_mm") or 0) / 1000
    return f"{line.get('item_spec') or ''} {meters:g} מ'".strip()


def receive_line(client, line_id, received_qty, received_date, location,
                 santaf_lengths=()):
    """Confirm a warehouse receipt for an ordered line and add the stock once."""
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

    line = _get_line(client, line_id)
    if line.get("status") != "ordered" or line.get("stock_applied"):
        raise OrderError("השורה כבר התקבלה או בוטלה. לא בוצע שינוי במלאי.")
    if line["category"] == "SANTAF" and santaf_lengths and line["length_mm"] not in santaf_lengths:
        raise OrderError(f"אורך סנטף {line['length_mm']} אינו ברשימת המידות הקיימת.")

    # Claim the line. Only one caller can win this conditional update, which
    # prevents a double receipt (double click, two users, page re-run).
    claim = (
        client.table(TABLE)
        .update({
            "status": "received", "stock_applied": True, "received_qty": qty,
            "received_date": received_date, "storage_location": location,
            "updated_at": _now(),
        })
        .eq("id", line_id).eq("status", "ordered").eq("stock_applied", False)
        .execute()
    )
    if not claim.data:
        raise OrderError("השורה כבר התקבלה על ידי משתמש אחר. לא בוצע שינוי במלאי.")

    stock_done = False
    try:
        if line["category"] == "LED":
            client.table("led_inventory").insert({
                "order_number": line.get("supplier_order_ref") or line["customer_order_number"],
                "led_type": _led_type_label(line),
                "quantity": qty,
                "notes": f"הזמנת פרגולה {line['customer_order_number']} | מיקום: {location}",
            }).execute()
            stock_done = True
        else:
            _santaf_adjust(client, line["length_mm"], qty)
            stock_done = True
            client.table("santaf_movements").insert({
                "length": line["length_mm"], "quantity": qty,
                "type": "IN", "date": received_date,
            }).execute()
    except Exception as exc:
        if line["category"] == "SANTAF" and stock_done:
            # Stock was added but the movement log failed: undo the stock add.
            try:
                _santaf_adjust(client, line["length_mm"], -qty)
            except Exception:
                raise OrderError(
                    "שגיאה באמצע הקליטה: המלאי עודכן אך היומן לא נשמר, והשחזור נכשל. "
                    "בדקו ידנית את מלאי הסנטף לפני המשך עבודה."
                ) from exc
        _release_claim(client, line_id)
        if isinstance(exc, OrderError):
            raise
        raise OrderError("הקליטה נכשלה ולא בוצע שינוי במלאי. נסו שוב.") from exc

    return _get_line(client, line_id)


def _release_claim(client, line_id):
    client.table(TABLE).update({
        "status": "ordered", "stock_applied": False, "received_qty": None,
        "received_date": None, "storage_location": None, "updated_at": _now(),
    }).eq("id", line_id).execute()
