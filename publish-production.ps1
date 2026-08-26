[CmdletBinding()]
param(
    [string] $ResourceGroup = "jobtracker-paid-rg",
    [string] $AppName = "swara-jobtracker-live",
    [string] $Location = "centralus",
    [string] $BackendAppName = "swara-jobtracker-live-api",
    [string] $BackendUrl = "https://swara-jobtracker-live-api.azurewebsites.net",
    [string] $FrontendUrl = "",
    [SecureString] $DatabaseUrl,
    [SecureString] $GeminiApiKey,
    [SecureString] $FlaskSecretKey,
    [SecureString] $AccessPassword,
    [SecureString] $ExtensionImportToken,
    [switch] $SkipFrontend,
    [switch] $SkipSmoke
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

function Require-Command([string] $Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is required. Install it, sign in with 'az login', then rerun this script."
    }
}

function New-RandomToken {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function ConvertTo-Secure([string] $Value) {
    return ConvertTo-SecureString $Value -AsPlainText -Force
}

function Get-AzureText {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Arguments)
    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: az $($Arguments -join ' ')"
    }
    return ($result -join [Environment]::NewLine).Trim()
}

Require-Command "az"
Require-Command "pwsh"
if (-not $SkipFrontend) {
    Require-Command "npm"
}

& az account show --only-show-errors | Out-Null

if ($null -eq $ExtensionImportToken) {
    $existingToken = Get-AzureText @("webapp", "config", "appsettings", "list", "--resource-group", $ResourceGroup, "--name", $BackendAppName, "--query", "[?name=='JOBTRACKER_EXTENSION_IMPORT_TOKEN'].value | [0]", "--output", "tsv")
    if ($existingToken) {
        $ExtensionImportToken = ConvertTo-Secure $existingToken
    }
    else {
        $generatedToken = New-RandomToken
        $ExtensionImportToken = ConvertTo-Secure $generatedToken
        Write-Host "Generated a new extension import token for this App Service. Paste it into the Chrome extension connection settings:"
        Write-Host $generatedToken
    }
}

$backendParameters = @{
    ResourceGroup = $ResourceGroup
    AppName = $AppName
    Location = $Location
    BackendUrl = $BackendUrl
    DatabaseUrl = $DatabaseUrl
    GeminiApiKey = $GeminiApiKey
    FlaskSecretKey = $FlaskSecretKey
    AccessPassword = $AccessPassword
    ExtensionImportToken = $ExtensionImportToken
    DisableEmbeddedAutomation = $true
}

& (Join-Path $ProjectRoot "02-backend.ps1") @backendParameters

if (-not $SkipFrontend) {
    & (Join-Path $ProjectRoot "03-frontend.ps1") `
        -ResourceGroup $ResourceGroup `
        -AppName $AppName `
        -Location $Location `
        -BackendUrl $BackendUrl `
        -BackendAppName $BackendAppName
    if (-not $FrontendUrl) {
        $frontendHost = Get-AzureText @("staticwebapp", "show", "--resource-group", $ResourceGroup, "--name", "$AppName-web", "--query", "defaultHostname", "--output", "tsv")
        if ($frontendHost) { $FrontendUrl = "https://$frontendHost" }
    }
}

if (-not $SkipSmoke) {
    $smokeParameters = @{
        BackendUrl = $BackendUrl
        FrontendUrl = $FrontendUrl
        AccessPassword = $AccessPassword
        ExtensionImportToken = $ExtensionImportToken
    }
    & (Join-Path $ProjectRoot "scripts/smoke-production.ps1") @smokeParameters
}

Write-Host "Production publish completed."
Write-Host "Backend: $BackendUrl"
if ($FrontendUrl) { Write-Host "Frontend: $FrontendUrl" }
