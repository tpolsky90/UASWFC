"""
UASWFC Execute Notebook Script
Runs in GitHub Actions. Triggered by Survey123 webhook (new submission).
Calls the AGOL REST API to execute the processing Notebook.

Environment variables (from GitHub Secrets):
    AGOL_USERNAME, AGOL_PASSWORD

IMPORTANT: You need to set NOTEBOOK_ITEM_ID below to your Notebook's item ID.
To find it: go to arcgis.com, open your Notebook item page, the item ID is in the URL.
"""

import os
import sys
import time
import requests
from datetime import datetime


# =============================================================================
# CONFIGURATION
# =============================================================================

AGOL_USERNAME = os.environ["AGOL_USERNAME"]
AGOL_PASSWORD = os.environ["AGOL_PASSWORD"]

# TODO: Replace with your actual Notebook item ID
# Find it: arcgis.com → Content → find your UASWFC Notebook → item ID is in the URL
NOTEBOOK_ITEM_ID = os.environ.get("NOTEBOOK_ITEM_ID", "REPLACE_WITH_YOUR_NOTEBOOK_ITEM_ID")

TOKEN_URL = "https://www.arcgis.com/sharing/rest/generateToken"
EXECUTE_URL = f"https://www.arcgis.com/sharing/rest/content/items/{NOTEBOOK_ITEM_ID}/execute"

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


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("UASWFC Execute Notebook")
    print(f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Notebook Item ID: {NOTEBOOK_ITEM_ID}")
    print("=" * 60)

    if "REPLACE" in NOTEBOOK_ITEM_ID:
        print("\nERROR: NOTEBOOK_ITEM_ID is not set.")
        print("Go to arcgis.com → Content → open your UASWFC Notebook")
        print("Copy the item ID from the URL and either:")
        print("  1. Add it as a GitHub secret named NOTEBOOK_ITEM_ID")
        print("  2. Or hardcode it in this script")
        sys.exit(1)

    token = get_agol_token()

    # Execute the notebook
    print("\nExecuting notebook...")
    resp = requests.post(EXECUTE_URL, data={
        "f": "json",
        "token": token
    })

    data = resp.json()
    print(f"Execute response: {data}")

    if "error" in data:
        print(f"\nERROR: Notebook execution failed: {data['error']}")
        sys.exit(1)

    # The execute endpoint may return immediately with a job ID
    # or it may run synchronously depending on the notebook configuration
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

    print(f"\nDone: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()
