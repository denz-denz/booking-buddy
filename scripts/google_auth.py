"""One-time Google OAuth on your laptop (spec §8). Opens a browser, writes the token JSON.

Usage:
    .venv/bin/python scripts/google_auth.py [--client-secret data/client_secret.json] [--token data/google_token.json]

Before running: in Google Cloud Console, enable the Google Calendar API, create an OAuth client of type
"Desktop app", download its JSON, and set the consent screen's publishing status to "In production"
(otherwise refresh tokens expire after 7 days). Expect an "unverified app" warning; continue past it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from calbot.backends.google_api import SCOPES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-secret", type=Path, default=ROOT / "data" / "client_secret.json")
    ap.add_argument("--token", type=Path, default=ROOT / "data" / "google_token.json")
    args = ap.parse_args()
    if not args.client_secret.exists():
        sys.exit(f"Client secret not found at {args.client_secret}")

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        sys.exit("No refresh token returned. Revoke the app at myaccount.google.com/permissions and retry.")
    args.token.parent.mkdir(parents=True, exist_ok=True)
    args.token.write_text(creds.to_json())
    args.token.chmod(0o600)
    args.token.with_suffix(".meta.json").write_text(
        json.dumps({"authorized_at": datetime.now(timezone.utc).isoformat()}))
    print(f"Wrote {args.token}. Scopes: {', '.join(SCOPES)}")


if __name__ == "__main__":
    main()
