"""Regression tests: guardian notification metadata, real-SMS demo opt-in,
GPS freshness/ownership safety, and GPS-only hardware ingest.

No test here performs real network I/O. TextBee HTTP calls are replaced with
fakes, and any unexpected network call fails the test.
"""

from __future__ import annotations

import json
import time

import pytest

import guardian.sms as sms
from backend import CARESBackend
from backend.api import CARESAPI
from backend.demo import DemoController
from backend.services import ConflictError
from engine.models import EngineOutput, RiskLevel

HIGH_ACTIONS = [
    "EMERGENCY_ALERT",
    "GUARDIAN_NOTIFICATION",
    "LOCATION_SHARE",
    "GUARDIAN_COMMUNICATION",
    "INCIDENT_LOG",
]


# --------------------------------------------------------------- helpers


def make_backend(tmp_path):
    return CARESBackend(db_path=str(tmp_path / "cares_safety.sqlite3"))


def high_output(timestamp=310.0):
    return EngineOutput(
        timestamp=timestamp,
        risk_level=RiskLevel.HIGH,
        risk_score=94.5,
        confidence=0.98,
        trend=1.2,
        baseline=70.0,
        current_value=110.0,
        deviation=40.0,
        pct_deviation=57.1,
        persistence=4.0,
        recovery_state="NO_RECOVERY",
        reason_codes=["TEST"],
        human_readable_explanation="Test HIGH",
        recommended_action=HIGH_ACTIONS,
    )


def notification_action(backend, user_id):
    return next(
        a for a in backend.list_actions(user_id)
        if a["action_type"] == "GUARDIAN_NOTIFICATION"
    )


def capture_sms(monkeypatch, result=None):
    calls = []

    def fake_sms(**kwargs):
        calls.append(kwargs)
        return result or {
            "success": True,
            "status": "MOCK_SENT",
            "provider": "MOCK",
            "real_sms_sent": False,
        }

    monkeypatch.setattr(sms, "send_guardian_sms", fake_sms)
    return calls


def user_with_guardian(backend, email="a@example.com"):
    user = backend.register("Test User", email, "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")
    return user


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "CARES_DEMO_REAL_SMS",
        "CARES_SMS_PROVIDER",
        "TEXTBEE_API_KEY",
        "TEXTBEE_DEVICE_ID",
        "CARES_ALLOW_SMS_IN_TESTS",
        "CARES_LOCATION_MAX_AGE_SECONDS",
        "CARES_DEVICE_TOKEN",
        "CARES_HARDWARE_USER_ID",
    ):
        monkeypatch.delenv(name, raising=False)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def no_network(*args, **kwargs):
    raise AssertionError("Unexpected network call in an automated test")


def textbee_env(monkeypatch, demo_flag="true"):
    monkeypatch.setenv("CARES_SMS_PROVIDER", "textbee")
    monkeypatch.setenv("TEXTBEE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TEXTBEE_DEVICE_ID", "device-1")
    monkeypatch.setenv("CARES_ALLOW_SMS_IN_TESTS", "true")
    if demo_flag is not None:
        monkeypatch.setenv("CARES_DEMO_REAL_SMS", demo_flag)


ACCEPTED = {"data": {"success": True, "smsBatchId": "batch-1", "recipientCount": 1}}


# ------------------------------------- _send_guardian_notification fixes


def test_no_guardians_marks_unavailable_without_real_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "a@example.com", "password-one")
    calls = capture_sms(monkeypatch)

    backend.persist_engine_output(user["id"], high_output())

    action = notification_action(backend, user["id"])
    assert calls == []
    assert action["status"] == "UNAVAILABLE"
    assert action["metadata"]["real_sms_sent"] is False
    assert action["metadata"]["delivery_confirmed"] is False


