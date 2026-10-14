"""Sheet Dialer: a spreadsheet-driven power dialer.

Reads leads from leads.xlsx, shows one lead at a time with its script,
dials through the Zoom desktop app (zoomphonecall:// links) and writes the
call outcome back into the same spreadsheet.

Run:  .venv/bin/python app.py   then open http://localhost:5050
"""
import json
import os
import re
from datetime import date, datetime

import hmac

from flask import Flask, Response, jsonify, render_template, request
from openpyxl import load_workbook

BASE = os.path.dirname(os.path.abspath(__file__))
LEADS_FILE = os.environ.get("LEADS_FILE", os.path.join(BASE, "leads.xlsx"))
CONFIG_FILE = os.path.join(BASE, "config.json")
SHEET = "Leads"
SCRIPT_SHEET = "Scripts"

COUNTRY_CODES = {"US": "1", "USA": "1", "CA": "1", "CANADA": "1", "UK": "44", "GB": "44", "PK": "92", "PAKISTAN": "92"}

app = Flask(__name__)


def load_config():
    cfg = {"calendly_link": "https://calendly.com/your-link/demo", "caller_id": "", "your_name": "",
           "product_name": "Acme Assist",
           "zoom_mode": "link", "access_password": ""}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    return cfg


def to_e164(raw, country):
    """Normalise a phone number to +<country><number> so Zoom dials it correctly."""
    if raw is None:
        return ""
    raw = str(raw).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    cc = COUNTRY_CODES.get(str(country or "").strip().upper())
    if cc is None:
        return "+" + digits
    if cc == "1":
        if len(digits) == 10:
            return "+1" + digits
        return "+" + digits
    if digits.startswith(cc) and len(digits) > 10:
        return "+" + digits
    return "+" + cc + digits.lstrip("0")


def fmt(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return "" if value is None else str(value)


def lead_sheets(wb):
    """Every tab that holds leads (anything except the scripts tab), main sheet first."""
    names = [n for n in wb.sheetnames if n != SCRIPT_SHEET]
    return sorted(names, key=lambda n: n != SHEET)


def pick_sheet(wb, name):
    return name if name in lead_sheets(wb) else SHEET


def read_leads(sheet=None):
    wb = load_workbook(LEADS_FILE)
    sheet = pick_sheet(wb, sheet)
    ws = wb[sheet]
    headers = [c.value for c in ws[1]]
    leads = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        lead = {h: fmt(v) for h, v in zip(headers, row) if h}
        lead["_row"] = row_idx
        lead["_dial"] = to_e164(lead.get("Phone"), lead.get("Country"))
        if sheet != SHEET and not lead["_dial"]:
            continue  # extra tabs list every lead for reference; only rows with a number are callable
        leads.append(lead)
    scripts = {}
    if SCRIPT_SHEET in wb.sheetnames:
        for name, text in wb[SCRIPT_SHEET].iter_rows(min_row=2, max_col=2, values_only=True):
            if name:
                scripts[name] = text or ""
    return leads, scripts, sheet, lead_sheets(wb)


def matches(lead, status, country):
    if country and lead.get("Country", "").upper() != country.upper():
        return False
    s = lead.get("Status", "")
    if status == "new":
        return s in ("", "New")
    if status == "followup":
        fu = lead.get("Follow-up Date", "")
        return s in ("Callback", "Interested", "No answer", "Voicemail") and (not fu or fu <= date.today().isoformat())
    if status == "all":
        return True
    return s == status


@app.before_request
def require_password():
    """Basic auth when access_password is set - needed once the app is exposed through ngrok."""
    password = load_config().get("access_password")
    if not password:
        if request.host.split(":")[0] in ("localhost", "127.0.0.1"):
            return None
        return Response("Set access_password in config.json before opening the dialer through ngrok.", 403)
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", password):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Sheet Dialer"'})


@app.route("/")
def index():
    cfg = load_config()
    cfg.pop("access_password", None)
    return render_template("dialer.html", config=cfg)


@app.route("/api/leads")
def api_leads():
    leads, scripts, sheet, sheets = read_leads(request.args.get("sheet"))
    status = request.args.get("status", "new")
    country = request.args.get("country", "")
    queue = [l for l in leads if matches(l, status, country)]
    queue.sort(key=lambda l: -int(l.get("Fit Score") or 0))
    stats = {
        "total": len(leads),
        "called": sum(1 for l in leads if l.get("Status") not in ("", "New")),
        "connected": sum(1 for l in leads if l.get("Status") in ("Interested", "Callback", "Not interested", "Demo booked")),
        "interested": sum(1 for l in leads if l.get("Status") in ("Interested", "Demo booked")),
        "booked": sum(1 for l in leads if l.get("Status") == "Demo booked"),
    }
    countries = sorted({l.get("Country", "") for l in leads if l.get("Country")})
    return jsonify(queue=queue, scripts=scripts, stats=stats, countries=countries, sheet=sheet, sheets=sheets)


@app.route("/api/lead/<int:row>", methods=["POST"])
def api_save(row):
    data = request.get_json(force=True)
    wb = load_workbook(LEADS_FILE)
    ws = wb[pick_sheet(wb, data.get("sheet"))]
    headers = {c.value: c.column for c in ws[1] if c.value}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    updates = {
        "Status": data.get("status", ""),
        "Call Outcome": data.get("status", "") + " @ " + stamp,
        "Follow-up Date": data.get("followup", ""),
        "Calendly Link Sent": "Yes" if data.get("calendly_sent") else None,
        "Call Duration": data.get("duration") or None,
    }
    note = (data.get("notes") or "").strip()
    if note and "Notes" in headers:
        old = ws.cell(row=row, column=headers["Notes"]).value or ""
        updates["Notes"] = (str(old) + "\n" if old else "") + "[" + stamp + "] " + note
    if "Attempts" in headers:
        updates["Attempts"] = int(ws.cell(row=row, column=headers["Attempts"]).value or 0) + 1
    for col, value in updates.items():
        if col in headers and value is not None:
            ws.cell(row=row, column=headers[col], value=value)
    try:
        wb.save(LEADS_FILE)
    except PermissionError:
        return jsonify(ok=False, error="leads.xlsx is locked - close it in Excel and try again"), 409
    return jsonify(ok=True)


if __name__ == "__main__":
    if not os.path.exists(LEADS_FILE):
        raise SystemExit("No leads.xlsx found. Run: .venv/bin/python make_sample.py")
    print("Dialer running at http://localhost:5050  (leads: %s)" % LEADS_FILE)
    app.run(port=5050, debug=False)
