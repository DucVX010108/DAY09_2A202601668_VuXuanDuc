param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\output'),
    [string]$Destination = (Join-Path $PSScriptRoot '..\submission_output.zip'),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedNames = 1..50 | ForEach-Object { 'EC_{0:D3}.json' -f $_ }
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
$files = @(Get-ChildItem -LiteralPath $resolvedOutput -File)
$actualNames = @($files.Name | Sort-Object)
$comparison = Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames

if ($comparison) {
    throw "output/ must contain exactly EC_001.json through EC_050.json and no other files."
}

if (Test-Path -LiteralPath $Destination) {
    if (-not $Force) {
        throw "Destination already exists: $Destination. Re-run with -Force after reviewing it."
    }
    Remove-Item -LiteralPath $Destination -Force
}

$archiveInputs = $expectedNames | ForEach-Object { Join-Path $resolvedOutput $_ }
Compress-Archive -LiteralPath $archiveInputs -DestinationPath $Destination
Write-Host "Created $Destination with 50 output JSON files."

