<#!
.SYNOPSIS
Runs the FACTS Parent Partner automation with the local authentication file.
#>

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

foreach ($requiredFile in @('.env', 'config.yaml', 'main.py')) {
    $path = Join-Path $projectRoot $requiredFile
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Error "Required file is missing: $path"
        exit 1
    }
}

Push-Location $projectRoot
try {
    & uv run --env-file .env python main.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
