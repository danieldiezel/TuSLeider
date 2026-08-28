#!/usr/bin/env python3
"""
BFV Sonntagabend-Ergebnis-Notifier für Telegram
=================================================
Holt die letzten Ergebnisse von zwei BFV-Mannschaften und schickt sie
per Telegram. Gedacht für einen wöchentlichen Cronjob (Sonntagabend).

Datenquelle: öffentliche, für den Browser bestimmte Team-Seiten auf
bfv.de (nicht die per robots.txt gesperrte widget.bfv.de-Subdomain).
Kein Login, keine privaten Daten - reine Ergebnis-/Terminanzeige,
so wie sie jeder Besucher im Browser sieht.

Setup:
  pip install requests beautifulsoup4 --break-system-packages
  Umgebungsvariablen setzen (z.B. in /etc/environment oder im Cronjob):
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

import os
import re
import sys
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfiguration: die beiden Mannschaften
# ---------------------------------------------------------------------------
TEAMS = [
    {
        "name": "TuS 1893 Aschaffenburg-Leider (1. Mannschaft)",
        "url": "https://www.bfv.de/mannschaften/tus-1893-aschaffenburg-leider/016PDSNBRC000000VV0AG811VTE5EA5R",
    },
    {
        "name": "(SG 1) DJK Aschaffenburg/TuS 1893 Leider 2",
        "url": "https://www.bfv.de/mannschaften/sg-1-djk-aschaffenburg-tus-1893-leider-2/016PEMIT7O000000VV0AG80NVV8OQVTB",
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    # Ehrlicher, erkennbarer User-Agent statt eines gefälschten Browser-UA -
    # das ist guter Stil für ein privates Automatisierungsskript.
    "User-Agent": "Mozilla/5.0 (compatible; TuS-Leider-ResultBot/1.0; privat, nicht kommerziell)"
}


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def find_last_result(html: str) -> str | None:
    """
    Sucht im sichtbaren Text der Seite nach dem Block 'Letztes Spiel ... Zum Spiel'
    und extrahiert Datum, Teams und Ergebnis.

    Rückgabe z.B.: "So. 23.08.2026: (SG1) Leider 2 2:1 FC Laufach"
    oder None, wenn nichts gefunden wurde (dann Fallback auf Spielberichte).
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Beispiel-Rohformat auf der Seite (nur wenn der Score wirklich im
    # server-seitigen HTML steckt - das ist bei bfv.de nicht garantiert,
    # da Angular manche Werte erst per JS im Browser nachlädt):
    # "Letztes Spiel So.. 23.08.2026 /14:00 Uhr TeamA TeamA 2 : 1 TeamB TeamB Zum Spiel"
    #
    # Wichtig: \d+ statt .+? für die Score-Gruppen, damit wir NIEMALS auf
    # eine leere " : "-Stelle matchen und dabei nachfolgenden Fließtext
    # (z.B. "Zum Spiel Nächstes Spiel ...") mit einsammeln.
    pattern = re.compile(
        r"Letztes Spiel\s+"
        r"(?P<tag>\w{2})\.\.\s+(?P<datum>\d{2}\.\d{2}\.\d{4})\s*/\s*(?P<zeit>\d{2}:\d{2})\s*Uhr\s+"
        r"(?P<heim>[^\d]+?)\s+"
        r"(?P<hs>\d+)\s*:\s*(?P<as>\d+)\s*"
        r"(?:\(\s*\d*\s*:\s*\d*\s*\)\s*)?"
        r"(?P<gast>[^\d]+?)\s+Zum Spiel",
        re.UNICODE,
    )
    m = pattern.search(text)
    if not m:
        return None

    return (
        f"{m.group('tag')}. {m.group('datum')}: "
        f"{m.group('heim').strip()} {m.group('hs')}:{m.group('as')} {m.group('gast').strip()}"
    )


def find_latest_match_report(html: str) -> str | None:
    """
    Fallback: nimmt die Überschrift des neuesten Eintrags unter 'Spielberichte'.
    Enthält kein exaktes Zahlen-Ergebnis, aber eine Kurzzusammenfassung
    (z.B. '25.08.2026: TuS ... nimmt drei Punkte mit nach Hause').
    """
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if "/spiele/spielbericht/" in a["href"]:
            t = a.get_text(strip=True)
            if t:
                return t
    return None


def get_result_line(team: dict) -> str:
    try:
        html = fetch_page(team["url"])
    except requests.RequestException as e:
        return f"⚠️ {team['name']}: Seite nicht erreichbar ({e})"

    result = find_last_result(html)
    if result:
        return f"⚽ {team['name']}\n{result}"

    fallback = find_latest_match_report(html)
    if fallback:
        return f"⚽ {team['name']}\n{fallback}\n(Score-Parsing fehlgeschlagen, siehe Spielbericht)"

    return f"⚠️ {team['name']}: Kein Ergebnis gefunden - Seite hat sich evtl. strukturell geändert."


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
    print(message)  # auch ins Cron-Log, zum Debuggen
    send_telegram_message(message)


if __name__ == "__main__":
    main()
