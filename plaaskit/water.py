"""Water management: irrigation advice (FAO-56 style soil water balance), usage logging, licence tracking."""
from __future__ import annotations

from datetime import date, timedelta

# Approximate FAO-56 crop coefficients: (kc_ini, kc_mid, kc_end, len_ini, len_dev, len_mid, len_late) in days
ANNUALS = {
    "maize": (0.30, 1.20, 0.35, 20, 35, 40, 30),
    "tomato": (0.60, 1.15, 0.80, 30, 40, 40, 25),
    "potato": (0.50, 1.15, 0.75, 25, 30, 45, 30),
    "wheat": (0.30, 1.15, 0.25, 20, 25, 60, 30),
    "onion": (0.70, 1.05, 0.75, 15, 25, 70, 40),
    "cabbage": (0.70, 1.05, 0.95, 40, 60, 50, 15),
    "butternut": (0.50, 1.00, 0.80, 20, 30, 30, 20),
    "beans": (0.40, 1.15, 0.35, 20, 30, 40, 20),
}
PERENNIALS = {"citrus": 0.65, "avocado": 0.85, "grapes": 0.70, "macadamia": 0.80}
DEFAULT_KC = 0.80
READILY_AVAILABLE_MM = 25.0  # allowable depletion before irrigating; tune per soil (sand lower, clay higher)


def kc(crop: str, days_after_planting: int) -> float:
    crop = crop.lower()
    if crop in PERENNIALS:
        return PERENNIALS[crop]
    if crop not in ANNUALS:
        return DEFAULT_KC
    k_i, k_m, k_e, l_i, l_d, l_m, l_l = ANNUALS[crop]
    d = max(0, days_after_planting)
    if d <= l_i:
        return k_i
    if d <= l_i + l_d:
        return k_i + (k_m - k_i) * (d - l_i) / l_d
    if d <= l_i + l_d + l_m:
        return k_m
    if d <= l_i + l_d + l_m + l_l:
        return k_m + (k_e - k_m) * (d - l_i - l_d - l_m) / l_l
    return k_e


def effective_rain(mm: float) -> float:
    return mm * 0.8 if mm >= 5 else 0.0


def advise_field(conn, field_row, days: list[dict], today: date, raw_mm: float = READILY_AVAILABLE_MM):
    """Estimate root-zone depletion over the last 7 days and say whether to irrigate. Estimate only."""
    out = []
    start = today - timedelta(days=7)
    for p in conn.execute("SELECT * FROM planting WHERE field_id = ? AND status = 'active'", (field_row["id"],)):
        planted = date.fromisoformat(p["planted_on"])
        if planted > today:
            continue
        etc = rain = 0.0
        for d in days:
            dd = date.fromisoformat(d["date"])
            if start <= dd < today and d["et0"] is not None:
                etc += d["et0"] * kc(p["crop"], (dd - planted).days)
                rain += effective_rain(d["rain"] or 0)
        irrig = conn.execute("SELECT COALESCE(SUM(mm),0) FROM water_log WHERE field_id=? AND logged_on BETWEEN ? AND ?",
                             (field_row["id"], start.isoformat(), today.isoformat())).fetchone()[0]
        coming = sum(effective_rain(d["rain"] or 0) for d in days
                     if today <= date.fromisoformat(d["date"]) <= today + timedelta(days=1))
        depletion = max(0.0, etc - rain - irrig)
        if depletion < raw_mm:
            action = "ok"
        elif coming >= 0.5 * depletion:
            action = "wait"
        else:
            action = "irrigate"
        mm = round(depletion)
        out.append({"field": field_row["name"], "crop": p["crop"], "etc_mm": round(etc, 1), "rain_mm": round(rain, 1),
                    "irrigation_mm": round(irrig, 1), "depletion_mm": round(depletion, 1), "action": action,
                    "mm": mm, "m3": round(mm * field_row["area_ha"] * 10, 1)})
    return out


def log_water(conn, field_row, on: date, source=None, mm=None, m3=None, note=None):
    if (mm is None) == (m3 is None):
        raise ValueError("give exactly one of --mm or --m3")
    per_mm = field_row["area_ha"] * 10  # 1 mm over 1 ha = 10 m3
    if mm is None:
        mm = m3 / per_mm
    else:
        m3 = mm * per_mm
    if mm <= 0:
        raise ValueError("amount must be positive")
    conn.execute("INSERT INTO water_log (field_id,logged_on,source,volume_m3,mm,note) VALUES (?,?,?,?,?,?)",
                 (field_row["id"], on.isoformat(), source, round(m3, 2), round(mm, 2), note))
    conn.commit()
    return round(mm, 1), round(m3, 1)


def usage_summary(conn, year: int):
    """Per-field water use and water productivity (kg harvested per m3) for a calendar year."""
    y = str(year)
    rows = []
    for f in conn.execute("SELECT * FROM field ORDER BY name"):
        m3 = conn.execute("SELECT COALESCE(SUM(volume_m3),0) FROM water_log WHERE field_id=? "
                          "AND strftime('%Y', logged_on)=?", (f["id"], y)).fetchone()[0]
        kg = conn.execute("SELECT COALESCE(SUM(h.qty_kg),0) FROM harvest h JOIN planting p ON p.id=h.planting_id "
                          "WHERE p.field_id=? AND strftime('%Y', h.harvested_on)=?", (f["id"], y)).fetchone()[0]
        rows.append({"field": f["name"], "m3": round(m3, 1), "mm": round(m3 / (f["area_ha"] * 10), 1),
                     "kg": round(kg), "kg_per_m3": round(kg / m3, 2) if m3 else None})
    total = round(sum(r["m3"] for r in rows), 1)
    farm = conn.execute("SELECT water_allocation_m3 FROM farm WHERE id = 1").fetchone()
    alloc = farm["water_allocation_m3"] if farm else None
    return rows, total, alloc, (round(100 * total / alloc, 1) if alloc else None)
