"""Sheet Dialer: a spreadsheet-driven power dialer.

Reads leads from leads.xlsx, shows one lead at a time with its script,
dials through the Zoom desktop app (zoomphonecall:// links) and writes the
call outcome back into the same spreadsheet.

Run:  python app.py   then open http://localhost:5050
"""
import hmac
import json
import os
import re
import threading
import time
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

from flask import Flask, Response, jsonify, render_template, request
from openpyxl import load_workbook

BASE = os.path.dirname(os.path.abspath(__file__))
LEADS_FILE = os.environ.get("LEADS_FILE", os.path.join(BASE, "leads.xlsx"))
CONFIG_FILE = os.environ.get("DIALER_CONFIG", os.path.join(BASE, "config.json"))
SHEET = "Leads"
SCRIPT_SHEET = "Scripts"

COUNTRY_CODES = {"US": "1", "USA": "1", "CA": "1", "CANADA": "1", "UK": "44", "GB": "44", "PK": "92", "PAKISTAN": "92"}

OUTCOMES = ("Interested", "Demo booked", "Callback", "No answer", "Voicemail", "Not interested",
            "Wrong number", "Do not call")
# Countries with a single time zone; US/CA leads need a Timezone column (IANA name, e.g. America/Chicago).
COUNTRY_TZ = {"UK": "Europe/London", "GB": "Europe/London", "PK": "Asia/Karachi", "PAKISTAN": "Asia/Karachi"}
CALL_HOURS = (8, 21)  # allowed local calling window: 8am to 9pm (US TCPA; a sensible default elsewhere)
NO_DIAL = ("Do not call", "Wrong number")  # outcomes that permanently stop a number being dialed
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DURATION_RE = re.compile(r"^\d{1,3}:\d{2}$")
MAX_NOTE = 5000

app = Flask(__name__)


DEFAULT_CONFIG = {"calendly_link": "https://calendly.com/your-link/demo", "caller_id": "", "your_name": "",
                  "product_name": "Acme Assist", "zoom_mode": "link", "access_password": ""}
_config_cache = {"mtime": None, "cfg": dict(DEFAULT_CONFIG)}


