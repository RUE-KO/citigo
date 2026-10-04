import streamlit as st
import requests
import datetime
import zoneinfo

# --- SEITEN-KONFIGURATION ---
st.set_page_config(
    page_title="EV Ladeplaner",
    page_icon="⚡",
    layout="centered"
)

st.markdown("""
    <style>
        .block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
        div[data-testid="stMetricValue"] {font-size: 1.0rem !important;}
        div[data-testid="stMetricLabel"] {font-size: 0.75rem !important;}
        
        [data-testid="stHorizontalBlock"] {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.5rem !important;
        }
        [data-testid="stHorizontalBlock"] > div {
            width: 50% !important;
            min-width: 0 !important;
        }
    </style>
""", unsafe_allow_html=True)

st.title("⚡ EV Ladeplaner")

TZ_BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")

# --- SEITENLEISTE / EINGABEN ---
st.sidebar.header("Fahrzeug & Einstellungen")

fahrzeug = st.sidebar.selectbox(
    "Fahrzeug wählen",
    ["Škoda Citigo e-iV", "Škoda Enyaq"]
)

default_akku = 32.3 if fahrzeug == "Škoda Citigo e-iV" else 77.0
default_kw = 7.2 if fahrzeug == "Škoda Citigo e-iV" else 11.0

aktueller_soc = st.sidebar.number_input(
    "Aktueller Akkustand (%)",
    min_value=0.0,
    max_value=100.0,
    value=60.0,
    step=5.0
)

akkugroesse_netto = st.sidebar.number_input("Akkugröße Netto (kWh)", value=default_akku, step=0.1)
ladeleistung_kw = st.sidebar.number_input("Ladeleistung (kW)", value=default_kw, step=0.1)

