# CARES Setup Guide (beginner friendly)

Commands assume you are inside the project folder (the one containing `README.md`).
On Windows use PowerShell; where a command differs, both are shown.

## 1. Install
```bash
python --version                     # tested with Python 3.12; use 3.10 or newer
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # installs pytest (the app itself uses only the standard library)
```

## 2. Configure `.env`
```bash
cp .env.example .env                 # Windows: copy .env.example .env
```
Open `.env` in an editor. Safe starting values are already there: `CARES_SMS_PROVIDER=mock`
and `CARES_DEMO_REAL_SMS=false`. Set `CARES_DEVICE_TOKEN` to a long random string and
`CARES_HARDWARE_USER_ID` to your CARES user's ID (use `1` if yours is the first account).
**Never commit `.env`** (it is in `.gitignore`).

## 3. Database (SQLite)
Nothing to install. The file `data/cares.sqlite3` is created automatically on first start.
Do not commit it. To start clean, stop the server and delete `data/cares.sqlite3*`.

## 4. Start the backend and frontend
```bash
python -m backend.server
```
The same process serves the dashboard. Open <http://127.0.0.1:8000>, click Register, then log in.

## 5. Run the tests
```bash
python -m pytest -v
```
Expected: all tests pass. No real SMS is sent by tests.

## 6. Register a guardian
Dashboard → Guardian page → add a name, relationship and a phone number in international format
(for example `+919876543210`).

## 7. Mock notification test (safe, no SMS)
1. Dashboard → demo panel → choose **SUSTAINED_HIGH** → leave "Send a real SMS" **unticked** → Start demo.
2. After the baseline period the engine reports HIGH. The notification shows **MOCK_SENT · SIMULATED**.
3. The terminal running the server prints the simulated SMS text.
4. Stay on HIGH: no second notification is created. Stop the demo and start another to see a new episode.

## 8. Test GPS without hardware (software only)
This is a simulated device call, **not** a hardware test:
```bash
curl -X POST http://127.0.0.1:8000/api/hardware/ingest \
  -H "Content-Type: application/json" -H "X-Device-Token: <your CARES_DEVICE_TOKEN>" \
  -d '{"device_id":"CARES_ESP32_01","latitude":12.2958,"longitude":76.6394}'
```
Expect `201`. The dashboard Location page now shows the fix. Start the mock demo within
5 minutes and the simulated alert will include the fix time and age. After 5 minutes it will
say `GPS location: Unavailable (last GPS fix is too old ...)`.

## 9. Safe TextBee test
1. Install the TextBee app on an Android phone with a SIM; note the API key and device ID from your TextBee account.
2. Put them in `.env` (`TEXTBEE_API_KEY`, `TEXTBEE_DEVICE_ID`). Keep `CARES_SMS_PROVIDER=mock` for now.
3. Run `python textbee_test_sms.py`: it sends exactly **one** clearly labelled test SMS, only after you type `SEND TEST SMS`, to a number whose owner agreed.
4. Check the TextBee dashboard / phone. "Accepted" is not "delivered".

## 10. Real-SMS presentation demo (optional, explicit)
1. In `.env`: `CARES_SMS_PROVIDER=textbee`, `CARES_DEMO_REAL_SMS=true`; restart the server.
2. Use a guardian number whose owner has agreed to get a demo message.
3. Start the demo with **"Send a real SMS to my guardian(s)"** ticked and confirm the prompt.
4. Expect status `PENDING` (provider accepted). The SMS is labelled *PRESENTATION DEMO / SIMULATED / NOT A REAL MEDICAL EMERGENCY*.
5. Refresh delivery: `GET /api/actions/<action_id>/delivery-status` (logged in). It moves to `SENT` / `DELIVERED` only if TextBee reports it.
6. Afterwards set `CARES_DEMO_REAL_SMS=false` again.

## 11. Run the complete system with the ESP32
Follow the checklist in `docs/CARES_HARDWARE_INTEGRATION.md`. Firmware is not in this ZIP, so
that part is **not verified** here.
