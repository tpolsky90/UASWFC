"""
UASWFC Execute Notebook Script
Runs in GitHub Actions. Triggered by webhook (new submission) or 5-min cron.
Checks for pending records first, then calls AGOL REST API to execute the
processing Notebook only if there's work to do.

Environment variables (from GitHub Secrets):
    AGOL_USERNAME, AGOL_PASSWORD, NOTEBOOK_ITEM_ID
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone


# =============================================================================
# CONFIGURATION
# =============================================================================

AGOL_USERNAME = os.environ["AGOL_USERNAME"]
AGOL_PASSWORD = os.environ["AGOL_PASSWORD"]
NOTEBOOK_ITEM_ID = os.environ.get("NOTEBOOK_ITEM_ID", "")

TOKEN_URL = "https://www.arcgis.com/sharing/rest/generateToken"

SURVEY_LAYER_URL = (
    "https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/"
    "service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0"
)

# How long to wait for notebook completion (seconds)
MAX_WAIT = 600  # 10 minutes
POLL_INTERVAL = 15  # Check every 15 seconds


# =============================================================================
# AGOL HELPERS
# =============================================================================

def get_agol_token():
    """Authenticate to AGOL and return a token."""
    resp = requests.post(TOKEN_URL, data={
        "username": AGOL_USERNAME,
        "password": AGOL_PASSWORD,
        "referer": "https://www.arcgis.com",
        "f": "json"
    })
    data = resp.json()
    if "token" in data:
        print(f"[AUTH] Token acquired")
        return data["token"]
    else:
        print(f"[AUTH] FAILED: {data}")
        sys.exit(1)


def check_pending_records(token):
    """Query for pending records. Returns count."""
    resp = requests.post(f"{SURVEY_LAYER_URL}/query", data={
        "where": "processing_status = 'pending'",
        "returnCountOnly": "true",
        "f": "json",
        "token": token
    })
    data = resp.json()
    return data.get("count", 0)


# =============================================================================
# MAIN
# =============================================================================

def main():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    print("=" * 60)
    print("UASWFC Execute Notebook")
    print(f"Run: {now}")
    print("=" * 60)

    if not NOTEBOOK_ITEM_ID:
        print("\nERROR: NOTEBOOK_ITEM_ID is not set.")
        print("Add it as a GitHub secret: Settings > Secrets > NOTEBOOK_ITEM_ID")
        print("Find it: arcgis.com > Content > open your UASWFC Notebook > item ID in URL")
        sys.exit(1)

    print(f"Notebook Item ID: {NOTEBOOK_ITEM_ID}")

    token = get_agol_token()

    # Check if there are pending records before burning Notebook credits
    pending_count = check_pending_records(token)
    print(f"\nPending submissions: {pending_count}")

    if pending_count == 0:
        print("Nothing to process. Done.")
        return

    print(f"\n{pending_count} pending record(s) found. Executing notebook...")

    # Execute the notebook
    execute_url = f"https://www.arcgis.com/sharing/rest/content/items/{NOTEBOOK_ITEM_ID}/execute"
    resp = requests.post(execute_url, data={
        "f": "json",
        "token": token
    })

    data = resp.json()
    print(f"Execute response: {data}")

    if "error" in data:
        print(f"\nERROR: Notebook execution failed: {data['error']}")
        sys.exit(1)

    if data.get("success"):
        print("\nNotebook execution triggered successfully.")

    # If there's a job ID, poll for completion
    job_id = data.get("jobId")
    if job_id:
        print(f"Job ID: {job_id}")
        print(f"Polling for completion (max {MAX_WAIT}s)...")

        elapsed = 0
        while elapsed < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            status_resp = requests.get(
                f"https://www.arcgis.com/sharing/rest/content/items/{NOTEBOOK_ITEM_ID}/jobs/{job_id}",
                params={"f": "json", "token": token}
            )
            status_data = status_resp.json()
            job_status = status_data.get("status", "unknown")
            print(f"  [{elapsed}s] Job status: {job_status}")

            if job_status in ("succeeded", "completed"):
                print("\nNotebook completed successfully!")
                return
            elif job_status in ("failed", "cancelled"):
                print(f"\nNotebook {job_status}: {status_data}")
                sys.exit(1)

        print(f"\nWARNING: Notebook did not complete within {MAX_WAIT}s.")
        print("The notebook may still be running. The email pipeline will pick up")
        print("the results when the status changes to 'awaiting_approval'.")
    else:
        print("\nNo job ID returned. Notebook may have executed synchronously.")
        print("The email pipeline will pick up results on the next poll cycle.")

    print(f"\nDone: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()
