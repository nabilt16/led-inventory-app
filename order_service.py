"""Order-line logic for the personal warehouse app.

Pure Python (no Streamlit), so it is unit-testable with a fake client.

Rules (enforced again by the database, see migrations/001_order_lines.sql):
- Registering an order line or changing its supplier status NEVER changes stock.
- Supplier status (NULL=לא הוזמן / ordered / pending / received) is separate from stock.
- Standard Santaf stock changes only through pick/unpick (available -> staged, deducted
  immediately) and issuance logs the movement WITHOUT deducting a second time.
- Customer-specific Santaf and supplier-prepared LED packages never touch general stock.
- A LED package = lines of one order with the same package_no; they share status,
  receipt and location and are always updated together.
"""
import re
from datetime import date, datetime, timezone

TABLE = "order_lines"

CATEGORIES = {"LED": "לדים", "SANTAF": "סנטף"}
STATUS_LABELS = {None: "לא הוזמן", "ordered": "הוזמן", "pending": "בהמתנה", "received": "התקבל"}
STATUS_VALUES = [None, "ordered", "pending", "received"]
SUPPLY_LABELS = {"stock": "מהמלאי הרגיל", "supplier": "הוזמן במיוחד ללקוח"}
DEFAULT_SANTAF_TYPE = "סנטף BH"
STANDARD_COLOR = "שקוף"


class OrderError(Exception):
    """User-facing error (message is shown to the user as-is)."""


# ---------------------------------------------------------------- helpers
def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value):
    if value is None:
        return None
    value = value.strip() if isinstance(value, str) else str(value).strip()
    return value or None


