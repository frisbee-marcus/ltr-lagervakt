"""Skraper norske nettbutikker for MTG LOTR: Tales of Middle-earth Set Booster (pakke) og Set Booster Display.

Kjør:  python skrap.py            -> skriver resultater.json + resultater.csv ved siden av skriptet
Kun standardbiblioteket, ingen pip install.
"""
import concurrent.futures as cf
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
    "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
}
EAN = {"195166204994": "pakke", "195166205007": "display"}
QUERIES = ["tales of middle-earth set booster", "lord of the rings set booster"]

# --- Butikkliste -----------------------------------------------------------
SHOPIFY = {
    "Midgard Games": "midgardgames.no",
    "Pokestore": "pokestore.no",
    "Manaheim": "manaheim.no",
    "Retroworld": "retroworld.no",
    "Spillbutikken": "spillbutikken.no",
    "Spillfabrikken": "spillfabrikken.no",
    "Norsk TCG": "norsktcg.no",
    "Boxershop": "boxershop.no",
    "Cardcenter": "cardcenter.no",
    "Spillbua": "spillbua.no",
}
WOO = {"Kanoncon": "www.kanoncon.no"}
# Butikker uten API: søkeside -> produktlenker -> JSON-LD / itemprop på produktsiden
HTML_SEARCH = {
    "Adamstuen Retro": ("https://adamstuenretro.no/index.php?route=product/search&search={q}", r'href="(https://adamstuenretro\.no/[^"?#]+)'),
    "MaxGaming": ("https://www.maxgaming.no/search?q={q}", r'href="(/no/magic-the-gathering/[^"?#]+)'),
    "Gamezone": ("https://gamezone.no/search?q={q}", r'href="(/[^"?#]*/\d+/[^"?#]+)'),
}
PRISJAKT = ["9599772"]  # produkt-ID-er på prisjakt.no (9599772 = Set Booster-pakke)


