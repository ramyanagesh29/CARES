# CARES Audit and Test Report

Scope: server-side audit of the uploaded project, fixes, regression tests. **The ESP32 firmware
was not part of the upload, so all device-side behaviour is NOT VERIFIED.**

## 1. Architecture found
- Standard-library HTTP server (`python -m backend.server`, `backend/server.py`) serving a static dashboard (`frontend/`) and a JSON API (`backend/api.py`). No FastAPI in this repository.
- `backend/services.py` (`CARESBackend`): users, sessions, guardians, engine-event persistence, incidents, actions, locations; SQLite via `backend/db.py` (tables: users, monitoring_sessions, engine_events, guardian_action_events, location_events, incidents, …).
- `engine/`: decision engine (personal baseline, features, escalation, risk engine) producing LOW/MEDIUM/HIGH. **Unchanged.**
- `backend/demo.py`: demo streams (WESAD-derived `NORMAL`, synthetic `ELEVATED` / `SUSTAINED_HIGH` / `RECOVERY`) fed through the normal engine.
- `guardian/`: action mapping and SMS (`sms.py`: MOCK or TextBee).
- Hardware entry: `POST /api/hardware/ingest` (device token). Firmware: absent.

## 2. What was wrong
| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | `UnboundLocalError` (`results` used in the no-guardian branch); the final metadata also hard-coded `real_sms_sent: False` | Bug — broke 3 tests and any HIGH event for a user without guardians | Fixed |
| 2 | Demo SMS time was `01-01-1970` (sample index used as clock time) | Bug | Fixed |
| 3 | Demo could never send a real SMS (forced mock), conflicting with the real-SMS demo requirement | Gap | Added explicit opt-in (default off) |
| 4 | Demo alerts always said GPS unavailable (location looked up per session; demo sessions can't hold hardware GPS) | Gap | Fixed with freshness + same-user rule |
| 5 | A stale GPS fix could be shown as the current location | Safety | Fixed: max age 300 s, future-dated/invalid/other-user rejected, age shown |
| 6 | `/api/hardware/ingest` rejected GPS-only payloads; ignored device timestamp | Scope mismatch | Fixed, heart-rate path preserved |
| 7 | `get_textbee_batch_status()` ignored its `device_id` argument | Bug | Fixed |
| 8 | Non-ASCII `X-Device-Token` caused a 422 with an internal message instead of 401 | Security/robustness | Fixed |
| 9 | `CARES_DEVICE_TOKEN` / `CARES_HARDWARE_USER_ID` in `.env` were not loaded until an SMS code path ran | Integration bug | `.env` loaded at server start |
| 10 | README claimed hardware was not integrated and used another person's local path; `.env.example` lacked hardware variables; `textbee_test_sms.py` needed an undeclared package | Docs/setup | Fixed |
| 11 | Upload contained private SQLite WAL/SHM files | Privacy | Excluded from the ZIP |

## 3. Already working (verified by tests or an HTTP smoke test)
Risk engine and its tests; baseline logic; duplicate-HIGH suppression and re-alert after recovery;
guardian CRUD and per-user isolation; mock SMS; TextBee request/response handling with mocked
network; delivery-status refresh (sent vs delivered); incidents and actions persistence; auth.

## 4. Incomplete / observations (not changed)
- Firmware, wiring and device behaviour: unverifiable here.
- The SMS is sent synchronously (HTTP timeout 15 s). A hardware heart-rate payload that starts a HIGH episode would hold that request until TextBee answers. GPS-only payloads are unaffected.
- A real heart-rate payload is still processed as `REAL_HARDWARE` and can start a real HIGH episode and a real SMS. If your firmware only sends GPS, this path is never used.
- Hardware data maps to one configured user (`CARES_HARDWARE_USER_ID`).
- `evaluation/evaluate_m2.py:119` emits a Python `SyntaxWarning` (invalid escape in a string); harmless, left alone.
- The new dashboard checkbox was syntax-checked (`node --check`) and its API call is covered by tests, but it was **not clicked in a real browser** in this review.
- The system is a research prototype; no medical accuracy or certification is claimed.

## 5. Testing report
Environment: Python 3.12.3, pytest. Commands run (from the project root):

| Command | Result |
| --- | --- |
| `python -m pytest -q` on the **original** upload | 64 passed, 3 failed (the 3 reported tests) |
| `python -m compileall -q backend engine guardian evaluation simulation tests textbee_test_sms.py` | OK (one pre-existing `SyntaxWarning`, see above) |
| `node --check frontend/app.js` | OK |
| `python -m pytest -v` on the **corrected** project | **107 passed, 0 failed, 0 skipped** (67 original + 40 new) |
| New tests run against the **original** code | 30 of 40 fail, confirming they detect the bugs above |
| Live HTTP smoke test (temporary server, mock provider, no credentials) | register/login/guardian OK; GPS-only ingest 201 (`timestamp_source: DEVICE`, no sample processed); bad token 401; real-SMS demo request refused with 409; mock SUSTAINED_HIGH demo: 76 HIGH readings → exactly 1 `MOCK_SENT`, 75 `GENERATED`, `real_sms_sent: false`, `delivery_confirmed: false`, `location_status: CURRENT`, simulated message labelled and time correct |

Per-category coverage in the 107 tests: backend/API (`test_backend`, new tests), guardian notification and TextBee with mocks (`test_guardian_sms_integration`, new tests), risk engine/baseline/escalation, database persistence (exercised through the backend tests), frontend asset/contract checks (`test_frontend`), demo integration.

**NOT VERIFIED:** ESP32 firmware, GPIO/GPS wiring, Wi-Fi, device-side token handling, a real GPS fix from hardware, a real TextBee SMS and handset delivery, dashboard behaviour in a browser. Steps to verify them are in `docs/CARES_HARDWARE_INTEGRATION.md` and `docs/CARES_SETUP_GUIDE.md`.

## 6. Secret review
Scanned the corrected tree for `.env`, key-like strings and SQLite files: none present. `.env.example` holds placeholders only; test files use obviously fake values.
