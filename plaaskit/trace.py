"""Traceability: fields, plantings, input (spray) records, harvest lots, audit reports."""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import date, timedelta

INPUT_KINDS = ("pesticide", "insecticide", "fungicide", "herbicide", "fertiliser", "other")
PESTICIDE_KINDS = ("pesticide", "insecticide", "fungicide", "herbicide")


class ComplianceError(Exception):
    """Raised when an action would break a compliance rule (e.g. harvest inside a PHI)."""


def get_farm(conn):
    row = conn.execute("SELECT * FROM farm WHERE id = 1").fetchone()
    if row is None:
        raise LookupError("No farm set up yet. Run: plaas init --name ... --code ... --lat ... --lon ...")
    return row


def init_farm(conn, name, code, lat, lon, owner=None, province=None, ggn=None, puc=None, allocation=None):
    code = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    if not 2 <= len(code) <= 6:
        raise ValueError("code must be 2-6 letters/digits, e.g. MVF")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude/longitude out of range (South Africa is roughly -22 to -35, 16 to 33)")
    conn.execute(
        "INSERT OR REPLACE INTO farm (id,name,code,owner,province,lat,lon,ggn,puc,water_allocation_m3) "
        "VALUES (1,?,?,?,?,?,?,?,?,?)",
        (name, code, owner, province, lat, lon, ggn, puc, allocation),
    )
    conn.commit()


def add_field(conn, name, area_ha, lat=None, lon=None, soil=None):
    if area_ha <= 0:
        raise ValueError("area must be greater than 0 ha")
    try:
        cur = conn.execute("INSERT INTO field (name,area_ha,lat,lon,soil) VALUES (?,?,?,?,?)",
                           (name, area_ha, lat, lon, soil))
    except sqlite3.IntegrityError:
        raise ValueError(f"field '{name}' already exists") from None
    conn.commit()
    return cur.lastrowid


def get_field(conn, name):
    row = conn.execute("SELECT * FROM field WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row is None:
        raise LookupError(f"unknown field '{name}'. See: plaas field list")
    return row


def add_planting(conn, field_name, crop, planted_on: date, variety=None):
    f = get_field(conn, field_name)
    cur = conn.execute("INSERT INTO planting (field_id,crop,variety,planted_on) VALUES (?,?,?,?)",
                       (f["id"], crop.strip().lower(), variety, planted_on.isoformat()))
    conn.commit()
    return cur.lastrowid


def resolve_planting(conn, field_name, crop=None):
    f = get_field(conn, field_name)
    q, args = "SELECT * FROM planting WHERE field_id = ? AND status = 'active'", [f["id"]]
    if crop:
        q += " AND lower(crop) = lower(?)"
        args.append(crop)
    rows = conn.execute(q, args).fetchall()
    if not rows:
        raise LookupError(f"no active planting on '{f['name']}'. Add one: plaas plant add --field ... --crop ...")
    if len(rows) > 1:
        raise ValueError(f"'{f['name']}' has several active plantings; add --crop")
    return rows[0]


def close_planting(conn, field_name, crop=None):
    p = resolve_planting(conn, field_name, crop)
    conn.execute("UPDATE planting SET status = 'closed' WHERE id = ?", (p["id"],))
    conn.commit()


def add_input(conn, planting, applied_on: date, kind, product, active=None, reg_no=None, dose=None,
              unit=None, phi_days=0, target=None, operator=None, note=None):
    if kind not in INPUT_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(INPUT_KINDS)}")
    if kind in PESTICIDE_KINDS and not reg_no:
        raise ValueError("pesticides need their Act 36 registration number (--reg-no), printed on the label")
    if phi_days < 0:
        raise ValueError("PHI days cannot be negative")
    if applied_on < date.fromisoformat(planting["planted_on"]):
        raise ValueError("application date is before the planting date")
    cur = conn.execute(
        "INSERT INTO input_use (planting_id,applied_on,kind,product,active_ingredient,reg_no,dose,unit,"
        "target,phi_days,operator,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (planting["id"], applied_on.isoformat(), kind, product, active, reg_no, dose, unit, target,
         phi_days, operator, note))
    conn.commit()
    return cur.lastrowid


def phi_conflicts(conn, planting_id, harvest_on: date):
    """Return [(product, safe_date)] for inputs whose pre-harvest interval has not elapsed."""
    out = []
    for r in conn.execute("SELECT product, applied_on, phi_days FROM input_use "
                          "WHERE planting_id = ? AND phi_days > 0", (planting_id,)):
        safe = date.fromisoformat(r["applied_on"]) + timedelta(days=r["phi_days"])
        if harvest_on < safe:
            out.append((r["product"], safe))
    return out


