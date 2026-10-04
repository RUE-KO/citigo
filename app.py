import streamlit as st
import requests
import datetime
import zoneinfo
import pandas as pd

# --- SEITEN-KONFIGURATION ---
st.set_page_config(
    page_title="EV Ladeplaner",
    page_icon="⚡",
    layout="centered"
)

# Layout & Styling
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
TIBBER_TOKEN = st.secrets.get("TIBBER_TOKEN", "")

# --- SEITENLEISTE ---
st.sidebar.header("Fahrzeug & Einstellungen")

fahrzeug = st.sidebar.selectbox("Fahrzeug wählen", ["Škoda Citigo e-iV", "Škoda Enyaq"])
default_akku = 32.3 if fahrzeug == "Škoda Citigo e-iV" else 77.0
default_kw = 4.6 if fahrzeug == "Škoda Citigo e-iV" else 11.0

aktueller_soc = st.sidebar.number_input("Aktueller Akkustand (%)", min_value=0.0, max_value=100.0, value=60.0, step=5.0)
akkugroesse_netto = st.sidebar.number_input("Akkugröße Netto (kWh)", value=default_akku, step=0.1)
ladeleistung_kw = st.sidebar.number_input("Realistische Ladeleistung (kW)", value=default_kw, step=0.1)

# --- TIBBER DATENABRUF ---
@st.cache_data(ttl=900)
def lade_tibber_daten():
    if not TIBBER_TOKEN:
        return [], [], False, "Kein Token in den Streamlit Secrets gefunden."

    url = "https://api.tibber.com/v1-beta/gql"
    headers = {"Authorization": f"Bearer {TIBBER_TOKEN}", "Content-Type": "application/json"}
    query = """
    {
      viewer {
        homes {
          currentSubscription {
            priceInfo {
              today { startsAt total }
              tomorrow { startsAt total }
            }
          }
        }
      }
    }
    """
    
    ts_15m, prices_cent = [], []
    morgen_verfuegbar = False
    
    try:
        response = requests.post(url, json={"query": query}, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "errors" in data:
                return [], [], False, f"Tibber API Fehler: {data['errors'][0]['message']}"
                
            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                return [], [], False, "Kein aktiver Tibber-Vertrag im Account gefunden."
                
            price_info = homes[0].get("currentSubscription", {}).get("priceInfo", {})
            raw_entries = price_info.get("today", []) or []
            tomorrow_entries = price_info.get("tomorrow", []) or []
            
            if tomorrow_entries:
                raw_entries.extend(tomorrow_entries)
                morgen_verfuegbar = True

            jetzt_ts = datetime.datetime.now(TZ_BERLIN).timestamp()

            for eintrag in raw_entries:
                dt = datetime.datetime.fromisoformat(eintrag["startsAt"]).astimezone(TZ_BERLIN)
                base_ts = int(dt.timestamp())
                total_cent = eintrag["total"] * 100.0
                
                for q in range(4):
                    q_ts = base_ts + (q * 900)
                    if q_ts + 900 >= jetzt_ts:
                        ts_15m.append(q_ts)
                        prices_cent.append(total_cent)

            return ts_15m, prices_cent, morgen_verfuegbar, None
        else:
            return [], [], False, f"HTTP Fehler {response.status_code}"
    except Exception as e:
        return [], [], False, str(e)

def finde_guenstigstes_fenster_fuer_ziel(timestamps, prices, start_stunde, end_stunde, anzahl_15m_bloecke):
    bestes_fenster, min_schnitt = None, float('inf')
    jetzt_ts = datetime.datetime.now(TZ_BERLIN).timestamp()
    
    if len(timestamps) < anzahl_15m_bloecke:
        return None, 0
        
    for i in range(len(timestamps) - anzahl_15m_bloecke + 1):
        ts = timestamps[i]
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

timestamps, prices, morgen_da, err = lade_tibber_daten()

if err:
    st.error(f"⚠️ {err}")
elif not timestamps:
    st.error("Keine Preisdaten verfügbar.")
else:
    if not morgen_da:
        st.sidebar.info("ℹ️ Preise für morgen stehen ab ca. 13:00–13:30 Uhr bereit.")

    st.caption(f"**{fahrzeug}** | Akkustand: **{aktueller_soc:.0f}%**")

    # MAIN TABS CREATION
    tab_empfehlungen, tab_preise = st.tabs(["🎯 Lade-Empfehlungen", "📊 Strompreise (Heute/Morgen)"])

    # --- TAB 1: EMPFEHLUNGEN ---
    with tab_empfehlungen:
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
                        anzahl_15m_bloecke = int(round((benoetigte_stunden / 0.25), 0))
                        
                        bestes_ts, schnitt_preis = finde_guenstigstes_fenster_fuer_ziel(
                            timestamps, prices, von, bis, anzahl_15m_bloecke
                        )
                        
                        if bestes_ts:
                            start_dt = datetime.datetime.fromtimestamp(bestes_ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                            end_dt = start_dt + datetime.timedelta(hours=benoetigte_stunden)
                            
                            st.metric("Preis", f"{schnitt_preis:.2f} ct")
                            st.text(f"🕒 {start_dt.strftime('%d.%m. %H:%M')}\n   bis {end_dt.strftime('%H:%M')}")
                            st.caption(f"⚙️ Abfahrt: **{end_dt.strftime('%H:%M')}**")
                        else:
                            st.caption("Kein Fenster ❌")

    # --- TAB 2: DIREKTE PREISEINSEHEN ---
    with tab_preise:
        # Preistabelle vorbereiten
        df_data = []
        for ts, p in zip(timestamps, prices):
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
            df_data.append({
                "Datum": dt.strftime("%d.%m.%Y"),
                "Uhrzeit": dt.strftime("%H:%M"),
                "Preis (ct/kWh)": round(p, 2),
                "Datum_Objekt": dt.date()
            })

        df = pd.DataFrame(df_data)
        
        heute_datum = datetime.datetime.now(TZ_BERLIN).date()
        morgen_datum = heute_datum + datetime.timedelta(days=1)

        tab_heute, tab_morgen = st.tabs(["Heute", "Morgen"])

        with tab_heute:
            df_heute = df[df["Datum_Objekt"] == heute_datum].drop(columns=["Datum_Objekt"])
            if not df_heute.empty:
                st.dataframe(df_heute, use_container_width=True, hide_index=True)
            else:
                st.info("Keine Daten für heute mehr verfügbar.")

        with tab_morgen:
            df_morgen = df[df["Datum_Objekt"] == morgen_datum].drop(columns=["Datum_Objekt"])
            if not df_morgen.empty:
                st.dataframe(df_morgen, use_container_width=True, hide_index=True)
            else:
                st.info("ℹ️ Preise für morgen sind noch nicht freigeschaltet (erst ab ca. 13:00–13:30 Uhr).")
