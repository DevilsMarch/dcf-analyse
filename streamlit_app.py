"""Semi-automatic DCF web app.

Enter a ticker -> financials + analyst consensus are pulled from Yahoo Finance
and default assumptions are derived automatically. A single per-year "driver"
table (revenue growth, EBITDA margin, D&A, capex, ΔNWC) and every other
assumption are editable; results and charts update live. Save/load scenarios as
JSON and download a formula-driven Excel workbook at any time.

Run:  streamlit run streamlit_app.py
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


def scalars_now() -> dict:
    """Snapshot the current scalar widget values (display units) from state."""
    return {k: st.session_state.get(k) for k in SCALAR_KEYS}


def assemble_assumptions(scalars: dict, drivers: dict, data, dflt: Assumptions) -> Assumptions:
    """Build an Assumptions object from a scenario dict (scalars in display units,
    drivers as percent lists). Shared by the live model and the comparison tab."""
    n = int(scalars["forecast_years"])

    def col(name, fb):
        vals = [float(v) / 100 for v in list(drivers.get(name, []))[:n]]
        return vals or [fb]

    gp = col("Wachstum %", dflt.initial_revenue_growth)
    mp = col("EBITDA-Marge %", dflt.ebitda_margin)
    dp = col("D&A %", dflt.da_pct_revenue)
    cp = col("Capex %", dflt.capex_pct_revenue)
    wp = col("ΔNWC %", dflt.nwc_pct_revenue_change)
    direct = scalars.get("wacc_mode") == "Direkt vorgeben"
    ew = float(scalars["ew"]) / 100
    return Assumptions(
        forecast_years=n,
        revenue_growth_path=gp, ebitda_margin_path=mp, da_pct_path=dp,
        capex_pct_path=cp, nwc_pct_path=wp, growth_source="manual",
        initial_revenue_growth=gp[0], terminal_revenue_growth=float(scalars["perp_g"]) / 100,
        ebitda_margin=mp[0], da_pct_revenue=dp[0], capex_pct_revenue=cp[0],
        nwc_pct_revenue_change=wp[0], tax_rate=float(scalars["tax_rate"]) / 100,
        risk_free=float(scalars["rf"]) / 100, equity_risk_premium=float(scalars["erp"]) / 100,
        beta=float(scalars["beta"]), pretax_cost_of_debt=float(scalars["kd"]) / 100,
        equity_weight=ew, debt_weight=1 - ew,
        wacc_override=float(scalars["wacc_override"]) / 100 if direct else None,
        perpetuity_growth=float(scalars["perp_g"]) / 100,
        exit_ebitda_multiple=float(scalars["exit_mult"]),
        net_debt_override=float(scalars["net_debt"]), minority_interests=float(scalars["minorities"]),
        pension_liability=float(scalars["pension"]), associates=float(scalars["associates"]),
    )


def run_scenario(scn: dict, data, dflt: Assumptions):
    a = assemble_assumptions(scn["scalars"], scn["drivers"], data, dflt)
    r = run_dcf(data, a, mid_year=bool(scn["scalars"].get("mid_year", True)))
    return a, r


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
    st.session_state["scenarios"] = {}   # comparison set is per-company

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
current_scn = {
    "ticker": data.ticker,
    "scalars": scalars_now(),
    "drivers": {c: list(edited[c]) for c in edited.columns},
}
assumptions, result = run_scenario(current_scn, data, defaults)

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
tab_val, tab_fc, tab_sens, tab_cmp, tab_exp = st.tabs(
    ["🎯 Bewertung", "📊 Prognose", "🌡️ Sensitivität", "⚖️ Vergleich", "💾 Export & Szenarien"])

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

with tab_cmp:
    scns: dict = st.session_state.setdefault("scenarios", {})
    st.markdown(f"Sammle Varianten (z. B. **Bull / Base / Bear**) und vergleiche sie "
                f"nebeneinander. Alle beziehen sich auf **{data.ticker}**.")

    ac1, ac2, ac3 = st.columns([2, 1, 1])
    nm = ac1.text_input("Name", value=f"Szenario {len(scns) + 1}", key="cmp_name",
                        label_visibility="collapsed", placeholder="Name des Szenarios")
    if ac2.button("＋ Aktuelles sichern", use_container_width=True):
        scns[nm.strip() or f"Szenario {len(scns) + 1}"] = current_scn
        st.rerun()
    if scns and ac3.button("🗑️ Alle löschen", use_container_width=True):
        scns.clear()
        st.rerun()

    if scns:
        rm = st.multiselect("Einzelne entfernen", list(scns.keys()), key="cmp_rm")
        if rm:
            for k in rm:
                scns.pop(k, None)
            st.rerun()

    # Evaluate the live state plus every stored scenario
    cols = {"Aktuell (live)": (assumptions, result)}
    for name, scn in scns.items():
        try:
            cols[name] = run_scenario(scn, data, defaults)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Szenario '{name}' konnte nicht berechnet werden: {e}")

    def _metrics(a, r):
        return {
            "Impl. Kurs · Perpetuity": r.price_perpetuity,
            "Impl. Kurs · Exit-Multiple": r.price_exit,
            "Prämie vs. Markt (Perp.)": r.premium_perpetuity,
            "Enterprise Value (Mio)": r.ev_perpetuity,
            "Equity Value (Mio)": r.equity_perpetuity,
            "WACC": r.wacc,
            "Ø Umsatzwachstum": float(np.mean(a.revenue_growth_path)),
            "Ø EBITDA-Marge": float(np.mean(a.ebitda_margin_path)),
            "Terminal Growth": a.perpetuity_growth,
            "Exit-Multiple": a.exit_ebitda_multiple,
            "TV-Anteil am EV": (r.pv_tv_perpetuity / r.ev_perpetuity) if r.ev_perpetuity else 0.0,
        }

    fmts = {
        "Impl. Kurs · Perpetuity": lambda v: f"{v:,.2f} {pccy}",
        "Impl. Kurs · Exit-Multiple": lambda v: f"{v:,.2f} {pccy}",
        "Prämie vs. Markt (Perp.)": lambda v: f"{v:+.1%}",
        "Enterprise Value (Mio)": lambda v: f"{v:,.0f}",
        "Equity Value (Mio)": lambda v: f"{v:,.0f}",
        "WACC": lambda v: f"{v:.2%}",
        "Ø Umsatzwachstum": lambda v: f"{v:.1%}",
        "Ø EBITDA-Marge": lambda v: f"{v:.1%}",
        "Terminal Growth": lambda v: f"{v:.2%}",
        "Exit-Multiple": lambda v: f"{v:.1f}x",
        "TV-Anteil am EV": lambda v: f"{v:.0%}",
    }
    table = pd.DataFrame({name: _metrics(a, r) for name, (a, r) in cols.items()})
    disp = table.copy().astype(object)
    for m in disp.index:
        disp.loc[m] = [fmts[m](v) for v in table.loc[m]]
    disp.insert(0, "Kennzahl", disp.index)
    st.dataframe(disp, hide_index=True, use_container_width=True)

    price_rows = [{"Szenario": name, "Perpetuity": r.price_perpetuity,
                   "Exit-Multiple": r.price_exit} for name, (a, r) in cols.items()]
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**Impliziter Kurs je Szenario**")
        st.altair_chart(charts.scenario_prices(price_rows, data.price, pccy),
                        use_container_width=True)
    with cc2:
        st.markdown("**Umsatzwachstum je Szenario**")
        gseries = [{"Szenario": name, "Jahr": y, "Wachstum": g}
                   for name, (a, r) in cols.items()
                   for y, g in zip(r.years, a.revenue_growth_path)]
        st.altair_chart(charts.scenario_growth(gseries), use_container_width=True)

    if not scns:
        st.info("Noch keine gespeicherten Szenarien. Passe die Annahmen an und klicke "
                "oben auf **＋ Aktuelles sichern**, um Varianten zu vergleichen.")

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
        st.download_button("💾 Szenario speichern (JSON)",
                           data=json.dumps(current_scn, indent=2, default=str).encode("utf-8"),
                           file_name=f"Szenario_{data.ticker}.json",
                           mime="application/json", use_container_width=True)
