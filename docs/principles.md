# Kernlessen & ontwerpprincipes

- **Economisch inzicht:** inhalen tegen het einde van een kwartier levert geen goedkopere kWh op — de capaciteitscomponent is al aangerekend. Vlakke regeling is optimaal, tenzij de auto een deadline heeft (dan `cap_boost_factor` = 1.15).
- **Feedback boven schatting:** rechtstreeks het nettovermogen (`P_net`) meten vangt impliciet het huishoudelijk verbruik op — geen aparte schatting van PV-overschot en verbruik nodig.
- **Ceiling-only correctie:** het setpoint mag binnen een kwartier enkel omlaag bijgesteld worden; opwaartse correcties zijn tijdsgated en step-limited om overshoot te vermijden.
- **evcc als actuator, HA als brein:** evcc verzorgt de hardware-abstractie; alle capaciteitstarief-beslissingen blijven in Home Assistant (of de standalone Python-laag).
- **evcc nooit bypassen:** de enige historische uitzondering was de v7 netladen-failsafe — inmiddels samen met alle netladen-logica volledig verwijderd.
- **Indevolt-tekenconventie:** batterijvermogen-sensoren in HA zijn positief bij ontladen — het omgekeerde van de weergave in de Indevolt-app.

## evcc-features die mogelijk (verder) benut kunnen worden (v0.306–0.311)

- Native Indevolt-batterijcontrole (v0.307.1) — eerdere raw-RPC-workaround kan hierdoor vervallen
- Huawei SUN2000: `returnEnergy`, battery-dim, curtailment (v0.308.0)
- `HoldCharge` batterijmodus (v0.309.0)
- MQTT `batteryMode` (`normal`/`hold`/`charge`, 60s keepalive) met verbeterde reconnect-handling
- Site-statistieken op kwartierbasis (sluit aan bij de Belgische kwartier-meting)
- evcc's nieuwe "optimizer" is enkel prijssignaal-gedreven — geen native capaciteitstarief/demand-charge peak-shaving; de HA v8-regelaar blijft dus de beslissingslaag, met evcc als actuator
