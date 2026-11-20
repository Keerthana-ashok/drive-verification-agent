"""Check a deployed stack answers on /health. Usage: python scripts/smoke_test.py DriveAgent-Prod"""
import json
import sys
import time
import urllib.request

from stack_outputs import get_outputs


def main() -> None:
    url = get_outputs(sys.argv[1])["ApiUrl"].rstrip("/") + "/health"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                body = json.loads(r.read())
                print(f"{url} -> {r.status} {body}")
                if r.status == 200 and body.get("ok"):
                    return
        except Exception as e:  # noqa: BLE001
            print(f"Attempt {attempt + 1} failed: {e}")
        time.sleep(5)
    sys.exit("Smoke test failed")


if __name__ == "__main__":
    main()
