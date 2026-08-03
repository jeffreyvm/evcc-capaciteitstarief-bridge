# evcc-capaciteitstarief-bridge

Home Assistant-regelaar die EV-laden via evcc, twee Indevolt-batterijen en een Huawei-omvormer coördineert om onder de Belgische Fluvius capaciteitstarief-piek (2.5 kW) te blijven — met evcc als actuator en HA als beslissingslaag.

## Wat doet dit project?

`evcc-capaciteitstarief-bridge` houdt de maandelijkse piek onder het Fluvius-drempel van 2.5 kW door realtime het netvermogen (P1-meter), zonneproductie (Huawei SUN2000), batterijcapaciteit (Indevolt Zolder + Garage) en EV-laadstroom (Škoda Enyaq via evcc) op elkaar af te stemmen. Home Assistant fungeert als brein — met een kwartier-feedback-regelaar, freeze-window rond kwartiergrenzen en step-limited aanpassingen — terwijl evcc als enige actuator de laadpaal aanstuurt. Geen directe hardware-writes, geen bypasses: alle beslissingen lopen via evcc.

## Hardware

| Component | Detail |
|---|---|
| Inverter | Huawei SUN2000 (`sensor.inverter_active_power`, W) |
| Batterij Zolder | Indevolt, 6 kWh, max 2 kW laden / 1 kW ontladen |
| Batterij Garage | Indevolt, 4 kWh, max 2 kW laden / 1 kW ontladen |
| Laadpaal | Eénfasig, oprit, via evcc |
| Meter | HomeWizard P1 (`sensor.p1_meter_power`, W, positief = afname) |
| EV | Škoda Enyaq, 230V, 6–32A, fase-switching uit |
| EV-controller | evcc (≥ v0.311), Indevolts native geconfigureerd (post v0.307.1) |

**Belangrijk:** batterij-vermogensensoren in HA zijn positief bij ontladen — het omgekeerde van de Indevolt-app.

## Kernbeperkingen

- Belgisch capaciteitstarief-minimum: 2.5 kW piek per maand
- evcc wordt nooit gebypassed (enige historische uitzondering: v7 netladen-failsafe, inmiddels verwijderd)
- Batterijen laden permanent enkel op zonne-energie — netladen staat uit

## Architectuur in het kort

```
P1-meter (net) ──┐
Huawei SUN2000 ───┼──► HA-regelaar (capaciteitstarief_v8) ──► select.evcc_oprit_max_current ──► evcc ──► laadpaal
Indevolt Zolder ──┤         │
Indevolt Garage ──┘         └──► MQTT batteryMode (optioneel, route B)
```

Zie [`docs/architecture.md`](docs/architecture.md) voor de volledige feedback-loop, formules en freeze/step-logica.

## Mapstructuur

```
evcc-capaciteitstarief-bridge/
├── README.md                          # dit bestand
├── docs/
│   ├── architecture.md                # v8-regelaar: formules, freeze window, step rules
│   ├── v7-vs-v8.md                    # wat v8 oploste t.o.v. v7, en waarom
│   ├── battery-discharge-decision.md  # open beslissing: Route A vs Route B
│   ├── entities-reference.md          # tabel van alle HA-entities
│   └── principles.md                  # kernlessen en ontwerpprincipes
├── home-assistant/
│   └── capaciteitstarief_v8.yaml       # placeholder — hier komt de effectieve YAML
└── captarief-python/
    └── README.md                       # status van de standalone Python-variant
```

## Status

- **Actief:** Capaciteitstarief v8 (Home Assistant), regelt op basis van net-feedback i.p.v. schatting
- **Open:** keuze tussen Route A (evcc `batteryDischargeControl: false`) en Route B (MQTT `batteryMode` keepalive) voor batterij-ondersteuning bij EV-laden
- **Ontworpen, niet gevalideerd:** standalone `captarief` Python-project (Debian 13 LXC, dashboard poort 8770)
