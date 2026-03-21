"""
UASWFC Execute Notebook Script
Runs in GitHub Actions. Triggered by webhook (new submission) or 5-min cron.
Checks for pending records first, then calls AGOL Notebook Server REST API
to execute the processing Notebook.

Environment variables (from GitHub Secrets):
    AGOL_USERNAME, AGOL_PASSWORD, NOTEBOOK_ITEM_ID, PAT_TOKEN
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
PAT_TOKEN = os.environ.get("PAT_TOKEN", "")

TOKEN_URL = "https://www.arcgis.com/sharing/rest/generateToken"

SURVEY_LAYER_URL = (
    "https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/"
    "service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0"
)

# AGOL Notebook Server execute endpoint (discovered from notebook_server._url)
NOTEBOOK_EXECUTE_URL = "https://notebooksservices3.arcgis.com/admin/notebooks/executeNotebook"

# How long to wait for notebook completion (seconds)
MAX_WAIT = 600  # 10 minutes
POLL_INTERVAL = 15  # Check every 15 seconds


# =============================================================================
# AGOL HELPERS
# =============================================================================

def get_agol_token():
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
    resp = requests.post(f"{SURVEY_LAYER_URL}/query", data={
        "where": "processing_status = 'pending'",
        "returnCountOnly": "true",
        "f": "json",
        "token": token
    })
    data = resp.json()
    return data.get("count", 0)


def fire_dispatch(event_type):
    if not PAT_TOKEN:
        print(f"  WARNING: PAT_TOKEN not set, cannot fire {event_type} dispatch")
        return
    resp = requests.post(
        "https://api.github.com/repos/tpolsky90/UASWFC/dispatches",
        headers={
            "Authorization": f"Bearer {PAT_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        },
        json={
            "event_type": event_type,
            "client_payload": {"source": "execute_notebook", "timestamp": datetime.now(timezone.utc).isoformat()}
        }
    )
    print(f"  Dispatch '{event_type}': {resp.status_code}")


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

    # Execute the notebook via Notebook Server REST API
    resp = requests.post(NOTEBOOK_EXECUTE_URL, data={
        "itemId": NOTEBOOK_ITEM_ID,
        "updatePortalItem": "false",
        "f": "json",
        "token": token
    })

    data = resp.json()
    print(f"Execute response status: {resp.status_code}")
    print(f"Execute response keys: {list(data.keys())}")

    if "error" in data:
        print(f"\nERROR: Notebook execution failed: {data['error']}")
        sys.exit(1)

    # Check for job ID to poll
    job_id = data.get("jobId") or data.get("jobid") or data.get("id")
    job_url = data.get("jobUrl") or data.get("statusUrl")

    if job_id or job_url:
        print(f"Job ID: {job_id}")
        print(f"Job URL: {job_url}")
        print(f"Polling for completion (max {MAX_WAIT}s)...")

        poll_url = job_url or f"{NOTEBOOK_EXECUTE_URL}/jobs/{job_id}"

        elapsed = 0
        while elapsed < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            try:
                status_resp = requests.get(
                    poll_url,
                    params={"f": "json", "token": token}
                )
                status_data = status_resp.json()
                job_status = status_data.get("status", status_data.get("jobStatus", "unknown"))
                print(f"  [{elapsed}s] Job status: {job_status}")

                if job_status.lower() in ("succeeded", "completed", "esrijobsucceeded"):
                    print("\nNotebook completed successfully!")
                    fire_dispatch("processing_complete")
                    return
                elif job_status.lower() in ("failed", "cancelled", "esrijobfailed"):
                    print(f"\nNotebook {job_status}: {status_data}")
                    sys.exit(1)
            except Exception as e:
                print(f"  [{elapsed}s] Poll error: {e}")

        print(f"\nWARNING: Notebook did not complete within {MAX_WAIT}s.")
        print("Firing processing_complete anyway (cron will catch email).")
        fire_dispatch("processing_complete")

    else:
        # No job ID means it may have run synchronously
        print(f"\nFull response: {data}")
        print("No job ID returned. Notebook may have executed synchronously.")
        print("Firing processing_complete dispatch.")
        fire_dispatch("processing_complete")

    print(f"\nDone: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()
