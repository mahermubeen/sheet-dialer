import os
import threading

from openpyxl import load_workbook

import app as dialer
from conftest import basic

GOOD = {"status": "Callback", "followup": "2030-01-02", "notes": "asked to call back", "duration": "1:05"}


def cell(path, row, column, sheet="Leads"):
    ws = load_workbook(path)[sheet]
    headers = {c.value: c.column for c in ws[1]}
    return ws.cell(row=row, column=headers[column]).value


def set_cell(path, row, column, value):
    wb = load_workbook(path)
    ws = wb["Leads"]
    headers = {c.value: c.column for c in ws[1]}
    ws.cell(row=row, column=headers[column], value=value)
    wb.save(path)


# ---- reading ----

def test_leads_endpoint_returns_sample_queue(client):
    data = client.get("/api/leads").get_json()
    assert len(data["queue"]) == 3
    assert data["sheets"] == ["Leads"]
    assert set(data["scripts"]) >= {"US", "UK", "PK"}


def test_non_numeric_fit_score_does_not_break_listing(client, leads_file):
    set_cell(leads_file, 2, "Fit Score", "N/A")
    assert client.get("/api/leads?status=all").status_code == 200


def test_invalid_phone_is_not_dialable(client, leads_file):
    set_cell(leads_file, 2, "Phone", "555 0101")
    lead = next(x for x in client.get("/api/leads?status=all").get_json()["queue"] if x["_row"] == 2)
    assert lead["_dial"] == "" and lead["_bad_phone"] == "555 0101"


def test_index_never_exposes_password(client, set_config):
    set_config(access_password="correct-horse-battery")
    page = client.get("/", headers=basic("correct-horse-battery", host="localhost")).data
    assert b"correct-horse-battery" not in page


# ---- saving ----

def test_save_writes_outcome_and_counts_attempts(client, leads_file):
    assert client.post("/api/lead/2", json=GOOD).get_json() == {"ok": True}
    client.post("/api/lead/2", json={"status": "No answer"})
    assert cell(leads_file, 2, "Status") == "No answer"
    assert cell(leads_file, 2, "Attempts") == 2
    assert "asked to call back" in cell(leads_file, 2, "Notes")
    assert cell(leads_file, 2, "Call Duration") == "1:05"


def test_save_rejects_non_json_body(client, leads_file):
    """A cross-site form can post text/plain without a CORS preflight; that must not write."""
    r = client.post("/api/lead/2", data='{"status":"Do not call"}', content_type="text/plain")
    assert r.status_code == 415
    assert cell(leads_file, 2, "Status") in (None, "")


def test_save_rejects_header_and_missing_rows(client, leads_file):
    assert client.post("/api/lead/1", json=GOOD).status_code == 404
    assert client.post("/api/lead/999", json=GOOD).status_code == 404
    assert load_workbook(leads_file)["Leads"].max_row == 4  # no phantom row created


def test_save_rejects_bad_bodies(client):
    assert client.post("/api/lead/2", data="null", content_type="application/json").status_code == 400
    for body in ([], {"status": "PWNED"}, {**GOOD, "followup": "tomorrow"},
                 {**GOOD, "duration": "long"}, {**GOOD, "notes": "x" * (dialer.MAX_NOTE + 1)}):
        r = client.post("/api/lead/2", json=body)
        assert r.status_code == 400, body
        assert r.get_json()["ok"] is False


def test_save_leaves_no_temp_file(client, leads_file):
    client.post("/api/lead/2", json=GOOD)
    assert os.listdir(leads_file.parent) == ["leads.xlsx"]


def test_parallel_saves_do_not_lose_updates(client, leads_file):
    def save(i):
        dialer.app.test_client().post("/api/lead/2", json={"status": "No answer", "notes": "note %d" % i})
    threads = [threading.Thread(target=save, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    notes = cell(leads_file, 2, "Notes")
    assert all("note %d" % i in notes for i in range(10))
    assert cell(leads_file, 2, "Attempts") == 10


# ---- do not call ----

def test_do_not_call_lead_is_never_dialable(client):
    client.post("/api/lead/2", json={"status": "Do not call"})
    for status in ("all", "Do not call", "new", "followup"):
        for lead in client.get("/api/leads?status=" + status).get_json()["queue"]:
            if lead["_row"] == 2:
                assert lead["_dial"] == "" and lead["_blocked"] == "Do not call"


def test_wrong_number_is_never_dialable(client):
    client.post("/api/lead/3", json={"status": "Wrong number"})
    lead = next(x for x in client.get("/api/leads?status=all").get_json()["queue"] if x["_row"] == 3)
    assert lead["_dial"] == ""


# ---- access control ----

def test_localhost_needs_no_password(client):
    assert client.get("/", headers={"Host": "localhost:5050"}).status_code == 200


def test_tunnel_without_password_is_refused(client):
    assert client.get("/", headers={"Host": "dialer.example.ngrok-free.app"}).status_code == 403


def test_short_password_is_refused(client, set_config):
    set_config(access_password="short")
    assert client.get("/", headers=basic("short")).status_code == 403


def test_password_required_and_checked(client, set_config):
    set_config(access_password="correct-horse-battery")
    assert client.get("/", headers={"Host": "dialer.example.ngrok-free.app"}).status_code == 401
    assert client.get("/", headers=basic("wrong-password-here")).status_code == 401
    assert client.get("/", headers=basic("correct-horse-battery")).status_code == 200


def test_failed_logins_are_throttled(client, set_config):
    set_config(access_password="correct-horse-battery")
    for _ in range(dialer.MAX_FAILURES):
        assert client.get("/", headers=basic("guess")).status_code == 401
    assert client.get("/", headers=basic("guess")).status_code == 429
    assert client.get("/", headers=basic("correct-horse-battery")).status_code == 429
