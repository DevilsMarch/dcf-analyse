"""Automated, semi-automatic DCF valuation toolkit.

Pipeline:
    data.py        -> fetch & normalize company financials (yfinance)
    assumptions.py -> derive sensible default forecast/WACC/TV assumptions
    model.py       -> run the DCF and sensitivity analysis
    excel_export.py-> write the result as a JP-Morgan-style .xlsx workbook

The Streamlit UI (app.py) ties these together with editable assumptions.
"""

from .data import CompanyData, fetch_company_data
from .assumptions import Assumptions, default_assumptions
from .model import DCFResult, run_dcf

__all__ = [
    "CompanyData",
    "fetch_company_data",
    "Assumptions",
    "default_assumptions",
    "DCFResult",
    "run_dcf",
]