def _iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _int(value, label, minimum=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise OrderError(f"{label} חייב להיות מספר שלם.")
    if number < minimum:
        raise OrderError(f"{label} חייב להיות גדול מאפס.")
    return number


def is_package(line):
    """LED lines are grouped into sealed supplier packages."""
    return line.get("category") == "LED"


def package_filter(query, line):
    """Restrict an update/select to the line's package (LED) or to the single line."""
    if is_package(line):
        return (query.eq("customer_order_number", line["customer_order_number"])
                .eq("category", "LED").eq("package_no", line["package_no"]))
    return query.eq("id", line["id"])


# ---------------------------------------------------------------- friendly errors
_ORDERERR = re.compile(r"ORDERERR:([^'\"}]*)")
_AUTH_HINTS = ("jwt", "pgrst301", "pgrst302", "401", "permission denied", "42501")
_CONSTRAINT_MESSAGES = {
    "ol_received_needs_date": 'סטטוס "התקבל" דורש תאריך קבלה.',
    "ol_date_only_if_received": "תאריך קבלה נשמר רק כשהסטטוס הוא התקבל.",
    "ol_issue_needs_recipient": "חובה להזין שם מקבל.",
    "ol_issue_requires_ready": "אפשר לנפק רק שורה שהתקבלה (או שנאספה במלואה).",
    "ol_stock_not_ordered": "שורה מהמלאי הרגיל לא מוזמנת מספק.",
    "ol_stock_standard": "סנטף מהמלאי הרגיל הוא שקוף בלבד.",
    "ol_led_is_supplier": "לדים מוזמנים תמיד מהספק.",
}


def friendly_error(exc):
    """Map a database/HTTP error to a safe, user-facing Hebrew message."""
    if isinstance(exc, OrderError):
        return str(exc)
    message = str(getattr(exc, "message", "") or "")
    text = f"{message} {exc}"
    found = _ORDERERR.search(message) or _ORDERERR.search(text)
    if found:
        return found.group(1).strip()
    for name, msg in _CONSTRAINT_MESSAGES.items():
        if name in text:
            return msg
    low = text.lower()
    if any(h in low for h in _AUTH_HINTS):
        return "ההתחברות פגה או שאין הרשאה. התחברו מחדש ונסו שוב. לא בוצע שינוי."
    return "הפעולה נכשלה ולא בוצע שינוי. נסו שוב."


def _run(fn):
    try:
        return fn()
    except OrderError:
        raise
    except Exception as exc:
        raise OrderError(friendly_error(exc)) from exc


# ---------------------------------------------------------------- creating lines
def build_line_payload(
    customer_order_number, category, quantity, length_mm, item_spec=None, color=None,
    supply=None, package_no=1, supplier_status=None, supplier_ref=None,
    supplier_order_date=None, received_date=None, storage_location=None,
    coordination_date=None, notes=None, santaf_lengths=(),
):
    """Validate form input and build the order_lines row. Raises OrderError."""
    order_no = _clean(customer_order_number)
    if not order_no:
        raise OrderError("חובה להזין מספר הזמנת פרגולה.")
    if category not in CATEGORIES:
        raise OrderError("חובה לבחור קטגוריה: לדים או סנטף.")
    quantity = _int(quantity, "הכמות")
    if length_mm in (None, ""):
        raise OrderError("חובה להזין אורך.")
    length_mm = _int(length_mm, "האורך")
    if supplier_status not in STATUS_VALUES:
        raise OrderError("סטטוס הזמנה לא תקין.")

    spec = _clean(item_spec)
    if category == "LED":
        supply, color = "supplier", None
        if not spec:
            raise OrderError("חובה להזין סוג לד.")
        package_no = _int(package_no, "מספר חבילה")
    else:
        package_no = 1
        spec = spec or DEFAULT_SANTAF_TYPE
        if supply not in SUPPLY_LABELS:
            raise OrderError("חובה לבחור מקור סנטף: מהמלאי הרגיל או הוזמן במיוחד.")
        color = _clean(color) or STANDARD_COLOR
        if supply == "stock":
            if color != STANDARD_COLOR:
                raise OrderError("סנטף צבעוני אינו במלאי הרגיל. בחרו 'הוזמן במיוחד ללקוח'.")
            if santaf_lengths and length_mm not in santaf_lengths:
                raise OrderError(f"אורך {length_mm} אינו אחד ממידות המלאי הרגיל. בחרו 'הוזמן במיוחד ללקוח'.")
            supplier_status = None

    received = _iso(received_date)
    if supplier_status == "received" and not received:
        raise OrderError('סטטוס "התקבל" דורש תאריך קבלה.')
    if supplier_status != "received":
        received, storage_location = None, None

    return {
        "customer_order_number": order_no, "category": category, "supply": supply,
        "package_no": package_no, "item_spec": spec, "color": color,
        "length_mm": length_mm, "quantity": quantity,
        "supplier_ref": _clean(supplier_ref), "supplier_order_date": _iso(supplier_order_date),
        "supplier_status": supplier_status, "coordination_date": _iso(coordination_date),
        "received_date": received, "storage_location": _clean(storage_location),
        "notes": _clean(notes),
    }


def create_line(client, payload):
    """Insert a line. A new LED line joins its package (inherits its status/receipt/location);
    a line without a coordination date inherits the order's date."""
    payload = dict(payload)

    def go():
        siblings = (client.table(TABLE).select("*")
                    .eq("customer_order_number", payload["customer_order_number"]).execute().data or [])
        if payload["category"] == "LED":
            pack = [s for s in siblings if s["category"] == "LED" and s["package_no"] == payload["package_no"]]
            if pack:
                if any(s.get("issued_on") for s in pack):
                    raise OrderError("החבילה כבר נופקה. הוסיפו את השורה לחבילה אחרת.")
                first = pack[0]
                for key in ("supplier_status", "supplier_ref", "supplier_order_date", "received_date",
                            "storage_location", "staging_location"):
                    payload[key] = first.get(key)
        if not payload.get("coordination_date"):
            payload["coordination_date"] = next(
                (s["coordination_date"] for s in siblings if s.get("coordination_date")), None)
        res = client.table(TABLE).insert(payload).execute()
        return (res.data or [None])[0]
    return _run(go)


# ---------------------------------------------------------------- reading
def search_lines(client, order_number=None, status=None, category=None,
                 coordination_date=None, limit=500):
    """status: None = any, 'none' = לא הוזמן (NULL), or 'ordered'/'pending'/'received'."""
    def go():
        q = client.table(TABLE).select("*")
        if _clean(order_number):
            q = q.ilike("customer_order_number", f"%{_clean(order_number)}%")
        if status == "none":
            q = q.is_("supplier_status", "null")
        elif status:
            q = q.eq("supplier_status", status)
        if category:
            q = q.eq("category", category)
        if coordination_date:
            q = q.eq("coordination_date", _iso(coordination_date))
        return q.order("created_at", desc=True).limit(limit).execute().data or []
    return _run(go)


def stock_levels(client):
    """Available standard Santaf stock per length."""
    def go():
        rows = client.table("santaf_inventory").select("length,quantity").execute().data or []
        return {int(r["length"]): int(r.get("quantity") or 0) for r in rows if r.get("length") is not None}
    return _run(go)


# ---------------------------------------------------------------- updating (plain, constraint-protected)
def _require_open(line):
    if line.get("issued_on"):
        raise OrderError("השורה כבר נופקה ולא ניתנת לשינוי.")


def update_supplier_info(client, line, status, supplier_ref=None, supplier_order_date=None,
                         received_date=None, storage_location=None):
    """Set supplier status/details. LED: the whole package; otherwise the single line.
    Never touches stock."""
    _require_open(line)
    if line.get("supply") == "stock":
        raise OrderError("שורה מהמלאי הרגיל לא מוזמנת מספק.")
    if status not in STATUS_VALUES:
        raise OrderError("סטטוס הזמנה לא תקין.")
    received = _iso(received_date)
    if status == "received" and not received:
        raise OrderError('סטטוס "התקבל" דורש תאריך קבלה.')
    changes = {
        "supplier_status": status, "supplier_ref": _clean(supplier_ref),
        "supplier_order_date": _iso(supplier_order_date),
        "received_date": received if status == "received" else None,
        "storage_location": _clean(storage_location) if status == "received" else None,
        "updated_at": _now(),
    }

    def go():
        res = package_filter(client.table(TABLE).update(changes), line).is_("issued_on", "null").execute()
        if not res.data:
            raise OrderError("לא עודכנה אף שורה.")
        return res.data
    return _run(go)


def set_location(client, line, field, value):
    """Record an actual location change. field: storage_location | staging_location."""
    if field not in ("storage_location", "staging_location"):
        raise OrderError("שדה מיקום לא תקין.")
    _require_open(line)
    value = _clean(value)
    if not value:
        raise OrderError("חובה להזין מיקום.")
    if field == "storage_location" and line.get("supply") == "supplier" and line.get("supplier_status") != "received":
        raise OrderError("מיקום אחסון נקבע אחרי שהסחורה התקבלה.")

    def go():
        res = (package_filter(client.table(TABLE).update({field: value, "updated_at": _now()}), line)
               .is_("issued_on", "null").execute())
        if not res.data:
            raise OrderError("לא עודכנה אף שורה.")
    return _run(go)


def set_coordination_date(client, order_number, new_date):
    """Customer coordination date applies to the whole order. Nothing is moved:
    physical location changes are recorded explicitly with set_location."""
    def go():
        (client.table(TABLE).update({"coordination_date": _iso(new_date), "updated_at": _now()})
         .eq("customer_order_number", order_number).is_("issued_on", "null").execute())
    return _run(go)


def update_notes(client, line_id, notes):
    return _run(lambda: client.table(TABLE).update({"notes": _clean(notes), "updated_at": _now()})
                .eq("id", line_id).execute())


def delete_line(client, line_id):
    """Delete a mistaken line. Only lines with no stock effect (not picked, not issued)."""
    def go():
        res = (client.table(TABLE).delete().eq("id", line_id).eq("picked_qty", 0)
               .is_("issued_on", "null").execute())
        if not res.data:
            raise OrderError("אי אפשר למחוק: השורה נאספה, נופקה או אינה קיימת.")
    return _run(go)


# ---------------------------------------------------------------- stock-changing actions (atomic DB functions)
def pick_line(client, line_id, qty, pick_date, staging_location):
    qty = _int(qty, "הכמות לאיסוף")
    if not _clean(staging_location):
        raise OrderError("חובה להזין מיקום הכנה.")
    return _rpc(client, "pick_stock_line", {"p_line_id": str(line_id), "p_qty": qty,
                                            "p_date": _iso(pick_date) or _iso(date.today()),
                                            "p_staging": _clean(staging_location)})


def unpick_line(client, line_id, on_date=None):
    return _rpc(client, "unpick_stock_line", {"p_line_id": str(line_id),
                                              "p_date": _iso(on_date) or _iso(date.today())})


def issue_lines(client, line_ids, issue_date, recipient):
    if not _clean(recipient):
        raise OrderError("חובה להזין שם מקבל.")
    return _rpc(client, "issue_order_lines", {"p_ids": [str(i) for i in line_ids],
                                              "p_date": _iso(issue_date) or _iso(date.today()),
                                              "p_recipient": _clean(recipient)})


# ---- legacy pages: the same atomic database operations (no read-modify-write from the app)
def receive_standard_santaf(client, quantities, receipt_date):
    """Add received quantities to standard Santaf stock. quantities: {length_mm: qty}. One atomic
    operation; an identical repeat within seconds is refused. Returns the total received."""
    items = [(int(l), _int(q, "כמות הקליטה")) for l, q in quantities.items()]
    if not items:
        raise OrderError("לא הוזנה שום כמות.")
    return _rpc(client, "santaf_receive_stock", {
        "p_lengths": [l for l, _ in items], "p_qtys": [q for _, q in items],
        "p_date": _iso(receipt_date) or _iso(date.today())})


def issue_standard_santaf(client, order_number, length_mm, qty, issue_date):
    """Direct issue of standard Santaf (legacy page). Atomic; refused if the order already has a
    planned stock line of that length (it must be picked/issued in the orders page instead)."""
    if not _clean(order_number):
        raise OrderError("חובה להזין מספר הזמנת פרגולה.")
    return _rpc(client, "santaf_issue_direct", {
        "p_order": _clean(order_number), "p_length": _int(length_mm, "האורך"),
        "p_qty": _int(qty, "כמות הניפוק"), "p_date": _iso(issue_date) or _iso(date.today())})


def issue_led_stock(client, led_row_id, qty):
    """Issue legacy LED stock atomically (never negative, no lost update). Returns the new balance."""
    return _rpc(client, "led_issue_stock", {"p_id": int(led_row_id), "p_qty": _int(qty, "כמות הניפוק")})


def issue_target_ids(client, line):
    """Ids to issue together: the whole LED package, or the single line."""
    if not is_package(line):
        return [line["id"]]
    rows = _run(lambda: package_filter(client.table(TABLE).select("id"), line).execute().data or [])
    return [r["id"] for r in rows]


def _rpc(client, name, params):
    def go():
        data = client.rpc(name, params).execute().data
        return data[0] if isinstance(data, list) and data else data
    return _run(go)


# ---------------------------------------------------------------- daily preparation (pure)
def line_state(line, shortages=None):
    """('issued'|'ready'|'missing', label) for one order line."""
    if line.get("issued_on"):
        return "issued", "נופק"
    if line.get("supply") == "stock":
        if line["picked_qty"] >= line["quantity"]:
            return "ready", "נאסף ומוכן"
        if shortages and line["length_mm"] in shortages:
            return "missing", "חסר במלאי"
        return "missing", "ממתין לאיסוף"
    if line.get("supplier_status") == "received":
        return "ready", "התקבל במחסן"
    return "missing", "לא התקבל: " + STATUS_LABELS[line.get("supplier_status")]


def daily_summary(lines, stock):
    """Group lines of one day by order, compute stock shortages per length and readiness.

    Stock sufficiency is checked per length across ALL orders of the day (they compete for
    the same available stock)."""
    need = {}
    for l in lines:
        if l["supply"] == "stock" and not l.get("issued_on"):
            need[l["length_mm"]] = need.get(l["length_mm"], 0) + (l["quantity"] - l["picked_qty"])
    shortages = {}
    for length, required in need.items():
        available = stock.get(length, 0)
        if required > available:
            shortages[length] = {"length": length, "required": required, "available": available,
                                 "shortfall": required - available}
    orders = {}
    for l in lines:
        orders.setdefault(l["customer_order_number"], []).append(l)
    out = []
    for number, group in orders.items():
        states = [line_state(l, shortages)[0] for l in group]
        status = ("issued" if all(s == "issued" for s in states)
                  else "ready" if all(s in ("ready", "issued") for s in states) else "missing")
        out.append({"order": number, "status": status, "lines": group})
    order_rank = {"missing": 0, "ready": 1, "issued": 2}
    out.sort(key=lambda o: (order_rank[o["status"]], o["order"]))
    return {"orders": out, "shortages": sorted(shortages.values(), key=lambda s: s["length"]),
            "counts": {k: sum(1 for o in out if o["status"] == k) for k in order_rank}}
