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

# Schlankeres Layout & 2-Spalten-Zwang für Mobilgeräte
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
    url = "https://api.energy-charts.info/price?bzn=DE-LU"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    timestamps, prices = [], []
    morgen_verfuegbar = False
    error_msg = None

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            
            # API Struktur auslesen
            raw_ts = data.get("unix_seconds", [])
            raw_pr = data.get("price", [])
            
            if raw_ts and raw_pr:
                heute_start_ts = datetime.datetime.now(TZ_BERLIN).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).timestamp()

                morgen_date = (datetime.datetime.now(TZ_BERLIN) + datetime.timedelta(days=1)).date()

                for ts, p in zip(raw_ts, raw_pr):
                    if ts >= heute_start_ts and p is not None:
                        timestamps.append(ts)
                        prices.append(p)
                        
                        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                        if dt.date() == morgen_date:
                            morgen_verfuegbar = True
            else:
                error_msg = f"API Antwort enthält keine Daten (keys: {list(data.keys())})"
        else:
            error_msg = f"API Statuscode: {response.status_code}"
    except Exception as e:
        error_msg = f"Netzwerk-/Verbindungsfehler: {str(e)}"

    return timestamps, prices, morgen_verfuegbar, error_msg

def berechne_tibber_preis(boerse_cent):
    fixkosten = 1.81 + 6.39 + 1.32 + 2.05 + 0.941 + 0.446 + 1.56
    return (boerse_cent + fixkosten) * 1.19

def finde_guenstigstes_fenster_fuer_ziel(timestamps, prices, start_stunde, end_stunde, feste_block_groesse):
    bestes_fenster, min_schnitt = None, float('inf')
    
    if len(timestamps) < feste_block_groesse:
        return None, 0
        
    for i in range(len(timestamps) - feste_block_groesse + 1):
        dt = datetime.datetime.fromtimestamp(timestamps[i], tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
        h = dt.hour
        
        if start_stunde == 0 and end_stunde == 24:
            im_bereich = True
        elif start_stunde < end_stunde:
            im_bereich = (start_stunde <= h < end_stunde)
        else:
            im_bereich = (h >= start_stunde or h < end_stunde)
            
        if im_bereich:
            fenster_preise = prices[i:i + feste_block_groesse]
            schnitt = sum(fenster_preise) / feste_block_groesse
            if schnitt < min_schnitt:
                min_schnitt = schnitt
                bestes_fenster = timestamps[i]
                
    return bestes_fenster, min_schnitt

timestamps, prices, morgen_da, err = lade_preisdaten()

if err:
    st.sidebar.error(f"Debug Info: {err}")

if not morgen_da and len(timestamps) > 0:
    st.sidebar.info("ℹ️ Preise für morgen stehen erst ab ca. 13:00 Uhr bereit.")

if not timestamps:
    st.error("Keine Preisdaten verfügbar. Siehe Fehler in der Seitenleiste.")
else:
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
                    block_groesse = int(round((benoetigte_stunden / 0.25), 0))
                    
                    bestes_ts, schnitt_boerse = finde_guenstigstes_fenster_fuer_ziel(
                        timestamps, prices, von, bis, block_groesse
                    )
                    
                    if bestes_ts:
                        start_dt = datetime.datetime.fromtimestamp(bestes_ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                        end_dt = start_dt + datetime.timedelta(hours=benoetigte_stunden)
                        preis = berechne_tibber_preis(schnitt_boerse / 10)
                        
                        st.metric("Preis", f"~{preis:.2f} ct")
                        st.text(f"🕒 {start_dt.strftime('%d.%m. %H:%M')}\n   bis {end_dt.strftime('%H:%M')}")
                        st.caption(f"⚙️ Abfahrt: **{end_dt.strftime('%H:%M')}**")
                    else:
                        st.caption("Kein Fenster ❌")
