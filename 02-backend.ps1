[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $AppName,
    [string] $Location = "eastus",
    [ValidateSet("AppService", "FunctionApp")] [string] $BackendMode = "AppService",
    [string] $BackendUrl = "",
    [string] $StorageAccountName = "",
    [SecureString] $DatabaseUrl,
    [SecureString] $GeminiApiKey,
    [SecureString] $FlaskSecretKey,
    [SecureString] $AccessPassword,
    [SecureString] $ExtensionImportToken,
    [switch] $DisableEmbeddedAutomation
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$BackendAppName = "$AppName-api"
$PlanName = "$AppName-plan"
$FileShareName = "jobtracker-data"
$DataMountPath = "/mounts/jobtracker-data"

function Require-Command([string] $Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is required. Install it, sign in with 'az login', then rerun this script."
    }
}

function Get-PlainText([SecureString] $Secret) {
    if ($null -eq $Secret) { return "" }
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function New-RandomSecret {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes)
}

function Get-StorageName([string] $RequestedName, [string] $Seed) {
    if ($RequestedName) { return $RequestedName.ToLowerInvariant() }
    $prefix = ($AppName.ToLowerInvariant() -replace "[^a-z0-9]", "")
    if ($prefix.Length -gt 14) { $prefix = $prefix.Substring(0, 14) }
    if ($prefix.Length -lt 3) { $prefix = "jobtracker" }
    $hash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Seed))
    $suffix = -join ($hash[0..4] | ForEach-Object { $_.ToString("x2") })
    return "$prefix$suffix"
}

function Invoke-AzureCli {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Arguments)
    & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: az $($Arguments -join ' ')"
    }
}

function Get-AzureCliText {
    param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Arguments)
    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: az $($Arguments -join ' ')"
    }
    return ($result -join [Environment]::NewLine).Trim()
}

