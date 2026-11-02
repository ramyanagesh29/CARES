# CARES Hardware Integration Guide

**Scope.** In this project the hardware's role is to supply **GPS location and a timestamp**
to the CARES backend. Continuous wrist-health monitoring is *not* the purpose of this
integration. An optional MAX30102 heart-rate/SpO2 reading is still accepted if the device
sends one.

## Verification status (read this first)

| Item | Status |
| --- | --- |
| Backend route `POST /api/hardware/ingest` (token check, validation, GPS-only, timestamp handling) | **Software-tested** (mocked, automated) |
| Location → dashboard → HIGH incident/SMS (freshness + same-user rule) | **Software-tested** |
| ESP32 firmware source | **NOT VERIFIED — firmware not included in the project ZIP** |
| GPIO pins (MAX30102 SDA 25 / SCL 26 as previously documented) | **NOT VERIFIED** |
| NEO-6M GPS wiring and UART pins | **NOT VERIFIED** |
| Wi-Fi connection, reconnect behaviour, HTTPS/HTTP choice on the device | **NOT VERIFIED** |
| Device-token behaviour on the device (header name, storage) | **NOT VERIFIED** (server side verified) |
| Real GPS fix reaching the dashboard from a physical device | **NOT VERIFIED** |
| Real TextBee SMS with a real phone | **NOT VERIFIED** (automated tests use mocks) |

No firmware was written or invented. The pin numbers above come from your earlier notes; they
could not be checked against source.

## End-to-end flow

```
NEO-6M GPS ─UART→ ESP32 ─Wi-Fi→ POST /api/hardware/ingest ─→ backend/api.py
   (MAX30102 optional, I²C)         header X-Device-Token         │ validate token + payload
                                                                  ▼
                                      backend/services.py: ingest_location()  → location_events (SQLite)
                                                                  │
   demo (backend/demo.py) or real samples → CARESDecisionEngine → HIGH episode starts
                                                                  ▼
                       persist_engine_output(): incident + GUARDIAN_NOTIFICATION action
                                                                  ▼
          _notification_location(): same user? valid? ≤ CARES_LOCATION_MAX_AGE_SECONDS?
                                                                  ▼
                guardian/sms.py send_guardian_sms(): MOCK or TextBee (rules below)
                                                                  ▼
        action status MOCK_SENT | PENDING (accepted) | FAILED | UNAVAILABLE
                                                                  ▼
   GET /api/actions/<id>/delivery-status → TextBee batch lookup → SENT / DELIVERED (only if provider confirms)
```

## API contract for the device

`POST /api/hardware/ingest` — header `X-Device-Token: <CARES_DEVICE_TOKEN>`, JSON body:

| Field | Required | Notes |
| --- | --- | --- |
| `latitude`, `longitude` | yes for a GPS payload (both together) | −90…90 / −180…180 |
| `timestamp` | optional | Unix epoch seconds (UTC). Used only if within `CARES_DEVICE_TIME_MAX_SKEW_SECONDS` (default 300) of server time. Otherwise the server receive time is used. |
| `device_id` | optional | default `CARES_ESP32_01` |
| `heart_rate` + `spo2` | optional pair | if either is sent, both are validated (HR 30–220, SpO2 70–100) and processed as `REAL_HARDWARE` data |

A payload needs a GPS location and/or the heart-rate pair. Example (GPS only):

```json
{"device_id": "CARES_ESP32_01", "latitude": 12.2958, "longitude": 76.6394, "timestamp": 1790000000}
```

Response (201) includes `timestamp`, `timestamp_source` (`DEVICE` or `SERVER_RECEIVED`),
`timestamp_note` (why a device clock was ignored, if so), `physiological_sample_processed`.
Errors: `401` bad/missing token, `422` invalid payload, `503` server not configured.

A GPS-only payload never runs the risk engine and never sends an SMS.
An ESP32 without a synced clock should either send GPS-derived UTC time or omit `timestamp`.

Other routes: `POST /api/location` and `POST /api/hardware/sample` (logged-in user session),
`GET /api/location/latest`, `GET /api/notification-status`, `POST /api/demo/start|stop`, `GET /api/demo/status`.

## Configuration variables (`.env`, see `.env.example`)

| Variable | Purpose | Default |
| --- | --- | --- |
| `CARES_DEVICE_TOKEN` | shared secret for the device header | none (route returns 503) |
| `CARES_HARDWARE_USER_ID` | CARES user that owns hardware data | none (503) |
| `CARES_LOCATION_MAX_AGE_SECONDS` | max age of a GPS fix shown to guardians | 300 |
| `CARES_DEVICE_TIME_MAX_SKEW_SECONDS` | accepted device-clock skew | 300 |
| `CARES_SMS_PROVIDER` | `mock` or `textbee` | `mock` |
| `TEXTBEE_API_KEY`, `TEXTBEE_DEVICE_ID` | TextBee credentials (never commit) | none |
| `CARES_DEMO_REAL_SMS` | allow a real SMS in the demo | `false` |

`.env` is loaded when the server starts.

## When is an SMS real?

| Situation | Result |
| --- | --- |
| `CARES_SMS_PROVIDER=mock` (default) | `MOCK_SENT`, nothing is sent |
| Demo, default | `MOCK_SENT` |
| Demo + `CARES_DEMO_REAL_SMS=true` + textbee + box ticked | real TextBee request, text labelled *simulated demonstration*; status `PENDING` (accepted, **not delivered**) |
| Real (non-demo) HIGH episode + textbee configured | real request, `PENDING`; later `SENT`/`DELIVERED` only if TextBee reports it |
| Automated tests | always mock; network is faked |

Repeated HIGH readings in one episode send **one** SMS; recovery then a new HIGH sends a new one.

## Physical hardware verification checklist (for you to run)

1. Put the real firmware project in the repo (e.g. `firmware/`) so it can be reviewed.
2. Confirm in the firmware: Wi-Fi SSID/password are **not** committed; token is read from a local, ignored file; URL points to your PC's LAN IP and port 8000 (server must be started with `--host 0.0.0.0` for LAN access; use only on a trusted network).
3. NEO-6M: wire VCC/GND/TX→ESP32 RX pin/RX→ESP32 TX pin as the firmware expects; take it outside or near a window; wait for a fix (can take minutes).
4. Serial monitor: confirm valid latitude/longitude and a sensible UTC time before any POST.
5. From the ESP32, send one payload. Server should reply 201; check `GET /api/location/latest` and the dashboard Location page.
6. Test a wrong token (expect 401) and unplug Wi-Fi/restore it (confirm the device reconnects).
7. If using MAX30102: confirm SDA/SCL pins against the firmware and run an I²C scan before relying on readings.
8. Run the **mock** demo with the real device online: the printed/dashboard alert should show your real GPS fix time and age.
9. Only then, optionally, run one real SMS demo to a consenting guardian (see `SMS_SETUP.md`), then refresh delivery status.
10. Record what you observed (photos/logs). Report anything not observed as "not verified".
