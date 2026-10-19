import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import streamlit as st  # noqa: E402

import orders_page  # noqa: E402
from fake_supabase import FakeClient  # noqa: E402

LENGTHS = [1500, 2000, 2500, 3000]

if "db" not in st.session_state:
    st.session_state["db"] = FakeClient(
        santaf_inventory=[{"id": i, "length": l, "quantity": 0, "min_quantity": 20}
                          for i, l in enumerate(LENGTHS, 1)],
        santaf_movements=[], led_inventory=[], order_lines=[],
    )
    st.session_state["cache_cleared"] = 0


def _cleared():
    st.session_state["cache_cleared"] += 1


orders_page.render(st.session_state["db"], LENGTHS, _cleared)
