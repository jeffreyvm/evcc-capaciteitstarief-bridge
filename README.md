# evcc-capaciteitstarief-bridge

Peak shaving for the Belgian capacity tariff, built as a companion to
[evcc](https://evcc.io). It runs in its own LXC, watches the running
quarter-hour, and steers evcc's charge current and battery mode so your
monthly peak stays where you want it.

## The problem

Under the Belgian capaciteitstarief your grid fee is driven by the highest
15-minute *average* import of the month, with a billing floor of 2.5 kW. Home
batteries in self-consumption mode already shave peaks — right up until they
are empty, which tends to be exactly when the evening peak arrives, because
baseload drained them at 22:00 the night before.

evcc deliberately does not solve this. Its battery controls exist to stop
discharge during fast charging and to charge on cheap tariffs; using evcc as a
full battery management system is out of scope, and peak shaving sits in its
open feature requests. This project fills that gap without replacing evcc and
without dragging Home Assistant into a control loop that has money attached to
it — evcc's REST API is the only thing this talks to.

## What it does

The insight is that self-consumption *is* peak shaving as long as there is
charge left. So this controls state of charge, not discharge:

| Mode | When | evcc battery mode |
|---|---|---|
| **Reserve** | Normal operation | `hold` below the reserve SoC, `normal` above it |
| **Precharge** | Lead-up to a peak window, SoC under target | `charge`, capped by the quarter budget |
| **Release** | Projected quarter average nears the billed peak | `normal` down to the hard floor |

Charge current and battery precharging are arbitrated by the same quarter-hour
budget, so precharging can never itself set the peak it was meant to prevent.

## Install

On the Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffreyvm/evcc-capaciteitstarief-bridge/main/ct/capaciteit.sh)"
```

Creates an unprivileged Debian 12 LXC (1 core, 512 MB, 4 GB), installs the
service, and starts it in dry-run mode. Read the script first — that advice
applies to anything you pipe into a shell, including this. It's standalone —
it doesn't source `build.func` from the community-scripts repo, so nothing
outside this repo can change what runs on your hypervisor.

Then point it at evcc and restart:

```bash
pct exec <ctid> -- nano /etc/capaciteit/capaciteit.env   # EVCC_URL, EVCC_API_KEY
pct exec <ctid> -- systemctl restart capaciteit
```

The dashboard is on port 8099.

**Existing container or VM?** Skip the helper script:

```bash
curl -fsSL https://raw.githubusercontent.com/jeffreyvm/evcc-capaciteitstarief-bridge/main/deploy/install.sh | bash
```

Re-running either upgrades in place and leaves your config alone — the
installer is idempotent and never touches an existing env file.

## Configuration

Everything lives in `/etc/capaciteit/capaciteit.env`; see
[`deploy/capaciteit.env.example`](deploy/capaciteit.env.example) for the
annotated list. The settings that matter most:

- `DRY_RUN` — starts `true`. Decisions are computed and shown, nothing is sent.
- `TARGET_PEAK_KW` — `0` learns the month peak from measurements. Set a value
  to aim lower than your actual peak, or while you have no history yet.
- `RESERVE_SOC` / `TARGET_SOC` / `HARD_FLOOR_SOC` — the SoC policy.
- `PEAK_WINDOWS` — when a peak is likely, e.g. `07:00-09:00,17:00-21:00`.
- `BATTERY_CONTROL` — set `false` to steer only the charger.

## Rolling it out

1. Leave `DRY_RUN=true` and watch the dashboard for a few days. Check the
   decisions it reports match what you would have done.
2. Set `DRY_RUN=false`. If you were running the old Home Assistant automation
   that pushed max current to evcc, disable it now — this service owns that
   setpoint.
3. Watch the month peak. It should stop climbing.

Failing is safe by construction: battery actuation goes through evcc's
`POST /api/batterymode/{normal|hold|charge}`, which is watchdog-guarded on
evcc's side — the control loop re-asserts every 15 s, and a dead container
just lets evcc resume its own logic. On a clean shutdown the controller hands
control back explicitly via `DELETE /api/batterymode`.

## Architecture

```
capaciteit/
  peak.py      quarter-hour integral, month peak, billing floor   (pure)
  logic.py     charge current decisions                           (pure)
  battery.py   reserve / precharge / release                      (pure)
  evcc.py      the only module that speaks HTTP to evcc
  loop.py      sequences the above and owns side effects
  store.py     one hour of history for the dashboard
  web.py       read-only dashboard and JSON API
```

evcc's REST API is the only thing the LXC talks to. `peak.py` now does the
quarter-hour integral and month-peak tracking itself from evcc's grid power —
that used to be Home Assistant's job via template sensors, and was the last
thing keeping HA in the control path.

The three pure modules hold every number that matters and import nothing but
the standard library, which is why the test suite runs in a second without a
container, an evcc, or a network. Two tests are named after phone screenshots:
they reproduce false positives from the original Home Assistant
implementation — a breach warning that fired while the meter was exporting,
and a "current lowered" action that lowered 10 A to 10 A. Both came from the
window budget legitimately collapsing near the end of a quarter, and both are
fixed by the breach floor: below the billed minimum, nothing that happens can
cost money.

The dashboard makes the 15-minute window the hero rather than a generic
gauge: elapsed consumption as a solid block, the projection as its faded
continuation to the window edge, your billed month peak as the line it must
not cross, and the remaining budget as a dashed ceiling. It's read-only by
design — everything that changes behaviour is in the env file, so nothing in
the UI can bypass `DRY_RUN`, and exposing port 8099 on your LAN carries no
extra risk.

```bash
pip install -e '.[test]' && pytest -q
```

## Where it fits

evcc keeps doing what it is good at: chargers, vehicles, tariffs, solar
surplus, and a UI worth looking at. This adds the one thing it does not model
— the Belgian capacity tariff — and talks to evcc through its documented REST
API rather than reaching into your inverters. If you would rather have one
system do everything, [OpenEMS](https://github.com/OpenEMS/openems) is the
serious open-source option and has real peak-shaving controllers, but it is a
much heavier stack and you will be writing device drivers.

## Licence

MIT.
