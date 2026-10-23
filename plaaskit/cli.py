"""Command line interface: `plaas ...` (or `python -m plaaskit ...`)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta

from . import __version__, alerts, db, sms, trace, water
from .weather import WeatherUnavailable, fetch


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a date; use YYYY-MM-DD") from None


def _table(rows, cols):
    if not rows:
        print("(nothing yet)")
        return
    data = [[("-" if r[c] is None else str(r[c])) for c in cols] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in data)) for i, c in enumerate(cols)]
    print("  ".join(c.upper().ljust(w) for c, w in zip(cols, widths)))
    for r in data:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


# ---- command handlers -------------------------------------------------------------------------

def cmd_init(conn, a):
    trace.init_farm(conn, a.name, a.code, a.lat, a.lon, a.owner, a.province, a.ggn, a.puc, a.allocation_m3)
    print(f"Farm '{a.name}' saved. Data file: {db.db_path()}")


def cmd_field(conn, a):
    if a.action == "add":
        trace.add_field(conn, a.name, a.ha, a.lat, a.lon, a.soil)
        print(f"Field '{a.name}' added ({a.ha} ha).")
    else:
        _table(conn.execute("SELECT name, area_ha, soil, lat, lon FROM field ORDER BY name").fetchall(),
               ["name", "area_ha", "soil", "lat", "lon"])


def cmd_plant(conn, a):
    if a.action == "add":
        trace.add_planting(conn, a.field, a.crop, a.date, a.variety)
        print(f"Planting recorded: {a.crop} on {a.field}, {a.date}.")
    elif a.action == "close":
        trace.close_planting(conn, a.field, a.crop)
        print("Planting closed.")
    else:
        _table(conn.execute("SELECT p.id, f.name AS field, p.crop, p.variety, p.planted_on, p.status "
                            "FROM planting p JOIN field f ON f.id = p.field_id ORDER BY p.planted_on").fetchall(),
               ["id", "field", "crop", "variety", "planted_on", "status"])


def cmd_spray(conn, a):
    if a.action == "add":
        p = trace.resolve_planting(conn, a.field, a.crop)
        trace.add_input(conn, p, a.date, a.kind, a.product, a.active, a.reg_no, a.dose, a.unit, a.phi,
                        a.target, a.operator, a.note)
        safe = a.date + timedelta(days=a.phi)
        print(f"Recorded. Do not harvest before {safe.isoformat()}." if a.phi else "Recorded.")
    else:
        rows = trace.input_register(conn, date(2000, 1, 1), date(2100, 1, 1))
        if a.field:
            rows = [r for r in rows if r["field"].lower() == a.field.lower()]
        _table(rows, ["applied_on", "field", "product", "reg_no", "dose", "unit", "phi_days", "safe_harvest_from"])


def cmd_harvest(conn, a):
    if a.action == "add":
        p = trace.resolve_planting(conn, a.field, a.crop)
        lot = trace.add_harvest(conn, p, a.date, a.kg, a.dest, a.force, a.note)
        print(f"Harvest recorded. Lot code: {lot}\nPrint it on the crate/pack label; trace it with: plaas trace {lot}")
    else:
        _table(conn.execute("SELECT h.lot_code, h.harvested_on, f.name AS field, p.crop, h.qty_kg, h.destination, "
                            "CASE h.phi_override WHEN 1 THEN 'OVERRIDE' ELSE 'ok' END AS phi "
                            "FROM harvest h JOIN planting p ON p.id = h.planting_id JOIN field f ON f.id = p.field_id "
                            "ORDER BY h.harvested_on DESC").fetchall(),
               ["lot_code", "harvested_on", "field", "crop", "qty_kg", "destination", "phi"])


def cmd_trace(conn, a):
    t = trace.trace_lot(conn, a.lot)
    print(json.dumps(t, indent=2) if a.json else trace.format_trace(t))


def cmd_export(conn, a):
    rows = trace.input_register(conn, a.since, a.until)
    trace.write_register_csv(a.out, rows)
    print(f"Wrote {len(rows)} spray/fertiliser records to {a.out}")


def cmd_forecast(conn, a):
    farm = trace.get_farm(conn)
    days, stale = fetch(farm["lat"], farm["lon"])
    if stale:
        print("! offline: showing cached forecast")
    rows = [{k: ("-" if v is None else round(v, 1) if isinstance(v, float) else v) for k, v in d.items()}
            for d in days if d["date"] >= date.today().isoformat()]
    _table(rows, ["date", "tmin", "tmax", "rain", "et0", "gust", "rh"])


def cmd_check(conn, a):
    cfg = None
    if a.config:
        with open(a.config) as fh:
            cfg = json.load(fh)
    alerts.run_check(conn, send=a.send, cfg=cfg)
    if not a.send:
        print("\n(preview only. Add --send to text subscribers.)")


def cmd_sub(conn, a):
    if a.action == "add":
        phone = sms.normalise_msisdn(a.phone)
        fid = trace.get_field(conn, a.field)["id"] if a.field else None
        conn.execute("INSERT OR REPLACE INTO subscriber (name, phone, field_id, lang) VALUES (?,?,?,?)",
                     (a.name, phone, fid, a.lang))
        conn.commit()
        print(f"Subscribed {a.name} ({phone}, {a.lang}).")
    elif a.action == "rm":
        conn.execute("DELETE FROM subscriber WHERE phone = ?", (sms.normalise_msisdn(a.phone),))
        conn.commit()
        print("Removed.")
    else:
        _table(conn.execute("SELECT s.name, s.phone, s.lang, f.name AS field FROM subscriber s "
                            "LEFT JOIN field f ON f.id = s.field_id").fetchall(), ["name", "phone", "lang", "field"])


def cmd_water(conn, a):
    if a.action == "log":
        mm, m3 = water.log_water(conn, trace.get_field(conn, a.field), a.date, a.source, a.mm, a.m3, a.note)
        print(f"Logged {mm} mm = {m3} m3 on {a.field}.")
    elif a.action == "advice":
        farm = trace.get_farm(conn)
        fields = [trace.get_field(conn, a.field)] if a.field else conn.execute("SELECT * FROM field").fetchall()
        for f in fields:
            lat = f["lat"] if f["lat"] is not None else farm["lat"]
            lon = f["lon"] if f["lon"] is not None else farm["lon"]
            days, _ = fetch(lat, lon)
            for r in water.advise_field(conn, f, days, date.today()):
                verdict = {"irrigate": f"IRRIGATE ~{r['mm']} mm ({r['m3']} m3)",
                           "wait": "WAIT: rain expected soon", "ok": "OK: no irrigation needed yet"}[r["action"]]
                print(f"{r['field']} ({r['crop']}): {verdict}\n   7-day crop use {r['etc_mm']} mm, effective rain "
                      f"{r['rain_mm']} mm, irrigated {r['irrigation_mm']} mm -> depletion {r['depletion_mm']} mm")
    else:
        rows, total, alloc, pct = water.usage_summary(conn, a.year)
        _table(rows, ["field", "m3", "mm", "kg", "kg_per_m3"])
        line = f"\nTotal {a.year}: {total} m3"
        if alloc:
            line += f" of {alloc:.0f} m3 licensed/allocated ({pct}%)"
            if pct >= 90:
                line += "  <-- near your limit"
        print(line)


def cmd_sms_test(conn, a):
    ok, detail = sms.send(sms.normalise_msisdn(a.phone), "PlaasKit test message. Alerts are working.")
    print("OK" if ok else f"FAILED: {detail}")
    return 0 if ok else 1


def cmd_backup(conn, a):
    conn.commit()
    shutil.copy2(db.db_path(), a.path)
    print(f"Backed up to {a.path}")


# ---- parser -------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="plaas", description="PlaasKit: traceability, SMS alerts and water "
                                "management for smallholder farms.")
    p.add_argument("--version", action="version", version=f"plaaskit {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    today = date.today()

    def add(name, fn, help_, action=None, choices=None):
        s = sub.add_parser(name, help=help_)
        if choices:
            s.add_argument("action", choices=choices)
        s.set_defaults(fn=fn)
        return s

    s = add("init", cmd_init, "set up your farm")
    s.add_argument("--name", required=True)
    s.add_argument("--code", required=True, help="2-6 letter code used in lot numbers")
    s.add_argument("--lat", type=float, required=True)
    s.add_argument("--lon", type=float, required=True)
    s.add_argument("--owner")
    s.add_argument("--province")
    s.add_argument("--ggn", help="GLOBALG.A.P. Number, if you have one")
    s.add_argument("--puc", help="Production unit code, if issued")
    s.add_argument("--allocation-m3", type=float, help="annual water allocation/licence in m3")

    s = add("field", cmd_field, "add/list fields", choices=["add", "list"])
    s.add_argument("name", nargs="?")
    s.add_argument("--ha", type=float)
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--soil")

    s = add("plant", cmd_plant, "add/close/list plantings", choices=["add", "close", "list"])
    s.add_argument("--field")
    s.add_argument("--crop")
    s.add_argument("--variety")
    s.add_argument("--date", type=_date, default=today)

    s = add("spray", cmd_spray, "record/list sprays and fertiliser", choices=["add", "list"])
    s.add_argument("--field")
    s.add_argument("--crop")
    s.add_argument("--kind", choices=trace.INPUT_KINDS, default="pesticide")
    s.add_argument("--product")
    s.add_argument("--active", help="active ingredient")
    s.add_argument("--reg-no", help="Act 36 registration number from the label")
    s.add_argument("--dose", type=float)
    s.add_argument("--unit", help="e.g. l/ha, g/100l")
    s.add_argument("--phi", type=int, default=0, help="pre-harvest interval in days (label)")
    s.add_argument("--target", help="pest/disease targeted")
    s.add_argument("--operator")
    s.add_argument("--note")
    s.add_argument("--date", type=_date, default=today)

    s = add("harvest", cmd_harvest, "record/list harvest lots", choices=["add", "list"])
    s.add_argument("--field")
    s.add_argument("--crop")
    s.add_argument("--kg", type=float)
    s.add_argument("--dest", help="buyer / packhouse / market")
    s.add_argument("--note")
    s.add_argument("--force", action="store_true", help="override a PHI block (recorded in audit trail)")
    s.add_argument("--date", type=_date, default=today)

    s = add("trace", cmd_trace, "full traceability record for a lot")
    s.add_argument("lot")
    s.add_argument("--json", action="store_true")

    s = add("export", cmd_export, "export spray/fertiliser register to CSV")
    s.add_argument("--since", type=_date, default=date(today.year, 1, 1))
    s.add_argument("--until", type=_date, default=today)
    s.add_argument("--out", default="input_register.csv")

    add("forecast", cmd_forecast, "show the 5-day forecast for your farm")

    s = add("check", cmd_check, "evaluate weather/pest/irrigation alerts (add --send to SMS)")
    s.add_argument("--send", action="store_true")
    s.add_argument("--config", help="JSON file overriding alert thresholds")

    s = add("sub", cmd_sub, "manage SMS subscribers", choices=["add", "rm", "list"])
    s.add_argument("--name")
    s.add_argument("--phone")
    s.add_argument("--field", help="only send this field's irrigation alerts")
    s.add_argument("--lang", choices=alerts.LANGS, default="en")

    s = add("water", cmd_water, "log water, get irrigation advice, usage report", choices=["log", "advice", "report"])
    s.add_argument("--field")
    s.add_argument("--mm", type=float)
    s.add_argument("--m3", type=float)
    s.add_argument("--source", help="borehole, dam, river, municipal...")
    s.add_argument("--note")
    s.add_argument("--date", type=_date, default=today)
    s.add_argument("--year", type=int, default=today.year)

    s = add("sms-test", cmd_sms_test, "send a test SMS")
    s.add_argument("phone")

    s = add("backup", cmd_backup, "copy the database to a file")
    s.add_argument("path")
    return p


REQUIRED = {
    ("field", "add"): ["name", "ha"], ("plant", "add"): ["field", "crop"], ("spray", "add"): ["field", "product"],
    ("harvest", "add"): ["field", "kg"], ("sub", "add"): ["name", "phone"], ("sub", "rm"): ["phone"],
    ("water", "log"): ["field"],
}


def main(argv=None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    for attr in REQUIRED.get((a.cmd, getattr(a, "action", None)), []):
        if getattr(a, attr) in (None, ""):
            parser.error(f"'{a.cmd} {a.action}' requires " + ("a NAME" if (a.cmd, attr) == ("field", "name")
                                                              else f"--{attr}"))
    conn = db.connect()
    try:
        return a.fn(conn, a) or 0
    except (LookupError, ValueError, trace.ComplianceError, WeatherUnavailable, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
