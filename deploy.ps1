[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $AppName,
    [string] $Location = "eastus",
    [ValidateSet("AppService", "FunctionApp")] [string] $BackendMode = "AppService",
    [string] $BackendUrl = "",
    [string] $BackendAppName = "",
    [string] $StorageAccountName = "",
    [SecureString] $DatabaseUrl,
    [SecureString] $GeminiApiKey,
    [SecureString] $FlaskSecretKey,
    [SecureString] $AccessPassword,
    [SecureString] $ExtensionImportToken,
    [switch] $DisableEmbeddedAutomation,
    [switch] $SkipFrontend
)

$ErrorActionPreference = "Stop"
$backendScript = Join-Path $PSScriptRoot "02-backend.ps1"
$frontendScript = Join-Path $PSScriptRoot "03-frontend.ps1"
$backendParameters = @{
    ResourceGroup = $ResourceGroup
    AppName = $AppName
    Location = $Location
    BackendMode = $BackendMode
    BackendUrl = $BackendUrl
    StorageAccountName = $StorageAccountName
    DatabaseUrl = $DatabaseUrl
    GeminiApiKey = $GeminiApiKey
    FlaskSecretKey = $FlaskSecretKey
    AccessPassword = $AccessPassword
    ExtensionImportToken = $ExtensionImportToken
    DisableEmbeddedAutomation = $DisableEmbeddedAutomation
}

& $backendScript @backendParameters

if (-not $SkipFrontend) {
    $frontendParameters = @{
        ResourceGroup = $ResourceGroup
        AppName = $AppName
        Location = $Location
        BackendMode = $BackendMode
        BackendUrl = $BackendUrl
        BackendAppName = $BackendAppName
    }
    & $frontendScript @frontendParameters
}
