param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Preset,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ClassesPath = Join-Path $ProjectRoot "Classes"

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$ClassesPath;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = $ClassesPath
}

Push-Location $ProjectRoot
try {
    & python -m maintainer_run $Preset @ExtraArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
