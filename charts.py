"""Altair chart builders for the DCF app (kept out of app.py for readability).

All charts use a shared palette and return `alt.Chart` objects sized to the
container. Colours are chosen to read on Streamlit's dark theme.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

NAVY = "#1F3864"
BLUE = "#4C86C6"
TEAL = "#2CA6A4"
AMBER = "#E8A33D"
GREEN = "#4C9F70"
RED = "#C0504D"
GREY = "#9AA0A6"

_AXIS = alt.Axis(labelColor="#C9D1D9", titleColor="#C9D1D9", gridColor="#2A2E36")


def growth_curve(years, growth) -> alt.Chart:
    df = pd.DataFrame({"Jahr": years, "Wachstum": growth})
    base = alt.Chart(df).encode(
        x=alt.X("Jahr:O", axis=_AXIS, title="Prognosejahr"),
        y=alt.Y("Wachstum:Q", axis=alt.Axis(format="%", **_axis_kwargs()), title="Umsatzwachstum"),
    )
    line = base.mark_line(color=AMBER, strokeWidth=3, point=alt.OverlayMarkDef(color=AMBER, size=70))
    labels = base.mark_text(dy=-12, color="#E6EDF3", fontSize=11).encode(
        text=alt.Text("Wachstum:Q", format=".1%")
    )
    return (line + labels).properties(height=240)


def revenue_ebitda(years, revenue, ebitda, ccy: str) -> alt.Chart:
    df = pd.DataFrame({"Jahr": years, "Umsatz": revenue, "EBITDA": ebitda})
    long = df.melt("Jahr", var_name="Kennzahl", value_name="Wert")
    chart = alt.Chart(long).mark_bar().encode(
        x=alt.X("Jahr:O", axis=_AXIS, title="Prognosejahr"),
        xOffset="Kennzahl:N",
        y=alt.Y("Wert:Q", axis=alt.Axis(format="~s", **_axis_kwargs()), title=f"Mio {ccy}"),
        color=alt.Color("Kennzahl:N",
                        scale=alt.Scale(domain=["Umsatz", "EBITDA"], range=[BLUE, TEAL]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=[alt.Tooltip("Jahr:O"), "Kennzahl:N",
                 alt.Tooltip("Wert:Q", format=",.0f")],
    ).properties(height=280)
    return chart


def fcf_chart(years, ufcf, pv, ccy: str) -> alt.Chart:
    df = pd.DataFrame({"Jahr": years, "Unlevered FCF": ufcf, "Barwert (PV)": pv})
    long = df.melt("Jahr", var_name="Typ", value_name="Wert")
    chart = alt.Chart(long).mark_bar().encode(
        x=alt.X("Jahr:O", axis=_AXIS, title="Prognosejahr"),
        xOffset="Typ:N",
        y=alt.Y("Wert:Q", axis=alt.Axis(format="~s", **_axis_kwargs()), title=f"Mio {ccy}"),
        color=alt.Color("Typ:N",
                        scale=alt.Scale(domain=["Unlevered FCF", "Barwert (PV)"], range=[GREEN, NAVY]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=[alt.Tooltip("Jahr:O"), "Typ:N", alt.Tooltip("Wert:Q", format=",.0f")],
    ).properties(height=280)
    return chart


def driver_curves(years, margin, da, capex, nwc) -> alt.Chart:
    """Operating-driver ratios over the forecast (all as % of revenue)."""
    df = pd.DataFrame({
        "Jahr": years,
        "EBITDA-Marge": margin, "D&A": da, "Capex": capex, "Δ NWC": nwc,
    }).melt("Jahr", var_name="Treiber", value_name="Wert")
    return alt.Chart(df).mark_line(point=True, strokeWidth=2.5).encode(
        x=alt.X("Jahr:O", axis=_AXIS, title="Prognosejahr"),
        y=alt.Y("Wert:Q", axis=alt.Axis(format="%", **_axis_kwargs()), title="% vom Umsatz"),
        color=alt.Color("Treiber:N",
                        scale=alt.Scale(domain=["EBITDA-Marge", "D&A", "Capex", "Δ NWC"],
                                        range=[TEAL, BLUE, AMBER, GREY]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=[alt.Tooltip("Jahr:O"), "Treiber:N", alt.Tooltip("Wert:Q", format=".1%")],
    ).properties(height=260)


def value_composition(pv_fcf: float, pv_tv: float, ccy: str) -> alt.Chart:
    total = pv_fcf + pv_tv or 1.0
    df = pd.DataFrame({
        "Komponente": ["PV der FCF", "PV Terminal Value"],
        "Wert": [pv_fcf, pv_tv],
        "Anteil": [pv_fcf / total, pv_tv / total],
        "y": ["EV", "EV"],
    })
    bar = alt.Chart(df).mark_bar().encode(
        y=alt.Y("y:N", axis=None, title=None),
        x=alt.X("Wert:Q", stack="normalize", axis=alt.Axis(format="%", **_axis_kwargs()),
                title="Anteil am Enterprise Value"),
        color=alt.Color("Komponente:N",
                        scale=alt.Scale(domain=["PV der FCF", "PV Terminal Value"], range=[BLUE, AMBER]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=["Komponente:N", alt.Tooltip("Wert:Q", format=",.0f"),
                 alt.Tooltip("Anteil:Q", format=".1%")],
    )
    text = alt.Chart(df).mark_text(color="white", fontWeight="bold", fontSize=12).encode(
        y=alt.Y("y:N", axis=None),
        x=alt.X("Wert:Q", stack="normalize", bandPosition=0.5),
        detail="Komponente:N",
        text=alt.Text("Anteil:Q", format=".0%"),
    )
    return (bar + text).properties(height=90)


def football_field(rows: list[dict], current_price: float, ccy: str) -> alt.Chart:
    """rows: [{'method','low','high','point','color'}]. Draws value ranges plus a
    dashed line at the current market price."""
    df = pd.DataFrame(rows)
    order = list(df["method"])

    bars = alt.Chart(df).mark_bar(height=18, opacity=0.85, cornerRadius=3).encode(
        y=alt.Y("method:N", sort=order, axis=alt.Axis(title=None, labelColor="#C9D1D9", labelLimit=200)),
        x=alt.X("low:Q", axis=alt.Axis(title=f"Impliziter Kurs ({ccy})", **_axis_kwargs())),
        x2="high:Q",
        color=alt.Color("method:N", sort=order,
                        scale=alt.Scale(domain=order, range=[r.get("color", BLUE) for r in rows]),
                        legend=None),
        tooltip=[alt.Tooltip("method:N", title="Methode"),
                 alt.Tooltip("low:Q", format=",.0f"), alt.Tooltip("high:Q", format=",.0f")],
    )
    points = alt.Chart(df).mark_tick(color="white", thickness=2, size=22).encode(
        y=alt.Y("method:N", sort=order), x="point:Q",
        tooltip=[alt.Tooltip("point:Q", title="Mittelwert", format=",.0f")],
    )
    cur = pd.DataFrame({"p": [current_price]})
    rule = alt.Chart(cur).mark_rule(color=RED, strokeDash=[6, 4], strokeWidth=2).encode(x="p:Q")
    rule_txt = alt.Chart(cur).mark_text(
        color=RED, dy=-6, dx=4, align="left", fontSize=11, fontWeight="bold"
    ).encode(x="p:Q", text=alt.value(f"Kurs {current_price:,.0f}"))
    return (bars + points + rule + rule_txt).properties(height=max(150, 42 * len(rows)))


def scenario_prices(rows: list[dict], current_price: float, ccy: str) -> alt.Chart:
    """rows: [{'Szenario', 'Perpetuity', 'Exit-Multiple'}]. Grouped bars per
    scenario with the current market price as a dashed reference line."""
    df = pd.DataFrame(rows).melt("Szenario", var_name="Methode", value_name="Kurs")
    order = [r["Szenario"] for r in rows]
    bars = alt.Chart(df).mark_bar().encode(
        x=alt.X("Szenario:N", sort=order, axis=alt.Axis(labelAngle=0, **_axis_kwargs()), title=None),
        xOffset="Methode:N",
        y=alt.Y("Kurs:Q", axis=alt.Axis(**_axis_kwargs()), title=f"Impliziter Kurs ({ccy})"),
        color=alt.Color("Methode:N",
                        scale=alt.Scale(domain=["Perpetuity", "Exit-Multiple"], range=[BLUE, TEAL]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=["Szenario:N", "Methode:N", alt.Tooltip("Kurs:Q", format=",.2f")],
    )
    cur = pd.DataFrame({"p": [current_price]})
    rule = alt.Chart(cur).mark_rule(color=RED, strokeDash=[6, 4], strokeWidth=2).encode(y="p:Q")
    txt = alt.Chart(cur).mark_text(color=RED, dy=-6, align="left", fontSize=11,
                                   fontWeight="bold").encode(
        y="p:Q", x=alt.value(4), text=alt.value(f"Kurs {current_price:,.0f}"))
    return (bars + rule + txt).properties(height=300)


def scenario_growth(series: list[dict]) -> alt.Chart:
    """series: [{'Szenario', 'Jahr', 'Wachstum'}] — one growth line per scenario."""
    df = pd.DataFrame(series)
    return alt.Chart(df).mark_line(point=True, strokeWidth=2.5).encode(
        x=alt.X("Jahr:O", axis=_AXIS, title="Prognosejahr"),
        y=alt.Y("Wachstum:Q", axis=alt.Axis(format="%", **_axis_kwargs()), title="Umsatzwachstum"),
        color=alt.Color("Szenario:N", legend=alt.Legend(orient="top", title=None, labelColor="#C9D1D9")),
        tooltip=["Szenario:N", "Jahr:O", alt.Tooltip("Wachstum:Q", format=".1%")],
    ).properties(height=280)


def _axis_kwargs() -> dict:
    return {"labelColor": "#C9D1D9", "titleColor": "#C9D1D9", "gridColor": "#2A2E36"}
