"""Altair chart builders for the DCF app (kept out of app.py for readability).

All charts use a shared palette and return `alt.Chart` objects sized to the
container. Colours are chosen to read on Streamlit's dark theme.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

# Black / gold / red palette. Legacy names kept as aliases so existing chart
# code needn't change — they now map onto the gold-red-neutral scheme.
GOLD = "#D4AF37"
GOLD_LIGHT = "#E7CC6E"
GOLD_DEEP = "#A67C1F"
BRONZE = "#8C6D2A"
RED = "#C0392B"
RED_BRIGHT = "#E24A3B"
GREY = "#8A8377"
CREAM = "#E8E0C8"

# Aliases used across the chart builders
NAVY = GOLD_DEEP
BLUE = GOLD
TEAL = BRONZE
AMBER = GOLD_LIGHT
GREEN = GOLD

_AXIS = alt.Axis(labelColor="#CFC7B2", titleColor="#CFC7B2", gridColor="#26221A")


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
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
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
                        scale=alt.Scale(domain=["Unlevered FCF", "Barwert (PV)"], range=[GOLD, BRONZE]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
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
                                        range=[GOLD, GOLD_LIGHT, RED, GREY]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
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
                        scale=alt.Scale(domain=["PV der FCF", "PV Terminal Value"], range=[GOLD, BRONZE]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
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
        y=alt.Y("method:N", sort=order, axis=alt.Axis(title=None, labelColor="#CFC7B2", labelLimit=200)),
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
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
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
        color=alt.Color("Szenario:N",
                        scale=alt.Scale(range=[GOLD, RED, CREAM, BRONZE, GOLD_LIGHT, GREY, RED_BRIGHT]),
                        legend=alt.Legend(orient="top", title=None, labelColor="#CFC7B2")),
        tooltip=["Szenario:N", "Jahr:O", alt.Tooltip("Wachstum:Q", format=".1%")],
    ).properties(height=280)


def methods_bar(rows: list[dict], market_price: float, ccy: str) -> alt.Chart:
    """rows: [{'Methode','Kurs'}] — implied price per valuation method vs market."""
    df = pd.DataFrame(rows)
    order = list(df["Methode"])
    bars = alt.Chart(df).mark_bar(height=20, cornerRadius=3, color=GOLD).encode(
        y=alt.Y("Methode:N", sort=order, axis=alt.Axis(title=None, labelColor="#CFC7B2", labelLimit=220)),
        x=alt.X("Kurs:Q", axis=alt.Axis(title=f"Impliziter Kurs ({ccy})", **_axis_kwargs())),
        tooltip=["Methode:N", alt.Tooltip("Kurs:Q", format=",.2f")],
    )
    labels = alt.Chart(df).mark_text(align="left", dx=4, color="#EDE9DE", fontSize=11).encode(
        y=alt.Y("Methode:N", sort=order), x="Kurs:Q", text=alt.Text("Kurs:Q", format=",.0f"))
    cur = pd.DataFrame({"p": [market_price]})
    rule = alt.Chart(cur).mark_rule(color=RED, strokeDash=[6, 4], strokeWidth=2).encode(x="p:Q")
    rtxt = alt.Chart(cur).mark_text(color=RED, dy=-6, align="left", dx=3, fontSize=11,
                                    fontWeight="bold").encode(x="p:Q", text=alt.value(f"Kurs {market_price:,.0f}"))
    return (bars + labels + rule + rtxt).properties(height=max(160, 40 * len(rows)))


def monte_carlo_hist(prices, market_price: float, median: float, ccy: str) -> alt.Chart:
    df = pd.DataFrame({"Kurs": prices})
    hist = alt.Chart(df).mark_bar(color=GOLD, opacity=0.85).encode(
        x=alt.X("Kurs:Q", bin=alt.Bin(maxbins=40),
                axis=alt.Axis(title=f"Impliziter Kurs ({ccy})", **_axis_kwargs())),
        y=alt.Y("count()", axis=alt.Axis(title="Simulationen", **_axis_kwargs())),
        tooltip=[alt.Tooltip("count()", title="Anzahl")],
    )
    m = alt.Chart(pd.DataFrame({"p": [market_price]})).mark_rule(
        color=RED, strokeWidth=2, strokeDash=[6, 4]).encode(x="p:Q")
    med = alt.Chart(pd.DataFrame({"p": [median]})).mark_rule(
        color=CREAM, strokeWidth=2).encode(x="p:Q")
    return (hist + m + med).properties(height=300)


def _axis_kwargs() -> dict:
    return {"labelColor": "#CFC7B2", "titleColor": "#CFC7B2", "gridColor": "#26221A"}
