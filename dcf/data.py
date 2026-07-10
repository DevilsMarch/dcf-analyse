"""Fetch and normalize company financials from Yahoo Finance (yfinance).

Works for worldwide tickers (US, UK, DE, ...). All monetary figures are kept
in the company's *reporting currency* and expressed in millions to match the
conventions of a banking DCF model.

The one subtlety with worldwide coverage is that some exchanges quote the share
price in a *minor* currency unit (e.g. London quotes pence "GBp" while the
accounts are in pounds "GBP"). We capture both and expose a `price_to_major`
factor so the equity bridge stays consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Currency codes quoted in a minor unit (1/100 of the major reporting unit).
_MINOR_UNIT_CURRENCIES = {"GBp": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


@dataclass
class CompanyData:
    """Normalized company data used to seed the DCF.

    Monetary series (`revenue`, `ebitda`, ...) are lists in reporting-currency
    **millions**, ordered oldest -> newest, one entry per fiscal year.
    """

    ticker: str
    name: str
    reporting_currency: str            # currency of the financial statements
    price_currency: str                # currency the share price is quoted in
    price_to_major: float              # divide quoted price by this to get major units
    price: float                       # latest share price (as quoted)
    shares_out: float                  # shares outstanding (millions)
    beta: Optional[float]

    # Equity -> enterprise value bridge (reporting-currency millions)
    total_debt: float
    cash: float

    # Historical fiscal years (oldest -> newest)
    years: list = field(default_factory=list)          # list[int] fiscal-year-end years
    fye_month: int = 12                                 # fiscal-year-end month
    revenue: list = field(default_factory=list)
    ebitda: list = field(default_factory=list)
    dep_amort: list = field(default_factory=list)       # positive magnitude
    ebit: list = field(default_factory=list)
    tax_rate: list = field(default_factory=list)        # fraction
    capex: list = field(default_factory=list)           # positive magnitude
    change_nwc: list = field(default_factory=list)      # cash-flow sign (+ = cash in)

    # Analyst consensus (best-effort; may be empty for thinly covered names)
    analyst_rev_growth: list = field(default_factory=list)  # near-term revenue growth [0y, +1y]
    analyst_ltg: "float | None" = None                      # long-term growth estimate
    price_targets: dict = field(default_factory=dict)       # {current, low, mean, median, high}
    num_analysts: "int | None" = None

    warnings: list = field(default_factory=list)

    # -- convenience -------------------------------------------------------
    @property
    def net_debt(self) -> float:
        return self.total_debt - self.cash

    @property
    def market_cap(self) -> float:
        return self.price / self.price_to_major * self.shares_out

    @property
    def last_revenue(self) -> float:
        return self.revenue[-1]

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "reporting_currency": self.reporting_currency,
            "price_currency": self.price_currency,
            "price": self.price,
            "shares_out_m": self.shares_out,
            "beta": self.beta,
            "net_debt_m": self.net_debt,
            "years": self.years,
        }


def _row(df: pd.DataFrame, *candidates: str) -> Optional[pd.Series]:
    """Return the first matching row of a yfinance statement, else None."""
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _to_millions(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v / 1e6


def _fetch_analyst(tk) -> dict:
    """Best-effort analyst consensus. Every branch is defensive: these Yahoo
    endpoints are frequently missing, especially outside the US."""
    out = {"rev_growth": [], "ltg": None, "targets": {}, "num_analysts": None}
    try:
        re = tk.revenue_estimate
        if re is not None and not re.empty and "growth" in re.columns:
            for p in ("0y", "+1y"):
                if p in re.index:
                    g = re.loc[p, "growth"]
                    if pd.notna(g):
                        out["rev_growth"].append(float(g))
            if "numberOfAnalysts" in re.columns and "0y" in re.index:
                na = re.loc["0y", "numberOfAnalysts"]
                out["num_analysts"] = int(na) if pd.notna(na) else None
    except Exception:
        pass
    try:
        ge = tk.growth_estimates
        if ge is not None and not ge.empty and "LTG" in ge.index:
            col = "stockTrend" if "stockTrend" in ge.columns else ge.columns[0]
            v = ge.loc["LTG", col]
            if pd.notna(v):
                out["ltg"] = float(v)
    except Exception:
        pass
    try:
        pt = tk.analyst_price_targets
        if isinstance(pt, dict):
            out["targets"] = {k: float(v) for k, v in pt.items()
                              if v is not None and pd.notna(v)}
    except Exception:
        pass
    return out


def search_companies(query: str, max_results: int = 8) -> list[dict]:
    """Look up companies by name or ticker via Yahoo's search.

    Returns a list of {symbol, name, exchange, type}, most relevant first,
    restricted to equities and ETFs. Empty list on failure or no match.
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        quotes = yf.Search(query, max_results=max_results).quotes or []
    except Exception:
        return []

    out, seen = [], set()
    for q in quotes:
        sym = (q.get("symbol") or "").strip()
        if not sym or sym in seen:
            continue
        if q.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        name = " ".join((q.get("shortname") or q.get("longname") or sym).split())
        out.append({
            "symbol": sym,
            "name": name,
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": q.get("quoteType"),
        })
        seen.add(sym)
    return out