def add_harvest(conn, planting, harvested_on: date, qty_kg, destination=None, force=False, note=None):
    if harvested_on < date.fromisoformat(planting["planted_on"]):
        raise ValueError("harvest date is before the planting date")
    conflicts = phi_conflicts(conn, planting["id"], harvested_on)
    if conflicts and not force:
        detail = "; ".join(f"{p}: safe from {d.isoformat()}" for p, d in conflicts)
        raise ComplianceError(f"Pre-harvest interval not met ({detail}). Use --force only if you have "
                              "verified the label; the override is recorded in the audit trail.")
    farm = get_farm(conn)
    prefix = f"{farm['code']}-{harvested_on:%y%m%d}-{(planting['crop'] + 'XXX')[:3].upper()}"
    seq = conn.execute("SELECT COUNT(*) FROM harvest WHERE lot_code LIKE ?", (prefix + "-%",)).fetchone()[0] + 1
    lot = f"{prefix}-{seq:02d}"
    conn.execute("INSERT INTO harvest (planting_id,harvested_on,lot_code,qty_kg,destination,phi_override,note) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (planting["id"], harvested_on.isoformat(), lot, qty_kg, destination, 1 if conflicts else 0, note))
    conn.commit()
    return lot


def trace_lot(conn, lot_code):
    h = conn.execute("SELECT * FROM harvest WHERE upper(lot_code) = upper(?)", (lot_code,)).fetchone()
    if h is None:
        raise LookupError(f"unknown lot '{lot_code}'. See: plaas harvest list")
    p = conn.execute("SELECT * FROM planting WHERE id = ?", (h["planting_id"],)).fetchone()
    f = conn.execute("SELECT * FROM field WHERE id = ?", (p["field_id"],)).fetchone()
    inputs = [dict(r) for r in conn.execute(
        "SELECT * FROM input_use WHERE planting_id = ? AND applied_on <= ? ORDER BY applied_on, id",
        (p["id"], h["harvested_on"]))]
    w = conn.execute("SELECT COALESCE(SUM(mm),0) mm, COALESCE(SUM(volume_m3),0) m3 FROM water_log "
                     "WHERE field_id = ? AND logged_on BETWEEN ? AND ?",
                     (f["id"], p["planted_on"], h["harvested_on"])).fetchone()
    return {
        "lot_code": h["lot_code"], "farm": dict(get_farm(conn)), "field": dict(f), "planting": dict(p),
        "harvest": dict(h), "inputs": inputs,
        "irrigation": {"mm": round(w["mm"], 1), "m3": round(w["m3"], 1)},
        "phi_compliant": not h["phi_override"],
    }


def format_trace(t) -> str:
    fm, f, p, h = t["farm"], t["field"], t["planting"], t["harvest"]
    L = [f"TRACEABILITY RECORD  {t['lot_code']}", "=" * 60,
         f"Farm       : {fm['name']}  (owner: {fm['owner'] or '-'})",
         f"GGN / PUC  : {fm['ggn'] or '-'} / {fm['puc'] or '-'}",
         f"Field      : {f['name']}  ({f['area_ha']} ha)",
         f"Crop       : {p['crop']} {p['variety'] or ''}  planted {p['planted_on']}",
         f"Harvested  : {h['harvested_on']}  {h['qty_kg']:.0f} kg  -> {h['destination'] or '-'}",
         f"Irrigation : {t['irrigation']['mm']} mm  ({t['irrigation']['m3']} m3) planting to harvest",
         f"PHI status : {'OK' if t['phi_compliant'] else 'OVERRIDDEN - verify before dispatch'}", "",
         "Inputs applied:"]
    if not t["inputs"]:
        L.append("  (none recorded)")
    for i in t["inputs"]:
        L.append(f"  {i['applied_on']}  {i['kind']:<11} {i['product']} "
                 f"[{i['active_ingredient'] or '-'}] reg {i['reg_no'] or '-'}  "
                 f"{i['dose'] or '-'} {i['unit'] or ''}  PHI {i['phi_days']}d  by {i['operator'] or '-'}")
    return "\n".join(L)


REGISTER_COLS = ["applied_on", "field", "crop", "kind", "product", "active_ingredient", "reg_no", "dose",
                 "unit", "target", "phi_days", "safe_harvest_from", "operator", "note"]


def input_register(conn, since: date, until: date):
    rows = []
    q = ("SELECT i.*, f.name AS field, p.crop FROM input_use i JOIN planting p ON p.id = i.planting_id "
         "JOIN field f ON f.id = p.field_id WHERE i.applied_on BETWEEN ? AND ? ORDER BY i.applied_on, i.id")
    for r in conn.execute(q, (since.isoformat(), until.isoformat())):
        d = dict(r)
        d["safe_harvest_from"] = (date.fromisoformat(d["applied_on"]) + timedelta(days=d["phi_days"])).isoformat()
        rows.append(d)
    return rows


def write_register_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REGISTER_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
