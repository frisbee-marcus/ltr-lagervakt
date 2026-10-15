"""Bygger artefakt.html fra mal.html + resultater.json.  Kjør etter skrap.py."""
import json
from pathlib import Path

here = Path(__file__).parent
data = json.loads((here / "resultater.json").read_text(encoding="utf-8"))
# </ i JSON kan avslutte <script>-taggen for tidlig
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
html = (here / "mal.html").read_text(encoding="utf-8").replace("__DATA__", payload)
(here / "artefakt.html").write_text(html, encoding="utf-8")
print(f"artefakt.html bygget med {len(data['resultater'])} tilbud (hentet {data['hentet']})")
