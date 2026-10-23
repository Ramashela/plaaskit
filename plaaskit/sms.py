"""SMS sending. Default backend uses the phone's own SIM through Termux:API (no gateway fees beyond airtime)."""
from __future__ import annotations

import os
import shutil
import subprocess


def normalise_msisdn(number: str) -> str:
    n = "".join(ch for ch in number if ch.isdigit() or ch == "+")
    if n.startswith("+27") and len(n) == 12:
        return n
    if n.startswith("27") and len(n) == 11:
        return "+" + n
    if n.startswith("0") and len(n) == 10:
        return "+27" + n[1:]
    raise ValueError(f"'{number}' is not a valid South African number (use 0821234567 or +27821234567)")


def backend() -> str:
    return os.environ.get("PLAAS_SMS_BACKEND") or ("termux" if shutil.which("termux-sms-send") else "console")


def send(phone: str, text: str) -> tuple[bool, str]:
    b = backend()
    if b == "console":
        print(f"[sms:console] {phone}: {text}")
        return True, "console"
    if b == "termux":
        if not shutil.which("termux-sms-send"):
            return False, "termux-sms-send not found (pkg install termux-api + install the Termux:API app)"
        try:
            r = subprocess.run(["termux-sms-send", "-n", phone, text], capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return (r.returncode == 0, r.stderr.strip() or "sent")
    return False, f"unknown PLAAS_SMS_BACKEND '{b}'"
