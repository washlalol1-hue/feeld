# Pure Dating App - Automated JWT Token Extractor (FINAL FIXED)
# Works with GeeLark "already logged" message

param(
    [string]$DeviceId = "23.236.115.68:20166",
    [string]$LoginCode = "f94b7f"
)

$ErrorActionPreference = "SilentlyContinue"

# === Use full path to adb.exe ===
$ADB = ".\adb.exe"

# --- Find device ---
if (-not $DeviceId) {
    $devices = & $ADB devices 2>&1 | Select-String "device$"
    if ($devices) {
        $DeviceId = ($devices[0] -split "\s+")[0]
        Write-Host "[*] Using device: $DeviceId"
    } else {
        Write-Host "[-] No device found."
        exit 1
    }
}

# --- Login if code provided ---
if ($LoginCode) {
    Write-Host "[*] Running glogin with code: $LoginCode"
    $loginResult = & $ADB -s $DeviceId shell "glogin $LoginCode" 2>&1

    if ($loginResult -match "success" -or $loginResult -match "already logged") {
        Write-Host "[+] glogin OK (already logged in)"
    } else {
        Write-Host "[-] glogin failed: $loginResult"
        exit 1
    }
}

# --- Check root access ---
$rootTest = & $ADB -s $DeviceId shell "su -c 'id'" 2>&1
if ($rootTest -notmatch "uid=0") {
    Write-Host "[-] Root access not available"
    exit 1
}
Write-Host "[+] Root access confirmed"

# --- Force restart app to trigger fresh token ---
Write-Host "[*] Restarting Pure app..."
& $ADB -s $DeviceId shell "am force-stop com.getpure.pure" 2>&1 | Out-Null
Start-Sleep -Seconds 1
& $ADB -s $DeviceId shell "monkey -p com.getpure.pure -c android.intent.category.LAUNCHER 1" 2>&1 | Out-Null
Write-Host "[*] Waiting for app to start and make network calls (8 seconds)..."
Start-Sleep -Seconds 8

# --- Read today's log ---
$today = (Get-Date).ToString("dd_MM_yyyy")
$logFile = "/data/data/com.getpure.pure/files/daily/${today}.txt"

Write-Host "[*] Reading log file: $logFile"
$logContent = & $ADB -s $DeviceId shell "su -c 'cat $logFile'" 2>&1

if (-not $logContent) {
    Write-Host "[-] Log file empty or not found"
    exit 1
}

$logText = $logContent -join "`n"

# --- Extract tokens ---
$accessToken = $null
$refreshToken = $null

# Method 1: From JSON response
if ($logText -match '"access_token":"(eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)"') {
    $accessToken = $Matches[1]
    Write-Host "[+] Access token extracted (from refresh response)"
}

if ($logText -match '"refresh_token":"(eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)"') {
    $refreshToken = $Matches[1]
    Write-Host "[+] Refresh token extracted"
}

# Method 2: From Authorization header (fallback)
if (-not $accessToken) {
    $headerMatches = [regex]::Matches($logText, 'Authorization: Bearer (eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*)')
    if ($headerMatches.Count -gt 0) {
        $accessToken = $headerMatches[$headerMatches.Count - 1].Groups[1].Value
        Write-Host "[+] Access token extracted from Authorization header"
    }
}

if (-not $accessToken) {
    Write-Host "[-] No JWT found in log."
    Write-Host "[-] Open the Pure app manually, wait 10 seconds, then re-run this script."
    exit 1
}

# --- Token info ---
try {
    $parts = $accessToken.Split(".")
    $payload = $parts[1]
    $payload = $payload.Replace("-", "+").Replace("_", "/")
    switch ($payload.Length % 4) {
        2 { $payload += "==" }
        3 { $payload += "=" }
    }
    $decoded = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload))
    $json = $decoded | ConvertFrom-Json
    $expiry = [DateTimeOffset]::FromUnixTimeSeconds($json.exp).DateTime
    $remaining = $expiry - [DateTime]::UtcNow

    Write-Host ""
    Write-Host "=== TOKEN INFO ==="
    Write-Host "User ID : $($json.user_id)"
    Write-Host "Email   : $($json.email)"
    Write-Host "Expires : $expiry UTC ($([math]::Round($remaining.TotalMinutes)) minutes left)"
    Write-Host ""
} catch {}

# --- Output ---
Write-Host "============================================"
Write-Host "ACCESS TOKEN (Bearer):"
Write-Host "============================================"
Write-Host $accessToken
Write-Host ""

if ($refreshToken) {
    Write-Host "============================================"
    Write-Host "REFRESH TOKEN:"
    Write-Host "============================================"
    Write-Host $refreshToken
    Write-Host ""
}

# --- Save files ---
$outputFile = Join-Path $PSScriptRoot "jwt_output.json"
@{
    bearerToken   = $accessToken
    refreshToken  = $refreshToken
    extracted_at  = (Get-Date -Format "o")
} | ConvertTo-Json | Out-File -FilePath $outputFile -Encoding utf8

Write-Host "[+] Tokens saved to: $outputFile"

Write-Host ""
Write-Host "=== PASTE THIS INTO THE DASHBOARD ==="
@{ bearerToken = $accessToken } | ConvertTo-Json -Compress