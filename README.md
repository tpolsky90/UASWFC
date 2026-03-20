# UASWFC Distribution Pipeline

Event-driven pipeline for the UAS Wildland Fire Collaborative IR deliverable system.
Handles the post-processing workflow: notifying pilots, collecting approvals, and distributing deliverable packages.

## Architecture

```
Pilot submits Survey123
  → Survey123 webhook
    → Google Apps Script
      → GitHub Actions: executes AGOL Notebook
        → Notebook generates deliverables, sets "awaiting_approval"
          → AGOL feature layer webhook
            → Google Apps Script
              → GitHub Actions: downloads attachments, emails pilot
                → Pilot reviews map PDF, clicks Approve in email
                  → Google Apps Script: updates AGOL status to "approved"
                    → AGOL feature layer webhook
                      → GitHub Actions: distributes to FTP + wider list
```

## Repository Structure

```
.github/workflows/
    pipeline.yml           # GitHub Actions workflow (3 jobs: notebook, email, distribute)

scripts/
    execute_notebook.py    # Triggers AGOL Notebook execution via REST API
    email_pilot.py         # Downloads deliverables, emails pilot with approve/revision links
    distribute.py          # Distributes approved packages to FTP + email (placeholder)

google_apps_script/
    webhook_handler.gs     # Central webhook hub (deploy to Google Apps Script)
```

## Setup

### 1. GitHub Secrets

| Secret | Description |
|--------|-------------|
| `AGOL_USERNAME` | ArcGIS Online username |
| `AGOL_PASSWORD` | ArcGIS Online password |
| `GMAIL_APP_PASSWORD` | Gmail app password for uaswfc@gmail.com |
| `PAT_TOKEN` | GitHub Personal Access Token (fine-grained, repo Contents read/write) |
| `APPROVAL_SECRET` | Shared HMAC secret for signing approval links |
| `APPROVAL_SCRIPT_URL` | Deployed Google Apps Script web app URL (set after deploying) |
| `NOTEBOOK_ITEM_ID` | AGOL item ID for the processing Notebook (optional, for auto-trigger) |

### 2. Google Apps Script Deployment

1. Go to [script.google.com](https://script.google.com) (sign in with uaswfc@gmail.com)
2. Create a new project, name it "UASWFC Webhook Hub"
3. Paste the contents of `google_apps_script/webhook_handler.gs`
4. Go to Project Settings → Script Properties, add:
    - `GITHUB_PAT` → same as PAT_TOKEN secret
    - `GITHUB_REPO` → `tpolsky90/UASWFC`
    - `AGOL_USERNAME` → `polsky90`
    - `AGOL_PASSWORD` → your AGOL password
    - `APPROVAL_SECRET` → same value as GitHub APPROVAL_SECRET
    - `SURVEY_LAYER_URL` → `https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0`
5. Deploy → New Deployment → Web App
    - Execute as: Me
    - Who has access: Anyone
6. Copy the deployment URL
7. Add it as the `APPROVAL_SCRIPT_URL` GitHub secret

### 3. Survey123 Webhook

1. Go to survey123.arcgis.com → your survey → Settings → Webhooks
2. Name: "UASWFC Pipeline Trigger"
3. Payload URL: the Google Apps Script deployment URL (same as APPROVAL_SCRIPT_URL)
4. Trigger events: New record submitted
5. Event data: check "Submitted record" and "Server response"
6. Status: On

### 4. AGOL Feature Layer Webhook

1. Go to arcgis.com → Organization → Settings → Webhooks
2. Click Advanced Settings
3. Create a webhook on the Survey123 response feature layer
4. Event: FeaturesUpdated
5. Payload URL: the Google Apps Script deployment URL
6. This fires when the Notebook updates processing_status

## Processing Status Flow

```
pending → processing → awaiting_approval → pilot_notified → approved → distributed
                                                          ↘ revision_requested
```

## Fallback

The workflow runs on a 15-minute cron schedule in addition to webhook triggers.
If a webhook is missed, the scheduled run picks up any records in `awaiting_approval` status.
