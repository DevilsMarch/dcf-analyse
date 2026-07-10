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
from dcf import (fetch_company_data, default_assumptions, run_dcf, search_companies,
                fetch_peer_multiples, historical_multiples)
from dcf.assumptions import Assumptions, consensus_growth_path, linear_fade_path
from dcf.excel_export import workbook_bytes
from dcf import valuation as val

st.set_page_config(page_title="DCF Terminal", page_icon="◆", layout="wide")


def inject_theme():
    """Professional black / gold / red styling on top of the dark base theme."""
    st.markdown(
        """
        <style>
        :root {
            --gold:#D4AF37; --gold-soft:#E7CC6E; --gold-deep:#A67C1F;
            --red:#C0392B; --ink:#0A0A0A; --panel:#15140F; --panel-2:#1C1A12;
            --line:rgba(212,175,55,.22); --text:#EDE9DE; --muted:#9A9488;
        }
        .stApp { background:
            radial-gradient(1200px 500px at 80% -10%, rgba(212,175,55,.06), transparent 60%),
            var(--ink); }
        .block-container { padding-top: 2.2rem; max-width: 1280px; }

        /* Brand header */
        .brand { display:flex; align-items:center; gap:.85rem; margin-bottom:.2rem; }
        .brand .mark { width:42px; height:42px; border-radius:9px;
            background:linear-gradient(145deg,#E7CC6E,#A67C1F);
            display:flex; align-items:center; justify-content:center;
            color:#0A0A0A; font-weight:800; font-size:1.25rem;
            box-shadow:0 2px 14px rgba(212,175,55,.28); }
        .brand h1 { font-size:1.7rem; font-weight:800; letter-spacing:.12em;
            margin:0; color:var(--text); text-transform:uppercase; }
        .brand h1 span { color:var(--gold); }
        .brand .sub { color:var(--muted); font-size:.82rem; letter-spacing:.04em; margin:.15rem 0 0; }
        .rule { height:2px; margin:.6rem 0 1.2rem;
            background:linear-gradient(90deg, var(--gold), rgba(212,175,55,0)); }

        h2,h3,h4 { color:var(--text) !important; letter-spacing:.01em; }
        h4 { border-left:3px solid var(--gold); padding-left:.55rem; }

        /* Metric cards */
        [data-testid="stMetric"] { background:linear-gradient(160deg,var(--panel-2),var(--panel));
            border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
        [data-testid="stMetricLabel"] p { color:var(--muted); font-size:.72rem;
            text-transform:uppercase; letter-spacing:.09em; }
        [data-testid="stMetricValue"] { color:var(--gold); font-weight:700; }

        /* Buttons */
        .stButton>button, [data-testid="stDownloadButton"]>button {
            border-radius:9px; font-weight:600; letter-spacing:.02em;
            border:1px solid var(--line); background:var(--panel-2); color:var(--text); }
        .stButton>button:hover, [data-testid="stDownloadButton"]>button:hover {
            border-color:var(--gold); color:var(--gold); }
        .stButton>button[kind="primary"], [data-testid="stDownloadButton"]>button[kind="primary"] {
            background:linear-gradient(145deg,#E7CC6E,#C29A2A); color:#0A0A0A !important;
            border:none; box-shadow:0 2px 12px rgba(212,175,55,.25); }
        .stButton>button[kind="primary"]:hover { filter:brightness(1.07); color:#0A0A0A; }

        /* Tabs */
        [data-baseweb="tab-list"] { gap:.3rem; border-bottom:1px solid var(--line); }
        [data-baseweb="tab"] { color:var(--muted); }
        [data-baseweb="tab"][aria-selected="true"] { color:var(--gold) !important; }
        [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { background:var(--gold) !important; }

        /* Sidebar */
        [data-testid="stSidebar"] { background:#0C0B08; border-right:1px solid var(--line); }
        [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
            color:var(--gold) !important; font-size:.95rem; letter-spacing:.06em;
            text-transform:uppercase; }

        /* Inputs focus */
        input:focus, textarea:focus { border-color:var(--gold) !important; }
        a { color:var(--gold) !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def brand_header():
    st.markdown(
        """
        <div class="brand">
          <div class="mark">◆</div>
          <div>
            <h1>DCF&nbsp;<span>Terminal</span></h1>
            <p class="sub">Semi-automatische Unternehmensbewertung · Daten via Yahoo Finance</p>
          </div>
        </div>
        <div class="rule"></div>
        """,
        unsafe_allow_html=True,
    )

# Scalar assumption widgets — keyed so scenarios can rehydrate them.
SCALAR_KEYS = ["forecast_years", "tax_rate", "wacc_mode", "wacc_override", "rf", "erp",
               "beta", "kd", "ew", "perp_g", "exit_mult", "net_debt", "minorities",
               "pension", "associates", "mid_year"]

DRIVER_COLS = ["Wachstum %", "EBITDA-Marge %", "D&A %", "Capex %", "ΔNWC %"]


@st.cache_data(show_spinner="Lade Finanzdaten & Analystenschätzungen …")
def load_company(ticker: str):
    return fetch_company_data(ticker)


@st.cache_data(show_spinner="Suche Unternehmen …")
def search_cached(query: str):
    return search_companies(query)


@st.cache_data(show_spinner="Lade Vergleichsunternehmen …")
def peers_cached(tickers: tuple):
    return fetch_peer_multiples(list(tickers))


def pct(x) -> str:
    return f"{x:.1%}" if x is not None and not (isinstance(x, float) and np.isnan(x)) else "–"


def scalars_now() -> dict:
    """Snapshot the current scalar widget values (display units) from state."""
    return {k: st.session_state.get(k) for k in SCALAR_KEYS}


def assemble_assumptions(scalars: dict, drivers: dict, data, dflt: Assumptions) -> Assumptions:
    """Build an Assumptions object from a scenario dict (scalars in display units,
    drivers as percent lists). Shared by the live model and the comparison tab.

    Streamlit drops the session_state of widgets that are not currently rendered
    (e.g. the CAPM build-up sliders when "WACC direkt vorgeben" is active), so any
    scalar may be missing/None — `num()` falls back to the company defaults, which
    are harmless because the inactive inputs don't affect the result anyway.
    """
    def num(key, fb):
        v = scalars.get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(fb)

    n = int(num("forecast_years", dflt.forecast_years))

    def col(name, fb):
        vals = [float(v) / 100 for v in list(drivers.get(name, []))[:n]]
        return vals or [fb]

    gp = col("Wachstum %", dflt.initial_revenue_growth)
    mp = col("EBITDA-Marge %", dflt.ebitda_margin)
    dp = col("D&A %", dflt.da_pct_revenue)
    cp = col("Capex %", dflt.capex_pct_revenue)
    wp = col("ΔNWC %", dflt.nwc_pct_revenue_change)
    direct = scalars.get("wacc_mode") == "Direkt vorgeben"
    ew = num("ew", dflt.equity_weight * 100) / 100
    return Assumptions(
        forecast_years=n,
        revenue_growth_path=gp, ebitda_margin_path=mp, da_pct_path=dp,
        capex_pct_path=cp, nwc_pct_path=wp, growth_source="manual",
        initial_revenue_growth=gp[0], terminal_revenue_growth=num("perp_g", dflt.perpetuity_growth * 100) / 100,
        ebitda_margin=mp[0], da_pct_revenue=dp[0], capex_pct_revenue=cp[0],
        nwc_pct_revenue_change=wp[0], tax_rate=num("tax_rate", dflt.tax_rate * 100) / 100,
        risk_free=num("rf", dflt.risk_free * 100) / 100,
        equity_risk_premium=num("erp", dflt.equity_risk_premium * 100) / 100,
        beta=num("beta", dflt.beta), pretax_cost_of_debt=num("kd", dflt.pretax_cost_of_debt * 100) / 100,
        equity_weight=ew, debt_weight=1 - ew,
        wacc_override=num("wacc_override", dflt.wacc() * 100) / 100 if direct else None,
        perpetuity_growth=num("perp_g", dflt.perpetuity_growth * 100) / 100,
        exit_ebitda_multiple=num("exit_mult", dflt.exit_ebitda_multiple),
        net_debt_override=num("net_debt", data.net_debt),
        minority_interests=num("minorities", dflt.minority_interests),
        pension_liability=num("pension", dflt.pension_liability),
        associates=num("associates", dflt.associates),
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
        # Skip None (e.g. build-up sliders saved while WACC was set directly) so
        # we don't feed None into a widget's session_state.
        if k in SCALAR_KEYS and v is not None:
            ss[k] = v
    drivers = scn.get("drivers")
    if drivers:
        df = pd.DataFrame(drivers)
        ss["drivers_base"] = df
        ss["forecast_years"] = int(len(df))
        ss["drivers_sig"] = (data.ticker, int(len(df)))
        ss["drivers_nonce"] = ss.get("drivers_nonce", 0) + 1


# --------------------------------------------------------------------------
# Header + company search (by name or ticker)
# --------------------------------------------------------------------------
inject_theme()
brand_header()

with st.sidebar:
    st.header("1 · Unternehmen")
    query = st.text_input("Unternehmen oder Ticker", key="query",
                          placeholder="z. B. Apple, Volkswagen, AAPL, SAP.DE",
                          help="Firmennamen oder Ticker-Symbol eingeben und suchen.")
    do_search = st.button("🔍 Suchen", type="primary", use_container_width=True)

# Run the search; auto-select on an exact ticker or single hit.
if do_search and query.strip():
    st.session_state["search_results"] = search_cached(query.strip())
    res = st.session_state["search_results"]
    q_up = query.strip().upper()
    exact = [r for r in res if r["symbol"].upper() == q_up]
    if exact:
        st.session_state["pending_load"] = exact[0]["symbol"]
    elif len(res) == 1:
        st.session_state["pending_load"] = res[0]["symbol"]

# Result picker (only when there is a choice to make)
results = st.session_state.get("search_results", [])
with st.sidebar:
    if results and not st.session_state.get("pending_load"):
        labels = [f"{r['name']} · {r['symbol']} · {r['exchange']}" for r in results]
        idx = st.selectbox("Treffer wählen", range(len(results)),
                           format_func=lambda i: labels[i], key="search_pick")
        if st.button(f"📥 Laden: {results[idx]['symbol']}", type="primary", use_container_width=True):
            st.session_state["pending_load"] = results[idx]["symbol"]
    elif do_search and query.strip() and not results:
        st.warning("Keine Treffer. Prüfe die Schreibweise oder gib das Ticker-Symbol ein.")

# Decide what to load: initial default, or a user pick
target = "AAPL" if "data" not in st.session_state else None
if st.session_state.get("pending_load"):
    target = st.session_state.pop("pending_load")

if target:
    try:
        data = load_company(target)
    except Exception as e:  # noqa: BLE001
        st.error(f"❌ {e}")
        st.stop()
    st.session_state["ticker"] = data.ticker
    st.session_state["data"] = data
    st.session_state["defaults"] = default_assumptions(data)
    reset_inputs(data, st.session_state["defaults"])
    st.session_state["scenarios"] = {}   # comparison set is per-company
    for k in ("search_results", "peers", "histmult", "mc_result"):
        st.session_state.pop(k, None)

if "data" not in st.session_state:
    st.stop()
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
          f"{result.premium_perpetuity:+.1%} vs. Markt", delta_color="off")
r2.metric("Impliziter Kurs — Exit-Multiple", f"{result.price_exit:,.2f} {pccy}",
          f"{result.premium_exit:+.1%} vs. Markt", delta_color="off")
avg = (result.price_perpetuity + result.price_exit) / 2
r3.metric("Ø beider Methoden", f"{avg:,.2f} {pccy}", f"{avg / data.price - 1:+.1%} vs. Markt",
          delta_color="off")

if result.price_perpetuity < 0 or result.equity_perpetuity < 0:
    st.warning(
        "⚠️ Impliziter Kurs negativ — typisch bei Unternehmen mit großem Finanz-/Leasingarm "
        "(z. B. Autohersteller, Banken), deren ausgewiesene Net Debt sehr hoch ist "
        f"({data.net_debt:,.0f} Mio {ccy}). Passe **Net Debt** in der Seitenleiste unter "
        "*EV → Equity Bridge* an die rein operative Nettoverschuldung an."
    )

# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------
tab_val, tab_fc, tab_sens, tab_models, tab_cmp, tab_exp = st.tabs(
    ["🎯 Bewertung", "📊 Prognose", "🌡️ Sensitivität", "🧮 Modelle",
     "⚖️ Vergleich", "💾 Export & Szenarien"])

with tab_val:
    st.markdown("#### Bewertungsspanne (Football Field)")
    s = result.sensitivity
    all_prices = [v for row in s["price_growth"] for v in row] + \
                 [v for row in s["price_multiple"] for v in row]
    ff_rows = [
        {"method": "DCF · Perpetuity", "low": result.price_perpetuity,
         "high": result.price_perpetuity, "point": result.price_perpetuity, "color": charts.GOLD},
        {"method": "DCF · Exit-Multiple", "low": result.price_exit,
         "high": result.price_exit, "point": result.price_exit, "color": charts.BRONZE},
        {"method": "DCF · Sensitivität", "low": min(all_prices), "high": max(all_prices),
         "point": result.price_perpetuity, "color": charts.GOLD_DEEP},
    ]
    t = data.price_targets
    if t.get("low") and t.get("high"):
        ff_rows.append({"method": "Analysten-Kursziele", "low": t["low"], "high": t["high"],
                        "point": t.get("mean", (t["low"] + t["high"]) / 2), "color": charts.CREAM})
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
            tt = (v - lo) / rng                      # 0 = low (red) → 1 = high (gold)
            r = int(150 + (212 - 150) * tt); g = int(45 + (175 - 45) * tt); b = int(38 + (55 - 38) * tt)
            txt = "#141208" if tt > 0.55 else "#F2ECD8"
            return f"background-color: rgb({r},{g},{b}); color:{txt}"
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

with tab_models:
    r_eq = assumptions.cost_of_equity()
    st.caption(f"Eigenkapitalkosten (CAPM) für die Equity-Modelle: **{r_eq:.2%}** · "
               f"WACC: **{result.wacc:.2%}** · alle Kurse in {pccy}.")

    # --- overview of the cheap, always-available models -------------------
    ddm_g_def = min(data.dividend_growth if data.dividend_growth is not None else 0.03, r_eq - 0.01)
    earn_g_def = float(np.clip(np.mean(assumptions.revenue_growth_path), -0.05, 0.20))
    ny = assumptions.forecast_years
    payout_def = data.payout_ratio if data.payout_ratio is not None else 0.4

    ov = [{"Methode": "DCF · Perpetuity", "Kurs": result.price_perpetuity},
          {"Methode": "DCF · Exit-Multiple", "Kurs": result.price_exit}]
    _v = val.gordon_ddm(data.dividend_ps, r_eq, ddm_g_def)
    if _v:
        ov.append({"Methode": "DDM (Gordon)", "Kurs": _v})
    _v = val.residual_income(data.eps, data.book_value_ps, r_eq, earn_g_def, ny, payout_def,
                             assumptions.perpetuity_growth)
    if _v:
        ov.append({"Methode": "Residual Income", "Kurs": _v})
    _v = val.future_income(data.eps, r_eq, earn_g_def, ny, assumptions.perpetuity_growth)
    if _v:
        ov.append({"Methode": "Future Income", "Kurs": _v})

    st.markdown("#### Methodenübersicht")
    st.altair_chart(charts.methods_bar(ov, data.price, pccy), use_container_width=True)
    st.caption("Standard-Parameter; Feineinstellung in den Reitern unten. Rote Linie = Marktkurs.")

    m_rev, m_rel, m_hist, m_ddm, m_ri, m_fi, m_mc = st.tabs(
        ["Reverse DCF", "Relative (Comps)", "Hist. Multiples", "DDM",
         "Residual Income", "Future Income", "Monte Carlo"])

    # ---- Reverse DCF -----------------------------------------------------
    with m_rev:
        st.markdown("**Welche Annahme preist der Markt ein?** Löse die DCF rückwärts "
                    "auf den Zielkurs.")
        param_label = {"Umsatzwachstum (konstant p.a.)": "growth", "EBITDA-Marge": "margin",
                       "WACC": "wacc", "Terminal Growth": "terminal_growth",
                       "Exit-EBITDA-Multiple": "exit_multiple"}
        rc1, rc2 = st.columns(2)
        choice = rc1.selectbox("Annahme", list(param_label), key="rev_param")
        tgt = rc2.number_input(f"Zielkurs ({pccy})", value=float(round(data.price, 2)),
                               step=1.0, key="rev_target")
        param = param_label[choice]
        x = val.reverse_solve(data, assumptions, param, tgt)
        if x is None:
            st.warning("Kein Wert im plausiblen Bereich — der Zielkurs lässt sich mit dieser "
                       "Annahme allein nicht erreichen. Andere Annahme wählen.")
        else:
            disp = f"{x:.1f}x" if param == "exit_multiple" else f"{x:.1%}"
            st.metric(f"Impliziert: {choice}", disp)
            st.caption(f"Ein Kurs von {tgt:,.2f} {pccy} entspricht **{disp}** — bei sonst "
                       "unveränderten Annahmen.")

    # ---- Relative valuation (comps) --------------------------------------
    with m_rel:
        st.markdown(f"**Peer-Multiples** (Median) angewandt auf {data.ticker}. "
                    f"Branche: *{data.sector or '–'} / {data.industry or '–'}*.")
        pc1, pc2 = st.columns([3, 1])
        peers_str = pc1.text_input("Vergleichs-Ticker (kommagetrennt)", key="peers_in",
                                   placeholder="z. B. MSFT, GOOGL, DELL, HPQ")
        if pc2.button("Comps laden", use_container_width=True, key="peers_btn") and peers_str.strip():
            tks = tuple(t.strip().upper() for t in peers_str.split(",") if t.strip())
            st.session_state["peers"] = peers_cached(tks)
        peers = st.session_state.get("peers", [])
        if peers:
            rv = val.relative_valuation(data, peers)
            dfp = pd.DataFrame(peers)
            show = dfp[["ticker", "name", "pe", "forward_pe", "ev_ebitda", "ev_sales", "pb"]].copy()
            show.columns = ["Ticker", "Name", "P/E", "Fwd P/E", "EV/EBITDA", "EV/Umsatz", "P/B"]
            st.dataframe(show.style.format({c: "{:.1f}" for c in
                         ["P/E", "Fwd P/E", "EV/EBITDA", "EV/Umsatz", "P/B"]}, na_rep="–"),
                         hide_index=True, use_container_width=True)
            if rv["prices"]:
                rows = [{"Methode": k, "Kurs": v} for k, v in rv["prices"].items()]
                st.altair_chart(charts.methods_bar(rows, data.price, pccy), use_container_width=True)
        else:
            st.info("Gib Vergleichsunternehmen (Ticker) ein und lade die Comps.")

    # ---- Historical multiples --------------------------------------------
    with m_hist:
        st.markdown("**Eigene historische Multiples** (Ø der letzten Jahre) auf aktuelle Kennzahlen.")
        if st.button("Historische Multiples berechnen", key="hist_btn"):
            with st.spinner("Lade Kurshistorie …"):
                st.session_state["histmult"] = historical_multiples(data.ticker, data)
        hm = st.session_state.get("histmult")
        if hm and (hm.get("pe_avg") or hm.get("ev_ebitda_avg")):
            hv = val.historical_valuation(data, hm)
            hc1, hc2 = st.columns(2)
            hc1.metric("Ø P/E (historisch)", f"{hm['pe_avg']:.1f}x" if hm.get("pe_avg") else "–")
            hc2.metric("Ø EV/EBITDA (historisch)", f"{hm['ev_ebitda_avg']:.1f}x" if hm.get("ev_ebitda_avg") else "–")
            if hv["prices"]:
                rows = [{"Methode": k, "Kurs": v} for k, v in hv["prices"].items()]
                st.altair_chart(charts.methods_bar(rows, data.price, pccy), use_container_width=True)
            st.caption("⚠️ Näherung: Aktienanzahl und Net Debt werden mit heutigen Werten angesetzt.")
        elif hm is not None:
            st.warning("Keine ausreichende Historie verfügbar.")
        else:
            st.info("Auf den Button klicken, um die historischen Multiples zu berechnen.")

    # ---- DDM -------------------------------------------------------------
    with m_ddm:
        if not data.dividend_ps:
            st.info(f"{data.name} zahlt aktuell keine Dividende — DDM nicht anwendbar.")
        else:
            st.markdown(f"Dividende je Aktie: **{data.dividend_ps:.2f} {pccy}** · "
                        f"Payout: {pct(data.payout_ratio)}")
            d1, d2, d3 = st.columns(3)
            r = d1.number_input("Eigenkapitalkosten r (%)", value=round(r_eq * 100, 2),
                                step=0.25, key="ddm_r") / 100
            g1 = d2.number_input("Wachstum Stufe 1 (%)",
                                 value=round((data.dividend_growth or 0.04) * 100, 2),
                                 step=0.5, key="ddm_g1") / 100
            yrs = d3.number_input("Jahre Stufe 1", 1, 20, 5, key="ddm_years")
            g2 = st.slider("Ewiges Wachstum Stufe 2 (%)", -1.0, 6.0, 2.0, 0.25, key="ddm_g2") / 100
            gordon = val.gordon_ddm(data.dividend_ps, r, min(g1, r - 0.005))
            two = val.two_stage_ddm(data.dividend_ps, r, g1, int(yrs), min(g2, r - 0.005))
            o1, o2 = st.columns(2)
            o1.metric("Gordon-Growth-Wert", f"{gordon:,.2f} {pccy}" if gordon else "n.a.",
                      f"{gordon/data.price-1:+.1%}" if gordon else None, delta_color="off")
            o2.metric("2-Stufen-DDM", f"{two:,.2f} {pccy}" if two else "n.a.",
                      f"{two/data.price-1:+.1%}" if two else None, delta_color="off")
            if (gordon and g1 >= r) or (two and g2 >= r):
                st.caption("Hinweis: Wachstum muss kleiner als r sein.")

    # ---- Residual Income -------------------------------------------------
    with m_ri:
        if data.eps is None or data.book_value_ps is None:
            st.info("EPS oder Buchwert nicht verfügbar — Residual-Income-Modell nicht anwendbar.")
        else:
            st.markdown(f"EPS: **{data.eps:.2f}** · Buchwert je Aktie: **{data.book_value_ps:.2f} {pccy}**")
            ri1, ri2, ri3 = st.columns(3)
            r = ri1.number_input("Eigenkapitalkosten r (%)", value=round(r_eq * 100, 2),
                                 step=0.25, key="ri_r") / 100
            g = ri2.number_input("Gewinnwachstum (%)", value=round(earn_g_def * 100, 2),
                                 step=0.5, key="ri_g") / 100
            yrs = ri3.number_input("Prognosejahre", 3, 20, ny, key="ri_years")
            rp1, rp2 = st.columns(2)
            payout = rp1.slider("Ausschüttungsquote (%)", 0.0, 100.0,
                                float(round(payout_def * 100, 0)), 5.0, key="ri_payout") / 100
            tg = rp2.slider("Terminales RI-Wachstum (%)", -1.0, 5.0, 2.0, 0.25, key="ri_tg") / 100
            v = val.residual_income(data.eps, data.book_value_ps, r, g, int(yrs), payout, tg)
            st.metric("Residual-Income-Wert je Aktie", f"{v:,.2f} {pccy}" if v else "n.a.",
                      f"{v/data.price-1:+.1%}" if v else None, delta_color="off")
            st.caption("V = Buchwert + Σ Barwert der Residualgewinne (EPS − r·Buchwert) + Terminalwert.")

    # ---- Future Income ---------------------------------------------------
    with m_fi:
        if data.eps is None or data.eps <= 0:
            st.info("Kein positives EPS verfügbar — Future-Income-Modell nicht anwendbar.")
        else:
            st.markdown(f"Aktuelles EPS: **{data.eps:.2f} {pccy}** — diskontierte künftige Gewinne.")
            f1, f2, f3 = st.columns(3)
            r = f1.number_input("Eigenkapitalkosten r (%)", value=round(r_eq * 100, 2),
                                step=0.25, key="fi_r") / 100
            g = f2.number_input("Gewinnwachstum (%)", value=round(earn_g_def * 100, 2),
                                step=0.5, key="fi_g") / 100
            yrs = f3.number_input("Prognosejahre", 3, 20, ny, key="fi_years")
            tg = st.slider("Ewiges Gewinnwachstum (%)", -1.0, 5.0, 2.0, 0.25, key="fi_tg") / 100
            v = val.future_income(data.eps, r, g, int(yrs), tg)
            st.metric("Future-Income-Wert je Aktie", f"{v:,.2f} {pccy}" if v else "n.a.",
                      f"{v/data.price-1:+.1%}" if v else None, delta_color="off")
            st.caption("V = Σ Barwert projizierter Gewinne je Aktie + Terminalwert (mit r diskontiert).")

    # ---- Monte Carlo -----------------------------------------------------
    with m_mc:
        st.markdown("**Monte-Carlo-Simulation** der DCF: Wachstum, Marge, WACC und Terminal "
                    "Growth werden zufällig um die aktuellen Annahmen variiert.")
        mc1, mc2, mc3 = st.columns(3)
        n_sims = mc1.select_slider("Simulationen", [500, 1000, 2000, 5000], value=2000, key="mc_n")
        sig_g = mc2.slider("σ Wachstum (pp)", 0.5, 8.0, 3.0, 0.5, key="mc_sg") / 100
        sig_m = mc3.slider("σ EBITDA-Marge (pp)", 0.5, 8.0, 3.0, 0.5, key="mc_sm") / 100
        mc4, mc5 = st.columns(2)
        sig_w = mc4.slider("σ WACC (pp)", 0.25, 3.0, 1.0, 0.25, key="mc_sw") / 100
        sig_tg = mc5.slider("σ Terminal Growth (pp)", 0.1, 2.0, 0.5, 0.1, key="mc_stg") / 100
        if st.button("▶ Simulation starten", type="primary", key="mc_run"):
            with st.spinner(f"Simuliere {n_sims} Szenarien …"):
                st.session_state["mc_result"] = val.monte_carlo(
                    data, assumptions, n_sims=int(n_sims), sig_growth=sig_g,
                    sig_margin=sig_m, sig_wacc=sig_w, sig_tg=sig_tg, mid_year=mid_year)
        mcres = st.session_state.get("mc_result")
        if mcres and mcres.get("stats"):
            s2 = mcres["stats"]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Median", f"{s2['p50']:,.0f} {pccy}")
            k2.metric("P5 – P95", f"{s2['p5']:,.0f} – {s2['p95']:,.0f}")
            k3.metric("Mittelwert", f"{s2['mean']:,.0f} {pccy}")
            k4.metric("P(Kurs > Markt)", f"{s2['prob_above_market']:.0%}")
            st.altair_chart(charts.monte_carlo_hist(mcres["prices"], data.price, s2["p50"], pccy),
                            use_container_width=True)
            st.caption("Rote Linie = Marktkurs, helle Linie = Median der Simulation.")
        else:
            st.info("Parameter wählen und **Simulation starten**.")

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
