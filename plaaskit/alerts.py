"""Weather and pest-risk rules, SMS rendering (English / Afrikaans) and de-duplicated dispatch."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import sms, water, weather
from .trace import get_farm

# Advisory thresholds. Override any of them with a JSON file: plaas check --config thresholds.json
DEFAULTS = {
    "frost_c": 2.0, "hard_frost_c": 0.0, "heat_c": 35.0, "heavy_rain_mm": 25.0, "gust_kmh": 60.0,
    "fungal_rh": 80.0, "fungal_tmean_min": 10.0, "fungal_tmean_max": 26.0, "fungal_days": 2,
    "armyworm_tmin": 15.0, "armyworm_tmax": 34.0,
}

TEXT = {
    "en": {
        "frost_hard": "HARD FROST {d}: min {tmin:.0f}C. Cover or protect seedlings and young trees tonight.",
        "frost": "Frost risk {d}: min {tmin:.0f}C. Protect frost-sensitive crops.",
        "heat": "Heat {d}: max {tmax:.0f}C. Irrigate early morning, avoid spraying midday.",
        "rain": "Heavy rain {d}: {rain:.0f}mm expected. Delay spraying and fertiliser, check drainage.",
        "wind": "Strong wind {d}: gusts {gust:.0f}km/h. Do not spray. Secure tunnels and nets.",
        "fungal": "Fungal disease risk from {d}: humid and mild. Scout leaves; if spraying, check the PHI.",
        "armyworm": "Fall armyworm risk {d}: warm nights. Scout maize plants for damage and eggs.",
        "irrigate": "Irrigate {field}: about {mm:.0f}mm ({m3:.0f} m3) needed. Best done before 9am.",
    },
    "af": {
        "frost_hard": "SWAAR RYP {d}: min {tmin:.0f}C. Beskerm saailinge en jong bome vannag.",
        "frost": "Rypgevaar {d}: min {tmin:.0f}C. Beskerm rypgevoelige gewasse.",
        "heat": "Hitte {d}: maks {tmax:.0f}C. Besproei vroegoggend, moenie middag spuit nie.",
        "rain": "Swaar reen {d}: {rain:.0f}mm verwag. Stel spuit en kunsmis uit, kyk dreinering.",
        "wind": "Sterk wind {d}: windstote {gust:.0f}km/h. Moenie spuit nie. Maak tonnels en nette vas.",
        "fungal": "Swamsiekterisiko vanaf {d}: vogtig en matig. Gaan blare na; as jy spuit, check die PHI.",
        "armyworm": "Herfsleerwurm-risiko {d}: warm nagte. Gaan mielies na vir skade en eiers.",
        "irrigate": "Besproei {field}: ongeveer {mm:.0f}mm ({m3:.0f} m3) nodig. Doen dit voor 09:00.",
    },
}
LANGS = tuple(TEXT)
SEVERITY = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Alert:
    kind: str
    severity: str
    date: str
    params: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.date}:{self.params.get('field', '')}"


def label(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %d %b")


def render(alert: Alert, lang: str = "en", prefix: str = "") -> str:
    tpl = TEXT.get(lang, TEXT["en"])[alert.kind]
    return (f"{prefix}: " if prefix else "") + tpl.format(d=label(alert.date), **alert.params)


def evaluate(forecast: list[dict], crops=(), cfg: dict | None = None) -> list[Alert]:
    c = {**DEFAULTS, **(cfg or {})}
    out, streak, start, fungal_done, army_done = [], 0, None, False, False
    for d in forecast:
        tmin, tmax, rain, gust, rh = d["tmin"], d["tmax"], d["rain"], d["gust"], d["rh"]
        if tmin is not None:
            if tmin <= c["hard_frost_c"]:
                out.append(Alert("frost_hard", "high", d["date"], {"tmin": tmin}))
            elif tmin <= c["frost_c"]:
                out.append(Alert("frost", "medium", d["date"], {"tmin": tmin}))
        if tmax is not None and tmax >= c["heat_c"]:
            out.append(Alert("heat", "medium", d["date"], {"tmax": tmax}))
        if rain is not None and rain >= c["heavy_rain_mm"]:
            out.append(Alert("rain", "medium", d["date"], {"rain": rain}))
        if gust is not None and gust >= c["gust_kmh"]:
            out.append(Alert("wind", "medium", d["date"], {"gust": gust}))
        if tmin is not None and tmax is not None:
            tmean = (tmin + tmax) / 2
            wet = (rh is not None and rh >= c["fungal_rh"]) or (rain or 0) >= 1.0
            if wet and c["fungal_tmean_min"] <= tmean <= c["fungal_tmean_max"]:
                streak += 1
                start = start or d["date"]
                if streak >= c["fungal_days"] and not fungal_done:
                    out.append(Alert("fungal", "medium", start))
                    fungal_done = True
            else:
                streak, start = 0, None
            if "maize" in crops and not army_done and tmin >= c["armyworm_tmin"] and tmax <= c["armyworm_tmax"]:
                out.append(Alert("armyworm", "low", d["date"]))
                army_done = True
    return out


def dispatch(conn, farm, alerts, send=False, max_sms=3, out=print):
    """Send (or preview) alerts. Each subscriber gets at most max_sms per run, and never the same alert twice."""
    subs = conn.execute("SELECT * FROM subscriber WHERE active = 1").fetchall()
    if not subs:
        out("No subscribers yet. Add one: plaas sub add --name NAME --phone 0821234567")
    for s in subs:
        mine = [a for a in alerts
                if a.params.get("field_id") is None or s["field_id"] is None or s["field_id"] == a.params["field_id"]]
        mine.sort(key=lambda a: (SEVERITY[a.severity], a.date))
        for a in mine[:max_sms]:
            text = render(a, s["lang"], farm["name"])
            if not send:
                out(f"[preview] {s['phone']}: {text}")
                continue
            if conn.execute("SELECT 1 FROM alert_log WHERE phone=? AND alert_key=? AND status='sent'",
                            (s["phone"], a.key)).fetchone():
                continue
            ok, detail = sms.send(s["phone"], text)
            conn.execute("INSERT INTO alert_log (phone, alert_key, message, status) VALUES (?,?,?,?)",
                         (s["phone"], a.key, text, "sent" if ok else f"failed: {detail}"))
            conn.commit()
            if not ok:
                out(f"SMS to {s['phone']} failed: {detail}")


def run_check(conn, fetch=weather.fetch, today: date | None = None, send=False, cfg=None, out=print):
    farm = get_farm(conn)
    today = today or date.today()
    days, stale = fetch(farm["lat"], farm["lon"])
    if stale:
        out("! offline: using cached forecast")
    fc = [d for d in days if d["date"] >= today.isoformat()]
    crops = {r["crop"] for r in conn.execute("SELECT crop FROM planting WHERE status = 'active'")}
    alerts = evaluate(fc, crops, cfg)
    for f in conn.execute("SELECT * FROM field").fetchall():
        fdays = days
        if f["lat"] is not None and f["lon"] is not None:
            try:
                fdays, _ = fetch(f["lat"], f["lon"])
            except weather.WeatherUnavailable:
                pass
        for adv in water.advise_field(conn, f, fdays, today):
            if adv["action"] == "irrigate":
                alerts.append(Alert("irrigate", "medium", today.isoformat(),
                                    {"field": f["name"], "field_id": f["id"], "mm": adv["mm"], "m3": adv["m3"]}))
    if not alerts:
        out("No alerts: conditions look normal for the next 5 days.")
    for a in alerts:
        out(f"[{a.severity}] {render(a)}")
    dispatch(conn, farm, alerts, send=send, out=out)
    return alerts
