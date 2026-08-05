# capbudget

Laag 0 en laag 1 van de herziene capaciteitstarief-regeling: meten en
budgetteren. De uitvoer is één getal in watt, dat evcc als circuitlimiet
gebruikt.

Deze map is bedoeld als submap van **evcc-capaciteitstarief-bridge**:

```
evcc-capaciteitstarief-bridge/
├── docs/
├── home-assistant/       # v7/v8 yaml — historisch
├── captarief-python/     # standalone port van de v8-regellus
└── capbudget/            # ← deze map: de herziene opzet
```

## Verhouding tot de rest van de repo

`capbudget` is geen refactor van `captarief-python` maar een andere
laagindeling. Het verschil in één zin: **evcc doet de snelle regeling, niet wij.**

| | `captarief-python` / v8 | `capbudget` |
|---|---|---|
| Snelle regeling | eigen lus, 10–15 s | evcc-circuit |
| Uitvoer | ampère naar de laadpaal | watt naar een MQTT-topic |
| Doelvermogen | vaste 2,5 kW-basis | `max(maandpiek, 2500)` |
| Freeze window, stapgrenzen, noodrem | ja | niet nodig |
| Offline testbaar | nee | ja, `replay.py` |

Zolang `capbudget` niet gevalideerd is, blijft de bestaande implementatie de
werkende versie. Zet ze **nooit tegelijk aan**: twee schrijvers op hetzelfde
actuatiepad regelen tegen elkaar in.

## Inhoud

```
core.py             regelwet, geen I/O — ook gebruikt door de replay-harness
bronnen.py          HA-lezer, gedeeld door daemon en logger
daemon.py           laag 1: HA lezen → bepaal() → MQTT publiceren
dashboard.py        alleen-lezen variant: HA lezen → bepaal(), publiceert niets
state.py            gedeelde toestand tussen lus en dashboard
web.py              HTTP-laag van het dashboard (FastAPI), poort 8770
logger.py           stap 1: CSV loggen, regelt niets
replay.py           stap 2: regelwet offline testen op gelogde data
proefdraai.html     interactieve simulatie in de browser
ha/capbudget.yaml   HA-package: meetlaag, vrijgaveknop, waakhond
evcc/circuits.yaml  fragment voor evcc.yaml — laag 2
systemd/            units + voorbeeld-env
docs/architectuur.md  volledige documentatie met diagrammen
tests/              25 tests, stdlib only
```

## De regelwet in vijf regels

```python
doel      = clamp(maandpiek_w, 2500, 9200) - marge_w
budget_wh = doel * 900 / 3600
rest_wh   = budget_wh - kwartier_verbruikt_wh
rem_sec   = max(900 - (ts % 900), 1.0)
envelope  = clamp(min(doel, rest_wh * 3600 / rem_sec), vloer_w, doel)
```

Die `min()` doet het werk. De hyperbolische term mag omhoog schieten zoveel hij
wil — hij komt nooit door het plafond. Correctie is dus alleen neerwaarts, en
de ampèreklim vlak voor de kwartiergrens uit v7 kan niet meer optreden.

Waarom `max(maandpiek, 2500)` en niet een vaste 2,5 kW: je wordt afgerekend op
de hoogste kwartierpiek van de maand. Heeft de oven al 4,2 kW gepiekt, dan is
laden tot 4,2 kW voor de rest van die maand gratis.

## Testen

```bash
python3 -m unittest discover -s tests
```

25 tests, geen externe afhankelijkheden. De belangrijkste is
`test_geen_hyperbolische_klim`: een expliciete regressietest op de v7-fout.

Open `proefdraai.html` in een browser om de regelwet met de hand te bespelen.
De simulatie draait dezelfde formule en dezelfde constanten.

## Dashboard

Alleen-lezen HTTP-dashboard op poort 8770 (`WEB_HOST`/`WEB_PORT`), toont het
laatst berekende `Besluit` — envelope, doel, reden, kwartierbudget — en
ververst elke 5 s via `GET /api/status`. Het schrijft nergens naartoe.

Er zijn twee manieren om het te draaien, en welke je gebruikt hangt af van
waar je in de uitrolvolgorde zit:

- **`dashboard.py`** — leest HA, rekent `bepaal()` uit, publiceert niets.
  Geen paho-mqtt-afhankelijkheid, geen actuatiepad. Dit is de aan te raden
  service zolang de daemon nog niet aan staat (stap 1-3), en kan gewoon
  naast de `capbudget-daemon` blijven draaien zodra die wél actief is —
  beide lezen dezelfde HA-sensoren, geen van beide schrijft naar de andere.
- **`daemon.py`** — start hetzelfde dashboard automatisch mee zodra de
  daemon actief is (stap 4+); een aparte `capbudget-dashboard` is dan niet
  nodig, maar schaadt ook niet.

