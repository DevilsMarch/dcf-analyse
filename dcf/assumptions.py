"""Derive sensible *default* DCF assumptions from historicals + market data.

These are only a starting point. In the semi-automatic workflow the user
reviews and overrides every one of them in the UI. The philosophy mirrors how
an analyst seeds a model:

  * Revenue growth starts near the recent historical rate and *fades* linearly
    to the long-term (perpetuity) growth rate by the end of the horizon.
  * Margins, D&A/capex/NWC ratios default to a normalized recent average.
  * WACC is built bottom-up via CAPM (cost of equity) blended with an after-tax
    cost of debt at the company's actual capital structure.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

from .data import CompanyData

# Market defaults (long-run, currency-agnostic starting points).
DEFAULT_RISK_FREE = 0.035          # ~ long-term government bond yield
DEFAULT_EQUITY_RISK_PREMIUM = 0.055
DEFAULT_BETA = 1.0
DEFAULT_PRETAX_COST_OF_DEBT = 0.055
DEFAULT_PERPETUITY_GROWTH = 0.02   # ~ long-run nominal GDP
MIN_WACC_SPREAD = 0.02             # WACC must exceed g by at least this much


@dataclass
class Assumptions:
    forecast_years: int = 10

    # Explicit per-year revenue growth path (fractions). If set and its length
    # matches forecast_years, the model uses it verbatim; otherwise it falls back
    # to a linear fade from initial_revenue_growth to terminal_revenue_growth.
    revenue_growth_path: "list | None" = None
    growth_source: str = "linear fade"   # for display: consensus / linear fade / manual

    # Optional per-year driver paths (fractions). When set they override the flat
    # scalar below; the model truncates/extends them to the horizon.
    ebitda_margin_path: "list | None" = None
    da_pct_path: "list | None" = None
    capex_pct_path: "list | None" = None
    nwc_pct_path: "list | None" = None

    # Growth / margins (fractions) — flat fallbacks / seed values
    initial_revenue_growth: float = 0.05
    terminal_revenue_growth: float = DEFAULT_PERPETUITY_GROWTH  # growth fades to this
    ebitda_margin: float = 0.20
    da_pct_revenue: float = 0.04          # D&A as % of revenue (positive)
    capex_pct_revenue: float = 0.04       # capex as % of revenue (positive)
    nwc_pct_revenue_change: float = 0.0   # ΔNWC cash flow as % of revenue
    tax_rate: float = 0.25

    # WACC build-up
    risk_free: float = DEFAULT_RISK_FREE
    equity_risk_premium: float = DEFAULT_EQUITY_RISK_PREMIUM
    beta: float = DEFAULT_BETA
    pretax_cost_of_debt: float = DEFAULT_PRETAX_COST_OF_DEBT
    equity_weight: float = 1.0            # E/(D+E)
    debt_weight: float = 0.0             # D/(D+E)
    wacc_override: Optional[float] = None  # if set, use directly instead of build-up

    # Terminal value
    perpetuity_growth: float = DEFAULT_PERPETUITY_GROWTH
    exit_ebitda_multiple: float = 8.5

    # Equity bridge adjustments (reporting-currency millions)
    net_debt_override: Optional[float] = None  # if set, use instead of data.net_debt
    minority_interests: float = 0.0
    pension_liability: float = 0.0
    associates: float = 0.0

    def cost_of_equity(self) -> float:
        return self.risk_free + self.beta * self.equity_risk_premium

    def wacc(self) -> float:
        if self.wacc_override is not None:
            return self.wacc_override
        ke = self.cost_of_equity()
        kd = self.pretax_cost_of_debt * (1 - self.tax_rate)
        w = self.equity_weight * ke + self.debt_weight * kd
        # guarantee a positive spread over perpetuity growth for TV stability
        return max(w, self.perpetuity_growth + MIN_WACC_SPREAD)

    def to_dict(self) -> dict:
        return asdict(self)


def _cagr(series: list[float]) -> float:
    vals = [v for v in series if v and v > 0]
    if len(vals) < 2:
        return DEFAULT_PERPETUITY_GROWTH
    n = len(vals) - 1
    ratio = vals[-1] / vals[0]
    if ratio <= 0:
        return DEFAULT_PERPETUITY_GROWTH
    return ratio ** (1 / n) - 1


def _safe_ratio_avg(num: list[float], den: list[float]) -> float:
    ratios = []
    for a, b in zip(num, den):
        if b and b != 0:
            ratios.append(a / b)
    if not ratios:
        return float("nan")
    return float(np.median(ratios))


def linear_fade_path(initial: float, terminal: float, n: int) -> list[float]:
    """Straight-line glide from `initial` (year 1) to `terminal` (year n)."""
    if n <= 1:
        return [terminal]
    return [initial + (terminal - initial) * (t / (n - 1)) for t in range(n)]


def consensus_growth_path(data: CompanyData, n: int, terminal: float,
                          fallback_initial: float | None = None) -> tuple[list[float], str]:
    """Build a per-year revenue growth path, seeded from analyst consensus.

    Near-term years use consensus revenue growth (0y, +1y); the mid/long term
    anchors on the long-term-growth estimate (LTG) if available, then glides to
    the terminal growth rate. Returns (path, source_label).
    """
    near = [g for g in (data.analyst_rev_growth or []) if g is not None][:2]
    ltg = data.analyst_ltg

    if not near and ltg is None:
        init = fallback_initial if fallback_initial is not None else DEFAULT_PERPETUITY_GROWTH
        return linear_fade_path(init, terminal, n), "linear fade"

    anchor = ltg if (ltg is not None) else (near[-1] if near else terminal)
    path: list[float] = []
    k = min(len(near), n)
    for i in range(n):
        if i < k:
            path.append(near[i])
        else:
            span = max(n - 1 - k, 1)
            frac = min((i - k) / span, 1.0)
            path.append(anchor + (terminal - anchor) * frac)

    path = [float(np.clip(g, -0.5, 0.5)) for g in path]
    source = "analyst consensus" if near else "consensus (LTG)"
    return path, source


def default_assumptions(data: CompanyData, forecast_years: int = 10) -> Assumptions:
    """Build default assumptions from a company's historicals and market data."""
    rev = data.revenue
    last_rev = rev[-1]

    # Growth: historical CAGR, damped and clamped into a plausible band.
    hist_cagr = _cagr(rev)
    initial_growth = float(np.clip(hist_cagr, -0.05, 0.25))

    # Prefer analyst consensus for the per-year growth path; fall back to fade.
    growth_path, growth_source = consensus_growth_path(
        data, forecast_years, DEFAULT_PERPETUITY_GROWTH, fallback_initial=initial_growth
    )
    initial_growth = round(growth_path[0], 4)

    # Margins & ratios: normalized recent averages.
    ebitda_margin = _safe_ratio_avg(data.ebitda, rev)
    if np.isnan(ebitda_margin):
        ebitda_margin = 0.20
    ebitda_margin = float(np.clip(ebitda_margin, 0.02, 0.6))

    da_pct = _safe_ratio_avg(data.dep_amort, rev)
    da_pct = float(np.clip(da_pct if not np.isnan(da_pct) else 0.04, 0.005, 0.2))

    capex_pct = _safe_ratio_avg(data.capex, rev)
    capex_pct = float(np.clip(capex_pct if not np.isnan(capex_pct) else 0.04, 0.005, 0.25))

    nwc_pct = _safe_ratio_avg(data.change_nwc, rev)
    nwc_pct = float(np.clip(nwc_pct if not np.isnan(nwc_pct) else 0.0, -0.1, 0.1))

    tax_rate = float(np.nanmedian([t for t in data.tax_rate if t is not None])) if data.tax_rate else 0.25
    if np.isnan(tax_rate):
        tax_rate = 0.25
    tax_rate = float(np.clip(tax_rate, 0.0, 0.5))

    # WACC build-up
    beta = data.beta if (data.beta and 0.1 < data.beta < 3.0) else DEFAULT_BETA
    equity_mv = data.market_cap
    debt_mv = max(data.total_debt, 0.0)
    total_cap = equity_mv + debt_mv
    if total_cap > 0:
        equity_w = equity_mv / total_cap
        debt_w = debt_mv / total_cap
    else:
        equity_w, debt_w = 1.0, 0.0

    a = Assumptions(
        forecast_years=forecast_years,
        revenue_growth_path=[round(g, 4) for g in growth_path],
        growth_source=growth_source,
        initial_revenue_growth=round(initial_growth, 4),
        terminal_revenue_growth=DEFAULT_PERPETUITY_GROWTH,
        ebitda_margin=round(ebitda_margin, 4),
        da_pct_revenue=round(da_pct, 4),
        capex_pct_revenue=round(capex_pct, 4),
        nwc_pct_revenue_change=round(nwc_pct, 4),
        tax_rate=round(tax_rate, 4),
        beta=round(float(beta), 3),
        equity_weight=round(equity_w, 4),
        debt_weight=round(debt_w, 4),
        perpetuity_growth=DEFAULT_PERPETUITY_GROWTH,
        exit_ebitda_multiple=8.5,
        minority_interests=0.0,
        pension_liability=0.0,
        associates=0.0,
    )
    return a
