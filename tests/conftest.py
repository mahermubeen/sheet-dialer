import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as dialer  # noqa: E402
import make_sample  # noqa: E402


@pytest.fixture
def leads_file(tmp_path, monkeypatch):
    """A fresh sample workbook (3 fictional leads) and an empty config, both in tmp_path."""
    path = tmp_path / "leads.xlsx"
    monkeypatch.setattr(make_sample, "OUT", str(path))
    make_sample.main()
    monkeypatch.setattr(dialer, "LEADS_FILE", str(path))
    monkeypatch.setattr(dialer, "CONFIG_FILE", str(tmp_path / "config.json"))
    dialer._failures.clear()
    return path


@pytest.fixture
def client(leads_file):
    return dialer.app.test_client()


@pytest.fixture
def set_config(tmp_path):
    def write(**values):
        (tmp_path / "config.json").write_text(json.dumps(values))
    return write


def basic(password, host="dialer.example.ngrok-free.app"):
    token = base64.b64encode(("me:" + password).encode()).decode()
    return {"Authorization": "Basic " + token, "Host": host}