def now():
    """Norsk tid også på GitHub sine servere (som går i UTC). Windows uten tzdata faller tilbake til lokal tid."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Europe/Oslo")).replace(tzinfo=None)
    except Exception:
        return dt.datetime.now()


def get(url, data=None, headers=None, timeout=25):
    # Shopify svarer 429 når vi henter mange produkter raskt — prøv igjen med pause
    for attempt in range(4):
        try:
            return _get(url, data, headers, timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503) or attempt == 3:
                raise
            wait = e.headers.get("Retry-After") if e.headers else None
            time.sleep(min(float(wait), 15) if wait and wait.isdigit() else 2 * (attempt + 1))


def _get(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.geturl(), raw.decode("utf-8")
        except UnicodeDecodeError:  # f.eks. Kanoncon blander inn cp1252-tankestrek
            return r.geturl(), raw.decode("cp1252", "replace")


# --- Klassifisering --------------------------------------------------------
# Krever «Middle-earth»/«Tales»/LTR — ellers slipper Decipher sitt LOTR-kortspill (2001–2004) gjennom
LOTR = re.compile(r"middle[\s-]?eart|\bLTR\b", re.I)
SET_BOOSTER = re.compile(r"set[\s-]?booster", re.I)
EXCLUDE = re.compile(r"collector|draft|jumpstart|starter|commander|bundle|scene box|gift|enkeltkort|special edition|sleeve|deck box|playmat|holiday", re.I)
DISPLAY = re.compile(r"display|booster[\s-]?box|\bbox\b|\bboks\b|\beske\b|30\s*(pk|pakker|packs|stk)", re.I)


def classify(title, ean=None):
    if ean and ean in EAN:
        return EAN[ean]
    t = html.unescape(title or "")
    if not LOTR.search(t) or EXCLUDE.search(t):
        return None
    # MaxGaming kaller pakken bare «Booster» — godta det siden draft/collector allerede er filtrert bort
    if not SET_BOOSTER.search(t) and not re.search(r"\bbooster\b", t, re.I):
        return None
    return "display" if DISPLAY.search(t) else "pakke"


def row(shop, title, price, in_stock, url, kind=None, ean=None, kilde="", note=""):
    kind = kind or classify(title, ean)
    if not kind:
        return None
    return {
        "butikk": shop, "produkt": kind, "tittel": html.unescape(title).replace("�", "–").strip(),
        "pris": round(float(price), 2) if price not in (None, "") else None,
        "lager": in_stock, "url": url, "kilde": kilde,
        "merknad": note or ("B-vare" if re.search(r"b-vare", title, re.I) else ""),
    }


# --- Adaptere --------------------------------------------------------------
# Kjente Shopify-produkter per domene, satt av vakt.py. Et domene som står her, sjekkes bare på disse
# produktsidene (1–2 forespørsler). Shopify struper hele IP-en med 429 hvis vi søker gjennom 10 butikker
# hvert 5. minutt. Et domene som mangler her, får et fullt søk.
KNOWN = {}


def shopify(shop, domain):
    handles = KNOWN.get(domain)
    if handles is None:
        handles = shopify_discover(domain)
    out = []
    for h in sorted(handles):
        try:
            p = json.loads(get(f"https://{domain}/products/{h}.js")[1])
        except urllib.error.HTTPError as e:
            if e.code == 404:  # produktet er slettet; alt annet (429 osv.) skal gjøre kilden «feilet»
                continue
            raise
        except UnicodeError:  # videresending til en adresse med ™/® (urllib takler ikke det), aldri en av våre
            continue
        v = p["variants"][0]
        r = row(shop, p["title"], p["price"] / 100, bool(p.get("available")), f"https://{domain}/products/{h}",
                ean=(v.get("barcode") or "").strip(), kilde="Shopify")
        if r:
            out.append(r)
    return out


def shopify_discover(domain):
    # suggest.json alene bommer (maks 10 treff, draft-varer fortrenger set), så vi høster også søkesiden
    handles = set()
    for q in QUERIES:
        qq = urllib.parse.quote(q)
        try:
            data = json.loads(get(f"https://{domain}/search/suggest.json?q={qq}&resources[type]=product"
                                  "&resources[limit]=10&resources[options][unavailable_products]=last")[1])
            handles |= {p["handle"] for p in data.get("resources", {}).get("results", {}).get("products", [])}
        except urllib.error.HTTPError as e:
            if e.code == 429:  # strupet: ufullstendig søk skal ikke lagres som fasit
                raise
        except Exception:
            pass
        for page in (1, 2):
            b = get(f"https://{domain}/search?q={qq}&type=product&options[unavailable_products]=last&page={page}")[1]
            handles |= set(re.findall(r"/products/([a-z0-9][a-z0-9\-]*)", b))
    # Noen butikker (f.eks. Midgard) skjuler utsolgte varer i søket — gå via settets kolleksjon i tillegg
    try:
        for page in (1, 2, 3):
            cols = json.loads(get(f"https://{domain}/collections.json?limit=250&page={page}")[1])["collections"]
            for c in cols:
                if re.search(r"middle-earth|tales-of-middle|ltr|lord-of-the-rings", c["handle"]):
                    prods = json.loads(get(f"https://{domain}/collections/{c['handle']}/products.json?limit=250")[1])
                    handles |= {p["handle"] for p in prods["products"]}
            if len(cols) < 250:
                break
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise
    except Exception:
        pass
    # Hent bare produktsider som ser ut som pakke/display; butikker som Pokestore har mye annet LOTR-utstyr
    return {h for h in handles
            if re.search(r"middle|ltr|lord-of-the-rings", h)
            and re.search(r"booster|display|box|boks|pakke|pack", h)
            and not re.search(r"collector|jumpstart|commander|scene|bundle|starter|gift", h)}


def woo(shop, domain):
    out, seen = [], set()
    for q in QUERIES:
        u = f"https://{domain}/wp-json/wc/store/v1/products?per_page=50&search={urllib.parse.quote(q)}"
        for p in json.loads(get(u)[1]):
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            pr = p["prices"]
            price = int(pr["price"]) / 10 ** pr.get("currency_minor_unit", 2)
            r = row(shop, p["name"], price, p.get("is_in_stock"), p["permalink"], ean=p.get("sku"), kilde="WooCommerce")
            if r:
                out.append(r)
    return out


def outland(shop="Outland"):
    gql = ('{products(search:%s,pageSize:40){items{name sku url_key stock_status '
           'price_range{minimum_price{final_price{value}}}}}}')
    out, seen = [], set()
    for q in QUERIES + ["Tales of Middle Earth Set Booster Display"]:
        body = json.dumps({"query": gql % json.dumps(q)}).encode()
        data = json.loads(get("https://www.outland.no/api/graphql", body, {"Content-Type": "application/json"})[1])
        for p in data["data"]["products"]["items"]:
            if p["sku"] in seen:
                continue
            seen.add(p["sku"])
            r = row(shop, p["name"], p["price_range"]["minimum_price"]["final_price"]["value"],
                    p["stock_status"] == "IN_STOCK", f"https://www.outland.no/p/{p['url_key']}", ean=p["sku"], kilde="Magento GraphQL")
            if r:
                out.append(r)
    return out


def parse_product_page(shop, url):
    final, b = get(url)
    title = price = stock = ean = None
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', b, re.S):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for node in d if isinstance(d, list) else d.get("@graph", [d]):
            if isinstance(node, dict) and node.get("@type") == "Product":
                title = node.get("name")
                ean = node.get("gtin13") or node.get("gtin") or node.get("model")
                off = node.get("offers") or {}
                off = off[0] if isinstance(off, list) else off
                price = off.get("price") or off.get("lowPrice")
                stock = "InStock" in str(off.get("availability", ""))
    if price is None:
        mp = re.search(r'itemprop="price" content="([\d.,]+)"', b)
        price = mp and mp.group(1).replace(",", ".")
        ma = re.search(r'itemprop="availability" content="([^"]+)"', b)
        stock = bool(ma and "instock" in ma.group(1).lower())
    if title is None:
        mt = re.search(r"<title>(.*?)</title>", b, re.S)
        title = mt.group(1).split(" - ")[0] if mt else url
    ean = re.sub(r"\D", "", str(ean or ""))[:12] or None
    if not ean:
        me = re.search(r"\b(19516620(?:4994|5007))\b", b)
        ean = me and me.group(1)
    return row(shop, title, price, stock, final, ean=ean, kilde="Produktside (JSON-LD)")


def html_search(shop, search_tpl, link_re):
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(search_tpl))
    links = set()
    for q in QUERIES:
        b = get(search_tpl.format(q=urllib.parse.quote_plus(q)))[1]
        for l in re.findall(link_re, b):
            if re.search(r"middle|tales", l, re.I) and re.search(r"booster|display|box", l, re.I):
                links.add(urllib.parse.urljoin(base, l))
    rows = []
    for l in sorted(links):
        try:
            r = parse_product_page(shop, l)
            if r:
                rows.append(r)
        except Exception as e:
            print(f"  ! {shop}: {l}: {e}", file=sys.stderr)
    return rows


def prisjakt(pid):
    b = get(f"https://www.prisjakt.no/product.php?p={pid}")[1]
    mt = re.search(r"<title>(.*?)</title>", b, re.S)
    title = html.unescape(mt.group(1)).split(" - ")[0] if mt else pid
    kind = classify(title)
    # Kun selve tilbudslisten (OfferClickoutButton) — productCardData-blokkene er anbefalinger av ANDRE produkter
    rows, seen = [], set()
    pat = r'data-test="OfferClickoutButton"[^>]*href="[^"]*go-to-shop/(\d+)/.*?alt="([^"]+)"(.*?)(?=data-test="OfferClickoutButton"|$)'
    for m in re.finditer(pat, b, re.S):
        sid, name, rest = m.groups()
        if sid in seen:
            continue
        seen.add(sid)
        txt = re.sub(r"<[^>]+>", " ", rest)[:2500]
        mp = re.search(r"(\d[\d\s ]*)\s*kr", txt)
        if not mp:
            continue
        low = txt.lower()
        stock = False if ("ikke på lager" in low or "utsolgt" in low) else (True if "på lager" in low else None)
        rows.append(row(f"{html.unescape(name)} (via Prisjakt)", title, re.sub(r"\D", "", mp.group(1)), stock,
                        f"https://www.prisjakt.no/product.php?p={pid}", kind=kind, kilde="Prisjakt"))
    return [r for r in rows if r]


# --- Kjøring ---------------------------------------------------------------
def scrape(verbose=True, only=None):
    """Kjører alle kilder (eller bare kildenavnene i `only`) og returnerer payload-dict (brukes også av vakt.py)."""
    jobs = {f"{n} ({d})": (shopify, n, d) for n, d in SHOPIFY.items()}
    jobs |= {f"{n} ({d})": (woo, n, d) for n, d in WOO.items()}
    jobs["Outland (outland.no)"] = (outland,)
    jobs |= {f"{n} (html)": (html_search, n, *v) for n, v in HTML_SEARCH.items()}
    jobs |= {f"Prisjakt p={p}": (prisjakt, p) for p in PRISJAKT}
    if only:
        jobs = {n: j for n, j in jobs.items() if n in only}

    results, status = [], []
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(fn, *args): name for name, (fn, *args) in jobs.items()}
        for f in cf.as_completed(futs):
            name = futs[f]
            try:
                rows = f.result()
                results += rows
                status.append({"kilde": name, "ok": True, "treff": len(rows)})
                if verbose:
                    print(f"OK   {name}: {len(rows)} treff")
            except Exception as e:
                status.append({"kilde": name, "ok": False, "feil": str(e)[:200]})
                if verbose:
                    print(f"FEIL {name}: {e}")

    # samme URL kan komme fra flere søk
    uniq = {(r["butikk"], r["url"], r["tittel"]): r for r in results}
    results = sorted(uniq.values(), key=lambda r: (r["produkt"], not r["lager"], r["pris"] or 1e9))
    return {"hentet": now().isoformat(timespec="minutes"), "resultater": results,
            "kilder": sorted(status, key=lambda s: s["kilde"])}


def save(payload):
    here = Path(__file__).parent
    results = payload["resultater"]
    tmp = here / "resultater.json.tmp"  # atomisk bytte, så bygg.py aldri leser en halvskrevet fil
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(here / "resultater.json")
    with open(here / "resultater.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()) if results else ["butikk"], delimiter=";")
        w.writeheader()
        w.writerows(results)


def main():
    payload = scrape()
    save(payload)
    print(f"\n{len(payload['resultater'])} tilbud skrevet til resultater.json / resultater.csv")


if __name__ == "__main__":
    main()
