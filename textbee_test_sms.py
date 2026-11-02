from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from guardian.sms import _load_local_env
import os
import json

_load_local_env()

api_key = os.getenv("TEXTBEE_API_KEY", "").strip()
device_id = os.getenv("TEXTBEE_DEVICE_ID", "").strip()

if not api_key or not device_id:
    print("ERROR: TextBee API key or device ID is missing.")
    raise SystemExit(1)

recipient = input("Enter the consenting guardian phone number (E.164): ").strip()

if not recipient.startswith("+"):
    print("ERROR: Use international format, e.g. +91XXXXXXXXXX")
    raise SystemExit(1)

message = (
    "CARES TEST - NOT AN EMERGENCY. "
    "This is a test of the CARES guardian notification system. "
    "Please confirm receipt."
)

print("\nREAL SMS TEST")
print("Recipient:", recipient)
print("Message:", message)
print("\nThis will send ONE real SMS.")

confirmation = input("Type SEND TEST SMS to continue: ").strip()

if confirmation != "SEND TEST SMS":
    print("Cancelled. No SMS was sent.")
    raise SystemExit(0)

body = {
    "recipients": [recipient],
    "message": message,
    "deviceId": device_id,
}

request = Request(
    "https://api.textbee.dev/api/v1/gateway/send-sms",
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
    with urlopen(request, timeout=15) as response:
        result = response.read().decode("utf-8")
        print("\nHTTP status:", response.status)
        print("TextBee response:", result)
        print("Provider response received. Check TextBee Messaging History.")
        print("Acceptance is not proof of handset delivery.")

except HTTPError as exc:
    print("\nHTTP error:", exc.code)
    print(exc.read().decode("utf-8")[:500])

except URLError as exc:
    print("\nConnection error:", exc.reason)

except TimeoutError:
    print("\nRequest timed out. Check TextBee Messaging History before retrying.")
