"""Tiny in-memory stand-in for the supabase-py query builder (tests only)."""
import copy
import uuid


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
            rows.append(row)
            return Result([copy.deepcopy(row)])
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in matched:
                r.update(copy.deepcopy(self.payload))
            return Result(copy.deepcopy(matched))
        out = copy.deepcopy(matched)
        return Result(out[: self._limit] if self._limit else out)


class FakeApiError(Exception):
    """Mimics postgrest APIError: has .message."""

    def __init__(self, message):
        super().__init__({"message": message})
        self.message = message


class RpcCall:
    """EMULATION of public.receive_order_line (migrations/001). The real function is
    tested against Postgres in tests/integration; this keeps UI/service tests offline."""

    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        db, p = self.db, self.params
        if self.name != "receive_order_line":
            raise FakeApiError(f"unknown function {self.name}")
        snapshot = copy.deepcopy(db.tables)           # transaction: all or nothing
        db.calls.append(("rpc", self.name))
        try:
            return Result(self._run(db, p))
        except Exception:
            db.tables = snapshot
            raise

    @staticmethod
    def _fail(msg):
        raise FakeApiError("ORDERERR:" + msg)

    def _run(self, db, p):
        if db.fail_on.get(("rpc", "receive_order_line")):
            db.fail_on[("rpc", "receive_order_line")].pop()
            raise FakeApiError("connection reset")
        if p["p_qty"] is None or p["p_qty"] <= 0:
            self._fail("הכמות שהתקבלה חייבת להיות גדולה מאפס.")
        if not (p["p_location"] or "").strip():
            self._fail("חובה להזין מיקום אחסון.")
        lines = db.tables.setdefault("order_lines", [])
        line = next((r for r in lines if r["id"] == p["p_line_id"]), None)
        if line is None:
            self._fail("שורת ההזמנה לא נמצאה.")
        if line["status"] != "ordered" or line.get("stock_applied"):
            self._fail("השורה כבר התקבלה או בוטלה. לא בוצע שינוי במלאי.")
        if line["category"] == "SANTAF":
            rows = [r for r in db.tables["santaf_inventory"] if r["length"] == line["length_mm"]]
            if len(rows) != 1:
                self._fail("לא נמצאה שורת מלאי יחידה לאורך סנטף.")
            rows[0]["quantity"] = (rows[0].get("quantity") or 0) + p["p_qty"]
            if db.fail_on.get(("santaf_movements", "insert")):      # fails AFTER the stock update
                db.fail_on[("santaf_movements", "insert")].pop()
                raise FakeApiError("simulated failure")
            db.tables.setdefault("santaf_movements", []).append(
                {"id": str(uuid.uuid4()), "length": line["length_mm"], "quantity": p["p_qty"],
                 "type": "IN", "date": p["p_date"]})
        else:
            if db.fail_on.get(("led_inventory", "insert")):
                db.fail_on[("led_inventory", "insert")].pop()
                raise FakeApiError("simulated failure")
            meters = line["length_mm"] / 1000
            db.tables.setdefault("led_inventory", []).append({
                "id": str(uuid.uuid4()),
                "order_number": line.get("supplier_order_ref") or line["customer_order_number"],
                "led_type": f"{line.get('item_spec') or ''} {meters:g} מ'".strip(),
                "quantity": p["p_qty"],
                "notes": f"הזמנת פרגולה {line['customer_order_number']} | מיקום: {p['p_location'].strip()}"})
        line.update(status="received", stock_applied=True, received_qty=p["p_qty"],
                    received_date=p["p_date"], storage_location=p["p_location"].strip())
        return copy.deepcopy(line)


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

    USERS = {"worker@example.test": "correct-password"}

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
