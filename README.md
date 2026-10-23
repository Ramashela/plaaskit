# PlaasKit

**Farm management for South African smallholders that runs on a phone.**
Export-compliance traceability, weather and pest alerts by SMS, and water management, in one free, offline-first
command line tool. No subscription, no server, no app store. It runs in [Termux](https://termux.dev) on any Android phone.

## Why

Agricultural exports need compliance paperwork (spray records, pre-harvest intervals, lot traceability), yet commercial
farm software is priced for large commercial operations. South Africa is also a water-stressed country, and many
smallholders have no simple way to track how much water they use or when irrigation is actually needed.
PlaasKit is a small, honest tool for those gaps.

## Features

| Area | What it does |
|---|---|
| **Traceability** | Fields, plantings, spray and fertiliser records (with Act 36 registration numbers), harvest lots with unique lot codes, a one-command audit record per lot, CSV spray register export. |
| **PHI protection** | Refuses to record a harvest inside a pre-harvest interval. An override (`--force`) is allowed but permanently flagged on the lot. |
| **SMS alerts** | Frost, heat, heavy rain, strong wind, fungal-disease conditions and fall armyworm risk, plus irrigation reminders. Sent from the phone's own SIM via Termux:API. English and Afrikaans. No duplicate messages. |
| **Water** | Irrigation log in mm or m3, FAO-56 style soil-water-balance advice per field, annual usage against your allocation or licence, and water productivity (kg per m3). |
| **Offline-first** | Data lives in one SQLite file. The last forecast is cached, so it still works with no signal. |

Weather data comes from [Open-Meteo](https://open-meteo.com) (free, no API key).

## Install on Android (Termux)

1. Install **Termux** and **Termux:API** from [F-Droid](https://f-droid.org) (not the Play Store versions).
2. In Android settings, give **Termux:API** the **SMS** permission.
3. In Termux:

```sh
pkg update && pkg install python git termux-api
git clone https://github.com/YOUR-USERNAME/plaaskit.git
cd plaaskit
pip install .
plaas --version
```

Without installing, you can also run `python -m plaaskit ...` from the repo folder. Works on any computer with Python 3.9+.

## Quick start

```sh
# 1. Your farm (lat/lon: long-press your location in Google Maps)
plaas init --name "Mvelase Farm" --code MVF --lat -29.62 --lon 30.39 --owner "T. Mvelase" --allocation-m3 12000

# 2. Fields and what is planted
plaas field add North --ha 2.5 --soil loam
plaas plant add --field North --crop maize --date 2026-09-20

# 3. Record a spray (reg number and PHI come from the product label)
plaas spray add --field North --product "Karate Zeon" --active lambda-cyhalothrin \
    --reg-no L5806 --dose 0.1 --unit l/ha --phi 7 --target "stalk borer" --operator Sipho

# 4. Harvest: you get a lot code to put on the crate label
plaas harvest add --field North --kg 850 --dest "Packhouse X"
plaas trace MVF-261201-MAI-01          # full audit record for that lot
plaas export --out spray_register.csv  # spray/fertiliser register for your auditor or buyer

# 5. Water
plaas water log --field North --mm 20 --source borehole
plaas water advice                     # irrigate / wait / ok, per field
plaas water report                     # usage vs allocation, kg per m3

# 6. Alerts by SMS
plaas sub add --name Thandi --phone 0821234567 --lang en
plaas sms-test 0821234567
plaas check                            # preview
plaas check --send                     # text subscribers
```

Run `plaas COMMAND --help` for every option.

### Automatic daily alerts

```sh
pkg install cronie termux-services
sv-enable crond
crontab -e        # add:  0 5 * * *  plaas check --send
```
Install **Termux:Boot** so cron restarts after a reboot, and run `termux-wake-lock` so Android does not suspend Termux.

## How the numbers work

- **Irrigation advice** estimates crop water use (ETc = reference evapotranspiration x crop coefficient) over the last
  7 days, subtracts effective rainfall and what you logged, and recommends irrigating once the shortfall passes 25 mm,
  unless useful rain is forecast in the next 2 days. Crop coefficients are approximate FAO-56 values. **It is an
  estimate:** soil type, root depth and your system matter. Treat it as a prompt to check the soil, not a command.
- **Alert rules** (override with `plaas check --config thresholds.json`; see `DEFAULTS` in `plaaskit/alerts.py`):
  frost at min <= 2 C (hard frost <= 0 C), heat at max >= 35 C, heavy rain >= 25 mm/day, gusts >= 60 km/h,
  fungal risk after 2+ consecutive humid or wet days with mean 10-26 C, fall armyworm risk for maize on warm nights.
- **1 mm of water over 1 ha = 10 m3.**

## Important limits

PlaasKit helps you keep good records. It does **not** certify you for GLOBALG.A.P., PPECB, or any export scheme, and it
is not legal advice. Always follow the product label and your scheme's current requirements; PHI values you enter are
only as correct as the label you copy them from. Alert thresholds are general advisory defaults, not agronomic advice
for your specific crop. Back up your data regularly with `plaas backup /sdcard/plaas-backup.db`.

## Project layout

```
plaaskit/
  db.py        SQLite schema
  trace.py     fields, plantings, inputs, harvest lots, audit records
  weather.py   Open-Meteo client with offline cache
  alerts.py    alert rules, English/Afrikaans messages, de-duplicated dispatch
  water.py     crop coefficients, irrigation advice, usage reporting
  sms.py       Termux SMS backend, number normalisation
  cli.py       `plaas` command
tests/         unit tests (python -m unittest discover)
```

## Roadmap

isiZulu / isiXhosa / Sesotho messages, inbound SMS (reply "SPRAYED 3 L" to log), packhouse-ready export formats,
soil-type presets, multi-user sync, USSD/WhatsApp delivery. Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT
