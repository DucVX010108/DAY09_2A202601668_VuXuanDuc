param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\output'),
    [string]$Destination = (Join-Path $PSScriptRoot '..\submission_output.zip'),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedNames = 1..50 | ForEach-Object { 'EC_{0:D3}.json' -f $_ }
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
# Compare every file, not just the .json ones: a stray such as submission.txt
# must fail here rather than be silently skipped, because a hand-made zip of
# output/ would carry it into the submission.
$files = @(Get-ChildItem -LiteralPath $resolvedOutput -File -Force |
    Where-Object { $_.Name -ne '.gitkeep' })
$actualNames = @($files.Name | Sort-Object)
$comparison = Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames

if ($comparison) {
    $detail = ($comparison | ForEach-Object {
        $side = if ($_.SideIndicator -eq '=>') { 'unexpected' } else { 'missing' }
        "$side`: $($_.InputObject)"
    }) -join '; '
    throw "output/ must contain exactly EC_001.json through EC_050.json and no other files. $detail"
}

if (Test-Path -LiteralPath $Destination) {
    if (-not $Force) {
        throw "Destination already exists: $Destination. Re-run with -Force after reviewing it."
    }
    Remove-Item -LiteralPath $Destination -Force
}

$archiveInputs = $expectedNames | ForEach-Object { Join-Path $resolvedOutput $_ }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open($Destination, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    for ($index = 0; $index -lt $expectedNames.Count; $index++) {
        $entryName = "output/{0}" -f $expectedNames[$index]
        $entry = $archive.CreateEntry($entryName)
        $source = [System.IO.File]::OpenRead($archiveInputs[$index])
        $target = $entry.Open()
        try {
            $source.CopyTo($target)
        }
        finally {
            $target.Dispose()
            $source.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
}
Write-Host "Created $Destination with 50 output/ JSON files."
