"""The DCF calculation engine.

Methodology mirrors the JP Morgan "Happy Hour Co" template:

  Unlevered FCF = EBIT*(1-tax) + D&A - Capex + ΔNWC
  Terminal value, two methods:
      perpetuity growth  : TV = FCF_N * (1+g) / (WACC - g)
      exit EBITDA multiple: TV = EBITDA_N * multiple
  The terminal value is discounted with the *final year's* discount factor
  (as the template does), FCFs with a mid-year convention by default.
  Enterprise value -> equity value via the net-debt bridge -> implied share
  price (converted back to the price's quoted currency unit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .data import CompanyData
from .assumptions import Assumptions


@dataclass
class DCFResult:
    wacc: float
    years: list                         # forecast fiscal-year-end years
    periods: list                       # discount periods (e.g. 0.5, 1.5, ...)
    revenue: list
    ebitda: list
    da: list
    ebit: list
    tax: list
    ebiat: list
    capex: list
    dnwc: list
    ufcf: list
    discount_factor: list
    pv_fcf_series: list

    pv_fcf: float
    # perpetuity-growth method
    tv_perpetuity: float
    pv_tv_perpetuity: float
    ev_perpetuity: float
    equity_perpetuity: float
    price_perpetuity: float             # in quoted price units
    premium_perpetuity: float
    # exit-multiple method
    tv_exit: float
    pv_tv_exit: float
    ev_exit: float
    equity_exit: float
    price_exit: float
    premium_exit: float

    bridge: dict = field(default_factory=dict)      # EV -> equity bridge items
    sensitivity: dict = field(default_factory=dict)  # grids


def _growth_path(a: Assumptions) -> list[float]:
    """Return the per-year revenue growth path.

    Uses the explicit `revenue_growth_path` when provided; if its length differs
    from the horizon it is truncated or extended by fading the last value to the
    terminal rate. Otherwise falls back to a linear fade.
    """
    n = a.forecast_years
    path = a.revenue_growth_path
    if path:
        if len(path) == n:
            return list(path)
        if len(path) > n:
            return list(path[:n])
        # extend: fade last known value to terminal over the remaining years
        out = list(path)
        last = path[-1]
        extra = n - len(path)
        for j in range(1, extra + 1):
            out.append(last + (a.terminal_revenue_growth - last) * (j / extra))
        return out
    if n == 1:
        return [a.terminal_revenue_growth]
    return [
        a.initial_revenue_growth
        + (a.terminal_revenue_growth - a.initial_revenue_growth) * (t / (n - 1))
        for t in range(n)
    ]


def _series(path, flat: float, n: int) -> list[float]:
    """Return an n-length driver series: use `path` when given (truncated or
    extended by holding its last value), else a flat line at `flat`."""
    if path:
        vals = list(path[:n])
        if len(vals) < n:
            vals += [vals[-1]] * (n - len(vals))
        return vals
    return [flat] * n


def _net_debt(data: CompanyData, a: Assumptions) -> float:
    return a.net_debt_override if a.net_debt_override is not None else data.net_debt


def _equity_from_ev(ev: float, data: CompanyData, a: Assumptions) -> float:
    return ev - _net_debt(data, a) - a.minority_interests - a.pension_liability - a.associates


def _price_from_equity(equity: float, data: CompanyData) -> float:
    """Equity value (reporting ccy, millions) -> price in the quoted unit."""
    per_share_major = equity / data.shares_out          # major reporting-ccy units
    return per_share_major * data.price_to_major        # e.g. GBP -> pence


def run_dcf(data: CompanyData, a: Assumptions, mid_year: bool = True) -> DCFResult:
    wacc = a.wacc()
    g = a.perpetuity_growth
    n = a.forecast_years

    growth = _growth_path(a)
    last_rev = data.last_revenue

    years, revenue = [], []
    prev_rev = last_rev
    base_year = data.years[-1] if data.years else 0
    for t in range(n):
        r = prev_rev * (1 + growth[t])
        revenue.append(r)
        years.append(base_year + t + 1)
        prev_rev = r

    margin = _series(a.ebitda_margin_path, a.ebitda_margin, n)
    da_p = _series(a.da_pct_path, a.da_pct_revenue, n)
    capex_p = _series(a.capex_pct_path, a.capex_pct_revenue, n)
    nwc_p = _series(a.nwc_pct_path, a.nwc_pct_revenue_change, n)

    ebitda = [r * m for r, m in zip(revenue, margin)]
    da = [r * d for r, d in zip(revenue, da_p)]
    ebit = [e - d for e, d in zip(ebitda, da)]
    tax = [max(eb, 0.0) * a.tax_rate for eb in ebit]     # no tax benefit on losses
    ebiat = [eb - tx for eb, tx in zip(ebit, tax)]
    capex = [r * c for r, c in zip(revenue, capex_p)]
    dnwc = [r * w for r, w in zip(revenue, nwc_p)]
    ufcf = [ei + d - cx + wc for ei, d, cx, wc in zip(ebiat, da, capex, dnwc)]

    # Discount periods: mid-year (t-0.5) or end-year (t).
    periods = [(t + 1) - (0.5 if mid_year else 0.0) for t in range(n)]
    disc = [1.0 / (1 + wacc) ** p for p in periods]
    pv_fcf_series = [f * df for f, df in zip(ufcf, disc)]
    pv_fcf = float(np.sum(pv_fcf_series))

    last_df = disc[-1]

    # Terminal values
    spread = wacc - g
    tv_perp = ufcf[-1] * (1 + g) / spread if spread > 1e-6 else float("nan")
    pv_tv_perp = tv_perp * last_df
    ev_perp = pv_fcf + pv_tv_perp

    tv_exit = ebitda[-1] * a.exit_ebitda_multiple
    pv_tv_exit = tv_exit * last_df
    ev_exit = pv_fcf + pv_tv_exit

    eq_perp = _equity_from_ev(ev_perp, data, a)
    eq_exit = _equity_from_ev(ev_exit, data, a)
    price_perp = _price_from_equity(eq_perp, data)
    price_exit = _price_from_equity(eq_exit, data)
    prem_perp = price_perp / data.price - 1 if data.price else float("nan")
    prem_exit = price_exit / data.price - 1 if data.price else float("nan")

    bridge = {
        "enterprise_value_perp": ev_perp,
        "enterprise_value_exit": ev_exit,
        "less_net_debt": -_net_debt(data, a),
        "less_minorities": -a.minority_interests,
        "less_pension": -a.pension_liability,
        "less_associates": -a.associates,
        "equity_value_perp": eq_perp,
        "equity_value_exit": eq_exit,
        "shares_out_m": data.shares_out,
    }

    sensitivity = _sensitivity(data, a, mid_year)

    return DCFResult(
        wacc=wacc, years=years, periods=periods, revenue=revenue, ebitda=ebitda,
        da=da, ebit=ebit, tax=tax, ebiat=ebiat, capex=capex, dnwc=dnwc, ufcf=ufcf,
        discount_factor=disc, pv_fcf_series=pv_fcf_series, pv_fcf=pv_fcf,
        tv_perpetuity=tv_perp, pv_tv_perpetuity=pv_tv_perp, ev_perpetuity=ev_perp,
        equity_perpetuity=eq_perp, price_perpetuity=price_perp, premium_perpetuity=prem_perp,
        tv_exit=tv_exit, pv_tv_exit=pv_tv_exit, ev_exit=ev_exit, equity_exit=eq_exit,
        price_exit=price_exit, premium_exit=prem_exit,
        bridge=bridge, sensitivity=sensitivity,
    )


def _ev_with(data, a, mid_year, wacc, method, g=None, mult=None) -> tuple[float, float]:
    """Recompute EV & implied price for a (wacc, g|multiple) point."""
    n = a.forecast_years
    growth = _growth_path(a)
    prev = data.last_revenue
    revenue = []
    for t in range(n):
        prev = prev * (1 + growth[t])
        revenue.append(prev)
    margin = _series(a.ebitda_margin_path, a.ebitda_margin, n)
    da_p = _series(a.da_pct_path, a.da_pct_revenue, n)
    capex_p = _series(a.capex_pct_path, a.capex_pct_revenue, n)
    nwc_p = _series(a.nwc_pct_path, a.nwc_pct_revenue_change, n)
    ebitda = [r * m for r, m in zip(revenue, margin)]
    da = [r * d for r, d in zip(revenue, da_p)]
    ebit = [e - d for e, d in zip(ebitda, da)]
    tax = [max(e, 0.0) * a.tax_rate for e in ebit]
    ebiat = [e - t for e, t in zip(ebit, tax)]
    capex = [r * c for r, c in zip(revenue, capex_p)]
    dnwc = [r * w for r, w in zip(revenue, nwc_p)]
    ufcf = [ei + d - cx + wc for ei, d, cx, wc in zip(ebiat, da, capex, dnwc)]
    periods = [(t + 1) - (0.5 if mid_year else 0.0) for t in range(n)]
    disc = [1.0 / (1 + wacc) ** p for p in periods]
    pv_fcf = float(np.sum([f * df for f, df in zip(ufcf, disc)]))
    if method == "perpetuity":
        spread = wacc - g
        tv = ufcf[-1] * (1 + g) / spread if spread > 1e-6 else float("nan")
    else:
        tv = ebitda[-1] * mult
    ev = pv_fcf + tv * disc[-1]
    eq = _equity_from_ev(ev, data, a)
    price = _price_from_equity(eq, data)
    return ev, price


def _sensitivity(data: CompanyData, a: Assumptions, mid_year: bool,
                 steps: int = 2) -> dict:
    """Build WACC×g and WACC×multiple grids centered on the base case."""
    base_wacc = a.wacc()
    wacc_step = 0.005
    waccs = [base_wacc + (i - steps) * wacc_step for i in range(2 * steps + 1)]

    g_step = 0.0025
    gs = [a.perpetuity_growth + (i - steps) * g_step for i in range(2 * steps + 1)]

    m_step = 0.5
    mults = [a.exit_ebitda_multiple + (i - steps) * m_step for i in range(2 * steps + 1)]

    ev_g = [[_ev_with(data, a, mid_year, w, "perpetuity", g=g)[0] for g in gs] for w in waccs]
    px_g = [[_ev_with(data, a, mid_year, w, "perpetuity", g=g)[1] for g in gs] for w in waccs]
    ev_m = [[_ev_with(data, a, mid_year, w, "exit", mult=m)[0] for m in mults] for w in waccs]
    px_m = [[_ev_with(data, a, mid_year, w, "exit", mult=m)[1] for m in mults] for w in waccs]

    return {
        "waccs": waccs,
        "growths": gs,
        "multiples": mults,
        "ev_growth": ev_g,
        "price_growth": px_g,
        "ev_multiple": ev_m,
        "price_multiple": px_m,
    }
