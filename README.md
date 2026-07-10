# Automatische DCF-Analyse

Semi-automatische Discounted-Cash-Flow-Bewertung nach dem Vorbild eines
JP-Morgan-Banking-Modells. Ticker eingeben → Finanzdaten werden automatisch von
Yahoo Finance geholt → sinnvolle Standard-Annahmen werden abgeleitet → alle
Annahmen live per Regler anpassbar → jederzeit als **Excel-Modell mit lebenden
Formeln** herunterladbar.

## Was automatisch kommt und was du steuerst

**Automatisch aus dem Internet (yfinance, weltweit):**
- Aktueller Kurs, Aktienanzahl, Net Debt, Beta
- Historische Zahlen: Umsatz, EBITDA, D&A, EBIT, Steuersatz, Capex, Working Capital
- **Analystenkonsens**: Umsatzwachstumsschätzungen (kurzfristig + LTG) und Kursziele

**Als Default vorbefüllt, von dir anpassbar:**
- **Wachstumskurve pro Jahr** — als editierbare Tabelle; Default aus Analystenkonsens
  (kurzfristige Schätzungen → Fade zum Terminal-Wachstum) oder linearem Fade
- **Treiber-Kurven pro Jahr** in einer editierbaren Tabelle: Umsatzwachstum,
  EBITDA-Marge, D&A %, Capex %, ΔNWC % — jeder Wert einzeln anpassbar
- Steuersatz, WACC (CAPM-Aufbau oder direkt), Perpetuity Growth, Exit-Multiple
- EV→Equity-Bridge (Net Debt, Minderheiten, Pension, Beteiligungen)

**Szenarien:** aktuelle Annahmen + Treiber-Tabelle als JSON speichern und später
wieder laden (Seitenleiste). Im Tab **⚖️ Vergleich** mehrere Varianten
(z. B. Bull / Base / Bear) sammeln und nebeneinander vergleichen — Kennzahlen-
Tabelle plus Charts (impliziter Kurs je Szenario, Wachstumskurven).

**Visualisierung:** Wachstumskurve, Treiber-Kurven, Umsatz/EBITDA, Free Cash Flow
& Barwert, Wertzusammensetzung des EV, und ein **Football-Field-Chart**
(DCF-Methoden + Sensitivitätsspanne vs. Analysten-Kursziele vs. aktueller Kurs).

Eine DCF lebt von den Zukunfts-Annahmen — die kann keine API liefern. Deshalb
semi-automatisch: die Maschine liefert einen fundierten Startpunkt, du triffst
die Urteile.

## Installation

```bash
pip install -r requirements.txt
```

## Starten

```bash
streamlit run streamlit_app.py
```

Die App öffnet sich im Browser (Standard: http://localhost:8501).
Ticker-Beispiele: `AAPL`, `MSFT`, `SAP.DE`, `ULVR.L`, `AIR.PA`, `7203.T`.

## Online stellen (Streamlit Community Cloud, kostenlos)

So wird aus der App eine echte Webseite mit fester URL. Du brauchst einen
(kostenlosen) **GitHub-** und **Streamlit-Account**.

1. **GitHub-Repo anlegen** und diesen Ordner hochladen. Ist lokal bereits ein
   Git-Repo mit erstem Commit vorbereitet — es fehlt nur das Remote:
   ```bash
   # Repo bei github.com anlegen (z. B. "dcf-analyse"), dann:
   git remote add origin https://github.com/<DEIN-NAME>/dcf-analyse.git
   git branch -M main
   git push -u origin main
   ```
2. Auf **https://share.streamlit.io** mit GitHub anmelden → **"Create app"** →
   **"Deploy a public app from GitHub"**.
3. Repository wählen, **Branch** `main`, **Main file path** `streamlit_app.py`.
4. Unter *Advanced settings* **Python 3.11** (oder 3.12/3.13) wählen. Klick auf
   **Deploy** — nach ~2 Minuten läuft die App unter
   `https://<name>.streamlit.app`.

Änderungen: einfach ins Repo pushen (`git push`) — die Cloud deployt automatisch
neu. `requirements.txt` und `.streamlit/config.toml` sind bereits enthalten.

> Hinweis: Die kostenlose Community Cloud macht die App öffentlich erreichbar.
> Sie enthält keine Geheimnisse (holt nur öffentliche Kursdaten live), das ist
> also unkritisch. Für privaten Zugriff bräuchtest du den kostenpflichtigen Plan
> oder eine der anderen Hosting-Varianten.

## Methodik

- **Unlevered FCF** = EBIT·(1−Steuer) + D&A − Capex + Δ Working Capital
- **Terminal Value** auf zwei Wegen: Perpetuity Growth `FCF·(1+g)/(WACC−g)`
  und Exit-EBITDA-Multiple `EBITDA·Multiple`
- Diskontierung mit Mid-Year-Convention; der Terminal Value wird mit dem
  Diskontfaktor des letzten Prognosejahres abgezinst (wie im Vorbild-Template)
- EV → Equity über die Net-Debt-Bridge → impliziter Kurs (inkl. Pence/Cent-
  Umrechnung bei Börsen mit Minor-Unit-Notierung wie London)
- Sensitivitäten: WACC × Perpetuity Growth und WACC × Exit-Multiple

## Projektstruktur

```
streamlit_app.py       Streamlit-Web-App (UI, Treiber-Tabelle, Regler, Tabs, Vergleich, Download)
charts.py              Altair-Charts (Wachstumskurve, FCF, Football-Field, ...)
dcf/
  data.py              Daten + Analystenkonsens holen & normalisieren (yfinance)
  assumptions.py       Standard-Annahmen ableiten (Konsens-Wachstumskurve, CAPM-WACC)
  model.py             DCF-Rechenmaschine + Sensitivitäten
  excel_export.py      Excel-Export mit lebenden Formeln + Charts (JP-Morgan-Layout)
```

## Excel-Export

Der Download enthält fünf Blätter: **Summary**, **Assumptions** (gelbe Zellen =
editierbar), **DCF** (Formeln; Wachstum, EBITDA-Marge, D&A, Capex und ΔNWC pro
Jahr editierbar), **Charts** (Balken-/Linien-/Kreisdiagramme) und **Sensitivity**
(mit Farbskala). Änderst du eine Treiber- oder Annahme-Zelle in Excel, rechnet die
Datei die komplette DCF neu und die Charts aktualisieren sich — voll
weiterverwendbar.

## Hinweis

Datenqualität und -verfügbarkeit von Yahoo Finance schwanken je nach Markt.
Bei Lücken werden Defaults aus Branchennäherungen gesetzt (Warnung erscheint).
Keine Anlageberatung.
