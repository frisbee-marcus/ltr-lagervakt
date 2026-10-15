"""Lagervakt: kjører skrapersen, varsler når et LTR Set Booster-produkt går fra utsolgt til på lager.

Kjøres av GitHub Actions hvert 5. minutt (.github/workflows/vakt.yml), og kan i tillegg
kjøres lokalt av Windows Oppgaveplanlegger (installer_vakt.ps1) for skrivebordsvarsler.
  python vakt.py          -> én runde
  python vakt.py --test   -> sender et testvarsel og avslutter
Mobilvarsling via ntfy: miljøvariabelen NTFY_TOPIC, eller config.json  {"ntfy_topic": "ditt-hemmelige-emne"}
Windows-varsler sendes bare når skriptet kjører på Windows.
"""
import base64
import datetime as dt
import json
import os
import runpy
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import skrap  # noqa: E402

STATE = HERE / "tilstand.json"
LOG = HERE / "vakt.log"
LOCK = HERE / "vakt.lock"
CONFIG = HERE / "config.json"
REMIND_MIN = 30          # påminnelse så lenge varen fortsatt er på lager
PULSE_MIN = 60           # stille «vakten lever»-melding i ntfy (prioritet 1: ingen lyd/banner)
OUTAGE_RUNS = 6          # ~30 min med de fleste kilder nede -> ett varsel
PS_APPID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
NAVN = {"display": "Set Booster Display", "pakke": "Set Booster-pakke"}


def log(msg):
    line = f"{skrap.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)  # synlig i Actions-loggen
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.replace(LOG.with_suffix(".log.1"))
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def toast(title, body, url=None):
    if os.name != "nt":
        return
    # scenario="reminder" blir liggende på skjermen til du lukker det
    launch = f' activationType="protocol" launch="{escape(url)}"' if url else ""
    actions = (f'<actions><action content="Åpne butikken" activationType="protocol" arguments="{escape(url)}"/>'
               '<action content="Lukk" activationType="system" arguments="dismiss"/></actions>') if url else ""
    xml = (f'<toast scenario="reminder"{launch}><visual><binding template="ToastGeneric">'
           f'<text>{escape(title)}</text><text>{escape(body)}</text></binding></visual>'
           f'<audio src="ms-winsoundevent:Notification.Looping.Alarm2" loop="false"/>{actions}</toast>')
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml(@'
{xml}
'@)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{PS_APPID}').Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                   creationflags=0x08000000, timeout=60, check=False)  # CREATE_NO_WINDOW


def ntfy(title, body, url=None, priority=5, tags=("rotating_light",)):
    topic = os.environ.get("NTFY_TOPIC") or (
        json.loads(CONFIG.read_text(encoding="utf-8")).get("ntfy_topic") if CONFIG.exists() else None)
    if not topic:
        return
    # JSON-publisering i stedet for headere: «PÅ LAGER» og andre særnorske tegn blir forvrengt i HTTP-headere
    msg = {"topic": topic, "title": title, "message": body, "priority": priority, "tags": list(tags)}
    if url:
        msg["click"] = url
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def notify(title, body, url=None):
    for fn in (toast, ntfy):
        try:
            fn(title, body, url)
        except Exception as e:
            log(f"VARSLING FEILET ({fn.__name__}): {e}")


def key(r):
    return f"{r['butikk']}|{r['url']}"


def source_ok(row, status):
    """Skjedde raden i en kilde som svarte denne runden? (Prisjakt-rader har butikknavn med suffiks.)"""
    for s in status:
        name = s["kilde"]
        if row["kilde"] == "Prisjakt" and name.startswith("Prisjakt"):
            return s["ok"]
        if name.startswith(row["butikk"] + " "):
            return s["ok"]
    return True


