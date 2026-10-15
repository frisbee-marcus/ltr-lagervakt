# LTR-lagervakt

Følger med på norske nettbutikker for *Magic: The Gathering – The Lord of the Rings: Tales of Middle-earth* **Set Booster** og **Set Booster Display**, og sender push-varsel via [ntfy](https://ntfy.sh) når noe går fra utsolgt til på lager.

- Kjører hvert 5. minutt på GitHub Actions (`.github/workflows/vakt.yml`).
- Kun Python-standardbiblioteket, så ingen avhengigheter trengs.
- Butikker: Shopify-butikker via søk og kolleksjoner, Kanoncon (WooCommerce), Outland (GraphQL), MaxGaming, Adamstuen Retro og Gamezone (produktsider), og Prisjakt.

## Oppsett

1. Legg ntfy-emnet som repo-hemmelighet `NTFY_TOPIC`.
2. Abonner på samme emne i ntfy-appen.
3. Kjør workflowen manuelt én gang (Actions → LTR-lagervakt → Run workflow).

Du får et varsel med høyeste prioritet når noe kommer på lager, og en påminnelse hvert 30. minutt så lenge det fortsatt er på lager. Én stille melding i timen viser at vakten lever.

## Lokalt

```
python skrap.py          # skrap én gang, skriv resultater.json/.csv
python vakt.py           # én vaktrunde (Windows-varsel + ntfy)
python vakt.py --test    # testvarsel
```

`installer_vakt.ps1` registrerer en lokal Windows-oppgave som gir skrivebordsvarsler.
