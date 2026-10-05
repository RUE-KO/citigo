import os
import requests
import smtplib
from email.mime.text import MIMEText

TIBBER_TOKEN = os.environ.get("TIBBER_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_TO = os.environ.get("EMAIL_TO")
APP_URL = os.environ.get("APP_URL", "https://citigo-nd6mvchpu5qc8mbqqy8qox.streamlit.app")

def check_and_send():
    if not all([TIBBER_TOKEN, EMAIL_USER, EMAIL_PASS, EMAIL_TO]):
        print("Fehler: Umgebungsvariablen nicht vollständig gesetzt.")
        return

    url = "https://api.tibber.com/v1-beta/gql"
    headers = {"Authorization": f"Bearer {TIBBER_TOKEN}", "Content-Type": "application/json"}
    query = "{ viewer { homes { currentSubscription { priceInfo { tomorrow { startsAt } } } } } }"

    try:
        r = requests.post(url, json={"query": query}, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            
            # Sicherer Zugriff auf das homes-Array
            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                print("Fehler: Keine Tibber-Home-Daten empfangen.")
                return

            tomorrow_data = homes[0].get("currentSubscription", {}).get("priceInfo", {}).get("tomorrow", [])
            
            if tomorrow_data and len(tomorrow_data) > 0:
                text = (
                    f"Hallo!\n\n"
                    f"Die neuen Tibber-Strompreise für morgen stehen bereit.\n\n"
                    f"Klicke hier, um eure optimalen Ladezeiten zu berechnen:\n"
                    f"🔗 {APP_URL}\n\n"
                    f"Gute Fahrt!"
                )
                
                msg = MIMEText(text, "plain", "utf-8")
                msg["Subject"] = "⚡ Neue Strompreise für morgen verfügbar!"
                msg["From"] = EMAIL_USER
                
                recipients = [e.strip() for e in EMAIL_TO.split(",")]
                msg["To"] = ", ".join(recipients)

                # WEB.DE Postausgangsserver (Port 587 / STARTTLS)
                with smtplib.SMTP("smtp.web.de", 587) as server:
                    server.starttls()
                    server.login(EMAIL_USER, EMAIL_PASS)
                    server.sendmail(EMAIL_USER, recipients, msg.as_string())
                    
                print(f"E-Mail erfolgreich gesendet an: {recipients}")
            else:
                print("Preise für morgen stehen aktuell noch nicht bereit (Abruf noch vor ~13:00 Uhr).")
        else:
            print(f"Tibber API antwortet mit HTTP Status {r.status_code}")
    except Exception as e:
        print(f"Fehler beim Senden: {e}")

if __name__ == "__main__":
    check_and_send()
