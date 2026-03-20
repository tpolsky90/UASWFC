"""
UASWFC Email Pilot Script
Runs in GitHub Actions. Queries AGOL for records with processing_status = 'awaiting_approval',
downloads all deliverable attachments, and emails them to the pilot with approve/revision links.

Approval links point to a static GitHub Pages site that calls AGOL REST API directly
from the browser using a short-lived token embedded in the URL.

Environment variables (from GitHub Secrets):
    AGOL_USERNAME, AGOL_PASSWORD, GMAIL_APP_PASSWORD
"""

import os
import sys
import json
import smtplib
import requests
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from urllib.parse import quote


# =============================================================================
# CONFIGURATION
# =============================================================================

AGOL_USERNAME = os.environ["AGOL_USERNAME"]
AGOL_PASSWORD = os.environ["AGOL_PASSWORD"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

GMAIL_FROM = "uaswfc@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

SURVEY_LAYER_URL = (
    "https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/"
    "service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0"
)

TOKEN_URL = "https://www.arcgis.com/sharing/rest/generateToken"

# GitHub Pages approval page URL
APPROVAL_PAGE_URL = "https://tpolsky90.github.io/UASWFC/approve.html"

# Token expiration for approval links (minutes)
APPROVAL_TOKEN_EXPIRATION = 60


# =============================================================================
# AGOL HELPERS
# =============================================================================

def get_agol_token(expiration_minutes=120):
    """Authenticate to AGOL and return a token with specified expiration."""
    resp = requests.post(TOKEN_URL, data={
        "username": AGOL_USERNAME,
        "password": AGOL_PASSWORD,
        "referer": "https://www.arcgis.com",
        "expiration": expiration_minutes,
        "f": "json"
    })
    data = resp.json()
    if "token" in data:
        print(f"[AUTH] Token acquired (expires in {expiration_minutes} min)")
        return data["token"]
    else:
        print(f"[AUTH] FAILED: {data}")
        sys.exit(1)


def query_features(token, where_clause, out_fields="*"):
    """Query the Survey123 response layer."""
    resp = requests.post(f"{SURVEY_LAYER_URL}/query", data={
        "where": where_clause,
        "outFields": out_fields,
        "f": "json",
        "token": token
    })
    data = resp.json()
    return data.get("features", [])


def get_attachments(token, oid):
    """Get list of attachments for a feature."""
    resp = requests.get(
        f"{SURVEY_LAYER_URL}/{oid}/attachments",
        params={"f": "json", "token": token}
    )
    data = resp.json()
    return data.get("attachmentInfos", [])


def download_attachment(token, oid, att_id, att_name, out_dir):
    """Download a single attachment to disk."""
    url = f"{SURVEY_LAYER_URL}/{oid}/attachments/{att_id}"
    resp = requests.get(url, params={"token": token}, stream=True)

    out_path = os.path.join(out_dir, att_name)
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size = os.path.getsize(out_path)
    print(f"  Downloaded: {att_name} ({size:,} bytes)")
    return out_path


def update_status(token, oid, status, notes=""):
    """Update processing_status and processing_notes on a feature."""
    feature = {
        "attributes": {
            "objectid": oid,
            "processing_status": status,
            "processing_notes": notes[:1000]
        }
    }
    resp = requests.post(f"{SURVEY_LAYER_URL}/updateFeatures", data={
        "features": json.dumps([feature]),
        "f": "json",
        "token": token
    })
    data = resp.json()
    results = data.get("updateResults", [])
    if results and results[0].get("success"):
        print(f"  Status updated to '{status}' for OID {oid}")
        return True
    else:
        print(f"  WARNING: Status update failed for OID {oid}: {data}")
        return False


# =============================================================================
# APPROVAL LINK GENERATION
# =============================================================================

def make_approval_url(oid, action, approval_token, incident_name):
    """Build an approval or revision URL pointing to GitHub Pages."""
    incident_encoded = quote(incident_name.strip())
    return (
        f"{APPROVAL_PAGE_URL}"
        f"?action={action}"
        f"&oid={oid}"
        f"&token={approval_token}"
        f"&incident={incident_encoded}"
    )


# =============================================================================
# EMAIL
# =============================================================================

def build_email_body(attrs, oid, approve_url, revision_url):
    """Build the HTML email body for the pilot."""
    incident = attrs.get("incident_name", "Unknown Fire")
    flight_date = ""
    if attrs.get("flight_date"):
        try:
            fd = attrs["flight_date"]
            if isinstance(fd, (int, float)):
                flight_date = datetime.utcfromtimestamp(fd / 1000).strftime("%Y-%m-%d")
            else:
                flight_date = str(fd)
        except Exception:
            flight_date = str(attrs.get("flight_date", ""))

    flight_time = attrs.get("flight_time", "")
    pilot_name = attrs.get("irin_name", "Pilot")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

<div style="background: #1a472a; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center;">
    <h1 style="margin: 0; font-size: 20px;">UAS Wildland Fire Collaborative</h1>
    <p style="margin: 8px 0 0 0; opacity: 0.9;">IR Deliverable Package Ready for Review</p>
</div>

<div style="background: #f8f9fa; padding: 24px; border: 1px solid #ddd;">

    <p>Hello {pilot_name},</p>

    <p>Your IR deliverable package for <strong>{incident}</strong> ({flight_date} {flight_time}) has been
    processed and is attached to this email.</p>

    <div style="background: white; border: 1px solid #ddd; border-radius: 6px; padding: 16px; margin: 20px 0;">
        <p style="margin: 0 0 8px 0; font-weight: bold;">Attached Files:</p>
        <p style="margin: 0; line-height: 1.8;">
            Map PDF (11x17 Topo, geospatial)<br>
            Shapefiles (NIROPS convention, zipped)<br>
            GDB (NIFS compatible, zipped)<br>
            KMZ (Google Earth, zipped)<br>
            IRN Log PDF (Interpreter's Daily Log)
        </p>
    </div>

    <p><strong>Please review the Map PDF carefully.</strong> Verify that the fire perimeter and heat sources
    are correctly positioned, and that the map info block is accurate. Then use one of the buttons below.</p>

    <div style="text-align: center; margin: 32px 0;">
        <a href="{approve_url}"
           style="display: inline-block; background: #28a745; color: white; text-decoration: none;
                  padding: 14px 32px; border-radius: 6px; font-size: 16px; font-weight: bold;
                  margin: 0 8px;">
            APPROVE DELIVERABLES
        </a>

        <a href="{revision_url}"
           style="display: inline-block; background: #dc3545; color: white; text-decoration: none;
                  padding: 14px 32px; border-radius: 6px; font-size: 16px; font-weight: bold;
                  margin: 0 8px;">
            REQUEST REVISION
        </a>
    </div>

    <p style="font-size: 13px; color: #666;">
        Approval links are valid for {APPROVAL_TOKEN_EXPIRATION} minutes. Once approved, the deliverable
        package will be automatically distributed to the incident team and uploaded to NIFC
        (when FTP credentials are available).
    </p>

</div>

<div style="background: #eee; padding: 12px; border-radius: 0 0 8px 8px; text-align: center; font-size: 11px; color: #999;">
    UASWFC Processing Engine v5 | Submission OID: {oid} | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
</div>

</body>
</html>"""

    return html


def send_email(to_address, subject, html_body, attachment_paths):
    """Send email with HTML body and file attachments via Gmail SMTP."""
    msg = MIMEMultipart("mixed")
    msg["From"] = GMAIL_FROM
    msg["To"] = to_address
    msg["Subject"] = subject

    # HTML body
    html_part = MIMEText(html_body, "html")
    msg.attach(html_part)

    # Attachments
    for fpath in attachment_paths:
        fname = os.path.basename(fpath)
        with open(fpath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
            msg.attach(part)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
        server.send_message(msg)

    print(f"  Email sent to {to_address} with {len(attachment_paths)} attachments")


# =============================================================================
# MAIN
# =============================================================================

def process_record(token, approval_token, feature):
    """Process a single awaiting_approval record: download attachments, email pilot."""
    attrs = feature["attributes"]
    oid = attrs["objectid"]
    incident = attrs.get("incident_name", "Unknown")
    pilot_email = attrs.get("irin_email", "")
    pilot_name = attrs.get("irin_name", "Pilot")

    print(f"\nProcessing OID {oid}: {incident}")

    if not pilot_email:
        print(f"  WARNING: No pilot email (irin_email) for OID {oid}. Skipping email.")
        update_status(token, oid, "error", "No pilot email address (irin_email) found on submission")
        return

    # Download all attachments
    attachments = get_attachments(token, oid)
    print(f"  Found {len(attachments)} attachments")

    # Filter to deliverable files only (skip the original NOVA zip upload)
    deliverable_keywords = ["_IR_11x17", "_IR.zip", "_IR_gdb", "_IR_KMZ", "_IRN_Log"]
    nova_keywords = ["nova", "NOVA"]

    deliverable_attachments = []
    for att in attachments:
        name = att["name"]
        is_nova = any(kw in name for kw in nova_keywords)
        is_deliverable = any(kw in name for kw in deliverable_keywords)

        if is_deliverable and not is_nova:
            deliverable_attachments.append(att)
        elif not is_nova:
            deliverable_attachments.append(att)

    if not deliverable_attachments:
        print(f"  WARNING: No deliverable attachments found for OID {oid}")
        update_status(token, oid, "error", "No deliverable attachments found on record")
        return

    print(f"  Deliverables to send: {[a['name'] for a in deliverable_attachments]}")

    # Download to temp dir
    tmp_dir = tempfile.mkdtemp(prefix="uaswfc_")
    downloaded = []
    for att in deliverable_attachments:
        path = download_attachment(token, oid, att["id"], att["name"], tmp_dir)
        downloaded.append(path)

    # Check total size (Gmail limit ~25MB)
    total_size = sum(os.path.getsize(p) for p in downloaded)
    print(f"  Total attachment size: {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")

    if total_size > 24 * 1024 * 1024:
        print(f"  WARNING: Total size exceeds 24 MB. Gmail may reject this.")

    # Build approval links using short-lived token
    approve_url = make_approval_url(oid, "approve", approval_token, incident)
    revision_url = make_approval_url(oid, "revision", approval_token, incident)

    # Build and send email
    flight_date_str = ""
    if attrs.get("flight_date"):
        try:
            fd = attrs["flight_date"]
            if isinstance(fd, (int, float)):
                flight_date_str = datetime.utcfromtimestamp(fd / 1000).strftime("%Y%m%d")
        except Exception:
            pass

    subject = f"UASWFC IR Deliverables: {incident.strip()} {flight_date_str}"
    html_body = build_email_body(attrs, oid, approve_url, revision_url)

    try:
        send_email(pilot_email, subject, html_body, downloaded)
        update_status(token, oid, "pilot_notified",
                      f"Email sent to {pilot_email} at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    except Exception as e:
        print(f"  ERROR sending email: {e}")
        update_status(token, oid, "error", f"Email send failed: {str(e)}")

    # Clean up temp files
    for p in downloaded:
        try:
            os.remove(p)
        except Exception:
            pass
    try:
        os.rmdir(tmp_dir)
    except Exception:
        pass


def main():
    print("=" * 60)
    print("UASWFC Email Pilot Script")
    print(f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # Get a long-lived token for downloading attachments
    token = get_agol_token(expiration_minutes=120)

    # Get a short-lived token for the approval links (embedded in URL)
    approval_token = get_agol_token(expiration_minutes=APPROVAL_TOKEN_EXPIRATION)
    print(f"Approval token valid for {APPROVAL_TOKEN_EXPIRATION} minutes")

    # Query for awaiting_approval records
    features = query_features(token, "processing_status = 'awaiting_approval'")
    print(f"\nRecords awaiting approval: {len(features)}")

    if not features:
        print("Nothing to process. Done.")
        return

    for feature in features:
        try:
            process_record(token, approval_token, feature)
        except Exception as e:
            oid = feature["attributes"].get("objectid", "?")
            print(f"\nERROR processing OID {oid}: {e}")
            try:
                update_status(token, oid, "error", f"Email pipeline error: {str(e)}")
            except Exception:
                pass

    print(f"\nDone: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()
