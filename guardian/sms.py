
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


IST = ZoneInfo("Asia/Kolkata")

TEXTBEE_SEND_URL = "https://api.textbee.dev/api/v1/gateway/send-sms"
HTTP_TIMEOUT_SECONDS = 15


def _format_ist(timestamp):
    """Convert a Unix timestamp to an explicit IST date/time string."""
    if timestamp is None:
        return "Unavailable"

    return datetime.fromtimestamp(
        float(timestamp), tz=timezone.utc
    ).astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p IST")


def _load_local_env() -> None:
    """Load the project .env file during normal runs, never during pytest."""

    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    env_path = Path(__file__).resolve().parent.parent / ".env"

    if not env_path.is_file():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value

    except OSError:
        return


def _env_flag(name: str) -> bool:
    """True only for an explicit 'true' value; unset or anything else is False."""

    _load_local_env()
    return os.getenv(name, "").strip().lower() == "true"


def demo_real_sms_status() -> dict:
    """Report whether a real-SMS demonstration is allowed right now.

    Never sends anything. ``reason`` explains why it is not available.
    """

    if not _env_flag("CARES_DEMO_REAL_SMS"):
        return {
            "enabled": False,
            "reason": "CARES_DEMO_REAL_SMS is not set to true on the server.",
        }

    if os.getenv("CARES_SMS_PROVIDER", "mock").strip().lower() != "textbee":
        return {
            "enabled": False,
            "reason": "CARES_SMS_PROVIDER must be textbee for a real SMS demo.",
        }

    if not os.getenv("TEXTBEE_API_KEY", "").strip():
        return {
            "enabled": False,
            "reason": "TEXTBEE_API_KEY is not configured.",
        }

    if (
        os.getenv("PYTEST_CURRENT_TEST")
        and os.getenv("CARES_ALLOW_SMS_IN_TESTS") != "true"
    ):
        return {
            "enabled": False,
            "reason": "Real SMS is disabled during automated tests.",
        }

    return {"enabled": True, "reason": None}


def _format_age(seconds: Optional[float]) -> str:
    try:
        total = max(int(float(seconds)), 0)
    except (TypeError, ValueError):
        return "unknown age"

    if total < 60:
        return "less than 1 min"

    minutes = total // 60

    if minutes < 60:
        return f"{minutes} min"

    return f"{minutes // 60} h {minutes % 60} min"


def _normalize_phone(phone_number: str) -> str:
    """Normalize a phone number and require international format."""

    phone = re.sub(r"[\s().-]", "", str(phone_number or ""))

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError(
            "Guardian phone number must use international format, "
            "e.g. +919876543210."
        )

    return phone


