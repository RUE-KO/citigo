import streamlit as st
import requests
import datetime
import math

# --- SEITEN-KONFIGURATION ---
st.set_page_config(
    page_title="EV Ladeplaner",
    page_icon="⚡",
    layout="centered"
)

st.title("⚡ EV Ladeplaner")
st.caption("Finde das günstigste Ladefenster für deinen Škoda auf Basis der Energy-Charts Preise.")

# --- SEITENLEISTE / EINGABEN ---
st.sidebar.header("Fahrzeug & Einstellungen")

# 1. Fahrzeug-Auswahl
fahrzeug = st.sidebar.selectbox(
    "Fahrzeug auswählen",
    ["Škoda Citigo e-iV", "Škoda Enyaq"]
)

# 2. Standardwerte je nach Fahrzeug setzen
if fahrzeug == "Škoda Citigo e-iV":
    default_akku = 32.3
    default_kw = 7.2
else:  # Enyaq (Standard z. B. Enyaq 80 / 85)
    default_akku = 77.0
    default_kw = 11.0

aktueller_soc = st.sidebar.number_input(
    "Aktueller Akkustand (%)",
    min_value=0.0,
    max_value=100.0,
    value=40.0,
    step=5.0
)

akkugroesse_netto = st.sidebar.number_input(
    "Akkugröße Netto (kWh)",
    value=default_akku,
    step=0.1
)

ladeleistung_kw = st.sidebar.number_input(
    "Ladeleistung (kW)",
    value=default_kw,
    step=0.1
)

# --- LOGIK & DATENABRUF ---
@st.cache_data(ttl=3600)  # Ergebnisse für 1 Stunde im Cache halten
def lade_preisdaten():
    heute = datetime.date.today()
    morgen = heute + datetime.timedelta(days=1)

    timestamps = []
    prices = []
    headers = {'User-Agent': 'Mozilla/5.0'}

    for tag in [heute, morgen]:
        url = f"https://api.energy-charts.info/price?bzn=DE-LU&start={tag.strftime('%Y-%m-%d')}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if "unix_seconds" in data and data["unix_seconds"]:
                    timestamps.extend(data["unix_seconds"])
                    prices.extend(data["price"])
        except Exception as e:
            pass
            
    return timestamps, prices

def berechne_tibber_preis(boerse_cent):
    weitere_beschaffung = 1.81
    netznutzung = 6.39
    konzessionsabgabe = 1.32
    stromsteuer = 2.05
    offshore_wind = 0.941
    kwk_uflage = 0.446
    strom_nev = 1.56
    
    netto_fixkosten = (weitere_beschaffung + netznutzung + konzessionsabgabe + 
                       stromsteuer + offshore_wind + kwk_uflage + strom_nev)
                       
    return (boerse_cent + netto_fixkosten) * 1.19

def finde_guenstigstes_fenster_fuer_ziel(timestamps, prices, start_stunde, end_stunde, feste_block_groesse):
    bestes_fenster = None
    min_schnitt = float('inf')
    
    if len(timestamps) < feste_block_groesse:
        return None, 0
        
    for i in range(len(timestamps) - feste_block_groesse + 1):
        dt = datetime.datetime.fromtimestamp(timestamps[i])
        h = dt.hour
        
        if start_stunde < end_stunde:
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

# Daten laden
timestamps, prices = lade_preisdaten()

if not timestamps:
    st.error("Fehler: Keine Preisdaten erhalten. Die API von energy-charts.info ist eventuell kurzzeitig nicht erreichbar.")
else:
    kategorien = {
        "🌙 Nacht (22:00 - 06:00 Uhr)": (22, 6),
        "🌆 Feierabend / Abend (17:00 - 22:00 Uhr)": (17, 22),
        "☀️ Tag / Vormittag (06:00 - 17:00 Uhr)": (6, 17)
    }

    ziele = [80.0, 100.0]

    st.subheader(f"Ergebnisse für **{fahrzeug}** (Start bei {aktueller_soc:.0f}%)")

    for kategorie_name, (von, bis) in kategorien.items():
        with st.expander(kategorie_name, expanded=True):
            for ziel_soc in ziele:
                if aktueller_soc >= ziel_soc:
                    st.info(f"🎯 **Ziel {int(ziel_soc)}%**: Akku ist bereits ausreichend geladen ({aktueller_soc:.0f}%).")
                    continue
                    
                benötigte_prozent = ziel_soc - aktueller_soc
                benötigte_kwh = (benötigte_prozent / 100.0) * akkugroesse_netto
                benötigte_stunden = benötigte_kwh / ladeleistung_kw
                block_groesse = int(round((benötigte_stunden / 0.25), 0))
                
                bestes_ts, schnitt_boerse = finde_guenstigstes_fenster_fuer_ziel(
                    timestamps, prices, von, bis, block_groesse
                )
                
                if bestes_ts:
                    start_dt = datetime.datetime.fromtimestamp(bestes_ts)
                    end_dt = start_dt + datetime.timedelta(hours=benötigte_stunden)
                    tibber_brutto = berechne_tibber_preis(schnitt_boerse / 10)
                    ziel_soc_10 = int(round(ziel_soc, -1))
                    
                    st.markdown(f"### 🎯 Ziel {int(ziel_soc)}%")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Ladefenster", f"{start_dt.strftime('%d.%m. %H:%M')} - {end_dt.strftime('%d.%m. %H:%M')}")
                        st.write(f"⏱️ **Dauer:** {benötigte_stunden*60:.0f} Min (~{benötigte_kwh:.1f} kWh)")
                    with col2:
                        st.metric("Geschätzter Preis", f"~{tibber_brutto:.2f} ct/kWh")
                    
                    st.success(f"👉 **Im {fahrzeug} einstellen:** Abfahrtszeit **{end_dt.strftime('%d.%m. um %H:%M')} Uhr** & Ladelimit **{ziel_soc_10}%**")
                    st.divider()
                else:
                    st.warning(f"🎯 **Ziel {int(ziel_soc)}%**: Kein passendes Zeitfenster gefunden (Fenster zu kurz für benötigte Ladezeit).")