def fetch_company_data(ticker: str) -> CompanyData:
    """Fetch and normalize financials for `ticker`. Raises ValueError if the
    ticker is unknown or lacks the minimum data needed for a DCF."""
    ticker = ticker.strip().upper()
    tk = yf.Ticker(ticker)

    try:
        info = tk.info or {}
    except Exception:  # network / parse issues -> treat as unknown
        info = {}

    income = tk.financials      # annual income statement
    cashflow = tk.cashflow      # annual cash flow statement

    if (income is None or income.empty) and not info.get("currentPrice"):
        raise ValueError(
            f"Keine Daten für '{ticker}' gefunden. Prüfe das Ticker-Symbol "
            f"(z. B. AAPL, SAP.DE, ULVR.L)."
        )

    warnings: list[str] = []

    # -- price / currency --------------------------------------------------
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    price_currency = info.get("currency") or ""
    reporting_currency = info.get("financialCurrency") or price_currency or "USD"

    price_to_major = 1.0
    if price_currency in _MINOR_UNIT_CURRENCIES:
        major, factor = _MINOR_UNIT_CURRENCIES[price_currency]
        price_to_major = factor
        if major != reporting_currency:
            warnings.append(
                f"Kurs in {price_currency} (Minor-Einheit), Abschluss in {reporting_currency}."
            )

    if price is None:
        raise ValueError(f"Kein Kurs für '{ticker}' verfügbar.")

    shares_out = info.get("sharesOutstanding")
    if not shares_out:
        # fall back to market cap / price
        mc = info.get("marketCap")
        shares_out = (mc / (price / price_to_major)) if mc else None
    if not shares_out:
        raise ValueError(f"Keine Aktienanzahl (shares outstanding) für '{ticker}'.")
    shares_out_m = shares_out / 1e6

    total_debt = _to_millions(info.get("totalDebt") or 0.0)
    cash = _to_millions(info.get("totalCash") or 0.0)

    # -- historical statements --------------------------------------------
    rev = _row(income, "Total Revenue", "Operating Revenue")
    if rev is None or rev.dropna().empty:
        raise ValueError(f"Keine Umsatzhistorie für '{ticker}' verfügbar — DCF nicht möglich.")

    ebitda_r = _row(income, "EBITDA", "Normalized EBITDA")
    ebit_r = _row(income, "EBIT", "Operating Income", "Total Operating Income As Reported")
    da_inc = _row(income, "Reconciled Depreciation")
    da_cf = _row(cashflow, "Depreciation And Amortization",
                 "Depreciation Amortization Depletion")
    tax_rate_r = _row(income, "Tax Rate For Calcs")
    tax_prov = _row(income, "Tax Provision")
    pretax = _row(income, "Pretax Income")
    capex_r = _row(cashflow, "Capital Expenditure")
    dnwc_r = _row(cashflow, "Change In Working Capital")

    # Columns are dates, newest first. Order oldest -> newest.
    cols = list(rev.dropna().index)
    cols = sorted(cols)  # ascending by date

    years, revenue, ebitda, dep, ebit, tax_rate, capex, dnwc = ([] for _ in range(8))

    def val(series, col, default=np.nan):
        if series is None or col not in series.index:
            return default
        return series[col]

    fye_month = cols[-1].month if cols else 12

    for col in cols:
        r = _to_millions(val(rev, col))
        if np.isnan(r) or r == 0:
            continue

        da = val(da_inc, col)
        if da is None or (isinstance(da, float) and np.isnan(da)):
            da = val(da_cf, col)
        da_m = abs(_to_millions(da)) if da is not None and not (isinstance(da, float) and np.isnan(da)) else np.nan

        e = val(ebit_r, col)
        ebit_m = _to_millions(e) if e is not None and not (isinstance(e, float) and np.isnan(e)) else np.nan

        eb = val(ebitda_r, col)
        ebitda_m = _to_millions(eb) if eb is not None and not (isinstance(eb, float) and np.isnan(eb)) else np.nan
        if np.isnan(ebitda_m) and not np.isnan(ebit_m) and not np.isnan(da_m):
            ebitda_m = ebit_m + da_m
        if np.isnan(ebit_m) and not np.isnan(ebitda_m) and not np.isnan(da_m):
            ebit_m = ebitda_m - da_m

        # tax rate
        tr = val(tax_rate_r, col)
        if tr is None or (isinstance(tr, float) and np.isnan(tr)):
            tp = val(tax_prov, col)
            pt = val(pretax, col)
            try:
                tr = float(tp) / float(pt) if pt else np.nan
            except (TypeError, ValueError, ZeroDivisionError):
                tr = np.nan
        try:
            tr = float(tr)
        except (TypeError, ValueError):
            tr = np.nan
        if not np.isnan(tr):
            tr = min(max(tr, 0.0), 0.6)  # clamp implausible effective rates

        cx = val(capex_r, col)
        capex_m = abs(_to_millions(cx)) if cx is not None and not (isinstance(cx, float) and np.isnan(cx)) else np.nan

        wc = val(dnwc_r, col)
        wc_m = _to_millions(wc) if wc is not None and not (isinstance(wc, float) and np.isnan(wc)) else 0.0

        years.append(col.year)
        revenue.append(r)
        ebitda.append(ebitda_m)
        dep.append(da_m)
        ebit.append(ebit_m)
        tax_rate.append(tr)
        capex.append(capex_m)
        dnwc.append(wc_m)

    if len(revenue) < 2:
        warnings.append("Weniger als 2 Jahre Historie — Defaults sind grob geschätzt.")

    # Fill remaining NaNs with column-median-ish fallbacks
    def _fill(seq, fallback):
        arr = np.array(seq, dtype=float)
        if np.isnan(arr).all():
            arr[:] = fallback
        else:
            med = np.nanmedian(arr)
            arr[np.isnan(arr)] = med
        return arr.tolist()

    # sensible fallbacks relative to revenue
    last_rev = revenue[-1]
    ebitda = _fill(ebitda, 0.15 * last_rev)
    dep = _fill(dep, 0.04 * last_rev)
    ebit = [eb - d for eb, d in zip(ebitda, dep)] if np.isnan(np.array(ebit)).any() else ebit
    ebit = _fill(ebit, 0.10 * last_rev)
    capex = _fill(capex, 0.04 * last_rev)
    tax_rate = _fill(tax_rate, 0.25)

    name = " ".join((info.get("shortName") or info.get("longName") or ticker).split())

    analyst = _fetch_analyst(tk)

    return CompanyData(
        ticker=ticker,
        name=name,
        reporting_currency=reporting_currency,
        price_currency=price_currency or reporting_currency,
        price_to_major=price_to_major,
        price=float(price),
        shares_out=shares_out_m,
        beta=info.get("beta"),
        total_debt=total_debt,
        cash=cash,
        years=years,
        fye_month=fye_month,
        revenue=revenue,
        ebitda=ebitda,
        dep_amort=dep,
        ebit=ebit,
        tax_rate=tax_rate,
        capex=capex,
        change_nwc=dnwc,
        analyst_rev_growth=analyst["rev_growth"],
        analyst_ltg=analyst["ltg"],
        price_targets=analyst["targets"],
        num_analysts=analyst["num_analysts"],
        warnings=warnings,
    )