def load_config():
    """Config from config.json over the defaults, re-read only when the file changes."""
    st = os.stat(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else None
    mtime = (st.st_mtime_ns, st.st_size) if st else None  # size too: catches edits within the same tick
    if mtime != _config_cache["mtime"]:
        cfg = dict(DEFAULT_CONFIG)
        if mtime is not None:
            with open(CONFIG_FILE) as f:
                cfg.update(json.load(f))
        _config_cache.update(mtime=mtime, cfg=cfg)
    return dict(_config_cache["cfg"])


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


def to_int(value, default=0):
    """Parse a spreadsheet cell as an int ("4", 4.0, "4.5" -> 4); anything else gives the default."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def valid_e164(number):
    """+ then 8-15 digits; North American (+1) numbers must have exactly 10 digits after the 1."""
    digits = number[1:]
    if not (number.startswith("+") and digits.isdigit() and 8 <= len(digits) <= 15):
        return False
    return len(digits) == 11 if digits.startswith("1") else True


def lead_timezone(lead):
    """The lead's IANA time zone from its Timezone column or its country, or "" if unknown or invalid."""
    name = (lead.get("Timezone") or COUNTRY_TZ.get(str(lead.get("Country") or "").strip().upper(), "")).strip()
    if not name or ZoneInfo is None:
        return ""
    try:
        ZoneInfo(name)
    except Exception:  # unknown zone name, or no tz database on this machine
        return ""
    return name


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
        if lead["_dial"] and not valid_e164(lead["_dial"]):
            lead["_bad_phone"] = lead.get("Phone", "")
            lead["_dial"] = ""
        lead["_tz"] = lead_timezone(lead)
        if lead.get("Status") in NO_DIAL:
            lead["_blocked"] = lead["Status"]
            lead["_dial"] = ""  # never sent to the browser as dialable, so no view or auto-dial can call it
        if sheet != SHEET and not lead["_dial"] and not lead.get("_blocked") and not lead.get("_bad_phone"):
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


MIN_PASSWORD = 12
MAX_FAILURES = 20          # failed logins allowed per window...
FAILURE_WINDOW = 10 * 60   # ...of this many seconds, before everyone gets 429 until the window passes
_failures = []
_failures_lock = threading.Lock()


def too_many_failures(now):
    with _failures_lock:
        _failures[:] = [t for t in _failures if now - t < FAILURE_WINDOW]
        return len(_failures) >= MAX_FAILURES


def record_failure(now):
    with _failures_lock:
        _failures.append(now)


@app.before_request
def require_password():
    """Basic auth when access_password is set - needed once the app is exposed through a tunnel such as ngrok.

    Behind a tunnel every request comes from 127.0.0.1, so failed logins are throttled globally, not per IP.
    """
    password = load_config().get("access_password")
    if not password:
        if request.host.split(":")[0] in ("localhost", "127.0.0.1"):
            return None
        return Response("Set access_password in config.json before opening the dialer through a tunnel.", 403)
    if len(password) < MIN_PASSWORD:
        return Response("access_password must be at least %d characters." % MIN_PASSWORD, 403)
    now = time.monotonic()
    if too_many_failures(now):
        return Response("Too many failed logins - try again later.", 429)
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", password):
        return None
    if auth:
        record_failure(now)
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Sheet Dialer"'})


@app.route("/")
def index():
    cfg = load_config()
    cfg.pop("access_password", None)
    cfg["call_hours"] = CALL_HOURS
    return render_template("dialer.html", config=cfg)


@app.route("/api/leads")
def api_leads():
    leads, scripts, sheet, sheets = read_leads(request.args.get("sheet"))
    status = request.args.get("status", "new")
    country = request.args.get("country", "")
    queue = [l for l in leads if matches(l, status, country)]
    queue.sort(key=lambda l: -to_int(l.get("Fit Score")))
    stats = {
        "total": len(leads),
        "called": sum(1 for l in leads if l.get("Status") not in ("", "New")),
        "connected": sum(1 for l in leads if l.get("Status") in ("Interested", "Callback", "Not interested", "Demo booked")),
        "interested": sum(1 for l in leads if l.get("Status") in ("Interested", "Demo booked")),
        "booked": sum(1 for l in leads if l.get("Status") == "Demo booked"),
    }
    countries = sorted({l.get("Country", "") for l in leads if l.get("Country")})
    return jsonify(queue=queue, scripts=scripts, stats=stats, countries=countries, sheet=sheet, sheets=sheets)


SAVE_LOCK = threading.Lock()


def save_workbook(wb, path):
    """Write to a temp file next to the target, then swap it in, so a crash mid-save can't corrupt the sheet."""
    folder, name = os.path.split(os.path.abspath(path))
    tmp = os.path.join(folder, "." + name + ".saving.xlsx")
    try:
        wb.save(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fail(status, error):
    return jsonify(ok=False, error=error), status


def validate_save(data):
    """Return an error message for a bad save body, or None if it is fine."""
    if not isinstance(data, dict):
        return "body must be a JSON object"
    if data.get("status") not in OUTCOMES:
        return "status must be one of: " + ", ".join(OUTCOMES)
    followup = data.get("followup") or ""
    if not isinstance(followup, str) or (followup and not DATE_RE.match(followup)):
        return "followup must be empty or YYYY-MM-DD"
    notes = data.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > MAX_NOTE:
        return "notes must be text up to %d characters" % MAX_NOTE
    duration = data.get("duration") or ""
    if not isinstance(duration, str) or (duration and not DURATION_RE.match(duration)):
        return "duration must be empty or m:ss"
    return None


@app.route("/api/lead/<int:row>", methods=["POST"])
def api_save(row):
    # JSON only: a cross-site <form> can send text/plain without a preflight, but not application/json.
    if not request.is_json:
        return fail(415, "Content-Type must be application/json")
    data = request.get_json(silent=True)
    error = validate_save(data)
    if error:
        return fail(400, error)
    # One save at a time: each save rewrites the whole file, so parallel saves would lose updates.
    with SAVE_LOCK:
        wb = load_workbook(LEADS_FILE)
        ws = wb[pick_sheet(wb, data.get("sheet"))]
        if not (2 <= row <= ws.max_row) or not any(c.value is not None for c in ws[row]):
            return fail(404, "no lead in row %d" % row)
        headers = {c.value: c.column for c in ws[1] if c.value}
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        updates = {
            "Status": data["status"],
            "Call Outcome": data["status"] + " @ " + stamp,
            "Follow-up Date": data.get("followup", ""),
            "Calendly Link Sent": "Yes" if data.get("calendly_sent") else None,
            "Call Duration": data.get("duration") or None,
        }
        note = (data.get("notes") or "").strip()
        if note and "Notes" in headers:
            old = ws.cell(row=row, column=headers["Notes"]).value or ""
            updates["Notes"] = (str(old) + "\n" if old else "") + "[" + stamp + "] " + note
        if "Attempts" in headers:
            updates["Attempts"] = to_int(ws.cell(row=row, column=headers["Attempts"]).value) + 1
        for col, value in updates.items():
            if col in headers and value is not None:
                ws.cell(row=row, column=headers[col], value=value)
        try:
            save_workbook(wb, LEADS_FILE)
        except PermissionError:
            return fail(409, "leads.xlsx is locked - close it in Excel and try again")
        return jsonify(ok=True)


if __name__ == "__main__":
    if not os.path.exists(LEADS_FILE):
        raise SystemExit("No leads.xlsx found. Run: python make_sample.py")
    password = load_config().get("access_password")
    if password and len(password) < MIN_PASSWORD:
        raise SystemExit("access_password in config.json must be at least %d characters." % MIN_PASSWORD)
    from waitress import serve  # production WSGI server; listens on localhost only (a tunnel forwards to it)

    print("Dialer running at http://localhost:5050  (leads: %s)" % LEADS_FILE)
    serve(app, host="127.0.0.1", port=5050, threads=4)
