# v7 → v8: wat werd opgelost en waarom

## Probleem 1 — Begin-van-de-maand kon 2.5 kW niet halen

**v7:** een multiplicatieve marge van 15% werd tweemaal toegepast, bovenop `allowed_power_w` dat de werkelijke (nog lage) maandpiek als basis gebruikte. Resultaat: vroeg in de maand werd het toegelaten vermogen kunstmatig te laag ingeschat.

**v8-oplossing:**
- Eigen kwartier-budget in plaats van een schatting op basis van de maandpiek
- Vaste basis van 2.5 kW
- Absolute marge van 150 W in plaats van een multiplicatieve marge

## Probleem 2 — Ampère-klim tegen het einde van een kwartier

**v7:** `allowed_power_w = resterende_energie / resterende_tijd`. Naarmate `resterende_tijd → 0`, explodeerde deze formule hyperbolisch, wat leidde tot bruuske stroomstijgingen vlak voor de kwartiergrens.

**v8-oplossing:**
- Ceiling-only correctie: het setpoint kan enkel dalen binnen een kwartier
- Freeze-window van 240s vóór elke kwartiergrens: geen opwaartse stappen meer toegestaan
- Step-limits: max +2A per stap, met minimaal 60s ertussen

## Probleem 3 — Huishoudelijk verbruik werd niet meegenomen

**v7:** vereiste een aparte schatting van PV-overschot en huishoudelijk verbruik, wat foutgevoelig was.

**v8-oplossing:** feedback op basis van het werkelijke nettovermogen (`P_net`, gemeten via de P1-meter). Het huishoudelijk verbruik zit daardoor impliciet vervat in de meting — geen aparte schatting meer nodig.

## Wat werd volledig verwijderd in v8

- MQTT keepalive voor netladen
- `netladen_stop`
- `netladen_doel_soc`
- De v7 netladen-failsafe (enige historische uitzondering op "nooit evcc bypassen")
- Directe Indevolt RPC-writes voor netladen-doeleinden

Al deze logica hing samen met het (nu permanent uitgeschakelde) netladen van de batterijen en is dus overbodig geworden.

## Economisch inzicht achter v8

Inhalen tegen het einde van een kwartier levert geen goedkopere kWh op — de capaciteitscomponent is al aangerekend zodra de piek is vastgesteld. Vlakke (flat) regeling is dus optimaal, tenzij de auto een deadline heeft (bv. vertrek 's ochtends vroeg) — dan wordt `cap_boost_factor` naar 1.15 gezet.
