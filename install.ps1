# CA LazyNest – Installer (Fusion Add-In)
# Ziel: %APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\LazyNest
# Behaelt Nutzerdaten, entfernt __pycache__; Backup nach API\Backups_out_of_AddIns (nicht in AddIns – sonst Autostart-Konflikt)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
$name = 'LazyNest'
$dst = Join-Path $root $name
$stamp = Get-Date -Format 'yyyyMMdd'
$userFiles = @('user_prefs.json', 'user_stock.json')

if (Get-Process -Name 'Fusion360' -ErrorAction SilentlyContinue) {
    Write-Host 'ACHTUNG: Fusion laeuft noch. Bitte Fusion schliessen und dann eine Taste druecken ...' -ForegroundColor Yellow
    [void][Console]::ReadKey($true)
}

New-Item -ItemType Directory -Force $root | Out-Null
$src = $here
if (-not (Test-Path -LiteralPath (Join-Path $src "$name.manifest"))) {
    Write-Host "Fehler: $name.manifest nicht neben install.ps1 gefunden." -ForegroundColor Red
    exit 1
}

if (Test-Path -LiteralPath $dst) {
    $bkRoot = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\Backups_out_of_AddIns'
    New-Item -ItemType Directory -Force $bkRoot | Out-Null
    $bk = Join-Path $bkRoot ($name + '_backup_' + $stamp)
    if (Test-Path -LiteralPath $bk) { $bk = $bk + '_' + (Get-Date -Format 'HHmmss') }
    Write-Host "Backup: $dst -> $bk"
    Copy-Item -LiteralPath $dst -Destination $bk -Recurse -Force
} else {
    New-Item -ItemType Directory -Force $dst | Out-Null
}

$keep = Join-Path $env:TEMP ('_ca_keep_' + $name + '_' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force $keep | Out-Null
foreach ($f in $userFiles) {
    $p = Join-Path $dst $f
    if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p (Join-Path $keep $f) -Force }
}
foreach ($d in @('logs','projects')) {
    $p = Join-Path $dst $d
    if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p (Join-Path $keep $d) -Recurse -Force }
}

Write-Host "Installiere $name nach $dst ..."
Get-ChildItem -LiteralPath $dst -Force | Where-Object {
    $_.Name -notin @('logs','projects') -and ($userFiles -notcontains $_.Name)
} | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
}

$skip = @('.git','.github','__pycache__','logs','projects','.gitignore')
Get-ChildItem -LiteralPath $src -Force | Where-Object { $skip -notcontains $_.Name } | ForEach-Object {
    $target = Join-Path $dst $_.Name
    if ($_.PSIsContainer) { Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force }
    else { Copy-Item -LiteralPath $_.FullName -Destination $target -Force }
}

Get-ChildItem -LiteralPath $dst -Filter '__pycache__' -Recurse -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

foreach ($f in $userFiles) {
    $s = Join-Path $keep $f; $d = Join-Path $dst $f
    if (Test-Path -LiteralPath $s) { Copy-Item -LiteralPath $s $d -Force; Write-Host "  erhalten: $f" }
}
foreach ($dname in @('logs','projects')) {
    $s = Join-Path $keep $dname
    if (Test-Path -LiteralPath $s) {
        $d = Join-Path $dst $dname
        New-Item -ItemType Directory -Force $d | Out-Null
        Copy-Item -LiteralPath (Join-Path $s '*') -Destination $d -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  erhalten: $dname\"
    }
}
Remove-Item -LiteralPath $keep -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "Fertig: $dst" -ForegroundColor Green
Write-Host "In Fusion: Dienstprogramme -> Add-Ins -> LazyNest laden/aktivieren."
Write-Host "Spende (optional): PayPal chris.n.adam@gmx.de"