function Test-AzResource([string[]] $Arguments) {
    & az @Arguments --only-show-errors 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Ensure-AzureProvider([string] $Namespace, [string] $SubscriptionId) {
    $providerUrl = "https://management.azure.com/subscriptions/$SubscriptionId/providers/$Namespace`?api-version=2021-04-01"
    $state = Get-AzureCliText rest --method get --url $providerUrl --query registrationState --output tsv
    if ($state -ne "Registered") {
        Invoke-AzureCli provider register --namespace $Namespace --only-show-errors | Out-Null
        for ($attempt = 1; $attempt -le 30; $attempt++) {
            Start-Sleep -Seconds 5
            $state = Get-AzureCliText rest --method get --url $providerUrl --query registrationState --output tsv
            if ($state -eq "Registered") { return }
        }
        throw "Azure provider $Namespace is still not registered. Wait a few minutes, then rerun the script."
    }
}

function New-DeploymentZip {
    $stagingDirectory = Join-Path ([IO.Path]::GetTempPath()) ("jobtracker-" + [guid]::NewGuid())
    $archivePath = Join-Path ([IO.Path]::GetTempPath()) ("jobtracker-" + [guid]::NewGuid() + ".zip")
    New-Item -ItemType Directory -Path $stagingDirectory | Out-Null
    $excludedDirectories = @(".git", ".venv", "data", "frontend", "node_modules", "__pycache__", "htmlcov", "coverage")
    try {
        Get-ChildItem -LiteralPath $ProjectRoot -Force -Recurse -File | ForEach-Object {
            $relativePath = $_.FullName.Substring($ProjectRoot.Length).TrimStart("/", "\\")
            $hasExcludedDirectory = ($relativePath -split "[\\/]") | Where-Object { $excludedDirectories -contains $_ }
            if (
                $relativePath -eq ".env" -or
                $relativePath -like ".env.*" -or
                $hasExcludedDirectory
            ) {
                return
            }
            $destination = Join-Path $stagingDirectory $relativePath
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
        Compress-Archive -Path (Join-Path $stagingDirectory "*") -DestinationPath $archivePath -Force
        return @{ ArchivePath = $archivePath; StagingDirectory = $stagingDirectory }
    }
    catch {
        Remove-Item -LiteralPath $stagingDirectory -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        throw
    }
}

Require-Command "az"
if ($BackendMode -ne "AppService") {
    throw "This Flask + SQLite + background-worker app requires BackendMode AppService. Function App is intentionally not supported by this deployer."
}
if ($BackendAppName.Length -ge 64 -or $BackendAppName -notmatch "^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$") {
    throw "AppName must produce a valid App Service name shorter than 64 characters."
}

Invoke-AzureCli account show --only-show-errors | Out-Null
$subscriptionId = Get-AzureCliText account show --query id --output tsv
$StorageAccountName = Get-StorageName $StorageAccountName "$subscriptionId/$ResourceGroup/$AppName"
if ($StorageAccountName -notmatch "^[a-z0-9]{3,24}$") {
    throw "StorageAccountName must contain 3-24 lowercase letters and numbers."
}

Ensure-AzureProvider "Microsoft.Storage" $subscriptionId
Ensure-AzureProvider "Microsoft.Web" $subscriptionId

if (-not (Test-AzResource @("group", "show", "--name", $ResourceGroup))) {
    Invoke-AzureCli group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null
}
if (-not (Test-AzResource @("storage", "account", "show", "--resource-group", $ResourceGroup, "--name", $StorageAccountName))) {
    Invoke-AzureCli storage account create --resource-group $ResourceGroup --name $StorageAccountName --location $Location --sku Standard_LRS --kind StorageV2 --https-only true --allow-blob-public-access false --only-show-errors | Out-Null
}
Invoke-AzureCli storage share create --account-name $StorageAccountName --name $FileShareName --quota 50 --only-show-errors | Out-Null

if (-not (Test-AzResource @("appservice", "plan", "show", "--resource-group", $ResourceGroup, "--name", $PlanName))) {
    Invoke-AzureCli appservice plan create --resource-group $ResourceGroup --name $PlanName --location $Location --sku B1 --is-linux --only-show-errors | Out-Null
}
if (-not (Test-AzResource @("webapp", "show", "--resource-group", $ResourceGroup, "--name", $BackendAppName))) {
    Invoke-AzureCli webapp create --resource-group $ResourceGroup --plan $PlanName --name $BackendAppName --runtime "PYTHON:3.12" --startup-file "gunicorn --workers 1 --bind=0.0.0.0 --timeout 180 app:app" --only-show-errors | Out-Null
}

$storageKey = Get-AzureCliText storage account keys list --resource-group $ResourceGroup --account-name $StorageAccountName --query "[0].value" --output tsv
$mountExists = Get-AzureCliText webapp config storage-account list --resource-group $ResourceGroup --name $BackendAppName --query "[?name=='jobtrackerdata'].name | [0]" --output tsv
$mountArguments = @(
    "webapp", "config", "storage-account", $(if ($mountExists) { "update" } else { "add" }),
    "--resource-group", $ResourceGroup,
    "--name", $BackendAppName,
    "--custom-id", "jobtrackerdata",
    "--storage-type", "AzureFiles",
    "--share-name", $FileShareName,
    "--account-name", $StorageAccountName,
    "--access-key", $storageKey,
    "--mount-path", $DataMountPath,
    "--only-show-errors"
)
Invoke-AzureCli @mountArguments | Out-Null

$flaskSecret = Get-PlainText $FlaskSecretKey
if (-not $flaskSecret) { $flaskSecret = New-RandomSecret }
$geminiKey = Get-PlainText $GeminiApiKey
$databaseUrlText = Get-PlainText $DatabaseUrl
$accessPassword = Get-PlainText $AccessPassword
$extensionImportTokenText = Get-PlainText $ExtensionImportToken
$release = (Get-Content (Join-Path $ProjectRoot "release.json") -Raw | ConvertFrom-Json).version
$appSettings = @(
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true",
    "FLASK_SECRET_KEY=$flaskSecret",
    "JOBTRACKER_DATA_DIR=$DataMountPath",
    "JOBTRACKER_AUTOMATION_ENABLED=$(if ($DisableEmbeddedAutomation) { 'false' } else { 'true' })",
    "JOBTRACKER_RELEASE_VERSION=$release",
    "JOB_AGENT_ENABLE_LOCAL_EMBEDDINGS=0",
    "COMPANY_DATABASE_PATH=config/companies.csv"
)
if ($databaseUrlText) {
    $appSettings += "DATABASE_URL=$databaseUrlText"
    $appSettings += "JOBTRACKER_DATABASE_BACKEND=postgres"
}
if ($geminiKey) { $appSettings += "GEMINI_API_KEY=$geminiKey" }
if ($accessPassword) {
    $appSettings += "JOBTRACKER_AUTH_REQUIRED=true"
    $appSettings += "JOBTRACKER_ACCESS_PASSWORD=$accessPassword"
}
if ($extensionImportTokenText) { $appSettings += "JOBTRACKER_EXTENSION_IMPORT_TOKEN=$extensionImportTokenText" }
Invoke-AzureCli webapp config appsettings set --resource-group $ResourceGroup --name $BackendAppName --settings $appSettings --only-show-errors | Out-Null
Invoke-AzureCli webapp config set --resource-group $ResourceGroup --name $BackendAppName --always-on true --startup-file "gunicorn --workers 1 --bind=0.0.0.0 --timeout 180 app:app" --only-show-errors | Out-Null

$package = New-DeploymentZip
try {
    Invoke-AzureCli webapp deploy --resource-group $ResourceGroup --name $BackendAppName --src-path $package.ArchivePath --type zip --async false --only-show-errors | Out-Null
}
finally {
    Remove-Item -LiteralPath $package.StagingDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $package.ArchivePath -Force -ErrorAction SilentlyContinue
    $storageKey = $null
    $flaskSecret = $null
    $geminiKey = $null
    $databaseUrlText = $null
    $accessPassword = $null
    $extensionImportTokenText = $null
}

$resolvedBackendUrl = if ($BackendUrl) { $BackendUrl.TrimEnd("/") } else { "https://$BackendAppName.azurewebsites.net" }
$healthy = $false
for ($attempt = 1; $attempt -le 18; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$resolvedBackendUrl/api/health" -TimeoutSec 20
        if ($health.status -eq "ok") { $healthy = $true; break }
    }
    catch { Start-Sleep -Seconds 10 }
}
if (-not $healthy) { throw "Backend deployment completed but $resolvedBackendUrl/api/health did not return status=ok. Check 'az webapp log tail'." }

Write-Host "Backend deployed: $resolvedBackendUrl"
Write-Host "Health check: $resolvedBackendUrl/api/health"
