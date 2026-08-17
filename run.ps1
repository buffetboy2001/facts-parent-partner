<#
.SYNOPSIS
Runs the installed FACTS Parent Partner tool for scheduled automation.
#>

param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.yaml')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Write-Error "Configuration file is missing: $ConfigPath"
    exit 1
}

 $toolBin = & uv tool dir --bin
 $executable = Join-Path $toolBin 'facts-parent-partner.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    Write-Error "FACTS Parent Partner is not installed. Run: uv tool install git+https://github.com/buffetboy2001/facts-parent-partner.git"
    exit 1
}

& $executable --config (Resolve-Path -LiteralPath $ConfigPath)
exit $LASTEXITCODE
