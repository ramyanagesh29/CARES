# ESP32 BPM Telemetry: implementation, testing, Git and Render guide

## Root cause
`POST /api/hardware/ingest` is the *emergency* path. It requires `heart_rate` **and** `spo2` together and sends every
accepted reading into the risk engine. A BPM-only payload (SpO2 unavailable) is therefore rejected with HTTP 422. In
addition, the dashboard hides everything inside `#dashboard-content` until the engine has produced its first event, so a
BPM card placed there would not appear for telemetry-only data.

The fix is a separate, display-only path. `/api/hardware/ingest` is unchanged.

## What was added (additive only: 609 lines added, none removed)
| File | Change |
| --- | --- |
| `backend/db.py` | New table `hardware_telemetry` (`id, user_id, device_id, heart_rate, latitude, longitude, timestamp`) + index. `CREATE TABLE IF NOT EXISTS`, so existing databases (including Render's) just gain the table. No existing table is altered. |
| `backend/services.py` | `record_hardware_telemetry()` validates, stores, trims to the newest 5000 rows per user, publishes the `hardware_telemetry` EventBus event. `latest_hardware_telemetry()` returns the newest row (with `age_seconds`). |
| `backend/api.py` | `POST /api/hardware/telemetry` (device-token auth via new `_authenticate_device()`, same 401/503 rules as ingest). `GET /api/dashboard/current` now also returns `hardware_telemetry` (`null` when none). |
| `frontend/index.html` | "ESP32 LIVE BPM" card, placed *outside* `#dashboard-content` so it shows before any engine event. Separate from "Current HR". |
| `frontend/app.js` | Reads `hardware_telemetry` from the dashboard API, listens for the `hardware_telemetry` event on the existing EventSource, shows an "unavailable" state, and marks readings older than 30 s as stale. Updates only this card. |
| `frontend/styles.css` | 3 lines for the stale look (not in the PDF's file list; add it to your commit). |
| `tests/test_hardware_telemetry.py` | 53 tests (new). |

## API
`POST /api/hardware/telemetry`, header `X-Device-Token`, JSON:
```json
{"heart_rate": 76.4, "device_id": "CARES_ESP32_01", "latitude": 12.3051, "longitude": 76.6551}
```
- `heart_rate` required, number 30 to 220 (same range as ingest). Strings, booleans, NaN and out-of-range are rejected (422).
- `latitude`/`longitude` optional, but both or neither; valid ranges; `0,0` is rejected because GPS modules report it when there is no fix. Omit GPS until you have a real fix.
- `device_id` optional (default `CARES_ESP32_01`), 1 to 80 characters.
- `timestamp` optional Unix seconds; used only if close to server time, otherwise the server receive time is used.
- Responses: `201` saved (`"risk_engine_invoked": false`), `401` bad/missing token, `422` invalid data, `503` server not configured (`CARES_DEVICE_TOKEN` or `CARES_HARDWARE_USER_ID` missing).

Telemetry GPS is stored with the reading only. It is **not** used for guardian alerts or the Location page; that still
comes from `/api/hardware/ingest` (freshness rules unchanged).

## Verified in this review
| Check | Result |
| --- | --- |
| `python -m py_compile backend/api.py backend/services.py backend/db.py` | OK |
| `node --check frontend/app.js` | OK |
| `python -m pytest -q` (full suite) | **160 passed, 0 failed, 0 skipped** (107 before + 53 new) |
| New tests against the code *before* this change | 51 of 53 fail, so they detect the missing feature |
| `git diff --check` (throwaway repo, CRLF-aware) | clean |
| Patch applies to the previous code with `git apply --check` | OK |
| Live HTTP run on a temporary server | valid POST 201; wrong token 401; BPM 500 rejected 422; the real `/api/events/stream` delivered the `hardware_telemetry` frame; dashboard returned it; engine event `null`, no actions, no incidents; no SMS sent (provider `mock`) |
| Dashboard in jsdom (real `index.html` + `app.js`, mocked API/EventSource) | empty state "No ESP32 reading received yet"; value from dashboard API; live update 76.4 to 88.2 via event; stale state after 5 min; card outside engine content |

The tests also prove that telemetry (including BPM values a risk engine would call HIGH) creates no engine events,
incidents, actions or SMS, and that the existing `/api/hardware/ingest` behaves as before.

## NOT verified
Real ESP32 readings, Wi-Fi, MAX30102 accuracy, your Render deployment, and the page in a real browser. **Do not treat
this integration as complete until a real ESP32 reading appears on your deployed dashboard.**

## Run locally
```bash
python -m pytest -q
# .env: CARES_DEVICE_TOKEN=<long random string>, CARES_HARDWARE_USER_ID=<your user id>
python -m backend.server
curl -X POST http://127.0.0.1:8000/api/hardware/telemetry \
  -H "Content-Type: application/json" -H "X-Device-Token: <token>" \
  -d '{"heart_rate": 76.4, "device_id": "CARES_ESP32_01"}'
```
Open the dashboard logged in as that user: the ESP32 LIVE BPM card updates without a refresh.
Then check: wrong token gives 401; `{"heart_rate": 500}` gives 422; Incidents and Guardian actions stay empty.

## ESP32 side (example only: not your firmware, not tested)
Your firmware is not available to me. Send only genuine MAX30102 BPM (finger on sensor, valid beat). Never send made-up values.
```cpp
// Example sketch fragment. Keep the token in a git-ignored secrets.h.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

bool sendTelemetry(float bpm, bool hasFix, double lat, double lon) {
  if (WiFi.status() != WL_CONNECTED) return false;
  WiFiClientSecure client;
  client.setInsecure();                 // first bring-up only; use client.setCACert(...) for real use
  HTTPClient http;
  http.setTimeout(10000);               // Render free instances can take a while to wake
  http.begin(client, "https://YOUR-RENDER-URL/api/hardware/telemetry");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  String body = String("{\"heart_rate\":") + String(bpm, 1) + ",\"device_id\":\"CARES_ESP32_01\"";
  if (hasFix) body += String(",\"latitude\":") + String(lat, 6) + ",\"longitude\":" + String(lon, 6);
  body += "}";
  int code = http.POST(body);
  http.end();
  return code == 201;                   // log other codes to Serial: 401 token, 422 bad data, 503 server config
}
```
Send about once every 2 to 5 seconds, not on every sensor sample. Include GPS only when the NEO-6M has a valid fix.

## Git (nothing is pushed for you)
```bash
git status && git branch && git remote -v
git checkout master && git pull origin master      # only if your working tree is clean
git checkout -b fix/esp32-bpm-telemetry
# apply the changes: copy the 7 files, or:  git apply telemetry-changes.patch
python -m py_compile backend/api.py backend/services.py backend/db.py
python -m pytest -q
git -c core.whitespace=cr-at-eol diff --check     # plain "git diff --check" flags CRLF line endings as "trailing whitespace"
git status && git diff --stat
git add backend/db.py backend/services.py backend/api.py frontend/index.html frontend/app.js frontend/styles.css tests/test_hardware_telemetry.py docs/ESP32_TELEMETRY_GUIDE.md
git commit -m "Add ESP32 BPM telemetry integration"
git push -u origin fix/esp32-bpm-telemetry         # only when you are ready
```
Note: the PDF's `git add` list omits `frontend/styles.css` and the tests. If your earlier audit fixes are still
uncommitted, commit them on their own branch first so this PR contains only the telemetry change.
Then open the Pull Request into `master`, review, merge.

## Render
1. After merging, confirm the service deploys from `master`; trigger a manual deploy if needed; read the logs.
2. Environment: `CARES_DEVICE_TOKEN` (strong random; rotate it if it was ever shared), `CARES_HARDWARE_USER_ID`,
   `CARES_COOKIE_SECURE=1` (HTTPS), `CARES_SMS_PROVIDER`, `TEXTBEE_*`, `CARES_LOCATION_MAX_AGE_SECONDS`. Render requires binding
   `0.0.0.0` on its `PORT`: start with `python -m backend.server --host 0.0.0.0 --port $PORT` (or set `CARES_HOST`/`CARES_PORT`).
3. Test the deployed URL with the curl above, then the dashboard.

## Remaining issues to know about
- **SQLite on Render:** without a persistent disk, Render's filesystem is wiped on each deploy/restart, so users, guardians and
  readings are lost and `CARES_HARDWARE_USER_ID` may point at a user that no longer exists. Attach a Render disk and set `CARES_DB_PATH` to it.
- No rate limiting on the device route. Retention (5000 rows/user) bounds storage.
- Server-sent events are in-memory and per process: run a single instance.
- A BPM on the dashboard is a raw reading, not clinically validated, and is deliberately not used for risk decisions.
