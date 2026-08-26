[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $AppName,
    [string] $Location = "eastus",
    [ValidateSet("AppService", "FunctionApp")] [string] $BackendMode = "AppService",
    [string] $BackendUrl = "",
    [string] $BackendAppName = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$FrontendAppName = "$AppName-web"
$TargetBackendAppName = if ($BackendAppName) { $BackendAppName } else { "$AppName-api" }

function Require-Command([string] $Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is required. Install it, sign in with 'az login', then rerun this script."
    }
}

Require-Command "az"
Require-Command "npm"
if ($BackendMode -ne "AppService") {
    throw "This project deploys its Flask backend with App Service; use BackendMode AppService."
}
if ($FrontendAppName.Length -ge 41 -or $FrontendAppName -notmatch "^[a-zA-Z0-9-]+$") {
    throw "AppName must produce a valid Static Web App name shorter than 41 characters."
}

& az account show --only-show-errors | Out-Null
& az group show --name $ResourceGroup --only-show-errors 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & az group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed to create resource group $ResourceGroup." }
}
if (-not (& az staticwebapp show --resource-group $ResourceGroup --name $FrontendAppName --only-show-errors 2>$null)) {
    & az staticwebapp create --resource-group $ResourceGroup --name $FrontendAppName --location $Location --sku Free --only-show-errors | Out-Null
}

$resolvedBackendUrl = if ($BackendUrl) { $BackendUrl.TrimEnd("/") } else { "https://$TargetBackendAppName.azurewebsites.net" }
$originalApiUrl = $env:VITE_API_URL
try {
    $env:VITE_API_URL = $resolvedBackendUrl
    Push-Location $FrontendRoot
    npm install --no-audit --no-fund
    npm run build
    $deploymentToken = (& az staticwebapp secrets list --resource-group $ResourceGroup --name $FrontendAppName --query "properties.apiKey" --output tsv).Trim()
    if (-not $deploymentToken) { throw "Azure did not return a Static Web Apps deployment token." }
    $env:SWA_CLI_DEPLOYMENT_TOKEN = $deploymentToken
    npx --yes @azure/static-web-apps-cli@2.0.6 deploy ./dist --deployment-token $env:SWA_CLI_DEPLOYMENT_TOKEN --env production
}
finally {
    Pop-Location
    $env:VITE_API_URL = $originalApiUrl
    Remove-Item Env:SWA_CLI_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
    $deploymentToken = $null
}

$frontendHost = (& az staticwebapp show --resource-group $ResourceGroup --name $FrontendAppName --query "defaultHostname" --output tsv).Trim()
if (-not $frontendHost) { throw "Azure did not return the Static Web Apps hostname." }
$frontendUrl = "https://$frontendHost"
& az webapp cors add --resource-group $ResourceGroup --name $TargetBackendAppName --allowed-origins $frontendUrl --only-show-errors | Out-Null

$healthy = $false
for ($attempt = 1; $attempt -le 12; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -TimeoutSec 20
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    }
    catch { Start-Sleep -Seconds 5 }
}
if (-not $healthy) { throw "Frontend deployment completed but $frontendUrl did not return HTTP 200." }

Write-Host "Frontend deployed: $frontendUrl"
Write-Host "Backend CORS allows: $frontendUrl"