```bash
sudo systemctl enable --now capbudget-dashboard   # alleen-lezen, geen risico
curl http://<lxc>:8770/api/status
```

## Uitrolvolgorde

| Stap | Actie | Duur | Regelt iets? |
|---|---|---|---|
| 1 | `ha/capbudget.yaml` plaatsen, `logger.py` draaien | 1 week | nee |
| 2 | `replay.py` over de logs | uren | nee |
| 3 | v8 uit, evcc-circuit statisch op 2500 W | 1 week | evcc |
| 4 | `daemon.py` aan via MQTT | — | ja |
| 5 | Batterijbeslissing op basis van stap 3 | — | later |

Stap 3 wordt het snelst overgeslagen en is het belangrijkst. Er is een reële
kans dat een statische circuitlimiet al het grootste deel van het resultaat
levert — dan bouw je laag 1 alleen nog voor de rest, en weet je dat vooraf.

## Installatie

```bash
# op de LXC
adduser --system --group --home /opt/capbudget capbudget
install -d -o capbudget -g capbudget /var/lib/capbudget
cp -r capbudget/* /opt/capbudget/
pip install -r /opt/capbudget/requirements.txt

cp systemd/capbudget.env.example /etc/capbudget.env
chmod 600 /etc/capbudget.env      # bevat het HA-token
cp systemd/*.service /etc/systemd/system/
systemctl enable --now capbudget-logger
```

De daemon pas inschakelen bij stap 4.

## CI/CD — automatische deploy vanaf GitHub Actions

`.github/workflows/deploy-capbudget.yml` draait tests op een GitHub-hosted
runner en rolt daarna `captarief-python/` uit naar `/opt/capbudget` op de LXC,
via een **self-hosted runner die op de LXC zelf draait**. Er hoeft geen poort
naar buiten open — de runner haalt jobs op (outbound naar GitHub).

Eenmalige setup op de LXC:

```bash
# 1. self-hosted runner installeren (als eigen systeemgebruiker, niet als capbudget)
adduser --system --group --home /opt/actions-runner runner
su - runner
mkdir /opt/actions-runner && cd /opt/actions-runner
# volg de "Add runner" instructies van de repo: Settings → Actions → Runners → New self-hosted runner
./config.sh --url https://github.com/<org>/evcc-capaciteitstarief-bridge --token <TOKEN> --labels capbudget
exit
cd /opt/actions-runner && sudo ./svc.sh install runner && sudo ./svc.sh start

# 2. rsync op de LXC beschikbaar maken
apt install -y rsync

# 3. gerichte sudo-rechten voor de runner-gebruiker (geen wachtwoord, alleen deze commando's)
cat >/etc/sudoers.d/capbudget-runner <<'EOF'
runner ALL=(root) NOPASSWD: /usr/bin/rsync, /usr/bin/chown, /usr/bin/pip, \
  /usr/bin/systemctl try-restart capbudget-logger.service, \
  /usr/bin/systemctl try-restart capbudget-dashboard.service, \
  /usr/bin/systemctl try-restart capbudget-daemon.service, \
  /usr/bin/systemctl is-active capbudget-logger.service, \
  /usr/bin/systemctl is-active capbudget-dashboard.service, \
  /usr/bin/systemctl is-active capbudget-daemon.service
EOF
visudo -cf /etc/sudoers.d/capbudget-runner   # syntax controleren
```

Maak in de GitHub-repo een environment `capbudget-lxc` aan (Settings →
Environments) om de deploy-job optioneel achter een goedkeuringsstap te
zetten voordat hij naar productie schrijft.

De workflow herstart met `systemctl try-restart`, wat alleen services aanraakt
die al actief zijn — veilig ongeacht of alleen `capbudget-logger` draait
(stap 1–3) of ook `capbudget-daemon` (stap 4+). Het `.env`-bestand met het
HA-token wordt nooit door de workflow aangeraakt; dat blijft handmatig beheer
op de LXC.

> **Klok gelijkzetten.** De kwartiergrenzen komen uit `ts % 900`. Loopt de klok
> scheef, dan liggen de gesimuleerde kwartieren naast die van de digitale meter
> en corrigeert de regelaar op de verkeerde momenten. Zorg dat er op de
> Proxmox-host een NTP-client actief synchroniseert.

## Wat nog niet af is

Laag 3 (batterijen als snelle shaver) is bewust uitgesteld tot na de meting van
stap 3. Zie `docs/architectuur.md`, hoofdstuk 10, voor de twee opties en waarom
de data eerst moet spreken.

De parameters van het laadmodel in `replay.py` — 6 A ondergrens, 32 A
bovengrens, 300 W/s helling — zijn aannames, geen metingen. Ze horen na stap 1
vervangen te worden door wat de laadpaal werkelijk doet.