def test_real_sms_sent_reflects_provider_acceptance_not_delivery(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    capture_sms(monkeypatch, {
        "success": True,
        "status": "PENDING",
        "provider": "TEXTBEE",
        "provider_status": "ACCEPTED",
        "sms_batch_id": "batch-1",
        "real_sms_sent": True,
    })

    backend.persist_engine_output(user["id"], high_output())

    action = notification_action(backend, user["id"])
    assert action["status"] == "PENDING"
    assert action["metadata"]["real_sms_sent"] is True
    assert action["metadata"]["provider_accepted"] is True
    assert action["metadata"]["delivery_confirmed"] is False


def test_mock_notification_is_never_reported_as_real(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    capture_sms(monkeypatch)

    backend.persist_engine_output(user["id"], high_output())

    action = notification_action(backend, user["id"])
    assert action["status"] == "MOCK_SENT"
    assert action["metadata"]["real_sms_sent"] is False
    assert action["metadata"]["delivery_mode"] == "MOCK"


def test_failed_provider_is_not_reported_as_real_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    capture_sms(monkeypatch, {
        "success": False,
        "status": "FAILED",
        "provider": "TEXTBEE",
        "reason": "boom",
        "real_sms_sent": False,
    })

    backend.persist_engine_output(user["id"], high_output())

    action = notification_action(backend, user["id"])
    assert action["status"] == "FAILED"
    assert action["metadata"]["real_sms_sent"] is False


def test_demo_alert_uses_wall_clock_time_not_sample_index(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)

    backend.persist_engine_output(
        user["id"], high_output(timestamp=310.0), source="DEMO_SYNTHETIC"
    )

    assert calls[0]["is_demo"] is True
    assert abs(calls[0]["timestamp"] - time.time()) < 30


# --------------------------------------------- real SMS demo safeguards


def test_demo_real_sms_defaults_to_disabled():
    status = sms.demo_real_sms_status()
    assert status["enabled"] is False
    assert "CARES_DEMO_REAL_SMS" in status["reason"]


@pytest.mark.parametrize("flag", ["false", "1", "yes", "TRUE ", ""])
def test_demo_real_sms_requires_explicit_true(monkeypatch, flag):
    textbee_env(monkeypatch, demo_flag=flag)
    expected = flag.strip().lower() == "true"
    assert sms.demo_real_sms_status()["enabled"] is expected


def test_demo_real_sms_disabled_during_pytest_without_opt_in(monkeypatch):
    textbee_env(monkeypatch)
    monkeypatch.delenv("CARES_ALLOW_SMS_IN_TESTS")
    assert sms.demo_real_sms_status()["enabled"] is False


def test_demo_alert_is_mock_without_per_demo_request(monkeypatch):
    textbee_env(monkeypatch)
    monkeypatch.setattr(sms, "urlopen", no_network)

    result = sms.send_guardian_sms(
        "+919876543210", 110.0, "Test User", is_demo=True, allow_real_demo=False
    )

    assert result["status"] == "MOCK_SENT"
    assert result["real_sms_sent"] is False


def test_demo_alert_is_mock_when_server_flag_is_off(monkeypatch):
    textbee_env(monkeypatch, demo_flag=None)
    monkeypatch.setattr(sms, "urlopen", no_network)

    result = sms.send_guardian_sms(
        "+919876543210", 110.0, "Test User", is_demo=True, allow_real_demo=True
    )

    assert result["status"] == "MOCK_SENT"
    assert result["real_sms_sent"] is False


def test_demo_alert_is_mock_under_pytest_even_if_fully_configured(monkeypatch):
    textbee_env(monkeypatch)
    monkeypatch.delenv("CARES_ALLOW_SMS_IN_TESTS")
    monkeypatch.setattr(sms, "urlopen", no_network)

    result = sms.send_guardian_sms(
        "+919876543210", 110.0, "Test User", is_demo=True, allow_real_demo=True
    )

    assert result["status"] == "MOCK_SENT"


def test_explicit_real_demo_sends_labelled_sms_and_is_not_delivery(monkeypatch):
    textbee_env(monkeypatch)
    sent = []

    def fake_urlopen(request, timeout=0):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse(ACCEPTED)

    monkeypatch.setattr(sms, "urlopen", fake_urlopen)

    result = sms.send_guardian_sms(
        "+919876543210", 110.0, "Test User", is_demo=True, allow_real_demo=True
    )

    assert len(sent) == 1
    assert result["status"] == "PENDING"
    assert result["provider_status"] == "ACCEPTED"
    assert result["real_sms_sent"] is True
    assert result["delivery_confirmed"] is False
    text = sent[0]["message"]
    assert "SIMULATED" in text
    assert "NOT A REAL MEDICAL EMERGENCY" in text
    assert "CARES EMERGENCY ALERT" not in text


def test_non_demo_alert_still_uses_textbee_when_configured(monkeypatch):
    textbee_env(monkeypatch, demo_flag=None)
    sent = []

    def fake_urlopen(request, timeout=0):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse(ACCEPTED)

    monkeypatch.setattr(sms, "urlopen", fake_urlopen)

    result = sms.send_guardian_sms("+919876543210", 110.0, "Test User")

    assert len(sent) == 1
    assert result["real_sms_sent"] is True
    assert "CARES EMERGENCY ALERT" in sent[0]["message"]


def test_demo_start_rejects_real_sms_when_not_enabled(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "a@example.com", "password-one")
    controller = DemoController(backend, interval_seconds=0.001)

    with pytest.raises(ConflictError):
        controller.start(user["id"], "ELEVATED", real_sms=True)

    assert backend.list_monitoring_sessions(user["id"]) == []


def test_demo_start_defaults_to_mock_and_records_choice(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "a@example.com", "password-one")
    controller = DemoController(backend, interval_seconds=0.001)

    status = controller.start(user["id"], "ELEVATED")
    try:
        assert status["session"]["metadata"]["real_sms_requested"] is False
    finally:
        controller.stop(user["id"])


def test_demo_start_accepts_real_sms_only_when_enabled(tmp_path, monkeypatch):
    textbee_env(monkeypatch)
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "a@example.com", "password-one")
    controller = DemoController(backend, interval_seconds=0.001)

    status = controller.start(user["id"], "ELEVATED", real_sms=True)
    try:
        assert status["session"]["metadata"]["real_sms_requested"] is True
    finally:
        controller.stop(user["id"])


def test_api_rejects_non_boolean_send_real_sms(tmp_path):
    backend = make_backend(tmp_path)
    backend.register("Test User", "a@example.com", "password-one")
    _, token = backend.login("a@example.com", "password-one")
    api = CARESAPI(backend, demo_controller=DemoController(backend, interval_seconds=0.001))

    response = api.handle(
        "POST",
        "/api/demo/start",
        {"scenario": "ELEVATED", "send_real_sms": "true"},
        {"Authorization": f"Bearer {token}"},
    )

    assert response.status == 422
    assert backend.list_monitoring_sessions(1) == []


# ------------------------------------------------------- GPS safety


def add_fix(backend, user_id, age_seconds=5.0, lat=12.2958, lon=76.6394):
    return backend.ingest_location(user_id, {
        "latitude": lat,
        "longitude": lon,
        "timestamp": time.time() - age_seconds,
        "source": "NEO-6M",
    })


def test_demo_alert_includes_recent_hardware_fix_with_age(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=90)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] == pytest.approx(12.2958)
    assert calls[0]["location_age_seconds"] == pytest.approx(90, abs=10)
    assert calls[0]["location_fix_timestamp"] is not None
    metadata = notification_action(backend, user["id"])["metadata"]
    assert metadata["location_status"] == "CURRENT"
    assert metadata["gps_available"] is True


