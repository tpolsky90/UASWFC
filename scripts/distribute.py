"""
UASWFC Distribution Script (Phase 7 — PLACEHOLDER)
Runs in GitHub Actions. Triggered when pilot approves deliverables.
Downloads attachments from AGOL and distributes to:
    1. NIFC FTP (when credentials available, FAMAuth change ~March 2026)
    2. Wider email distribution list

Environment variables (from GitHub Secrets):
    AGOL_USERNAME, AGOL_PASSWORD, GMAIL_APP_PASSWORD
    Future: NIFC_FTP_HOST, NIFC_FTP_USER, NIFC_FTP_PASS
"""

import os
import sys
import json
import requests
from datetime import datetime


AGOL_USERNAME = os.environ["AGOL_USERNAME"]
AGOL_PASSWORD = os.environ["AGOL_PASSWORD"]

SURVEY_LAYER_URL = (
    "https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/"
    "service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0"
)

TOKEN_URL = "https://www.arcgis.com/sharing/rest/generateToken"


def get_agol_token():
    resp = requests.post(TOKEN_URL, data={
        "username": AGOL_USERNAME,
        "password": AGOL_PASSWORD,
        "referer": "https://www.arcgis.com",
        "f": "json"
    })
    data = resp.json()
    if "token" in data:
        return data["token"]
    print(f"[AUTH] FAILED: {data}")
    sys.exit(1)


def main():
    print("=" * 60)
    print("UASWFC Distribution Script (Phase 7)")
    print(f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    token = get_agol_token()

    # Query for approved records
    resp = requests.post(f"{SURVEY_LAYER_URL}/query", data={
        "where": "processing_status = 'approved'",
        "outFields": "*",
        "f": "json",
        "token": token
    })
    features = resp.json().get("features", [])
    print(f"\nApproved records: {len(features)}")

    if not features:
        print("Nothing to distribute. Done.")
        return

    for feature in features:
        oid = feature["attributes"]["objectid"]
        incident = feature["attributes"].get("incident_name", "Unknown")
        print(f"\nOID {oid}: {incident}")

        # TODO: Download attachments (same pattern as email_pilot.py)
        # TODO: Upload to NIFC FTP (blocked on credentials)
        # TODO: Email to wider distribution list
        # TODO: Update status to 'distributed'

        print(f"  PLACEHOLDER: Distribution not yet implemented.")
        print(f"  Waiting on NIFC FTP credentials (FAMAuth change).")

    print(f"\nDone: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()
