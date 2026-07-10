"""Semi-automatic DCF web app.

Enter a ticker -> financials + analyst consensus are pulled from Yahoo Finance
and default assumptions are derived automatically. A single per-year "driver"
table (revenue growth, EBITDA margin, D&A, capex, ΔNWC) and every other
assumption are editable; results and charts update live. Save/load scenarios as
JSON and download a formula-driven Excel workbook at any time.

Run:  streamlit run app.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

import charts
from dcf import fetch_company_data, default_assumptions, run_dcf
from dcf.assumptions import Assumptions, consensus_growth_path, linear_fade_path
from dcf.excel_export import workbook_bytes

st.set_page_config(page_title="Automatische DCF-Analyse", page_icon="📈", layout="wide")

# Scalar assumption widgets — keyed so scenarios can rehydrate them.
SCALAR_KEYS = ["forecast_years", "tax_rate", "wacc_mode", "wacc_override", "rf", "erp",
               "beta", "kd", "ew", "perp_g", "exit_mult", "net_debt", "minorities",
               "pension", "associates", "mid_year"]

DRIVER_COLS = ["Wachstum %", "EBITDA-Marge %", "D&A %", "Capex %", "ΔNWC %"]


@st.cache_data(show_spinner="Lade Finanzdaten & Analystenschätzungen …")
def load_company(ticker: str):
    return fetch_company_data(ticker)


def pct(x) -> str:
    return f"{x:.1%}" if x is not None and not (isinstance(x, float) and np.isnan(x)) else "–"


def default_drivers_df(data, a: Assumptions, n: int, perp: float) -> pd.DataFrame:
    fc_years = [(data.years[-1] if data.years else 0) + i for i in range(1, n + 1)]
    gp, _ = consensus_growth_path(data, n, perp, fallback_initial=a.initial_revenue_growth)
    return pd.DataFrame({
        "Jahr": fc_years,
        "Wachstum %": [round(g * 100, 2) for g in gp],
        "EBITDA-Marge %": [round(a.ebitda_margin * 100, 2)] * n,
        "D&A %": [round(a.da_pct_revenue * 100, 2)] * n,
        "Capex %": [round(a.capex_pct_revenue * 100, 2)] * n,
        "ΔNWC %": [round(a.nwc_pct_revenue_change * 100, 2)] * n,
    })


def seed_drivers(data, a: Assumptions):
    ss = st.session_state
    n = int(ss["forecast_years"])
    ss["drivers_base"] = default_drivers_df(data, a, n, ss["perp_g"] / 100)
    ss["drivers_sig"] = (data.ticker, n)
    ss["drivers_nonce"] = ss.get("drivers_nonce", 0) + 1


def reset_inputs(data, a: Assumptions):
    ss = st.session_state
    ss["forecast_years"] = int(a.forecast_years)
    ss["tax_rate"] = round(a.tax_rate * 100, 2)
    ss["wacc_mode"] = "Aufbau (CAPM)"
    ss["wacc_override"] = round(a.wacc() * 100, 2)
    ss["rf"] = round(a.risk_free * 100, 2)
    ss["erp"] = round(a.equity_risk_premium * 100, 2)
    ss["beta"] = round(float(a.beta), 2)
    ss["kd"] = round(a.pretax_cost_of_debt * 100, 2)
    ss["ew"] = round(a.equity_weight * 100, 1)
    ss["perp_g"] = round(a.perpetuity_growth * 100, 2)
    ss["exit_mult"] = float(a.exit_ebitda_multiple)
    ss["net_debt"] = float(round(data.net_debt, 1))
    ss["minorities"] = float(a.minority_interests)
    ss["pension"] = float(a.pension_liability)
    ss["associates"] = float(a.associates)
    ss["mid_year"] = True
    seed_drivers(data, a)


def apply_scenario(scn: dict, data):
    """Rehydrate all widget state from a saved scenario (same ticker)."""
    ss = st.session_state
    for k, v in scn.get("scalars", {}).items():
        if k in SCALAR_KEYS:
            ss[k] = v
    drivers = scn.get("drivers")
    if drivers:
        df = pd.DataFrame(drivers)
        ss["drivers_base"] = df
        ss["forecast_years"] = int(len(df))
        ss["drivers_sig"] = (data.ticker, int(len(df)))
        ss["drivers_nonce"] = ss.get("drivers_nonce", 0) + 1


# --------------------------------------------------------------------------
# Header + ticker
# --------------------------------------------------------------------------
st.title("📈 Automatische DCF-Analyse")
st.caption(
    "Ticker eingeben → Daten + Analystenkonsens automatisch → Treiber pro Jahr und "
    "Annahmen live anpassen → Szenarien speichern/laden → als Excel herunterladen. "
    "Datenquelle: Yahoo Finance (weltweit)."
)

with st.sidebar:
    st.header("1 · Unternehmen")
    ticker = st.text_input("Ticker-Symbol", value=st.session_state.get("ticker", "AAPL"),
                           help="Beispiele: AAPL · MSFT · SAP.DE · ULVR.L · AIR.PA · 7203.T"
                           ).strip().upper()
    load = st.button("Daten laden", type="primary", use_container_width=True)

if load or "data" not in st.session_state:
    if not ticker:
        st.stop()
    try:
        data = load_company(ticker)
    except Exception as e:  # noqa: BLE001
        st.error(f"❌ {e}")
        st.stop()
    st.session_state["ticker"] = ticker
    st.session_state["data"] = data
    st.session_state["defaults"] = default_assumptions(data)
    reset_inputs(data, st.session_state["defaults"])

data = st.session_state["data"]
defaults: Assumptions = st.session_state["defaults"]
ccy = data.reporting_currency
pccy = data.price_currency

for w in data.warnings:
    st.warning("⚠️ " + w)

# --------------------------------------------------------------------------
# Scenario LOAD — must run before the scalar widgets are created
# --------------------------------------------------------------------------
with st.sidebar:
    with st.expander("💾 Szenario laden", expanded=False):
        up = st.file_uploader("JSON-Datei", type=["json"], key="scn_upload",
                              label_visibility="collapsed")
        if up is not None:
            fid = getattr(up, "file_id", None) or f"{up.name}:{up.size}"
            if st.session_state.get("applied_scn_id") != fid:
                st.session_state["applied_scn_id"] = fid
                try:
                    scn = json.load(up)
                except Exception:
                    scn = None
                if not scn:
                    st.error("Datei konnte nicht gelesen werden.")
                elif scn.get("ticker") != data.ticker:
                    st.warning(f"Szenario ist für **{scn.get('ticker')}** — bitte zuerst "
                               f"diesen Ticker laden, dann erneut hochladen.")
                else:
                    apply_scenario(scn, data)
                    st.rerun()

# --------------------------------------------------------------------------
# Scalar assumption widgets (keyed, values come from session_state)
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("2 · Annahmen")

    with st.expander("Prognose & Steuer", expanded=True):
        forecast_years = st.slider("Prognosejahre", 3, 15, key="forecast_years")
        tax_rate = st.slider("Steuersatz (%)", 0.0, 50.0, step=0.5, key="tax_rate") / 100

    with st.expander("WACC (Diskontsatz)", expanded=False):
        wacc_mode = st.radio("Methode", ["Aufbau (CAPM)", "Direkt vorgeben"],
                             horizontal=True, key="wacc_mode")
        if wacc_mode == "Direkt vorgeben":
            wacc_override = st.slider("WACC (%)", 3.0, 20.0, step=0.1, key="wacc_override") / 100
            rf, erp, beta = defaults.risk_free, defaults.equity_risk_premium, defaults.beta
            kd, ew, dw = defaults.pretax_cost_of_debt, defaults.equity_weight, defaults.debt_weight
        else:
            wacc_override = None
            rf = st.slider("Risikofreier Zins (%)", 0.0, 8.0, step=0.1, key="rf") / 100
            erp = st.slider("Marktrisikoprämie (%)", 3.0, 9.0, step=0.1, key="erp") / 100
            beta = st.slider("Beta", 0.2, 2.5, step=0.05, key="beta")
            kd = st.slider("Fremdkapitalkosten vor Steuern (%)", 0.0, 12.0, step=0.1, key="kd") / 100
            ew = st.slider("Eigenkapital-Gewicht E/(D+E) (%)", 0.0, 100.0, step=1.0, key="ew") / 100
            dw = 1 - ew

    with st.expander("Terminal Value", expanded=False):
        perp_g = st.slider("Perpetuity Growth Rate (%)", -1.0, 6.0, step=0.1, key="perp_g") / 100
        exit_mult = st.slider("Exit-EBITDA-Multiple (x)", 3.0, 20.0, step=0.5, key="exit_mult")

    with st.expander("EV → Equity Bridge", expanded=False):
        net_debt = st.number_input(f"Net Debt / (Cash) (Mio {ccy})", step=100.0, key="net_debt")
        minorities = st.number_input("Minderheiten (Mio)", step=50.0, key="minorities")
        pension = st.number_input("Pensionsverpflichtungen (Mio)", step=50.0, key="pension")
        associates = st.number_input("Beteiligungen (Mio)", step=50.0, key="associates")

    mid_year = st.checkbox("Mid-Year-Convention", key="mid_year",
                           help="Cashflows in der Jahresmitte diskontieren (Banking-Standard).")

# --------------------------------------------------------------------------
# Driver table (editable per-year) — reseed on ticker/horizon change
# --------------------------------------------------------------------------
if st.session_state.get("drivers_sig") != (data.ticker, forecast_years):
    seed_drivers(data, defaults)

st.subheader(f"{data.name}  ·  {data.ticker}")
st.markdown("#### Treiber pro Jahr")

cons_available = bool(data.analyst_rev_growth) or data.analyst_ltg is not None
pc1, pc2, pc3, pc4 = st.columns([1, 1, 1, 3])
if cons_available and pc1.button("Konsens-Wachstum", use_container_width=True):
    gp, _ = consensus_growth_path(data, forecast_years, perp_g,
                                  fallback_initial=defaults.initial_revenue_growth)
    st.session_state["drivers_base"]["Wachstum %"] = [round(g * 100, 2) for g in gp]
    st.session_state["drivers_nonce"] += 1
    st.rerun()
if pc2.button("Linearer Fade", use_container_width=True):
    fp = linear_fade_path(defaults.initial_revenue_growth, perp_g, forecast_years)
    st.session_state["drivers_base"]["Wachstum %"] = [round(g * 100, 2) for g in fp]
    st.session_state["drivers_nonce"] += 1
    st.rerun()
if pc3.button("↺ Zurücksetzen", use_container_width=True):
    seed_drivers(data, defaults)
    st.rerun()
if cons_available:
    na = f" · {data.num_analysts} Analysten" if data.num_analysts else ""
    near = ", ".join(f"{g:+.1%}" for g in data.analyst_rev_growth) or "–"
    pc4.caption(f"📊 Konsens Umsatzwachstum: {near}{na}"
                + (f" · LTG {data.analyst_ltg:.1%}" if data.analyst_ltg else ""))
else:
    pc4.caption("Keine Analystenschätzungen — Default aus historischem Wachstum.")

ed1, ed2 = st.columns([1, 1])
with ed1:
    num_cfg = {c: st.column_config.NumberColumn(c, format="%.2f", step=0.25) for c in DRIVER_COLS}
    num_cfg["Jahr"] = st.column_config.NumberColumn("Jahr", disabled=True, format="%d")
    edited = st.data_editor(
        st.session_state["drivers_base"],
        key=f"drivers_{st.session_state['drivers_nonce']}",
        hide_index=True, num_rows="fixed", use_container_width=True, column_config=num_cfg,
    )

growth_path = [float(v) / 100 for v in edited["Wachstum %"].tolist()]
margin_path = [float(v) / 100 for v in edited["EBITDA-Marge %"].tolist()]
da_path = [float(v) / 100 for v in edited["D&A %"].tolist()]
capex_path = [float(v) / 100 for v in edited["Capex %"].tolist()]
nwc_path = [float(v) / 100 for v in edited["ΔNWC %"].tolist()]

with ed2:
    st.altair_chart(charts.growth_curve(edited["Jahr"].tolist(), growth_path),
                    use_container_width=True)

# --------------------------------------------------------------------------
# Assemble assumptions and run
# --------------------------------------------------------------------------
assumptions = Assumptions(
    forecast_years=forecast_years,
    revenue_growth_path=growth_path,
    ebitda_margin_path=margin_path, da_pct_path=da_path,
    capex_pct_path=capex_path, nwc_pct_path=nwc_path,
    growth_source="manual",
    initial_revenue_growth=growth_path[0] if growth_path else defaults.initial_revenue_growth,
    terminal_revenue_growth=perp_g,
    ebitda_margin=margin_path[0] if margin_path else defaults.ebitda_margin,
    da_pct_revenue=da_path[0] if da_path else defaults.da_pct_revenue,
    capex_pct_revenue=capex_path[0] if capex_path else defaults.capex_pct_revenue,
    nwc_pct_revenue_change=nwc_path[0] if nwc_path else defaults.nwc_pct_revenue_change,
    tax_rate=tax_rate,
    risk_free=rf, equity_risk_premium=erp, beta=beta, pretax_cost_of_debt=kd,
    equity_weight=ew, debt_weight=dw, wacc_override=wacc_override,
    perpetuity_growth=perp_g, exit_ebitda_multiple=exit_mult,
    net_debt_override=net_debt, minority_interests=minorities,
    pension_liability=pension, associates=associates,
)
result = run_dcf(data, assumptions, mid_year=mid_year)

# --------------------------------------------------------------------------
# Header metrics
# --------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Aktueller Kurs", f"{data.price:,.2f} {pccy}")
c2.metric("Marktkap.", f"{data.market_cap:,.0f} Mio {ccy}")
c3.metric("Net Debt", f"{net_debt:,.0f} Mio {ccy}")
c4.metric("WACC", pct(result.wacc))

r1, r2, r3 = st.columns(3)
r1.metric("Impliziter Kurs — Perpetuity", f"{result.price_perpetuity:,.2f} {pccy}",
          f"{result.premium_perpetuity:+.1%} vs. Markt")
r2.metric("Impliziter Kurs — Exit-Multiple", f"{result.price_exit:,.2f} {pccy}",
          f"{result.premium_exit:+.1%} vs. Markt")
avg = (result.price_perpetuity + result.price_exit) / 2
r3.metric("Ø beider Methoden", f"{avg:,.2f} {pccy}", f"{avg / data.price - 1:+.1%} vs. Markt")

# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------
tab_val, tab_fc, tab_sens, tab_exp = st.tabs(
    ["🎯 Bewertung", "📊 Prognose", "🌡️ Sensitivität", "💾 Export & Szenarien"])

with tab_val:
    st.markdown("#### Bewertungsspanne (Football Field)")
    s = result.sensitivity
    all_prices = [v for row in s["price_growth"] for v in row] + \
                 [v for row in s["price_multiple"] for v in row]
    ff_rows = [
        {"method": "DCF · Perpetuity", "low": result.price_perpetuity,
         "high": result.price_perpetuity, "point": result.price_perpetuity, "color": charts.BLUE},
        {"method": "DCF · Exit-Multiple", "low": result.price_exit,
         "high": result.price_exit, "point": result.price_exit, "color": charts.TEAL},
        {"method": "DCF · Sensitivität", "low": min(all_prices), "high": max(all_prices),
         "point": result.price_perpetuity, "color": charts.NAVY},
    ]
    t = data.price_targets
    if t.get("low") and t.get("high"):
        ff_rows.append({"method": "Analysten-Kursziele", "low": t["low"], "high": t["high"],
                        "point": t.get("mean", (t["low"] + t["high"]) / 2), "color": charts.AMBER})
    st.altair_chart(charts.football_field(ff_rows, data.price, pccy), use_container_width=True)
    st.caption("Balken = Spanne, weißer Strich = Mittelwert, rote Linie = aktueller Kurs.")

    b1, b2 = st.columns([3, 2])
    with b1:
        st.markdown("**Enterprise Value → Equity Value (Perpetuity)**")
        bridge = pd.DataFrame({
            "Position": ["PV der Free Cash Flows", "PV des Terminal Value", "= Enterprise Value",
                         "– Net Debt", "– Minderheiten/Pension/Beteil.", "= Equity Value"],
            f"Mio {ccy}": [result.pv_fcf, result.pv_tv_perpetuity, result.ev_perpetuity,
                           -net_debt, -(minorities + pension + associates), result.equity_perpetuity],
        })
        st.dataframe(bridge.style.format({f"Mio {ccy}": "{:,.0f}"}), hide_index=True,
                     use_container_width=True)
    with b2:
        st.markdown("**Wertzusammensetzung des EV**")
        st.altair_chart(charts.value_composition(result.pv_fcf, result.pv_tv_perpetuity, ccy),
                        use_container_width=True)
        tv_share = result.pv_tv_perpetuity / result.ev_perpetuity if result.ev_perpetuity else 0
        if tv_share > 0.75:
            st.caption("⚠️ Sehr hoher TV-Anteil — Ergebnis stark von langfristigen Annahmen abhängig.")

with tab_fc:
    fc1, fc2 = st.columns(2)
    with fc1:
        st.markdown("**Umsatz & EBITDA**")
        st.altair_chart(charts.revenue_ebitda(result.years, result.revenue, result.ebitda, ccy),
                        use_container_width=True)
    with fc2:
        st.markdown("**Free Cash Flow & Barwert**")
        st.altair_chart(charts.fcf_chart(result.years, result.ufcf, result.pv_fcf_series, ccy),
                        use_container_width=True)

    st.markdown("**Treiber-Kurven (% vom Umsatz)**")
    rev = np.array(result.revenue)
    st.altair_chart(charts.driver_curves(
        result.years,
        (np.array(result.ebitda) / rev).tolist(),
        (np.array(result.da) / rev).tolist(),
        (np.array(result.capex) / rev).tolist(),
        (np.array(result.dnwc) / rev).tolist()), use_container_width=True)

    st.markdown("**Detailtabelle**")
    proj = pd.DataFrame({
        "Jahr": result.years, "Wachstum": growth_path,
        "Umsatz": result.revenue, "EBITDA": result.ebitda, "EBIT": result.ebit,
        "Steuern": [-x for x in result.tax], "Capex": [-c for c in result.capex],
        "Δ NWC": result.dnwc, "Unlev. FCF": result.ufcf,
        "Diskontfaktor": result.discount_factor, "PV FCF": result.pv_fcf_series,
    }).set_index("Jahr")
    fmt = {c: "{:,.0f}" for c in proj.columns if c not in ("Diskontfaktor", "Wachstum")}
    fmt |= {"Diskontfaktor": "{:.3f}", "Wachstum": "{:.1%}"}
    st.dataframe(proj.style.format(fmt), use_container_width=True)

with tab_sens:
    st.markdown("Impliziter Kurs bei variierendem WACC und Wachstum / Multiple.")

    def sens_df(row_vals, col_vals, grid, col_fmt):
        df = pd.DataFrame(grid, index=[f"{w:.1%}" for w in row_vals],
                          columns=[col_fmt(c) for c in col_vals])
        df.index.name = "WACC"
        return df

    def heat(df: pd.DataFrame):
        lo, hi = df.values.min(), df.values.max()
        rng = (hi - lo) or 1.0

        def color(v):
            tt = (v - lo) / rng
            r = int(200 + (76 - 200) * tt); g = int(80 + (159 - 80) * tt); b = int(77 + (112 - 77) * tt)
            return f"background-color: rgb({r},{g},{b}); color: #f5f5f5"
        return df.style.format("{:,.0f}").map(color)

    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown("**WACC × Perpetuity Growth**")
        st.dataframe(heat(sens_df(s["waccs"], s["growths"], s["price_growth"], lambda x: f"{x:.2%}")),
                     use_container_width=True)
    with sc2:
        st.markdown("**WACC × Exit-Multiple**")
        st.dataframe(heat(sens_df(s["waccs"], s["multiples"], s["price_multiple"], lambda x: f"{x:.1f}x")),
                     use_container_width=True)

with tab_exp:
    e1, e2 = st.columns(2)
    with e1:
        st.markdown("**Excel-Modell**")
        st.caption("Lebende Formeln, Charts, Sensitivitäten. Gelbe Zellen editierbar.")
        st.download_button("⬇️ Excel herunterladen",
                           data=workbook_bytes(data, assumptions, result),
                           file_name=f"DCF_{data.ticker}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", use_container_width=True)
    with e2:
        st.markdown("**Szenario speichern**")
        st.caption("Alle Annahmen + Treiber-Tabelle als JSON. Laden über die Seitenleiste.")
        scenario = {
            "ticker": data.ticker,
            "scalars": {k: st.session_state.get(k) for k in SCALAR_KEYS},
            "drivers": {c: list(edited[c]) for c in edited.columns},
        }
        st.download_button("💾 Szenario speichern (JSON)",
                           data=json.dumps(scenario, indent=2, default=str).encode("utf-8"),
                           file_name=f"Szenario_{data.ticker}.json",
                           mime="application/json", use_container_width=True)
