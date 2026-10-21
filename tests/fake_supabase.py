"""Tiny in-memory stand-in for the supabase-py query builder (tests only).

It EMULATES the database functions of migrations/001 so UI/service tests run offline.
The real SQL (constraints, trigger, atomic functions, RLS) is tested against Postgres in
tests/integration; this fake is only a convenience and is not proof of database behaviour."""
import copy
import itertools
import uuid

_COUNTER = itertools.count(1)
ORDER_LINE_DEFAULTS = {
    "manual_issue": False,
    "supply": "supplier", "package_no": 1, "item_spec": None, "color": None, "supplier_ref": None,
    "supplier_order_date": None, "supplier_status": None, "coordination_date": None,
    "received_date": None, "storage_location": None, "staging_location": None, "picked_qty": 0,
    "picked_on": None, "issued_on": None, "recipient": None, "notes": None,
}


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters = "select", None, []
        self._limit = None

    def select(self, *_):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c, "")) >= str(v))
        return self

    def lte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c, "")) <= str(v))
        return self

    def is_(self, col, val):
        assert val == "null"
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def ilike(self, col, pattern):
        needle = pattern.strip("%").lower()
        self.filters.append(lambda r, c=col: needle in str(r.get(c, "")).lower())
        return self

    def order(self, *_, **__):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self.db.calls.append((self.name, self.op))
        pending = self.db.fail_on.get((self.name, self.op))
        if pending:
            pending.pop()
            raise RuntimeError(f"simulated failure on {self.name}.{self.op}")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            row = {"id": str(uuid.uuid4()), **copy.deepcopy(self.payload)}
            if self.name == "order_lines":
                row = {**ORDER_LINE_DEFAULTS, **row, "created_at": f"{next(_COUNTER):012d}"}
            rows.append(row)
            return Result([copy.deepcopy(row)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in matched:
                r.update(copy.deepcopy(self.payload))
            return Result(copy.deepcopy(matched))
        if self.op == "delete":
            gone = copy.deepcopy(matched)
            self.db.tables[self.name] = [r for r in rows if r not in matched]
            return Result(gone)
        out = copy.deepcopy(matched)
        return Result(out[: self._limit] if self._limit else out)


class FakeApiError(Exception):
    """Mimics postgrest APIError: has .message."""

    def __init__(self, message):
        super().__init__({"message": message})
        self.message = message


def fail(msg):
    raise FakeApiError("ORDERERR:" + msg)


class RpcCall:
    """Emulation of is_approved_user / pick_stock_line / unpick_stock_line / issue_order_lines."""

    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        db, p = self.db, self.params
        if self.name == "is_approved_user":
            if db.fail_on.get(("rpc", "is_approved_user")):
                raise FakeApiError("function not found")
            return Result(db.token == "fake-access-worker@example.test")
        handler = {"pick_stock_line": self._pick, "unpick_stock_line": self._unpick,
                   "issue_order_lines": self._issue, "santaf_receive_stock": self._receive_stock,
                   "santaf_issue_direct": self._issue_direct, "led_issue_stock": self._led_issue}.get(self.name)
        if handler is None:
            raise FakeApiError(f"unknown function {self.name}")
        snapshot = copy.deepcopy(db.tables)           # one transaction: all or nothing
        db.calls.append(("rpc", self.name))
        try:
            if db.fail_on.get(("rpc", self.name)):
                db.fail_on[("rpc", self.name)].pop()
                raise FakeApiError("connection reset")
            return Result(handler(db, p))
        except Exception:
            db.tables = snapshot
            raise

    @staticmethod
    def _guard(db, fingerprint):
        """Emulates guard_duplicate (the real one uses a 15 second window; the fake keeps the
        fingerprint until it is cleared, which is stricter and good enough offline)."""
        seen = db.__dict__.setdefault("fingerprints", set())
        if fingerprint in seen:
            fail("פעולה זהה בוצעה זה עתה ולא בוצעה שוב. אם התכוונתם לבצע אותה שוב, המתינו מספר שניות.")
        seen.add(fingerprint)

    @staticmethod
    def _line(db, line_id):
        line = next((r for r in db.tables.get("order_lines", []) if r["id"] == line_id), None)
        if line is None:
            fail("שורת ההזמנה לא נמצאה.")
        return line

    @staticmethod
    def _inv(db, length):
        rows = [r for r in db.tables["santaf_inventory"] if r["length"] == length]
        if len(rows) != 1:
            fail(f"לא נמצאה שורת מלאי יחידה לאורך {length}. לא בוצע שינוי.")
        return rows[0]

    @staticmethod
    def _movement(db, length, qty, typ, when):
        if db.fail_on.get(("santaf_movements", "insert")):
            db.fail_on[("santaf_movements", "insert")].pop()
            raise FakeApiError("simulated failure")
        db.tables.setdefault("santaf_movements", []).append(
            {"id": str(uuid.uuid4()), "length": length, "quantity": qty, "type": typ, "date": when})

    def _pick(self, db, p):
        st = (p["p_staging"] or "").strip()
        if p["p_qty"] is None or p["p_qty"] <= 0:
            fail("הכמות לאיסוף חייבת להיות גדולה מאפס.")
        if not st:
            fail("חובה להזין מיקום הכנה.")
        l = self._line(db, p["p_line_id"])
        if l["supply"] != "stock":
            fail("רק שורת סנטף מהמלאי הרגיל נאספת מהמלאי.")
        if l["issued_on"]:
            fail("השורה כבר נופקה.")
        if p["p_qty"] > l["quantity"] - l["picked_qty"]:
            fail(f"הכמות גדולה ממה שנותר לאסוף ({l['quantity'] - l['picked_qty']}).")
        if any(o["customer_order_number"] == l["customer_order_number"] and o["length_mm"] == l["length_mm"]
               and o["supply"] == "stock" and o.get("manual_issue") and o["id"] != l["id"]
               for o in db.tables["order_lines"]):
            fail("להזמנה זו כבר נופק ידנית סנטף באותו אורך (דף ניפוק סנטפים). לא נאסף כדי לא לנכות פעמיים.")
        self._guard(db, f"pick|{p['p_line_id']}|{p['p_qty']}")
        inv = self._inv(db, l["length_mm"])
        if (inv.get("quantity") or 0) < p["p_qty"]:
            fail(f"אין מספיק מלאי זמין באורך {l['length_mm']} (נדרש {p['p_qty']}). לא בוצע שינוי.")
        inv["quantity"] -= p["p_qty"]
        self._movement(db, l["length_mm"], p["p_qty"], "PICK", p["p_date"])
        l.update(picked_qty=l["picked_qty"] + p["p_qty"], picked_on=p["p_date"], staging_location=st)
        return copy.deepcopy(l)

    def _unpick(self, db, p):
        l = self._line(db, p["p_line_id"])
        if l["supply"] != "stock":
            fail("רק שורת סנטף מהמלאי הרגיל נאספת מהמלאי.")
        if l["issued_on"]:
            fail("שורה שנופקה אי אפשר להחזיר למלאי.")
        if l["picked_qty"] <= 0:
            fail("אין איסוף להחזרה.")
        inv = self._inv(db, l["length_mm"])
        db.__dict__.setdefault("fingerprints", set())
        db.fingerprints = {f for f in db.fingerprints if not f.startswith(f"pick|{p['p_line_id']}|")}
        inv["quantity"] = (inv.get("quantity") or 0) + l["picked_qty"]
        self._movement(db, l["length_mm"], l["picked_qty"], "UNPICK", p["p_date"])
        l.update(picked_qty=0, picked_on=None)
        return copy.deepcopy(l)

    def _issue(self, db, p):
        if not (p["p_recipient"] or "").strip():
            fail("חובה להזין שם מקבל.")
        ids = list(dict.fromkeys(p["p_ids"]))
        lines = [self._line(db, i) for i in ids]
        for l in lines:
            if l["issued_on"]:
                fail("השורה כבר נופקה. לא בוצע שינוי.")
            if l["supply"] == "stock":
                if l["picked_qty"] != l["quantity"]:
                    fail(f"שורת סנטף {l['length_mm']} מ\"מ עדיין לא נאספה במלואה.")
            elif l["supplier_status"] != "received":
                fail("השורה עדיין לא התקבלה במחסן.")
        for l in lines:
            if l["supply"] == "stock":
                self._movement(db, l["length_mm"], l["picked_qty"], "OUT", p["p_date"])
            l.update(issued_on=p["p_date"], recipient=p["p_recipient"].strip())
        return len(lines)


    def _receive_stock(self, db, p):
        lengths, qtys = p["p_lengths"], p["p_qtys"]
        if not lengths or len(lengths) != len(qtys):
            fail("לא הוזנו כמויות לקליטה.")
        if any(q is None or q <= 0 for q in qtys):
            fail("כמות הקליטה חייבת להיות גדולה מאפס.")
        self._guard(db, f"receive|{lengths}|{qtys}|{p['p_date']}")
        for length, qty in zip(lengths, qtys):
            inv = self._inv(db, length)
            inv["quantity"] = (inv.get("quantity") or 0) + qty
            self._movement(db, length, qty, "IN", p["p_date"])
        return sum(qtys)

    def _issue_direct(self, db, p):
        order = (p["p_order"] or "").strip()
        if not order:
            fail("חובה להזין מספר הזמנת פרגולה.")
        if p["p_qty"] is None or p["p_qty"] <= 0:
            fail("כמות הניפוק חייבת להיות גדולה מאפס.")
        if any(o["customer_order_number"] == order and o["supply"] == "stock"
               and o["length_mm"] == p["p_length"] and not o["issued_on"] for o in db.tables["order_lines"]):
            fail(f"להזמנה זו כבר קיימת שורת סנטף מהמלאי הרגיל באורך {p['p_length']} שטרם נופקה. "
                 "בצעו איסוף וניפוק בדף ההזמנות כדי לא לנכות פעמיים.")
        self._guard(db, f"direct|{order}|{p['p_length']}|{p['p_qty']}|{p['p_date']}")
        inv = self._inv(db, p["p_length"])
        if (inv.get("quantity") or 0) < p["p_qty"]:
            fail(f"אין מספיק מלאי זמין באורך {p['p_length']} (נדרש {p['p_qty']}). לא בוצע שינוי.")
        inv["quantity"] -= p["p_qty"]
        self._movement(db, p["p_length"], p["p_qty"], "PICK", p["p_date"])
        self._movement(db, p["p_length"], p["p_qty"], "OUT", p["p_date"])
        line = {**ORDER_LINE_DEFAULTS, "id": str(uuid.uuid4()), "customer_order_number": order,
                "category": "SANTAF", "supply": "stock", "item_spec": "סנטף BH", "color": "שקוף",
                "length_mm": p["p_length"], "quantity": p["p_qty"], "picked_qty": p["p_qty"],
                "picked_on": p["p_date"], "issued_on": p["p_date"], "recipient": "ניפוק ידני",
                "manual_issue": True, "created_at": f"{next(_COUNTER):012d}"}
        db.tables.setdefault("order_lines", []).append(line)
        return copy.deepcopy(line)

    def _led_issue(self, db, p):
        if p["p_qty"] is None or p["p_qty"] <= 0:
            fail("כמות הניפוק חייבת להיות גדולה מאפס.")
        self._guard(db, f"ledissue|{p['p_id']}|{p['p_qty']}")
        row = next((r for r in db.tables["led_inventory"] if r["id"] == p["p_id"]), None)
        if row is None:
            fail("שורת הלדים לא נמצאה.")
        if (row.get("quantity") or 0) < p["p_qty"]:
            fail("אין מספיק מלאי לדים בשורה זו. לא בוצע שינוי.")
        row["quantity"] -= p["p_qty"]
        return row["quantity"]


class _Session:
    def __init__(self, email, ttl=3600):
        import time
        self.access_token = f"fake-access-{email}"
        self.refresh_token = f"fake-refresh-{email}"
        self.expires_at = int(time.time()) + ttl


class _AuthResponse:
    def __init__(self, session):
        self.session = session


class FakeAuth:
    """Stand-in for client.auth. Users: {email: password}."""

    USERS = {"worker@example.test": "correct-password", "stranger@example.test": "stranger-password"}

    def __init__(self, db):
        self.db = db

    def sign_in_with_password(self, creds):
        self.db.calls.append(("auth", "sign_in"))
        if self.USERS.get(creds["email"]) != creds["password"]:
            raise RuntimeError("Invalid login credentials")
        return _AuthResponse(_Session(creds["email"]))

    def refresh_session(self, refresh_token):
        self.db.calls.append(("auth", "refresh"))
        if not refresh_token.startswith("fake-refresh-"):
            raise RuntimeError("bad refresh token")
        return _AuthResponse(_Session(refresh_token.replace("fake-refresh-", "")))

    def sign_out(self):
        self.db.calls.append(("auth", "sign_out"))


class _Postgrest:
    def __init__(self, db):
        self.db = db

    def auth(self, token):
        self.db.token = token


class FakeClient:
    def __init__(self, **tables):
        self.tables = {k: copy.deepcopy(v) for k, v in tables.items()}
        self.calls = []
        self.fail_on = {}
        self.token = None
        self.auth = FakeAuth(self)
        self.postgrest = _Postgrest(self)

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        return RpcCall(self, name, params)
