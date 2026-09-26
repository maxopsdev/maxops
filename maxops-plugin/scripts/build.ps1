param(
    [ValidateSet("chrome", "edge", "firefox", "all")]
    [string] $Browser = "all"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$shared = Join-Path $root "shared"
$manifests = Join-Path $root "manifests"
$dist = Join-Path $root "dist"

$targets = if ($Browser -eq "all") {
    @("chrome", "edge", "firefox")
} else {
    @($Browser)
}

foreach ($target in $targets) {
    $targetDist = Join-Path $dist $target
    $targetManifest = Join-Path $manifests "$target.json"

    if (!(Test-Path $targetManifest)) {
        throw "Missing manifest for target '$target': $targetManifest"
    }

    if (Test-Path $targetDist) {
        Remove-Item -LiteralPath $targetDist -Recurse -Force
    }

    New-Item -ItemType Directory -Path $targetDist | Out-Null
    Copy-Item -Path (Join-Path $shared "*") -Destination $targetDist -Recurse
    Copy-Item -Path $targetManifest -Destination (Join-Path $targetDist "manifest.json")

    Write-Host "Built $target extension at $targetDist"
}
