# Architectuur — Capaciteitstarief v8

## Doel

Blijf onder de Fluvius-piek van 2.5 kW per kwartier, door het EV-laadvermogen dynamisch bij te sturen op basis van reële netmeting — niet op basis van schattingen van PV-overschot of verbruik.

## Datastroom

```
sensor.p1_meter_power (W, + = afname)
        │
        ▼
sensor.net_afname_w = max(0, p1_meter_power)     [template]
        │
        ▼  Riemann-integratie
sensor.net_afname_energie
        │
        ▼  utility_meter (cyclus: kwartier)
sensor.net_afname_kwartier
```

Dit vervangt de oudere afhankelijkheid van `sensor.remaining_energy_this_quarter_hour` / `allowed_power_w` uit v7.

## Kern-feedbackformule

```
budget_w = P_ev + (doel − P_net) + bat_extra
setpoint = min(vlak, plafond)      # plafond kan enkel dalen
```

- **P_ev**: huidig laadvermogen van de EV
- **doel**: streefwaarde netvermogen (afgeleid van 2.5 kW-drempel)
- **P_net**: huidige nettoafname (P1-meter)
- **bat_extra**: extra ontlaadcapaciteit van de batterijen (optioneel, zie `battery-discharge-decision.md`)

### Ceiling-only correctie

Het plafond (`plafond_w`) kan tijdens een kwartier enkel omlaag gecorrigeerd worden, nooit omhoog. Dit voorkomt overshoot: als er tijdelijk marge is, wordt die niet meteen volledig benut, wat later in het kwartier tot een te snelle terugval zou leiden.

## `sensor.aanbevolen_laadstroom`

Trigger-based template sensor, herberekend elke 10s. Attributen:
- `doel_net_w`
- `plafond_w`
- `resterend_sec`
- `kwartier_verbruikt_wh`
- `in_freeze`

## Freeze window

240 seconden vóór elke kwartiergrens: geen opwaartse stappen toegestaan. Voorkomt de "hyperbolische" ampère-klim die in v7 optrad wanneer `resterende_energie / resterende_tijd` tegen het einde van een kwartier explodeerde.

## Step rules

- **Omhoog:** max +2A per stap, met minstens 60s tussen twee verhogingen
- **Omlaag:** onmiddellijk, geen deadband

## Noodrem (`cap_noodrem_v8`)

Als P1 > doel + 800W gedurende 10 seconden aaneengesloten → forceer laadstroom naar 6A.

## Kwartier-reset (`cap_kwartier_reset_v8`)

Triggert op elk kwartiermoment + 3s. Klemt het setpoint terug naar de vlakke (flat) waarde, als startpunt voor het nieuwe kwartier.

## Statistiek-sensoren

| Sensor | Functie |
|---|---|
| `sensor.p1_vermogen_30s` | gemiddelde over 30s |
| `sensor.laadpaal_vermogen_30s` | gemiddelde over 30s |
| `sensor.aanbevolen_laadstroom_min_90s` | minimum over 90s, gebruikt als gate voor opwaartse stappen |

## Input numbers (instelbare parameters)

| Entity | Waarde | Betekenis |
|---|---|---|
| `cap_marge_w` | 150 W | absolute veiligheidsmarge |
| `cap_boost_factor` | 1.00 (flat) / 1.15 (catch-up) | vermenigvuldigingsfactor bij deadline |
| `cap_freeze_sec` | 240 | lengte van het freeze-window |
| `cap_bat_soc_reserve` | 10% | minimale SoC-reserve voor batterij-ondersteuning |
| `cap_fases` | 1 | aantal actieve fases (single-phase laadpaal) |

## Batterijterm (`bat_extra`)

Telt enkel de *extra* ontlaadcapaciteit boven het reeds gebruikte vermogen. Energie-gelimiteerd:

```
bat_extra ≤ usable_wh * 3600 / rem_sec
bat_extra ≤ 1000 W per batterij
```

met SoC-reserve via `cap_bat_soc_reserve`. Wordt volledig op 0 gezet wanneer `input_boolean.cap_batterij_ondersteunt_ev` uit staat.

## Overgenomen uit v7

- `sensor.effectieve_maandpiek` = `max(werkelijke maandpiek, 2.5)` kW
- Notificatieketen bij naderende piek
- Auto-stop / auto-restart van de regellus