def test_stale_fix_is_reported_unavailable_not_used(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=3600)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] is None
    assert "too old" in calls[0]["location_unavailable_reason"]
    metadata = notification_action(backend, user["id"])["metadata"]
    assert metadata["location_status"] == "STALE"
    assert metadata["gps_available"] is False


def test_max_age_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("CARES_LOCATION_MAX_AGE_SECONDS", "30")
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=120)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] is None


def test_missing_fix_is_reported_unavailable(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] is None
    assert "no hardware GPS fix" in calls[0]["location_unavailable_reason"]
    assert notification_action(backend, user["id"])["metadata"]["location_status"] == "MISSING"


def test_other_users_location_is_never_used(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend, "a@example.com")
    other = backend.register("Other", "b@example.com", "password-two")
    calls = capture_sms(monkeypatch)
    add_fix(backend, other["id"], age_seconds=5)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] is None


def test_future_dated_fix_is_rejected(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=-7200)

    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")

    assert calls[0]["latitude"] is None
    assert notification_action(backend, user["id"])["metadata"]["location_status"] == "INVALID"


def test_real_alert_also_drops_stale_session_fix(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=3600)

    backend.persist_engine_output(user["id"], high_output())

    assert calls[0]["is_demo"] is False
    assert calls[0]["latitude"] is None