def run():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"varer": {}, "nede_runder": 0}
    payload = skrap.scrape(verbose=False)
    rows, status = payload["resultater"], payload["kilder"]
    now = time.time()

    failed = [s["kilde"] for s in status if not s["ok"]]
    if len(failed) > len(status) / 2:
        state["nede_runder"] = state.get("nede_runder", 0) + 1
        if state["nede_runder"] == OUTAGE_RUNS:
            notify("LTR-vakt: får ikke kontakt", f"{len(failed)} av {len(status)} kilder har feilet de siste {OUTAGE_RUNS} rundene. Sjekk nettet/VPN.")
    else:
        state["nede_runder"] = 0

    seen = state["varer"]
    alerts = []
    for r in rows:
        k = key(r)
        prev = seen.get(k, {})
        entry = {**prev, "lager": r["lager"], "pris": r["pris"], "tittel": r["tittel"], "produkt": r["produkt"],
                 "sist_sett": payload["hentet"]}
        entry.setdefault("forst_sett", payload["hentet"])
        if r["lager"] is True:
            newly = prev.get("lager") is not True
            due = now - prev.get("sist_varslet", 0) > REMIND_MIN * 60
            if newly or due:
                alerts.append((r, newly))
                entry["sist_varslet"] = now
            if newly:
                entry["paa_lager_siden"] = payload["hentet"]
        else:
            entry.pop("sist_varslet", None)
            entry.pop("paa_lager_siden", None)
        seen[k] = entry

    # Rader som forsvant fra en kilde som svarte = tatt ut av sortiment; behold dem fra kilder som feilet
    present = {key(r) for r in rows}
    for k in list(seen):
        if k not in present and seen[k].get("lager") is True:
            butikk = k.split("|")[0]
            if not any(s["kilde"].startswith(butikk + " ") and not s["ok"] for s in status):
                seen[k]["lager"] = False

    for r, newly in alerts:
        pris = f"{r['pris']:,.0f} kr".replace(",", " ") if r["pris"] else "ukjent pris"
        head = ("PÅ LAGER: " if newly else "Fortsatt på lager: ") + NAVN[r["produkt"]]
        body = f"{r['butikk']} – {pris}\n{r['tittel']}"
        notify(head, body, r["url"])
        log(f"VARSEL {head} | {r['butikk']} | {pris} | {r['url']}")

    on = sum(1 for r in rows if r["lager"] is True)
    # Rundehistorikk siste døgn, til statuslinjen på siden og pulsmeldingen
    runs = [x for x in state.get("runder", []) if now - x["t"] < 86400]
    runs.append({"t": now, "feil": len(failed)})
    state["runder"] = runs
    payload["vakt"] = {"runder_24t": len(runs), "runder_med_feil_24t": sum(1 for x in runs if x["feil"]),
                       "intervall_min": 5}

    skrap.save(payload)
    try:
        runpy.run_path(str(HERE / "bygg.py"))
    except Exception as e:
        log(f"bygg.py feilet: {e}")
    state["sist_kjort"] = payload["hentet"]

    if now - state.get("sist_puls", 0) >= PULSE_MIN * 60:
        try:
            where = "GitHub" if os.environ.get("GITHUB_ACTIONS") else "PC"
            ntfy(f"✓ Vakten lever {skrap.now():%H:%M} ({where})",
                 f"{len(rows)} tilbud, {on} på lager, {len(failed)} kilder feilet. "
                 f"{len(runs)} runder siste døgn.", priority=1, tags=("white_check_mark",))
            state["sist_puls"] = now
        except Exception as e:
            log(f"PULS FEILET: {e}")

    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"OK {len(rows)} tilbud, {on} på lager, {len(failed)} kilder feilet{': ' + ', '.join(failed) if failed else ''}")


def main():
    if "--test" in sys.argv:
        notify("LTR-vakt: testvarsel", "Varsling virker. Slik ser det ut når en Set Booster Display kommer på lager.",
               "https://claude.ai/artifact/KWC7QvF4N8icCLtVdDvs7r")
        return
    # Hopp over runden hvis forrige fortsatt kjører (låsen regnes som død etter 15 min)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 900:
        log("Hoppet over: forrige runde kjører fortsatt")
        return
    LOCK.write_text(str(os.getpid()))
    try:
        run()
    except Exception as e:
        log(f"FEIL i runden: {e!r}")
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
