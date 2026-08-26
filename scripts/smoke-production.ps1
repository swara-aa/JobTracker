[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $BackendUrl,
    [string] $FrontendUrl = "",
    [SecureString] $AccessPassword,
    [SecureString] $ExtensionImportToken,
    [switch] $SkipAuthenticatedPages
)

$ErrorActionPreference = "Stop"

function Get-PlainText([SecureString] $Secret) {
    if ($null -eq $Secret) { return "" }
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Assert-Status(
    [string] $Name,
    [string] $Uri,
    [int[]] $Expected,
    [Microsoft.PowerShell.Commands.WebRequestSession] $Session = $null
) {
    $parameters = @{
        Uri = $Uri
        TimeoutSec = 30
        MaximumRedirection = 0
        SkipHttpErrorCheck = $true
        ErrorAction = "SilentlyContinue"
    }
    if ($Session) { $parameters.WebSession = $Session }
    $response = Invoke-WebRequest @parameters
    if ($Expected -notcontains [int] $response.StatusCode) {
        throw "$Name returned HTTP $($response.StatusCode); expected $($Expected -join ', ')."
    }
    Write-Host "PASS $Name -> HTTP $($response.StatusCode)"
    return $response
}

$baseUrl = $BackendUrl.TrimEnd("/")
$accessPasswordText = Get-PlainText $AccessPassword
$extensionImportTokenText = Get-PlainText $ExtensionImportToken
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

$health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 30
if ($health.status -ne "ok") { throw "Health check did not return status=ok." }
Write-Host "PASS health -> $($health.status) release=$($health.release)"

Assert-Status "unauthenticated dashboard guard" "$baseUrl/" @(200, 302) | Out-Null

if (-not $SkipAuthenticatedPages -and $accessPasswordText) {
    $login = Invoke-WebRequest -Uri "$baseUrl/login" -Method Post -WebSession $session -Body @{ password = $accessPasswordText; next = "/" } -MaximumRedirection 0 -SkipHttpErrorCheck -ErrorAction SilentlyContinue
    if ([int] $login.StatusCode -ne 302) { throw "Login returned HTTP $($login.StatusCode); expected 302." }
    Write-Host "PASS login -> HTTP 302"
    Assert-Status "dashboard" "$baseUrl/" @(200) $session | Out-Null
    Assert-Status "jobs radar" "$baseUrl/jobs?posted_within=week&sort=match&minimum_score=60" @(200) $session | Out-Null
    Assert-Status "resume library" "$baseUrl/resumes" @(200) $session | Out-Null
    Assert-Status "operations" "$baseUrl/operations" @(200) $session | Out-Null
    Assert-Status "LinkedIn import page" "$baseUrl/linkedin-review" @(200) $session | Out-Null
}
elseif (-not $SkipAuthenticatedPages) {
    Write-Host "SKIP authenticated pages -> provide -AccessPassword to test login pages."
}

if ($extensionImportTokenText) {
    $headers = @{ Authorization = "Bearer $extensionImportTokenText" }
    $importResponse = Invoke-RestMethod -Uri "$baseUrl/api/linkedin/import?defer_enrichment=1" -Method Post -Headers $headers -ContentType "application/json" -Body "[]" -TimeoutSec 30
    if ($null -eq $importResponse.saved -or $null -eq $importResponse.total) {
        throw "Extension import smoke test did not return saved/total fields."
    }
    Write-Host "PASS extension import endpoint -> saved=$($importResponse.saved) total=$($importResponse.total)"
}
else {
    Write-Host "SKIP extension import endpoint -> provide -ExtensionImportToken to test token auth."
}

if ($FrontendUrl) {
    Assert-Status "frontend" $FrontendUrl.TrimEnd("/") @(200) | Out-Null
}

Write-Host "Production smoke tests passed."
