from datetime import date, timedelta

import pytest

import app as dialer


@pytest.mark.parametrize("raw,country,expected", [
    ("+1 555 010 0001", "US", "+15550100001"),
    ("(416) 555-0101", "CA", "+14165550101"),
    ("07700 900001", "UK", "+447700900001"),
    ("0300 0000000", "PK", "+923000000000"),
    ("0044 20 7946 0000", "", "+442079460000"),
    ("92 300 0000000", "PK", "+923000000000"),
    ("", "US", ""),
    (None, "US", ""),
])
def test_to_e164(raw, country, expected):
    assert dialer.to_e164(raw, country) == expected


@pytest.mark.parametrize("number,ok", [
    ("+15550100001", True),
    ("+5550101", False),      # 7-digit US number normalised without an area code
    ("+1555010000", False),   # NANP number one digit short
    ("+4412", False),
    ("+447700900001", True),
    ("+923000000000", True),
])
def test_valid_e164(number, ok):
    assert dialer.valid_e164(number) is ok


@pytest.mark.parametrize("value,expected", [("4", 4), (4.0, 4), ("4.5", 4), ("N/A", 0), (None, 0), ("", 0)])
def test_to_int(value, expected):
    assert dialer.to_int(value) == expected


def test_matches_filters_by_status_and_country():
    today = date.today().isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()
    assert dialer.matches({"Status": "", "Country": "US"}, "new", "")
    assert not dialer.matches({"Status": "Callback"}, "new", "")
    assert dialer.matches({"Status": "Callback", "Follow-up Date": today}, "followup", "")
    assert not dialer.matches({"Status": "Callback", "Follow-up Date": later}, "followup", "")
    assert not dialer.matches({"Status": "", "Country": "UK"}, "new", "US")


@pytest.mark.parametrize("lead,tz", [
    ({"Timezone": "America/Chicago", "Country": "US"}, "America/Chicago"),
    ({"Country": "UK"}, "Europe/London"),
    ({"Country": "PK"}, "Asia/Karachi"),
    ({"Country": "US"}, ""),                      # US spans several zones: needs the column
    ({"Timezone": "Mars/Olympus", "Country": "US"}, ""),
])
def test_lead_timezone(lead, tz):
    assert dialer.lead_timezone(lead) == tz
