# captarief (Python-variant)

Standalone migratie van de v8-logica buiten Home Assistant, ontworpen om te draaien op een **Debian 13 LXC in Proxmox**.

## Status

**Ontworpen en gecodeerd — deployment nog niet gevalideerd.**

## Geplande features

- Directe hardware-API-calls (HomeWizard P1, Indevolt OpenData RPC, evcc REST)
- Webdashboard op poort **8770**
- Prometheus-metrics op `/metrics`
- Systemd-service met hardening

## Openstaande placeholders

- evcc loadpoint-ID (momenteel aangenomen: `1`, voor "oprit")
- IP-adres Indevolt Zolder-batterij
- IP-adres P1-meter

## Relatie tot de Home Assistant-laag

Dit is een **mogelijke** vervanging van of aanvulling op `../home-assistant/capaciteitstarief.yaml` — niet iets dat er parallel ongecontroleerd mee moet draaien. Bij ingebruikname moet duidelijk vastgelegd worden welke laag (HA of Python) de effectieve beslisser is, om dubbele/conflicterende sturing van `select.evcc_oprit_max_current` te vermijden.

## TODO (Jeffrey)

- Plaats de effectieve Python-broncode hier
- Vul de IP/loadpoint-placeholders in
- Valideer deployment op de Debian 13 LXC
