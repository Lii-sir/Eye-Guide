[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Low')]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$projectPrefix = $projectRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar

function Assert-LocalDirectory {
    param([string]$Path)
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target is outside the project: $fullPath"
    }
    $current = Get-Item -LiteralPath $fullPath -Force
    if (-not $current.PSIsContainer) { throw "Not a directory: $fullPath" }
    while ($current.FullName -ne $projectRoot) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Cleanup refuses linked directories: $($current.FullName)"
        }
        $current = $current.Parent
    }
    if (Get-ChildItem -LiteralPath $fullPath -Recurse -Force |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw "Cleanup refuses a directory containing links: $fullPath"
    }
}

# Fixed generated-output paths only; assets and environments stay intact.
$targets = @(@('build', 'dist', 'packaging/dist', 'packaging/build', '__pycache__') |
    ForEach-Object { Join-Path $projectRoot $_ } |
    Where-Object { Test-Path -LiteralPath $_ })
foreach ($relativeRoot in @('eyeguide', 'scripts', 'packaging')) {
    $sourceRoot = Join-Path $projectRoot $relativeRoot
    if (Test-Path -LiteralPath $sourceRoot) {
        Assert-LocalDirectory $sourceRoot
        $targets += @(Get-ChildItem -LiteralPath $sourceRoot -Directory -Recurse -Force |
            Where-Object { $_.Name -eq '__pycache__' } |
            Select-Object -ExpandProperty FullName)
    }
}
$targets = @($targets | Sort-Object -Unique)
# Validate the entire list before deleting anything.
foreach ($target in $targets) { Assert-LocalDirectory $target }
foreach ($target in $targets) {
    if ($PSCmdlet.ShouldProcess($target, 'Remove generated directory')) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Output "Removed: $target"
    }
}
