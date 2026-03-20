/**
 * UASWFC Webhook Hub (Google Apps Script)
 * 
 * This script handles three things:
 * 1. POST from Survey123 webhook (new submission) → triggers GitHub Action to run Notebook
 * 2. POST from AGOL feature layer webhook (status changed) → triggers GitHub Action to email pilot
 * 3. GET from pilot clicking Approve/Revision link in email → updates AGOL status
 *
 * Deploy as: Web App → Execute as: Me → Access: Anyone
 *
 * Script Properties (set in Project Settings → Script Properties):
 *   GITHUB_PAT          - GitHub Personal Access Token (fine-grained, repo scope)
 *   GITHUB_REPO         - "tpolsky90/UASWFC"
 *   AGOL_USERNAME        - "polsky90"
 *   AGOL_PASSWORD        - AGOL account password
 *   APPROVAL_SECRET      - Shared secret for HMAC signing (matches GitHub secret)
 *   SURVEY_LAYER_URL     - "https://services3.arcgis.com/SLthvBvwSE65InmN/arcgis/rest/services/service_76ac8ff74c8644ccad5843dfbc61c6d8/FeatureServer/0"
 */


// =============================================================================
// CONFIGURATION
// =============================================================================

function getConfig() {
  var props = PropertiesService.getScriptProperties();
  return {
    githubPat: props.getProperty('GITHUB_PAT'),
    githubRepo: props.getProperty('GITHUB_REPO'),
    agolUsername: props.getProperty('AGOL_USERNAME'),
    agolPassword: props.getProperty('AGOL_PASSWORD'),
    approvalSecret: props.getProperty('APPROVAL_SECRET'),
    surveyLayerUrl: props.getProperty('SURVEY_LAYER_URL')
  };
}