def test_real_alert_uses_fresh_session_fix(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = user_with_guardian(backend)
    calls = capture_sms(monkeypatch)
    add_fix(backend, user["id"], age_seconds=10)

    backend.persist_engine_output(user["id"], high_output())

    assert calls[0]["latitude"] == pytest.approx(12.2958)


def test_message_shows_fix_time_and_age_or_unavailable_reason():
    fresh, url = sms._build_message(
        "Test User", 110.0, 12.2958, 76.6394, None, time.time(), is_demo=True,
        location_age_seconds=125, location_fix_timestamp=time.time() - 125,
    )
    assert url and "maps?q=12.2958,76.6394" in url
    assert "GPS fix time:" in fresh
    assert "2 min" in fresh

    stale, url = sms._build_message(
        "Test User", 110.0, None, None, None, time.time(), is_demo=True,
        location_unavailable_reason="last GPS fix is too old (60 min)",
    )
    assert url is None
    assert "GPS location: Unavailable (last GPS fix is too old (60 min))" in stale


def test_demo_message_is_labelled_simulated():
    text, _ = sms._build_message(
        "Test User", 110.0, None, None, None, time.time(), is_demo=True
    )
    assert "PRESENTATION DEMO" in text
    assert "SIMULATED" in text
    assert "NOT A REAL MEDICAL EMERGENCY" in text


def test_batch_status_lookup_uses_device_id_of_the_batch(monkeypatch):
    monkeypatch.setenv("TEXTBEE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TEXTBEE_DEVICE_ID", "configured-device")
    seen = []

    def fake_urlopen(request, timeout=0):
        seen.append(request.full_url)
        return FakeResponse({"data": {"batch": {"status": "completed"}, "messages": []}})

    monkeypatch.setattr(sms, "urlopen", fake_urlopen)

    result = sms.get_textbee_batch_status("batch-1", device_id="batch-device")

    assert result["success"] is True
    assert "/devices/batch-device/" in seen[0]


# ------------------------------------------- GPS-only hardware ingest


def hardware_api(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "a@example.com", "password-one")
    monkeypatch.setenv("CARES_DEVICE_TOKEN", "device-token-for-tests")
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", str(user["id"]))
    return backend, user, CARESAPI(backend)


TOKEN = {"X-Device-Token": "device-token-for-tests"}


def ingest(api, body, headers=TOKEN):
    return api.handle("POST", "/api/hardware/ingest", body, headers)


def test_gps_only_payload_is_accepted_without_physiological_data(tmp_path, monkeypatch):
    backend, user, api = hardware_api(tmp_path, monkeypatch)

    response = ingest(api, {
        "device_id": "CARES_ESP32_01",
        "latitude": 12.2958,
        "longitude": 76.6394,
        "timestamp": time.time() - 2,
    })

    assert response.status == 201
    body = response.body
    assert body["physiological_sample_processed"] is False
    assert body["result"] is None
    assert body["heart_rate"] is None and body["spo2"] is None
    assert body["timestamp_source"] == "DEVICE"
    assert body["location"]["latitude"] == pytest.approx(12.2958)
    assert body["location"]["source"] == "NEO-6M"
    assert backend.latest_location(user["id"]) is not None
    assert backend.list_actions(user["id"]) == []


def test_gps_only_payload_never_triggers_risk_or_sms(tmp_path, monkeypatch):
    backend, user, api = hardware_api(tmp_path, monkeypatch)
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")
    calls = capture_sms(monkeypatch)

    ingest(api, {"latitude": 12.2958, "longitude": 76.6394})

    assert calls == []
    assert backend.list_actions(user["id"]) == []


def test_boot_relative_device_timestamp_falls_back_to_server_time(tmp_path, monkeypatch):
    _, _, api = hardware_api(tmp_path, monkeypatch)

    body = ingest(api, {
        "latitude": 12.2958, "longitude": 76.6394, "timestamp": 12345,
    }).body

    assert body["timestamp_source"] == "SERVER_RECEIVED"
    assert "ignored" in body["timestamp_note"]
    assert abs(body["timestamp"] - time.time()) < 30


def test_missing_timestamp_uses_server_receive_time(tmp_path, monkeypatch):
    _, _, api = hardware_api(tmp_path, monkeypatch)

    body = ingest(api, {"latitude": 12.2958, "longitude": 76.6394}).body

    assert body["timestamp_source"] == "SERVER_RECEIVED"
    assert body["timestamp_note"] is None


def test_existing_heart_rate_payload_still_works(tmp_path, monkeypatch):
    backend, user, api = hardware_api(tmp_path, monkeypatch)

    response = ingest(api, {
        "heart_rate": 72, "spo2": 98,
        "latitude": 12.2958, "longitude": 76.6394,
    })

    assert response.status == 201
    assert response.body["physiological_sample_processed"] is True
    assert response.body["heart_rate"] == 72.0
    assert response.body["result"] is not None
    assert response.body["location"] is not None


def test_ingest_rejects_empty_and_partial_payloads(tmp_path, monkeypatch):
    backend, user, api = hardware_api(tmp_path, monkeypatch)

    assert ingest(api, {"device_id": "CARES_ESP32_01"}).status == 422
    assert ingest(api, {"latitude": 12.2958}).status == 422
    assert ingest(api, {"heart_rate": 72}).status == 422
    assert ingest(api, {"latitude": 123.0, "longitude": 76.0}).status == 422
    assert backend.latest_location(user["id"]) is None


def test_ingest_requires_valid_device_token(tmp_path, monkeypatch):
    backend, user, api = hardware_api(tmp_path, monkeypatch)
    body = {"latitude": 12.2958, "longitude": 76.6394}

    assert ingest(api, body, headers={}).status == 401
    assert ingest(api, body, headers={"X-Device-Token": "wrong"}).status == 401
    assert ingest(api, body, headers={"X-Device-Token": "tökén-ñ"}).status == 401
    assert backend.latest_location(user["id"]) is None


def test_notification_status_reports_demo_real_sms_disabled_by_default(tmp_path):
    backend = make_backend(tmp_path)
    backend.register("Test User", "a@example.com", "password-one")
    _, token = backend.login("a@example.com", "password-one")
    api = CARESAPI(backend)

    response = api.handle(
        "GET", "/api/notification-status", None,
        {"Authorization": f"Bearer {token}"},
    )

    assert response.status == 200
    assert response.body["demo_real_sms_enabled"] is False
    assert "TEXTBEE_API_KEY" not in json.dumps(response.body)
