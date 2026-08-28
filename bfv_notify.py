#!/usr/bin/env python3
"""
BFV Sonntagabend-Ergebnis-Notifier für Telegram (v2 - mit echten Ergebniszahlen)
==================================================================================
Holt die letzten Ergebnisse zweier BFV-Mannschaften und schickt sie per Telegram.

Datenquelle: der offizielle "Vereinsspielplan"-PDF-Export von bfv.de
(derselbe Button, der auf der Vereinsseite als "Vereinsspielplan - Alle
künftigen Spiele des Vereins ... als PDF öffnen" verlinkt ist). Das PDF
wird serverseitig fertig gerendert, enthält also - anders als die HTML-
Seite - die tatsächlichen Ergebniszahlen im Text, nicht per JavaScript
nachgeladen.

Setup:
  pip install requests pdfplumber --break-system-packages
  Umgebungsvariablen TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID setzen.
"""

import os
import re
import sys
import tempfile
from datetime import datetime

import requests

try:
    import pdfplumber
except ImportError:
    print("Bitte installieren: pip install pdfplumber --break-system-packages", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
# Jede Mannschaft spielt in einer eigenen Liga (staffel) - deshalb ein
# eigener "Spieltagsübersicht"-PDF-Link pro Team. Dieses PDF enthält
# (anders als der Vereinsspielplan) auch die bereits gespielten
# Spieltage mit echten Ergebniszahlen.
TEAMS = [
    {
        "name": "TuS 1893 Aschaffenburg-Leider (1. Mannschaft)",
        "pdf_url": "https://service.bfv.de/rest/pdfexport/spieltagsuebersicht?staffel=0307PL25JS000057VS5489BUVT69HBT7-G",
        "keywords": ["TuS 1893 Aschaffenburg-Leider", "TuS 1893 Aschaffenburg-<wbr>Leider"],
        "exclude": ["Leider 2", "Leider II"],  # nicht mit der 2. Mannschaft verwechseln
    },
    {
        "name": "(SG 1) DJK Aschaffenburg/TuS 1893 Leider 2",
        "pdf_url": "https://service.bfv.de/rest/pdfexport/spieltagsuebersicht?staffel=0318S0SUUK000006VS5489BTVSK8S3O6-G",
        "keywords": ["DJK Aschaffenburg", "Leider 2", "Leider II"],
        "exclude": [],
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TuS-Leider-ResultBot/1.0; privat, nicht kommerziell)"
}

# Zeile im PDF, z.B.:
# "064 26.08.2026 19:00 TuS 1893 Aschaffenburg-Leider - SpVgg Hösbach-Bahnhof 1:2"
#
# Wichtig: Team-Namen können selbst Bindestriche enthalten (z.B.
# "Aschaffenburg-Leider", "Ringheim-Grossostheim") - die haben KEIN
# Leerzeichen um den Bindestrich. Der eigentliche Trenner zwischen den
# beiden Mannschaften hat dagegen immer ein Leerzeichen auf beiden
# Seiten (" - "). Deshalb \s-\s (mit Leerzeichen) statt nur \s*-\s*.
LINE_PATTERN = re.compile(
    r"(?P<datum>\d{2}\.\d{2}\.\d{4})\s+\d{2}:\d{2}\s+"
    r"(?P<heim>.+?)\s-\s(?P<gast>.+?)\s+"
    r"(?P<hs>\d{1,2}):(?P<as>\d{1,2})\b"
)


def download_pdf_text(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(resp.content)
        path = f.name

    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text += page_text + "\n"
    os.unlink(path)
    return text


def find_matches_for_team(pdf_text: str, team: dict) -> list[dict]:
    matches = []
    for line in pdf_text.splitlines():
        m = LINE_PATTERN.search(line)
        if not m:
            continue

        heim = m.group("heim").strip()
        gast = m.group("gast").strip()
        combined = f"{heim} {gast}"

        if not any(kw in combined for kw in team["keywords"]):
            continue
        if any(ex in combined for ex in team["exclude"]):
            continue

        try:
            datum = datetime.strptime(m.group("datum"), "%d.%m.%Y")
        except ValueError:
            continue

        matches.append(
            {
                "datum": datum,
                "datum_str": m.group("datum"),
                "heim": heim,
                "gast": gast,
                "hs": m.group("hs"),
                "as": m.group("as"),
                "line": line.strip(),
            }
        )
    return matches


def get_result_line(team: dict) -> str:
    try:
        pdf_text = download_pdf_text(team["pdf_url"])
    except requests.RequestException as e:
        return f"⚠️ {team['name']}: PDF konnte nicht geladen werden ({e})"

    if os.environ.get("DEBUG"):
        print(f"----- PDF-Rohtext (DEBUG) für {team['name']} -----", file=sys.stderr)
        print(pdf_text, file=sys.stderr)
        print("--------------------------------", file=sys.stderr)

    matches = find_matches_for_team(pdf_text, team)
    if not matches:
        return f"⚠️ {team['name']}: Keine passende Zeile im PDF gefunden."

    # jüngstes Spiel mit Datum <= heute (also ein bereits gespieltes Spiel)
    past = [m for m in matches if m["datum"] <= datetime.now()]
    if not past:
        return f"⚠️ {team['name']}: Nur zukünftige Spiele gefunden, noch kein Ergebnis."

    last = max(past, key=lambda m: m["datum"])
    return (
        f"⚽ {team['name']}\n"
        f"{last['datum_str']}: {last['heim']} {last['hs']}:{last['as']} {last['gast']}"
    )


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


def main():
    lines = [get_result_line(team) for team in TEAMS]
    message = "🟢 <b>BFV-Ergebnisse am Wochenende</b>\n\n" + "\n\n".join(lines)
    print(message)
    send_telegram_message(message)


if __name__ == "__main__":
    main()
