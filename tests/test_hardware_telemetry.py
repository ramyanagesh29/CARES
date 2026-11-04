"""ESP32 BPM telemetry: POST /api/hardware/telemetry.

Telemetry is display-only. These tests prove it is stored separately, requires
the device token, validates input, reaches the dashboard and the EventBus, and
never touches the risk engine, incidents, guardian actions or SMS.
"""

from __future__ import annotations

import queue
import sqlite3
import time
from pathlib import Path

import pytest

import backend.services as services_module
import guardian.sms as sms
from backend import CARESBackend
from backend.api import CARESAPI
from backend.db import Database

TOKEN = "telemetry-token-for-tests"
HEADERS = {"X-Device-Token": TOKEN}
ROOT = Path(__file__).resolve().parents[1]

ENGINE_TABLES = (
    "engine_events",
    "incidents",
    "guardian_action_events",
    "location_events",
    "monitoring_sessions",
    "baseline_daily_records",
    "baseline_adaptation_events",
)


@pytest.fixture(autouse=True)
def device_env(monkeypatch):
    monkeypatch.setenv("CARES_DEVICE_TOKEN", TOKEN)
    for name in ("CARES_HARDWARE_USER_ID", "CARES_SMS_PROVIDER", "TEXTBEE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    backend = CARESBackend(db_path=str(tmp_path / "telemetry.sqlite3"))
    user = backend.register("Test User", "a@example.com", "password-one")
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", str(user["id"]))
    return backend, user, CARESAPI(backend)


def post(api, body, headers=HEADERS):
    return api.handle("POST", "/api/hardware/telemetry", body, headers)


def count(backend, table):
    return backend.database.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


# ---------------------------------------------------------------- database


def test_table_exists_with_required_columns(tmp_path):
    db = Database(str(tmp_path / "t.sqlite3"))
    columns = {r["name"] for r in db.fetch_all("PRAGMA table_info(hardware_telemetry)")}
    assert columns == {"id", "user_id", "device_id", "heart_rate", "latitude", "longitude", "timestamp"}


def test_existing_database_gains_table_without_touching_other_data(tmp_path):
    path = tmp_path / "existing.sqlite3"
    backend = CARESBackend(db_path=str(path))
    user = backend.register("Test User", "a@example.com", "password-one")
    backend.database.execute("DROP TABLE hardware_telemetry")  # simulate a pre-telemetry database
    backend.database.close()

    reopened = CARESBackend(db_path=str(path))
    assert reopened.database.fetch_one("SELECT COUNT(*) AS n FROM hardware_telemetry")["n"] == 0
    assert reopened.database.fetch_one("SELECT email FROM users WHERE id = ?", (user["id"],))["email"] == "a@example.com"


# --------------------------------------------------------- accepted readings


def test_valid_bpm_only_reading_is_accepted_and_stored(setup):
    backend, user, api = setup

    response = post(api, {"heart_rate": 76.4, "device_id": "CARES_ESP32_01"})

    assert response.status == 201
    assert response.body["ok"] is True
    assert response.body["risk_engine_invoked"] is False
    record = response.body["telemetry"]
    assert record["heart_rate"] == pytest.approx(76.4)
    assert record["device_id"] == "CARES_ESP32_01"
    assert record["latitude"] is None and record["longitude"] is None
    assert record["user_id"] == user["id"]
    assert count(backend, "hardware_telemetry") == 1


def test_optional_gps_is_stored_with_reading(setup):
    backend, user, api = setup

    response = post(api, {"heart_rate": 80, "latitude": 12.3051, "longitude": 76.6551})

    assert response.status == 201
    record = response.body["telemetry"]
    assert record["latitude"] == pytest.approx(12.3051)
    assert record["longitude"] == pytest.approx(76.6551)
    assert record["device_id"] == "CARES_ESP32_01"  # default when omitted


def test_integer_bpm_and_boundaries_are_accepted(setup):
    _, _, api = setup
    for bpm in (30, 72, 220):
        assert post(api, {"heart_rate": bpm}).status == 201


# --------------------------------------------------------------- auth


def test_missing_or_wrong_token_returns_401_and_stores_nothing(setup):
    backend, _, api = setup
    body = {"heart_rate": 76}

    assert post(api, body, headers={}).status == 401
    assert post(api, body, headers={"X-Device-Token": "wrong"}).status == 401
    assert post(api, body, headers={"X-Device-Token": ""}).status == 401
    assert post(api, body, headers={"X-Device-Token": "tökén-ñ"}).status == 401
    assert count(backend, "hardware_telemetry") == 0


def test_header_name_is_case_insensitive(setup):
    _, _, api = setup
    assert post(api, {"heart_rate": 70}, headers={"x-device-token": TOKEN}).status == 201


def test_unconfigured_server_returns_503(setup, monkeypatch):
    _, _, api = setup
    monkeypatch.delenv("CARES_DEVICE_TOKEN")
    assert post(api, {"heart_rate": 70}).status == 503


def test_missing_hardware_user_returns_503(setup, monkeypatch):
    _, _, api = setup
    monkeypatch.delenv("CARES_HARDWARE_USER_ID")
    assert post(api, {"heart_rate": 70}).status == 503


def test_unknown_hardware_user_is_rejected_cleanly(setup, monkeypatch):
    backend, _, api = setup
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", "9999")
    response = post(api, {"heart_rate": 70})
    assert response.status == 503
    assert "CARES_HARDWARE_USER_ID=9999" in response.body["error"]
    assert count(backend, "hardware_telemetry") == 0


def test_user_session_alone_cannot_post_telemetry(setup):
    backend, _, api = setup
    _, session_token = backend.login("a@example.com", "password-one")
    response = post(api, {"heart_rate": 70}, headers={"Authorization": f"Bearer {session_token}"})
    assert response.status == 401


# ----------------------------------------------------------- validation


@pytest.mark.parametrize(
    "bad",
    [None, "abc", "76", True, False, [], {}, float("nan"), float("inf"), -76, 0, 29.9, 220.1, 500],
)
def test_invalid_bpm_values_are_rejected(setup, bad):
    backend, _, api = setup
    response = post(api, {"heart_rate": bad})
    assert response.status == 422
    assert count(backend, "hardware_telemetry") == 0


def test_missing_bpm_is_rejected(setup):
    _, _, api = setup
    assert post(api, {"device_id": "CARES_ESP32_01"}).status == 422


@pytest.mark.parametrize(
    "gps",
    [
        {"latitude": 12.3},                                  # longitude missing
        {"longitude": 76.6},                                 # latitude missing
        {"latitude": 91, "longitude": 76.6},
        {"latitude": -91, "longitude": 76.6},
        {"latitude": 12.3, "longitude": 181},
        {"latitude": "12.3", "longitude": 76.6},
        {"latitude": float("nan"), "longitude": 76.6},
        {"latitude": True, "longitude": 76.6},
        {"latitude": 0.0, "longitude": 0.0},                 # "no fix" value from GPS modules
    ],
)
def test_invalid_gps_is_rejected(setup, gps):
    backend, _, api = setup
    assert post(api, {"heart_rate": 70, **gps}).status == 422
    assert count(backend, "hardware_telemetry") == 0


@pytest.mark.parametrize("device_id", ["", "   ", 5, None, "x" * 81])
def test_invalid_device_id_is_rejected(setup, device_id):
    _, _, api = setup
    assert post(api, {"heart_rate": 70, "device_id": device_id}).status == 422


# ------------------------------------- telemetry never reaches risk/alerts


def test_telemetry_does_not_touch_engine_incidents_actions_or_sms(setup, monkeypatch):
    backend, user, api = setup
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    def forbidden(*args, **kwargs):
        raise AssertionError("Telemetry must not reach the risk engine, incidents or SMS")

    monkeypatch.setattr(backend, "process_sample", forbidden)
    monkeypatch.setattr(backend, "persist_engine_output", forbidden)
    monkeypatch.setattr(sms, "send_guardian_sms", forbidden)

    before = {table: count(backend, table) for table in ENGINE_TABLES}

    for bpm in (72, 130, 210, 218):  # includes values a risk engine would call HIGH
        assert post(api, {"heart_rate": bpm, "latitude": 12.3, "longitude": 76.6}).status == 201

    after = {table: count(backend, table) for table in ENGINE_TABLES}
    for table in ENGINE_TABLES:
        expected = before[table] + (
            4 if table == "location_events" else
            1 if table == "monitoring_sessions" else
            0
        )
        assert after[table] == expected
    assert backend.list_actions(user["id"]) == []
    assert backend.list_incidents(user["id"]) == []
    assert count(backend, "hardware_telemetry") == 4


# ---------------------------------------------------------------- dashboard


def dashboard(backend, api):
    _, token = backend.login("a@example.com", "password-one")
    response = api.handle("GET", "/api/dashboard/current", None, {"Authorization": f"Bearer {token}"})
    assert response.status == 200
    return response.body


def test_dashboard_reports_no_telemetry_before_first_reading(setup):
    backend, _, api = setup
    assert dashboard(backend, api)["hardware_telemetry"] is None


def test_dashboard_returns_latest_telemetry_separate_from_engine(setup):
    backend, _, api = setup
    post(api, {"heart_rate": 70})
    post(api, {"heart_rate": 88.5, "device_id": "CARES_ESP32_01"})

    body = dashboard(backend, api)

    assert body["hardware_telemetry"]["heart_rate"] == pytest.approx(88.5)
    assert body["hardware_telemetry"]["age_seconds"] < 30
    assert body["engine_event"] is None          # engine output untouched
    assert body["actions"] == []
    assert "heart_rate" not in (body["engine_event"] or {})


def test_dashboard_never_shows_another_users_telemetry(setup):
    backend, user, api = setup
    other = backend.register("Other", "b@example.com", "password-two")
    backend.record_hardware_telemetry(other["id"], "OTHER_DEVICE", 99.0)

    assert dashboard(backend, api)["hardware_telemetry"] is None
    assert backend.latest_hardware_telemetry(other["id"])["heart_rate"] == 99.0


def test_existing_dashboard_fields_are_unchanged(setup):
    backend, _, api = setup
    body = dashboard(backend, api)
    for field in ("engine_event", "session", "location", "actions", "demo"):
        assert field in body


# ---------------------------------------------------------------- EventBus


def test_hardware_telemetry_event_is_published_to_the_user(setup):
    backend, user, api = setup
    subscriber = backend.events.subscribe(user["id"])
    other = backend.register("Other", "b@example.com", "password-two")
    other_subscriber = backend.events.subscribe(other["id"])

    post(api, {"heart_rate": 77.7})

    event = subscriber.get(timeout=1)
    assert event["type"] == "hardware_telemetry"
    assert event["data"]["heart_rate"] == pytest.approx(77.7)
    assert subscriber.empty()
    with pytest.raises(queue.Empty):
        other_subscriber.get(timeout=0.1)


def test_rejected_reading_publishes_nothing(setup):
    backend, user, api = setup
    subscriber = backend.events.subscribe(user["id"])
    post(api, {"heart_rate": 5})
    post(api, {"heart_rate": 70}, headers={})
    assert subscriber.empty()


# ------------------------------------------------- service-level behaviour


def test_latest_returns_most_recent_reading(setup):
    backend, user, _ = setup
    backend.record_hardware_telemetry(user["id"], "CARES_ESP32_01", 60)
    backend.record_hardware_telemetry(user["id"], "CARES_ESP32_01", 90)
    assert backend.latest_hardware_telemetry(user["id"])["heart_rate"] == 90


def test_retention_keeps_only_newest_rows(setup, monkeypatch):
    backend, user, _ = setup
    monkeypatch.setattr(services_module, "TELEMETRY_RETENTION_ROWS", 5)
    for bpm in range(60, 80):
        backend.record_hardware_telemetry(user["id"], "CARES_ESP32_01", bpm)
    rows = backend.database.fetch_all(
        "SELECT heart_rate FROM hardware_telemetry WHERE user_id = ? ORDER BY id", (user["id"],)
    )
    assert [r["heart_rate"] for r in rows] == [75, 76, 77, 78, 79]


def test_device_timestamp_is_used_only_when_plausible(setup):
    _, _, api = setup
    good = post(api, {"heart_rate": 70, "timestamp": time.time() - 2}).body
    assert good["timestamp_source"] == "DEVICE"
    boot_relative = post(api, {"heart_rate": 70, "timestamp": 12345}).body
    assert boot_relative["timestamp_source"] == "SERVER_RECEIVED"
    assert abs(boot_relative["telemetry"]["timestamp"] - time.time()) < 30


# --------------------------------------------- existing ingest unchanged


def test_existing_ingest_route_behaviour_is_unchanged(setup):
    backend, user, api = setup
    full = api.handle(
        "POST", "/api/hardware/ingest",
        {"heart_rate": 72, "spo2": 98, "latitude": 12.2958, "longitude": 76.6394},
        HEADERS,
    )
    assert full.status == 201
    assert full.body["physiological_sample_processed"] is True
    assert count(backend, "engine_events") == 1
    assert count(backend, "hardware_telemetry") == 0   # ingest does not write telemetry

    bpm_only = api.handle("POST", "/api/hardware/ingest", {"heart_rate": 72}, HEADERS)
    assert bpm_only.status == 422                      # documented: use /telemetry for BPM-only


# ---------------------------------------------------------------- frontend


def test_dashboard_markup_has_separate_live_bpm_card_outside_engine_content():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "ESP32 LIVE BPM" in html
    card = html.index('id="esp32-bpm"')
    # Must not live inside #dashboard-content, which is hidden until the first engine event.
    assert card < html.index('id="dashboard-content"')
    assert 'data-field="current_value"' in html          # engine BPM card still present


def test_frontend_listens_for_telemetry_event_and_reads_dashboard_field():
    js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert '"hardware_telemetry"' in js
    assert "hardware_telemetry" in js and "renderTelemetry" in js
    assert 'new EventSource("/api/events/stream")' in js
    assert "No ESP32 reading received yet" in js
    assert "esp32-bpm" not in js.split("function setFields")[1].split("function renderCurrent")[0]


# ----------------------------------------- hardware user configuration


def test_valid_hardware_user_records_telemetry_for_that_user(setup):
    backend, user, api = setup
    response = post(api, {"heart_rate": 76.4, "device_id": "CARES_ESP32_01"})
    assert response.status == 201
    assert response.body["telemetry"]["user_id"] == user["id"]
    assert backend.latest_hardware_telemetry(user["id"])["heart_rate"] == pytest.approx(76.4)


@pytest.mark.parametrize("bad_id", ["", "abc", "0", "-1", "1.5", " "])
def test_missing_or_malformed_hardware_user_id_returns_503(setup, monkeypatch, bad_id):
    backend, _, api = setup
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", bad_id)
    response = post(api, {"heart_rate": 70})
    assert response.status == 503
    assert "CARES_HARDWARE_USER_ID" in response.body["error"]
    assert count(backend, "hardware_telemetry") == 0


def test_nonexistent_hardware_user_returns_actionable_503_not_user_not_found(setup, monkeypatch):
    backend, user, api = setup
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", str(user["id"] + 5))
    response = post(api, {"heart_rate": 70})
    assert response.status == 503
    assert response.body["ok"] is False
    assert response.body["error"] != "User not found."
    assert "/api/auth/me" in response.body["error"]
    assert count(backend, "hardware_telemetry") == 0


def test_invalid_token_is_rejected_before_user_check_and_leaks_nothing(setup, monkeypatch):
    _, _, api = setup
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", "9999")   # misconfigured on purpose
    response = post(api, {"heart_rate": 70}, headers={"X-Device-Token": "wrong"})
    assert response.status == 401
    assert "CARES_HARDWARE_USER_ID" not in str(response.body)


def test_misconfigured_hardware_user_never_reaches_engine_or_sms(setup, monkeypatch):
    backend, user, api = setup
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", "9999")

    def forbidden(*args, **kwargs):
        raise AssertionError("must not run")

    monkeypatch.setattr(backend, "process_sample", forbidden)
    monkeypatch.setattr(sms, "send_guardian_sms", forbidden)
    assert post(api, {"heart_rate": 70}).status == 503
    assert backend.list_actions(user["id"]) == []


def test_recording_telemetry_preserves_existing_accounts(setup):
    backend, user, api = setup
    backend.register("Second", "b@example.com", "password-two")
    before = backend.database.fetch_all("SELECT id, email, password_hash FROM users ORDER BY id")

    assert post(api, {"heart_rate": 70}).status == 201

    after = backend.database.fetch_all("SELECT id, email, password_hash FROM users ORDER BY id")
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    backend.login("a@example.com", "password-one")      # existing logins still work


def test_fresh_database_after_redeploy_scenario(tmp_path, monkeypatch):
    """Reproduces the Render symptom: a stale CARES_HARDWARE_USER_ID against a
    brand-new database, then the fix: pointing it at the real user's id."""

    old = CARESBackend(db_path=str(tmp_path / "old.sqlite3"))
    old_ids = [old.register(f"U{i}", f"u{i}@x.com", "password-one")["id"] for i in range(1, 4)]
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", str(old_ids[-1]))      # e.g. 3

    fresh = CARESBackend(db_path=str(tmp_path / "fresh.sqlite3"))       # ephemeral disk wiped
    new_user = fresh.register("New", "new@x.com", "password-one")
    assert new_user["id"] == 1                                          # ids restart at 1
    api = CARESAPI(fresh)

    with pytest.raises(Exception):
        fresh.login("u3@x.com", "password-one")                         # old account is gone

    stale = post(api, {"heart_rate": 76.4})
    assert stale.status == 503 and "CARES_HARDWARE_USER_ID=3" in stale.body["error"]

    monkeypatch.setenv("CARES_HARDWARE_USER_ID", str(new_user["id"]))   # the one-time config fix
    assert post(api, {"heart_rate": 76.4}).status == 201
    assert fresh.latest_hardware_telemetry(new_user["id"])["heart_rate"] == pytest.approx(76.4)


def test_user_id_is_an_integer_matching_the_env_format(setup):
    backend, user, _ = setup
    assert isinstance(user["id"], int)
    _, token = backend.login("a@example.com", "password-one")
    me = CARESAPI(backend).handle("GET", "/api/auth/me", None, {"Authorization": f"Bearer {token}"})
    assert me.body["user"]["id"] == user["id"]                          # what to copy into Render