// =============================================================================
// WEBHOOK RECEIVER (POST)
// =============================================================================

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    Logger.log("POST received: " + JSON.stringify(payload).substring(0, 500));
    
    // Determine source by checking payload structure
    if (payload.surveyId || payload.feature) {
      // Survey123 webhook payload
      Logger.log("Survey123 webhook detected");
      triggerGitHubDispatch("survey_submitted", {
        source: "survey123",
        timestamp: new Date().toISOString()
      });
      return ContentService.createTextOutput(JSON.stringify({status: "ok", action: "survey_submitted"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    if (payload.changesUrl || payload.layerId !== undefined) {
      // AGOL feature layer webhook payload
      Logger.log("AGOL feature layer webhook detected");
      triggerGitHubDispatch("processing_complete", {
        source: "agol_webhook",
        timestamp: new Date().toISOString()
      });
      return ContentService.createTextOutput(JSON.stringify({status: "ok", action: "processing_complete"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    // Unknown payload, log and acknowledge
    Logger.log("Unknown POST payload structure");
    return ContentService.createTextOutput(JSON.stringify({status: "ok", action: "unknown"}))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (err) {
    Logger.log("POST error: " + err.toString());
    return ContentService.createTextOutput(JSON.stringify({status: "error", message: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}


// =============================================================================
// APPROVAL HANDLER (GET)
// =============================================================================

function doGet(e) {
  try {
    var action = e.parameter.action;   // "approve" or "revision"
    var oid = e.parameter.oid;         // Survey123 feature ObjectID
    var sig = e.parameter.sig;         // HMAC signature
    var notes = e.parameter.notes || "";  // Optional revision notes
    
    // Validate required params
    if (!action || !oid || !sig) {
      return createHtmlResponse("Missing Parameters", 
        "This link appears to be incomplete. Please use the link from your email.", 
        "#dc3545");
    }
    
    // Verify HMAC signature
    var config = getConfig();
    var expectedSig = computeHmac(action + ":" + oid, config.approvalSecret);
    
    if (sig !== expectedSig) {
      Logger.log("Signature mismatch. Expected: " + expectedSig + " Got: " + sig);
      return createHtmlResponse("Invalid Link", 
        "This approval link could not be verified. It may have been tampered with or expired.", 
        "#dc3545");
    }
    
    // Get AGOL token
    var token = getAgolToken(config.agolUsername, config.agolPassword);
    if (!token) {
      return createHtmlResponse("Authentication Error", 
        "Could not connect to ArcGIS Online. Please try again or contact the system administrator.", 
        "#dc3545");
    }
    
    // Check current status before updating
    var currentStatus = getFeatureStatus(config.surveyLayerUrl, oid, token);
    if (currentStatus === "approved" || currentStatus === "distributed") {
      return createHtmlResponse("Already Processed", 
        "This submission (OID " + oid + ") has already been " + currentStatus + ". No action needed.", 
        "#17a2b8");
    }
    
    if (action === "approve") {
      // Update Survey123 record
      var success = updateFeatureStatus(config.surveyLayerUrl, oid, token, "approved", "Approved by pilot via email");
      
      if (success) {
        // Trigger distribution workflow
        triggerGitHubDispatch("submission_approved", {
          oid: oid,
          timestamp: new Date().toISOString()
        });
        
        return createHtmlResponse("Deliverables Approved!", 
          "Submission OID " + oid + " has been approved. The deliverable package will be distributed to the incident team shortly.", 
          "#28a745");
      } else {
        return createHtmlResponse("Update Failed", 
          "Could not update the submission status. Please try again or contact the system administrator.", 
          "#dc3545");
      }
      
    } else if (action === "revision") {
      var revisionNotes = notes || "Revision requested by pilot (no details provided)";
      var success = updateFeatureStatus(config.surveyLayerUrl, oid, token, "revision_requested", revisionNotes);
      
      if (success) {
        return createHtmlResponse("Revision Requested", 
          "Submission OID " + oid + " has been flagged for revision. The processing team has been notified.", 
          "#ffc107");
      } else {
        return createHtmlResponse("Update Failed", 
          "Could not update the submission status. Please try again or contact the system administrator.", 
          "#dc3545");
      }
      
    } else {
      return createHtmlResponse("Unknown Action", 
        "The action '" + action + "' is not recognized. Please use the links from your email.", 
        "#dc3545");
    }
    
  } catch (err) {
    Logger.log("GET error: " + err.toString());
    return createHtmlResponse("Error", 
      "An unexpected error occurred: " + err.toString(), 
      "#dc3545");
  }
}


// =============================================================================
// GITHUB DISPATCH
// =============================================================================

function triggerGitHubDispatch(eventType, clientPayload) {
  var config = getConfig();
  var url = "https://api.github.com/repos/" + config.githubRepo + "/dispatches";
  
  var options = {
    method: "post",
    contentType: "application/json",
    headers: {
      "Authorization": "Bearer " + config.githubPat,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28"
    },
    payload: JSON.stringify({
      event_type: eventType,
      client_payload: clientPayload || {}
    }),
    muteHttpExceptions: true
  };
  
  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();
  Logger.log("GitHub dispatch '" + eventType + "' response: " + code);
  
  if (code !== 204) {
    Logger.log("GitHub dispatch failed: " + response.getContentText());
  }
  
  return code === 204;
}


// =============================================================================
// AGOL HELPERS
// =============================================================================

function getAgolToken(username, password) {
  var url = "https://www.arcgis.com/sharing/rest/generateToken";
  var options = {
    method: "post",
    payload: {
      username: username,
      password: password,
      referer: "https://www.arcgis.com",
      f: "json"
    },
    muteHttpExceptions: true
  };
  
  var response = UrlFetchApp.fetch(url, options);
  var data = JSON.parse(response.getContentText());
  
  if (data.token) {
    return data.token;
  }
  Logger.log("AGOL token error: " + JSON.stringify(data));
  return null;
}


function getFeatureStatus(layerUrl, oid, token) {
  var url = layerUrl + "/query";
  var options = {
    method: "post",
    payload: {
      where: "objectid = " + oid,
      outFields: "processing_status",
      f: "json",
      token: token
    },
    muteHttpExceptions: true
  };
  
  var response = UrlFetchApp.fetch(url, options);
  var data = JSON.parse(response.getContentText());
  
  if (data.features && data.features.length > 0) {
    return data.features[0].attributes.processing_status;
  }
  return null;
}


function updateFeatureStatus(layerUrl, oid, token, status, notes) {
  var url = layerUrl + "/updateFeatures";
  var feature = {
    attributes: {
      objectid: parseInt(oid),
      processing_status: status,
      processing_notes: (notes || "").substring(0, 1000)
    }
  };
  
  var options = {
    method: "post",
    payload: {
      features: JSON.stringify([feature]),
      f: "json",
      token: token
    },
    muteHttpExceptions: true
  };
  
  var response = UrlFetchApp.fetch(url, options);
  var data = JSON.parse(response.getContentText());
  Logger.log("Update response: " + JSON.stringify(data));
  
  if (data.updateResults && data.updateResults.length > 0) {
    return data.updateResults[0].success === true;
  }
  return false;
}


// =============================================================================
// HMAC SIGNATURE
// =============================================================================

function computeHmac(message, secret) {
  var signature = Utilities.computeHmacSha256Signature(message, secret);
  // Convert byte array to hex string
  return signature.map(function(byte) {
    return ('0' + (byte & 0xFF).toString(16)).slice(-2);
  }).join('');
}


// =============================================================================
// HTML RESPONSE BUILDER
// =============================================================================

function createHtmlResponse(title, message, color) {
  var html = '<!DOCTYPE html>'
    + '<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    + '<title>UASWFC ' + title + '</title>'
    + '<style>'
    + 'body { font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #f5f5f5; }'
    + '.card { background: white; border-radius: 12px; padding: 48px; max-width: 500px; text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,0.1); }'
    + '.icon { font-size: 64px; margin-bottom: 16px; }'
    + 'h1 { color: ' + color + '; margin: 0 0 16px 0; font-size: 24px; }'
    + 'p { color: #555; line-height: 1.6; margin: 0; }'
    + '.footer { margin-top: 24px; font-size: 12px; color: #999; }'
    + '</style></head><body>'
    + '<div class="card">'
    + '<div class="icon">' + (color === '#28a745' ? '✅' : color === '#ffc107' ? '🔄' : color === '#17a2b8' ? 'ℹ️' : '❌') + '</div>'
    + '<h1>' + title + '</h1>'
    + '<p>' + message + '</p>'
    + '<p class="footer">UAS Wildland Fire Collaborative</p>'
    + '</div></body></html>';
  
  return HtmlService.createHtmlOutput(html)
    .setTitle('UASWFC ' + title);
}


// =============================================================================
// TEST FUNCTIONS (run manually in Apps Script editor)
// =============================================================================

function testDispatch() {
  triggerGitHubDispatch("test_ping", {message: "Hello from Apps Script", timestamp: new Date().toISOString()});
}

function testAgolToken() {
  var config = getConfig();
  var token = getAgolToken(config.agolUsername, config.agolPassword);
  Logger.log("Token: " + (token ? "SUCCESS (" + token.substring(0, 20) + "...)" : "FAILED"));
}

function testHmac() {
  var config = getConfig();
  var sig = computeHmac("approve:42", config.approvalSecret);
  Logger.log("HMAC test: " + sig);
}
