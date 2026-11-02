# Changes made during the audit (original upload vs. corrected project)

Compared file-by-file against the original upload. Existing line endings (CRLF in some files)
were preserved so Git diffs stay small. No existing test was modified or removed.

## Modified files
| File | Change |
| --- | --- |
| `backend/services.py` | Fixed the `UnboundLocalError` in `_send_guardian_notification()`: `real_sms_sent` is `False` in the no-guardian branch and computed from accepted, non-mock results in the final metadata. Demo alerts use real wall-clock time (was 1970). New `_notification_location()` / `_recent_hardware_location()`: GPS shown to guardians only if same user, valid, and recent; age and status recorded in action metadata. |
| `guardian/sms.py` | Demo can send a real SMS only with `CARES_DEMO_REAL_SMS=true` + textbee + explicit per-demo request; pytest guard now applies to demo too. New `demo_real_sms_status()`. Message shows GPS fix time/age or why location is unavailable. Fixed `get_textbee_batch_status()` ignoring its `device_id` argument. |
| `backend/demo.py` | `start(..., real_sms=False)`; refuses (409) a real-SMS request when not enabled; records the choice on the session. |
| `backend/api.py` | `/api/demo/start` accepts strict boolean `send_real_sms`. `/api/hardware/ingest` accepts GPS-only payloads, optional device timestamp (validated, falls back to server time), unchanged heart-rate/SpO2 path. Device-token comparison no longer errors on non-ASCII input (401). `/api/notification-status` reports `demo_real_sms_enabled`. Comment corrected (MAX30102). |
| `backend/server.py` | Loads `.env` at startup so `CARES_DEVICE_TOKEN` / `CARES_HARDWARE_USER_ID` work from `.env`. |
| `frontend/index.html`, `app.js`, `styles.css` | Demo panel: unticked-by-default "Send a real SMS" box with confirmation; shows `Guardian SMS: MOCK / REAL SMS REQUESTED`. |
| `textbee_test_sms.py` | Uses the project's own `.env` loader (no `python-dotenv` needed). |
| `.env.example` | Added all variables with placeholders (no secrets). |
| `README.md`, `SMS_SETUP.md` | Corrected outdated hardware statement and another person's local path; documented status meanings and the real-SMS demo. |

## New files
`tests/test_demo_sms_and_gps_safety.py` (40 tests), `CHANGES.md`, `docs/CARES_SETUP_GUIDE.md`,
`docs/CARES_HARDWARE_INTEGRATION.md`, `docs/CARES_AUDIT_AND_TEST_REPORT.md`, `docs/GITHUB_UPDATE_GUIDE.md`.

## Not changed on purpose
Risk engine, baseline, escalation/alert-transition logic, database schema, existing tests,
authentication, the heart-rate hardware path, and the dashboard layout.

## Removed from the package (private runtime data)
`data/cares.sqlite3-wal` and `data/cares.sqlite3-shm` (your local database files). No `.env` was present.
