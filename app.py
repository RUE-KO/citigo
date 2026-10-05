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

# 2-Spalten-Zwang für Mobilgeräte
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

# --- FAHRZEUG- & SPALTEN-EINSTELLUNGEN ---
col_fzg1, col_fzg2 = st.columns(2)

with col_fzg1:
    fahrzeug = st.selectbox("Fahrzeug wählen", ["Škoda Citigo e-iV", "Škoda Enyaq"])

# Parameter basierend auf gewähltem Fahrzeug setzen
if fahrzeug == "Škoda Citigo e-iV":
    # 80 % minus 45 % (für 100 km) = 35 % Start-SOC
    default_soc = 35.0          
    akkugroesse_netto = 32.3    # Citigo e-iV Akkugröße (netto)
    ladeleistung_kw = 7.2       # 2-phasig max 7.2 kW
else:
    # 80 % minus 24 % (für 100 km) = 56 % Start-SOC
    default_soc = 56.0          
    akkugroesse_netto = 77.0    # Enyaq iV 80/85 (netto)
    ladeleistung_kw = 11.0      # 3-phasig max 11 kW

with col_fzg2:
    aktueller_soc = st.number_input(
        "Aktueller Akkustand (%)", 
        min_value=0.0, 
        max_value=100.0, 
        value=default_soc, 
        step=1.0
    )

# --- TIBBER API DATENABRUF ---
@st.cache_data(ttl=900)
def lade_tibber_daten():
    if not TIBBER_TOKEN:
        return [], [], False, "Kein Token in den Streamlit Secrets gefunden."

    url = "https://api.tibber.com/v1-beta/gql"
    headers = {
        "Authorization": f"Bearer {TIBBER_TOKEN}",
        "Content-Type": "application/json"
    }
    
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
                return [], [], False, "Kein aktiver Tibber-Vertrag gefunden."
                
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
                
                # Stunden auf 15-Minuten-Raster aufteilen
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

def finde_guenstigstes_fenster(timestamps, prices, start_stunde, end_stunde, anzahl_15m_bloecke, ziel_datum):
    bestes_fenster, min_schnitt = None, float('inf')
    jetzt_ts = datetime.datetime.now(TZ_BERLIN).timestamp()
    
    if len(timestamps) < anzahl_15m_bloecke:
        return None, 0
        
    for i in range(len(timestamps) - anzahl_15m_bloecke + 1):
        ts = timestamps[i]
        
        # Nur zukünftige Fenster
        if ts + 900 < jetzt_ts:
            continue
            
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
        
        # Auf das gewünschte Datum filtern (Heute oder Morgen)
        if dt.date() != ziel_datum:
            continue

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

def render_empfehlungen_fuer_tag(timestamps, prices, ziel_datum, aktueller_soc, akkugroesse_netto, ladeleistung_kw):
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
                    
                    bestes_ts, schnitt_preis = finde_guenstigstes_fenster(
                        timestamps, prices, von, bis, anzahl_15m_bloecke, ziel_datum
                    )
                    
                    if bestes_ts:
                        start_dt = datetime.datetime.fromtimestamp(bestes_ts, tz=datetime.timezone.utc).astimezone(TZ_BERLIN)
                        end_dt = start_dt + datetime.timedelta(hours=benoetigte_stunden)
                        
                        # Ladedauer in Stunden und Minuten umrechnen
                        dauer_std = int(benoetigte_stunden)
                        dauer_min = int(round((benoetigte_stunden - dauer_std) * 60))
                        dauer_str = f"{dauer_std}h {dauer_min}m" if dauer_std > 0 else f"{dauer_min}m"
                        
                        st.metric("Preis", f"{schnitt_preis:.2f} ct")
                        st.text(f"🕒 {start_dt.strftime('%H:%M')}\n   bis {end_dt.strftime('%H:%M')}")
                        
                        # Abfahrtszeit inkl. Ladedauer in Klammern dahinter
                        st.caption(f"⚙️ Abfahrt: **{end_dt.strftime('%H:%M')}** ({dauer_str})")
                    else:
                        st.caption("Kein Fenster ❌")

timestamps, prices, morgen_da, err = lade_tibber_daten()

if err:
    st.error(f"⚠️ {err}")
elif not timestamps:
    st.error("Keine Preisdaten verfügbar.")
else:
    heute_datum = datetime.datetime.now(TZ_BERLIN).date()
    morgen_datum = heute_datum + datetime.timedelta(days=1)

    st.caption(f"Aktuell ausgewählt: **{fahrzeug}** | Akkustand: **{aktueller_soc:.0f}%**")

    # TAB-TRENNUNG FÜR HEUTE UND MORGEN
    tab_heute, tab_morgen = st.tabs(["📅 Heute", "📅 Morgen"])

    with tab_heute:
        render_empfehlungen_fuer_tag(
            timestamps, prices, heute_datum, aktueller_soc, akkugroesse_netto, ladeleistung_kw
        )

    with tab_morgen:
        if morgen_da:
            render_empfehlungen_fuer_tag(
                timestamps, prices, morgen_datum, aktueller_soc, akkugroesse_netto, ladeleistung_kw
            )
        else:
            st.info("ℹ️ Die Empfehlungen für morgen stehen erst ab ca. 13:00–13:30 Uhr zur Verfügung, wenn Tibber die neuen Börsenpreise freischaltet.")
