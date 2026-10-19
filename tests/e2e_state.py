"""Shared in-memory fake database for the browser e2e run (fake data only)."""
from fake_supabase import FakeClient

LENGTHS = [1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000]

CLIENT = FakeClient(
    santaf_inventory=[{"id": i, "length": l, "quantity": 10 if l == 2000 else 0, "min_quantity": 20}
                      for i, l in enumerate(LENGTHS, 1)],
    santaf_movements=[],
    led_inventory=[{"id": 1, "order_number": "OLD-1", "led_type": "LED legacy 3 מ'", "quantity": 5,
                    "notes": "legacy row", "created_at": "2026-05-01T10:00:00"}],
    order_lines=[],
)
