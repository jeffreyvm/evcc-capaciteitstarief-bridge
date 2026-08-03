# Open beslissing: batterij-ondersteuning bij EV-laden

## Huidige situatie

De evcc-schakelaar "Boost from Home-Battery" voor het laadpunt staat op **OFF**. Dit wijst erop dat `batteryDischargeControl` op site-niveau actief is — evcc blokkeert daardoor de Indevolt-batterijen om de laadpaal te ondersteunen. Daarom staat `input_boolean.cap_batterij_ondersteunt_ev` momenteel ook uit.

**Impact:** zonder batterij-ondersteuning kan 's avonds ongeveer 8A geladen worden; met ondersteuning zou dat ongeveer 17A zijn.

## Route A — Simpeler

1. Zet `batteryDischargeControl: false` in `evcc.yaml`
2. Verwijder de `bat_extra`-term uit `cap_regellus_v8` (batterijbijdrage zit dan al impliciet vervat in de `P_net`-feedback)
3. Verwijder `input_boolean.cap_batterij_ondersteunt_ev`
4. Verwijder de RPC-failsafe-laag volledig

**Caveat:** de batterij kan in PV-modus ook leeglopen in de auto — er is geen expliciete controle meer over wanneer dat gebeurt.

## Route B — Actieve controle

Implementeer een MQTT `batteryMode` keepalive-blok (`normal` / `hold`, elke 60s herhaald) om actief te schakelen tijdens kwartier-piekmanagement. Geeft fijnere controle, maar voegt complexiteit en een extra keepalive-mechanisme toe (vergelijkbaar in geest met de retired v7 netladen-keepalive, maar dan voor ontladen in plaats van laden).

## Afweging

| | Route A | Route B |
|---|---|---|
| Complexiteit | Laag | Hoger (MQTT keepalive, reconnect-handling) |
| Controle over batterij-gedrag | Impliciet, via evcc's eigen PV-logica | Expliciet, per kwartier aanstuurbaar |
| Risico | Batterij kan ongecontroleerd leeglopen richting auto in PV-modus | Extra faalpunt bij MQTT-verbindingsverlies |
| Past bij "evcc als actuator, HA als brein"? | Gedeeltelijk — evcc beslist mee over batterijgedrag | Ja, volledig — HA blijft de enige beslisser |

*Beslissing nog niet definitief genomen.*
