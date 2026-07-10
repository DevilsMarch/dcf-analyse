"""Entry-point shim.

The application lives in ``streamlit_app.py`` (the name Streamlit Community Cloud
uses by default). This tiny file exists so the deployment also works if it was
configured to run ``app.py`` — it executes the real app fresh on every Streamlit
rerun, preserving normal reactive behaviour.
"""
from pathlib import Path

_app = Path(__file__).with_name("streamlit_app.py")
exec(compile(_app.read_text(encoding="utf-8"), str(_app), "exec"))