def _build_message(
    user_name: str,
    heart_rate: float,
    latitude: Optional[float],
    longitude: Optional[float],
    location_address: Optional[str],
    timestamp: Optional[float | str],
    is_demo: bool = False,
    location_age_seconds: Optional[float] = None,
    location_fix_timestamp: Optional[float] = None,
    location_unavailable_reason: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Build either a real-event alert or an explicitly labelled demo alert."""

    location_url = None

    if latitude is not None and longitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)

            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError

            location_url = (
                f"https://www.google.com/maps?q={latitude},{longitude}"
            )

        except (TypeError, ValueError):
            location_url = None

    # Incident timestamp: always display in Asia/Kolkata.
    try:
        if timestamp is None:
            event_time = datetime.now(IST).strftime(
                "%d-%m-%Y %I:%M:%S %p IST"
            )
        else:
            event_time = _format_ist(timestamp)

    except (TypeError, ValueError, OverflowError, OSError):
        event_time = str(timestamp)

    if is_demo:
        lines = [
            "CARES PRESENTATION DEMO",
            "",
            "SIMULATED HIGH-RISK CONDITION DETECTED",
            f"User: {user_name}",
            "Risk Level: HIGH (SIMULATED)",
            f"Heart Rate: {heart_rate:.1f} BPM (SYNTHETIC DATA)",
            f"Time: {event_time}",
            "",
            "NOT A REAL MEDICAL EMERGENCY.",
            "No emergency response is required.",
        ]
    else:
        lines = [
            "CARES EMERGENCY ALERT",
            "",
            "A HIGH-risk event has been detected.",
            f"User: {user_name}",
            "Risk Level: HIGH",
            f"Heart Rate: {heart_rate:.1f} BPM",
            f"Time: {event_time}",
        ]

    if location_address:
        lines.append(f"Location: {location_address}")

    if location_url:
        lines.append(f"Google Maps: {location_url}")

    # GPS fix timestamp: always display in Asia/Kolkata.
    if location_fix_timestamp is not None:
        try:
            fix_time = _format_ist(location_fix_timestamp)
            lines.append(
                f"GPS fix time: {fix_time} "
                f"({_format_age(location_age_seconds)} before this alert)"
            )
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    else:
        reason = (
            f" ({location_unavailable_reason})"
            if location_unavailable_reason
            else ""
        )
        lines.append(f"GPS location: Unavailable{reason}")

    if not is_demo:
        lines.extend([
            "",
            "Please contact the user and provide immediate assistance.",
        ])

    lines.append("- CARES System")

    return "\n".join(lines), location_url


def _send_textbee(
    phone_number: str,
    message: str,
) -> dict:
    """Submit an SMS to TextBee.

    PENDING means accepted by the provider, not delivered to the handset.
    """

    _load_local_env()

    api_key = os.getenv("TEXTBEE_API_KEY", "").strip()

    if not api_key:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "reason": (
                "TEXTBEE_API_KEY is not configured in the project "
                ".env or environment."
            ),
        }

    body = {
        "recipients": [phone_number],
        "message": message,
    }

    device_id = os.getenv("TEXTBEE_DEVICE_ID", "").strip()

    if device_id:
        body["deviceId"] = device_id

    request = Request(
        TEXTBEE_SEND_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "CARES/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
            status_code = response.status

    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
        except Exception:
            error_body = ""

        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": exc.code,
            "reason": error_body[:500] or str(exc),
        }

    except (URLError, TimeoutError, OSError) as exc:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "reason": f"Could not contact TextBee: {exc}",
        }

    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": "TextBee returned an invalid JSON response.",
        }

    data = parsed.get("data", parsed) if isinstance(parsed, dict) else {}

    try:
        success_count = int(data.get("successCount") or 0)
    except (TypeError, ValueError):
        success_count = 0

    accepted = status_code == 200 and (
        data.get("success") is True
        or bool(data.get("smsBatchId"))
        or success_count > 0
    )

    if not accepted:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": str(
                data.get("message")
                or parsed.get("message")
                or "TextBee did not accept the SMS."
            ),
        }

    return {
        "success": True,
        "status": "PENDING",
        "provider": "TEXTBEE",
        "provider_status": "ACCEPTED",
        "http_status": status_code,
        "sms_batch_id": data.get("smsBatchId"),
        "device_id": device_id,
        "recipient_count": data.get("recipientCount", 1),
        "warning": data.get("warning"),
        "delivery_confirmed": False,
    }


def get_textbee_batch_status(
    sms_batch_id: str,
    device_id: Optional[str] = None,
) -> dict:
    """Read TextBee batch delivery status without sending an SMS."""

    from urllib.parse import quote

    _load_local_env()

    api_key = os.getenv("TEXTBEE_API_KEY", "").strip()

    # Prefer the device that actually accepted the batch; fall back to config.
    device_id = (
        str(device_id or "").strip()
        or os.getenv("TEXTBEE_DEVICE_ID", "").strip()
    )
    batch_id = str(sms_batch_id or "").strip()

    if not api_key or not device_id or not batch_id:
        return {
            "success": False,
            "provider": "TEXTBEE",
            "reason": "TextBee credentials, device ID, or batch ID missing.",
        }

    url = (
        "https://api.textbee.dev/api/v1/gateway/devices/"
        f"{quote(device_id, safe='')}/sms-batch/{quote(batch_id, safe='')}"
    )

    request = Request(
        url,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "CARES/1.0",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
            status_code = response.status

    except HTTPError as exc:
        return {
            "success": False,
            "provider": "TEXTBEE",
            "http_status": exc.code,
            "reason": "TextBee status lookup failed.",
        }

    except (URLError, TimeoutError, OSError) as exc:
        return {
            "success": False,
            "provider": "TEXTBEE",
            "reason": f"TextBee status lookup connection failed: {exc}",
        }

    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": "TextBee returned invalid JSON.",
        }

    if status_code != 200 or not isinstance(parsed, dict):
        return {
            "success": False,
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": "TextBee status lookup was unsuccessful.",
        }

    data = parsed.get("data", parsed)

    if not isinstance(data, dict):
        return {
            "success": False,
            "provider": "TEXTBEE",
            "reason": "Unexpected TextBee response format.",
        }

    batch = data.get("batch") or {}
    messages = data.get("messages") or []

    return {
        "success": True,
        "provider": "TEXTBEE",
        "batch_status": batch.get("status"),
        "messages": [
            {
                "status": str(message.get("status") or "unknown").lower(),
                "error_code": message.get("errorCode"),
                "error_message": message.get("errorMessage"),
                "delivered_at": message.get("deliveredAt"),
            }
            for message in messages
            if isinstance(message, dict)
        ],
        "counts": {
            key: batch.get(key)
            for key in (
                "recipientCount",
                "successCount",
                "failureCount",
                "pendingCount",
                "dispatchedCount",
                "sentCount",
                "deliveredCount",
                "unknownCount",
            )
        },
    }


def send_guardian_sms(
    phone_number: str,
    heart_rate: float,
    user_name: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    location_address: Optional[str] = None,
    timestamp: Optional[float | str] = None,
    is_demo: bool = False,
    location_age_seconds: Optional[float] = None,
    location_fix_timestamp: Optional[float] = None,
    location_unavailable_reason: Optional[str] = None,
    allow_real_demo: bool = False,
) -> dict:
    """Send a guardian notification using MOCK or TextBee."""

    try:
        recipient = _normalize_phone(phone_number)
    except ValueError as exc:
        return {
            "success": False,
            "status": "FAILED",
            "reason": str(exc),
        }

    user_name = str(user_name or "").strip() or "CARES User"

    try:
        heart_rate = float(heart_rate)

        if not math.isfinite(heart_rate):
            raise ValueError

    except (TypeError, ValueError):
        return {
            "success": False,
            "status": "FAILED",
            "reason": "Invalid heart-rate value.",
        }

    message, location_url = _build_message(
        user_name=user_name,
        heart_rate=heart_rate,
        latitude=latitude,
        longitude=longitude,
        location_address=location_address,
        timestamp=timestamp,
        is_demo=is_demo,
        location_age_seconds=location_age_seconds,
        location_fix_timestamp=location_fix_timestamp,
        location_unavailable_reason=location_unavailable_reason,
    )

    _load_local_env()

    provider = os.getenv(
        "CARES_SMS_PROVIDER", "mock"
    ).strip().lower()

    # Demo notifications are MOCK unless a real SMS demonstration was
    # explicitly requested for this demo AND enabled on the server.
    if is_demo and not (
        allow_real_demo and _env_flag("CARES_DEMO_REAL_SMS")
    ):
        provider = "mock"

    # Never allow accidental real SMS during pytest (demo or not).
    # Tests can explicitly opt in when the network is mocked.
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        and os.getenv("CARES_ALLOW_SMS_IN_TESTS") != "true"
    ):
        provider = "mock"

    if provider == "mock":
        mock_message = (
            message
            + "\n\nThis is a simulated CARES guardian notification. "
            "No real SMS was sent."
        )

        print("\n" + "=" * 55)
        print("CARES MOCK SMS SERVICE")
        print("=" * 55)
        print(f"Recipient: {recipient}")
        print("\n" + mock_message)
        print("=" * 55)
        print("SIMULATION STATUS: MOCK_SENT")
        print("REAL SMS DELIVERY: NOT ATTEMPTED")
        print("=" * 55 + "\n")

        return {
            "success": True,
            "status": "MOCK_SENT",
            "provider": "MOCK",
            "recipient": recipient,
            "message": mock_message,
            "location_url": location_url,
            "real_sms_sent": False,
        }

    if provider == "textbee":
        result = _send_textbee(recipient, message)

        result.update({
            "recipient": recipient,
            "message": message,
            "location_url": location_url,
            "real_sms_sent": bool(
                result.get("success")
                and result.get("provider_status") == "ACCEPTED"
            ),
        })

        return result

    return {
        "success": False,
        "status": "FAILED",
        "provider": provider.upper(),
        "reason": (
            "Unsupported CARES_SMS_PROVIDER. "
            "Use 'mock' or 'textbee'."
        ),
    }