# --- LOGIK & DATENABRUF ---
@st.cache_data(ttl=300)
def lade_preisdaten():
    raw_timestamps, raw_prices = [], []
    morgen_verfuegbar = False
    source_used = None
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    # 1. Energy-Charts
    try:
        url = "https://api.energy-charts.info/price?bzn=DE-LU"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            raw_ts = data.get("unix_seconds", [])
            raw_pr = data.get("price", []) # EUR / MWh
            if raw_ts and raw_pr:
                raw_timestamps = raw_ts
                raw_prices = [p / 10.0 if p is not None else None for p in raw_pr] # -> Cent/kWh
                source_used = "Energy-Charts"
    except Exception:
        pass

    # 2. Awattar Fallback
    if not raw_timestamps:
        try:
            jetzt_start = int(datetime.datetime.now(TZ_BERLIN).replace(hour=0, minute=0, second=0).timestamp() * 1000)
            url_awattar = f"https://api.awattar.de/v1/marketdata?start={jetzt_start}"
            res = requests.get(url_awattar, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json().get("data", [])
                for eintrag in data:
                    raw_timestamps.append(int(eintrag["start_timestamp"] / 1000))
                    raw_prices.append(eintrag["marketprice"] / 10.0) # -> Cent/kWh
                source_used = "Awattar"
        except Exception:
            pass

    if raw_timestamps:
        jetzt_ts = datetime.datetime.now(TZ_BERLIN).timestamp()
        morgen_date = (datetime.datetime.now(TZ_BERLIN) + datetime.timedelta(days=1)).date()

        # Stunden-Daten in exakte 15-Minuten-Blöcke aufspalten
        ts_15m, prices_15m = [], []
        for ts, p in zip(raw_timestamps, raw_prices):
            if ts + 3600 >= jetzt_ts and p is not None:
                for quarter in range(4):
                    q_ts = ts + (quarter * 900)
                    ts_15m.append(q_ts)
                    prices_15m.append(p)
                    
                    dt = datetime.datetime.fromtimestamp(q_ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                    if dt.date() == morgen_date:
                        morgen_verfuegbar = True
                    
        return ts_15m, prices_15m, morgen_verfuegbar, source_used

    return [], [], False, None

def berechne_tibber_preis(boerse_cent):
    fixkosten = 1.81 + 6.39 + 1.32 + 2.05 + 0.941 + 0.446 + 1.56
    return (boerse_cent + fixkosten) * 1.19

def finde_guenstigstes_fenster_fuer_ziel(timestamps, prices, start_stunde, end_stunde, anzahl_15m_bloecke):
    bestes_fenster, min_schnitt = None, float('inf')
    jetzt_ts = datetime.datetime.now(TZ_BERLIN).timestamp()
    
    if len(timestamps) < anzahl_15m_bloecke:
        return None, 0
        
    for i in range(len(timestamps) - anzahl_15m_bloecke + 1):
        ts = timestamps[i]
        
        # Abgelaufene Blöcke überspringen
        if ts + 900 < jetzt_ts:
            continue
            
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
        h = dt.hour
        
        if start_stunde == 0 and end_stunde == 24:
            im_bereich = True
        elif start_stunde < end_stunde:
            im_bereich = (start_stunde <= h < end_stunde)
        else:
            im_bereich = (h >= start_stunde or h < end_stunde)
            
        if im_bereich:
            fenster_preise = prices[i:i + anzahl_15m_bloecke]
            schnitt = sum(fenster_preise) / anzahl_15m_bloecke
            if schnitt < min_schnitt:
                min_schnitt = schnitt
                bestes_fenster = ts
                
    return bestes_fenster, min_schnitt

timestamps, prices, morgen_da, quelle = lade_preisdaten()

if not timestamps:
    st.error("⚠️ Aktuell kann keine Preis-Schnittstelle erreicht werden. Bitte versuche es gleich erneut.")
else:
    if quelle:
        st.sidebar.caption(f"Datenquelle: {quelle}")
        
    if not morgen_da:
        st.sidebar.info("ℹ️ Preise für morgen stehen erst ab ca. 13:00 Uhr bereit.")

    st.caption(f"**{fahrzeug}** | Akkustand: **{aktueller_soc:.0f}%**")

    kategorien = {
        "🚀 Absolut günstigste Zeit": (0, 24),
        "☀️ Tag / Vormittag (06 - 17 Uhr)": (6, 17),
        "🌙 Nacht (22 - 06 Uhr)": (22, 6),
        "🌆 Abend (17 - 22 Uhr)": (17, 22)
    }

    for kat_name, (von, bis) in kategorien.items():
        with st.expander(kat_name, expanded=True):
            col80, col100 = st.columns(2)
            
            for idx, ziel_soc in enumerate([80.0, 100.0]):
                spalte = col80 if idx == 0 else col100
                
                with spalte:
                    st.markdown(f"**Ziel {int(ziel_soc)}%**")
                    if aktueller_soc >= ziel_soc:
                        st.caption("Bereits erreicht ✅")
                        continue
                        
                    benoetigte_prozent = ziel_soc - aktueller_soc
                    benoetigte_kwh = (benoetigte_prozent / 100.0) * akkugroesse_netto
                    benoetigte_stunden = benoetigte_kwh / ladeleistung_kw
                    
                    # Exakte Anzahl 15-Minuten-Häppchen
                    anzahl_15m_bloecke = int(round((benoetigte_stunden / 0.25), 0))
                    
                    bestes_ts, schnitt_boerse = finde_guenstigstes_fenster_fuer_ziel(
                        timestamps, prices, von, bis, anzahl_15m_bloecke
                    )
                    
                    if bestes_ts:
                        start_dt = datetime.datetime.fromtimestamp(bestes_ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                        end_dt = start_dt + datetime.timedelta(hours=benoetigte_stunden)
                        preis = berechne_tibber_preis(schnitt_boerse)
                        
                        st.metric("Preis", f"~{preis:.2f} ct")
                        st.text(f"🕒 {start_dt.strftime('%d.%m. %H:%M')}\n   bis {end_dt.strftime('%H:%M')}")
                        st.caption(f"⚙️ Abfahrt: **{end_dt.strftime('%H:%M')}**")
                    else:
                        st.caption("Kein Fenster ❌")
