$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$bridgeDirectory = Join-Path $repositoryRoot "third_party\zhong_wmpf_bridge"

if (-not (Test-Path -LiteralPath (Join-Path $bridgeDirectory "package-lock.json"))) {
    throw "WMPF bridge source was not found: $bridgeDirectory"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js was not found. Install 64-bit Node.js and retry."
}

& npm.cmd ci --prefix $bridgeDirectory --no-audit --no-fund --foreground-scripts
if ($LASTEXITCODE -ne 0) {
    throw "WMPF bridge dependency installation failed: exit code=$LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath (Join-Path $bridgeDirectory "node_modules\frida\build\frida_binding.node"))) {
    throw "Frida native binding was not installed. Retry with npm.cmd ci --foreground-scripts."
}

Write-Output "WMPF bridge dependencies are ready: $bridgeDirectory"
