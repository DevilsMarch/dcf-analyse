"""Alternative valuation methods that complement the base DCF.

All per-share equity models (DDM, residual income, future income) work in the
share price's currency and return a value directly comparable to `data.price`.
The market-multiple methods use enterprise-value bridges consistent with the DCF.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .data import CompanyData
from .assumptions import Assumptions
from .model import implied_price


# ==========================================================================
# 1) Reverse DCF — what does the current price imply?
# ==========================================================================
def _apply(a: Assumptions, param: str, x: float) -> Assumptions:
    n = a.forecast_years
    if param == "growth":
        return replace(a, revenue_growth_path=[x] * n)
    if param == "margin":
        return replace(a, ebitda_margin_path=[x] * n)
    if param == "wacc":
        return replace(a, wacc_override=x)
    if param == "terminal_growth":
        return replace(a, perpetuity_growth=x, terminal_revenue_growth=x)
    if param == "exit_multiple":
        return replace(a, exit_ebitda_multiple=x)
    return a


def _default_bounds(param: str, a: Assumptions) -> tuple[float, float]:
    return {
        "growth": (-0.30, 0.60),
        "margin": (0.02, 0.60),
        "wacc": (max(a.perpetuity_growth + 0.006, 0.02), 0.25),
        "terminal_growth": (-0.02, min(a.wacc() - 0.006, 0.06)),
        "exit_multiple": (1.0, 40.0),
    }.get(param, (0.0, 1.0))


def reverse_solve(data: CompanyData, a: Assumptions, param: str,
                  target_price: float | None = None, mid_year: bool = True,
                  samples: int = 100) -> float | None:
    """Solve for the value of `param` that makes the DCF match `target_price`
    (default: the current market price). Returns None if no crossing is found."""
    target = data.price if target_price is None else target_price
    method = "exit" if param == "exit_multiple" else "perpetuity"
    lo, hi = _default_bounds(param, a)
    prev_x = prev_f = None
    for i in range(samples):
        x = lo + (hi - lo) * i / (samples - 1)
        try:
            p = implied_price(data, _apply(a, param, x), mid_year, method)
        except Exception:
            prev_x = prev_f = None
            continue
        if p is None or not np.isfinite(p):
            prev_x = prev_f = None
            continue
        f = p - target
        if prev_f is not None and (prev_f <= 0 <= f or f <= 0 <= prev_f) and f != prev_f:
            return prev_x + (x - prev_x) * (0 - prev_f) / (f - prev_f)
        prev_x, prev_f = x, f
    return None


# ==========================================================================
# 2) Dividend Discount Model
# ==========================================================================
def gordon_ddm(d0: float, r: float, g: float) -> float | None:
    if not d0 or d0 <= 0 or g >= r:
        return None
    return d0 * (1 + g) / (r - g)


def two_stage_ddm(d0: float, r: float, g1: float, years: int, g2: float) -> float | None:
    if not d0 or d0 <= 0 or g2 >= r:
        return None
    pv = 0.0
    d = d0
    for t in range(1, years + 1):
        d = d * (1 + g1)
        pv += d / (1 + r) ** t
    d_term = d * (1 + g2)
    tv = d_term / (r - g2)
    pv += tv / (1 + r) ** years
    return pv


# ==========================================================================
# 3) Residual Income Model (per share)
# ==========================================================================
def residual_income(eps0: float, book0: float, r: float, g: float, years: int,
                    payout: float, terminal_growth: float) -> float | None:
    if eps0 is None or book0 is None or book0 <= 0:
        return None
    retained = max(0.0, min(1.0, 1 - payout))
    b = book0
    value = book0
    ri_last = 0.0
    for t in range(1, years + 1):
        eps_t = eps0 * (1 + g) ** t
        ri = eps_t - r * b               # equity charge on opening book value
        value += ri / (1 + r) ** t
        ri_last = ri
        b = b + eps_t * retained         # clean-surplus book roll-forward
    if terminal_growth < r:
        tv = ri_last * (1 + terminal_growth) / (r - terminal_growth)
        value += tv / (1 + r) ** years
    return value


# ==========================================================================
# 4) Future Income Model — discounted future earnings (per share)
# ==========================================================================
def future_income(eps0: float, r: float, g: float, years: int,
                  terminal_growth: float) -> float | None:
    if eps0 is None or eps0 <= 0:
        return None
    pv = 0.0
    eps_t = eps0
    for t in range(1, years + 1):
        eps_t = eps0 * (1 + g) ** t
        pv += eps_t / (1 + r) ** t
    if terminal_growth < r:
        tv = eps_t * (1 + terminal_growth) / (r - terminal_growth)
        pv += tv / (1 + r) ** years
    return pv


# ==========================================================================
# 5) Monte Carlo Simulation
# ==========================================================================
def monte_carlo(data: CompanyData, a: Assumptions, n_sims: int = 2000,
                sig_growth: float = 0.03, sig_margin: float = 0.03,
                sig_wacc: float = 0.01, sig_tg: float = 0.005,
                mid_year: bool = True, seed: int = 42) -> dict:
    """Randomise growth, margin, WACC and terminal growth around the current
    assumptions and return the distribution of implied (perpetuity) prices."""
    rng = np.random.default_rng(seed)
    n = a.forecast_years
    base_g = float(np.mean(a.revenue_growth_path)) if a.revenue_growth_path else a.initial_revenue_growth
    base_m = float(np.mean(a.ebitda_margin_path)) if a.ebitda_margin_path else a.ebitda_margin
    base_w = a.wacc()
    base_tg = a.perpetuity_growth

    gs = rng.normal(base_g, sig_growth, n_sims)
    ms = np.clip(rng.normal(base_m, sig_margin, n_sims), 0.01, 0.85)
    ws = rng.normal(base_w, sig_wacc, n_sims)
    tgs = rng.normal(base_tg, sig_tg, n_sims)

    prices = []
    for i in range(n_sims):
        w = max(float(ws[i]), float(tgs[i]) + 0.006)     # keep WACC > terminal growth
        aa = replace(a, revenue_growth_path=[float(gs[i])] * n,
                     ebitda_margin_path=[float(ms[i])] * n,
                     wacc_override=w, perpetuity_growth=float(tgs[i]),
                     terminal_revenue_growth=float(tgs[i]))
        try:
            p = implied_price(data, aa, mid_year, "perpetuity")
        except Exception:
            continue
        if p is not None and np.isfinite(p):
            prices.append(p)

    prices = np.array(prices, dtype=float)
    if prices.size == 0:
        return {"prices": prices, "stats": {}}
    pct = {f"p{q}": float(np.percentile(prices, q)) for q in (5, 25, 50, 75, 95)}
    stats = {
        **pct,
        "mean": float(np.mean(prices)),
        "std": float(np.std(prices)),
        "prob_above_market": float(np.mean(prices > data.price)),
        "n": int(prices.size),
    }
    return {"prices": prices, "stats": stats,
            "inputs": {"growth": base_g, "margin": base_m, "wacc": base_w, "tg": base_tg}}


# ==========================================================================
# 6) Relative Valuation (peer multiples)
# ==========================================================================
def _median(vals) -> float | None:
    vals = [v for v in vals if v is not None and np.isfinite(v) and 0 < v < 500]
    return float(np.median(vals)) if vals else None


def _price_from_ev_multiple(multiple: float, metric_last: float, data: CompanyData) -> float | None:
    if multiple is None or metric_last is None or metric_last <= 0:
        return None
    ev = multiple * metric_last                       # reporting-ccy millions
    equity = ev - data.net_debt
    return equity / data.shares_out * data.price_to_major


def relative_valuation(data: CompanyData, peers: list[dict]) -> dict:
    med = {
        "pe": _median([p.get("pe") for p in peers]),
        "forward_pe": _median([p.get("forward_pe") for p in peers]),
        "ev_ebitda": _median([p.get("ev_ebitda") for p in peers]),
        "ev_sales": _median([p.get("ev_sales") for p in peers]),
        "pb": _median([p.get("pb") for p in peers]),
    }
    prices = {}
    if med["pe"] and data.eps and data.eps > 0:
        prices["P/E"] = med["pe"] * data.eps
    if med["forward_pe"] and data.forward_eps and data.forward_eps > 0:
        prices["Forward P/E"] = med["forward_pe"] * data.forward_eps
    if med["ev_ebitda"]:
        prices["EV/EBITDA"] = _price_from_ev_multiple(med["ev_ebitda"], data.ebitda[-1], data)
    if med["ev_sales"]:
        prices["EV/Umsatz"] = _price_from_ev_multiple(med["ev_sales"], data.revenue[-1], data)
    if med["pb"] and data.book_value_ps and data.book_value_ps > 0:
        prices["P/B"] = med["pb"] * data.book_value_ps
    prices = {k: v for k, v in prices.items() if v is not None and np.isfinite(v)}
    return {"medians": med, "prices": prices}


# ==========================================================================
# 7) Historical Multiple Valuation (company's own history)
# ==========================================================================
def historical_valuation(data: CompanyData, hist: dict) -> dict:
    prices = {}
    if hist.get("pe_avg") and data.eps and data.eps > 0:
        prices["Ø P/E"] = hist["pe_avg"] * data.eps
    if hist.get("ev_ebitda_avg"):
        prices["Ø EV/EBITDA"] = _price_from_ev_multiple(hist["ev_ebitda_avg"], data.ebitda[-1], data)
    prices = {k: v for k, v in prices.items() if v is not None and np.isfinite(v)}
    return {"prices": prices, "pe_avg": hist.get("pe_avg"), "ev_ebitda_avg": hist.get("ev_ebitda_avg")}
