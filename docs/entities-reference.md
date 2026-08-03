# Entities-referentie

## Kernentiteiten

| Entity | Rol |
|---|---|
| `sensor.p1_meter_power` | Netafname (W, positief = import) |
| `sensor.net_afname_w` | Template: `max(0, p1_meter_power)` |
| `sensor.net_afname_energie` | Riemann-integratie van `net_afname_w` |
| `sensor.net_afname_kwartier` | Kwartier-energie-accumulator (utility_meter) |
| `sensor.aanbevolen_laadstroom` | Berekend laadstroom-setpoint (A) |
| `select.evcc_oprit_max_current` | evcc-doelstroom |
| `sensor.evcc_oprit_charge_current` | Werkelijke laadstroom |
| `binary_sensor.evcc_oprit_connected` | EV-verbindingsstatus |
| `sensor.indevolt_garage_battery_power` / `_soc` | Batterij Garage (4 kWh) |
| `sensor.indevolt_zolder_battery_power` / `_soc` | Batterij Zolder (6 kWh) |
| `sensor.inverter_active_power` | Huawei SUN2000 productie |
| `sensor.effectieve_maandpiek` | `max(werkelijke maandpiek kW, 2.5)` |
| `input_boolean.cap_batterij_ondersteunt_ev` | Batterij-ondersteuning aan/uit (default: uit) |

## Statistiek-sensoren

| Entity | Functie |
|---|---|
| `sensor.p1_vermogen_30s` | Gemiddelde P1-vermogen over 30s |
| `sensor.laadpaal_vermogen_30s` | Gemiddeld laadpaalvermogen over 30s |
| `sensor.aanbevolen_laadstroom_min_90s` | Minimum aanbevolen stroom over 90s (gate voor opwaartse stappen) |

## Input numbers

| Entity | Default | Betekenis |
|---|---|---|
| `cap_marge_w` | 150 W | Absolute veiligheidsmarge |
| `cap_boost_factor` | 1.00 | Vlak (1.15 = catch-up bij deadline) |
| `cap_freeze_sec` | 240 | Lengte freeze-window vóór kwartiergrens |
| `cap_bat_soc_reserve` | 10% | Minimale batterij-SoC-reserve |
| `cap_fases` | 1 | Aantal actieve laadfases |

## Automations / scripts

| Naam | Trigger | Functie |
|---|---|---|
| `cap_noodrem_v8` | P1 > doel + 800W gedurende 10s | Forceer laadstroom naar 6A |
| `cap_kwartier_reset_v8` | Elk kwartiermoment + 3s | Klem setpoint terug naar vlakke waarde |

## Kanttekening: evcc/Indevolt-entiteiten niet zichtbaar voor Assist

Zoekopdrachten via `Home Assistant:GetLiveContext` naar evcc/Indevolt-entiteiten (`evcc battery`, `indevolt`, `oprit`, domein `select`) leveren geen resultaten op — deze entiteiten zijn niet blootgesteld aan de Assist/conversation-agent. Admin-wachtwoordconfiguratie in de ha-evcc-integratie is vereist opdat batterij- en meter-controle-entiteiten zouden verschijnen. Er zijn geen standalone evcc MCP-tools beschikbaar; alle evcc-interactie moet via HA entity exposure of directe MQTT lopen.

## Externe API's / interfaces

- **evcc MQTT:** `evcc/site/batteryMode/set`
- **evcc REST API:** loadpoint `maxcurrent`, `batteryboost`, `batteryboostlimit`, `batterymode`
- **HomeWizard P1 lokale API:** `GET /api/v1/data` — velden `active_power_w`, `montly_power_peak_w` (sic)
- **Indevolt OpenData RPC** (POST, poort 8080):
  - register 6000 — vermogen (magnitude)
  - register 6001 — laadstatus / teken
  - register 6002 — SoC
