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


class FakeClient:
    def __init__(self, **tables):
        self.tables = {k: copy.deepcopy(v) for k, v in tables.items()}
        self.calls = []
        self.fail_on = {}

    def table(self, name):
        return Query(self, name)
