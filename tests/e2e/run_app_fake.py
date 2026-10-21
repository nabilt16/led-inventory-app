"""Launch the REAL app.py with an in-memory fake Supabase (no network, no production).
    streamlit run tests/e2e/run_app_fake.py --server.port 8599 --server.headless true
Login: worker@example.test / correct-password"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import supabase  # noqa: E402

import e2e_state  # noqa: E402  (module-level singleton survives Streamlit reruns)

supabase.create_client = lambda *a, **k: e2e_state.CLIENT
runpy.run_path(os.path.join(ROOT, "app.py"), run_name="__main__")
