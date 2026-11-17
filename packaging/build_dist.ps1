<#
    Build DARA Desktop for Windows:
      dist\DARA\                    runnable in place (DARA.exe + bundled CPython + DARA sources)
      dist\DARA-Setup-<version>.exe per-user installer (Inno Setup 6)

    Usage (from a clean checkout, no admin rights, internet needed on the first build):
      powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_dist.ps1

    Reuses the pinned uv / CPython setup of scripts\start_dara_web.ps1 (tools\ folder). Downloaded
    build tools (WebView2 SDK, Inno Setup) go to packaging\.build\. BGMN is never bundled: its licence
    forbids redistribution, so DARA.exe downloads it on first start (scripts\ensure_bgmn.py).
#>
param(
    [switch]$SkipInstaller,  # stop after dist\DARA (faster when only testing the app)
    [switch]$ReuseRuntime    # keep dist\DARA\python as it is (same dependencies): restage sources, DARA.exe, installer
)

$ErrorActionPreference = 'Stop'

# Pinned build tools; update version and hash together.
$WebView2Version = '1.0.4314.35'
$WebView2Sha256  = '90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4'
$InnoVersion     = '6.7.4'
$InnoSha256      = 'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'
$InnoUrl         = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe'  # linked from jrsoftware.org/isdl.php
# rsync for the COD structure download (src\dara\structure_library.py): MSYS2 packages from
# repo.msys2.org/msys/x86_64, SHA-256 checked against msys.db on 2026-11-17.
$MsysPackages = [ordered]@{
    'rsync-3.5.2-2'          = 'e27c183d8af0ed250dc7502ba3b4aebe5c32086c4252fe4f16a4cff328c8e803'
    'msys2-runtime-3.6.12-1' = 'c0dbaa624e29be6d08cf5c65609dca6c749431109e4572bf446a3ca9b4c4fd7c'
    'libxxhash-0.8.4-1'      = 'ba784ad117626401cd5d3ec0cf70399f9de72a63755f96c28fa2032303f9c330'
    'popt-1.19-1'            = 'b402bd1d03815c5e28b7ab415d8e20f487eff2460ae0271a99674eeb0d77194f'
    'libzstd-1.5.7-1'        = '5e370b2e725a0a7e5ff58d84653c9a0cd5a10b9cfe930f2d949a28ad2a11c1f2'
    'liblz4-1.11.1-1'        = 'b35db1392281429746698daaee05934d3eaa437a585f723b1723bca409189742'
    'libopenssl-4.1.0-1'     = '71f093de4a002913f39783b79d18da33b63ce8baf825434218cc1d8bd40304fb'
    'libiconv-1.19-1'        = '0fa55ea2a6ccf97cf8c58b24b2615815e15e16e6e4e888091c263c2c83c5313d'
    'libidn2-2.3.8-1'        = '55e0de96b893e9f67f14a4283d411fc2a45d7b59333bc16e2a9b16e5bfc9282f'
    'libunistring-1.4.2-1'   = 'bcd04706d95b5e7127d39834565bceed6b400192f5fb51599745aba28d5da018'
    'libintl-0.22.5-1'       = '336d66b9d95cf9c1804958f8e260762a3e83bf158ed5981f783bc772a31073cf'
}
# rsync.exe and its DLL import closure (read from the PE import tables of these versions, no delay
# loads); update together with the packages. gcc-libs, msys-ssl and msys-charset are not needed.
$RsyncFiles = @('rsync.exe', 'msys-2.0.dll', 'msys-crypto-4.dll', 'msys-iconv-2.dll', 'msys-idn2-0.dll', 'msys-intl-8.dll',
                'msys-lz4-1.dll', 'msys-popt-0.dll', 'msys-unistring-5.dll', 'msys-xxhash-0.dll', 'msys-zstd-1.dll')

$Root     = Split-Path -Parent $PSScriptRoot
$Dist     = Join-Path $Root 'dist'
$App      = Join-Path $Dist 'DARA'
$BuildDir = Join-Path $PSScriptRoot '.build'
$Started  = Get-Date

# Install-Uv, Install-ManagedPython, Invoke-Native and the pinned $UvVersion / $PythonVersion.
. (Join-Path $Root 'scripts\start_dara_web.ps1')

function Write-Phase {
    param([string]$Text)
    Write-Host ("== {0}  ({1:N0} s)" -f $Text, ((Get-Date) - $Started).TotalSeconds) -ForegroundColor Cyan
}

function Get-VerifiedDownload {
    param([string]$Url, [string]$Path, [string]$Sha256)
    if ((Test-Path -LiteralPath $Path) -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256) { return }
    [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path))
    Write-Host "Downloading $Url"
    $partial = "$Path.partial"
    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -TimeoutSec 600
    $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) {
        Remove-Item -LiteralPath $partial -Force
        throw "SHA-256 mismatch for $Url (expected $Sha256, got $actual)"
    }
    Move-Item -LiteralPath $partial -Destination $Path -Force
}

function Assert-Signed {
    param([string]$Path, [string]$Publisher)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch "CN=$Publisher") {
        throw "$Path is not validly signed by $Publisher (status $($signature.Status))."
    }
}

function Get-FolderMB {
    param([string]$Path)
    return [math]::Round(((Get-ChildItem -LiteralPath $Path -Recurse -File -Force | Measure-Object -Sum Length).Sum / 1MB), 1)
}

function Install-BundledRsync {
    # Unpacks the pinned MSYS2 packages into $Destination: rsync.exe, its DLLs, licence texts, README.txt.
    param([string]$Destination)
    # bsdtar of Windows 11 reads .tar.zst (libzstd); Git's GNU tar may have no zstd.
    $tar = Join-Path $env:WINDIR 'System32\tar.exe'
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    [void](New-Item -ItemType Directory -Force -Path $Destination)
    $readme = @(
        'rsync for DARA Desktop',
        '',
        'DARA runs rsync.exe as a separate program to download COD crystal structures',
        '(rsync://www.crystallography.net/cif/) for the structure library, and falls back to HTTP',
        'when rsync cannot connect. The files in this folder are unmodified binaries of the MSYS2',
        'project (https://www.msys2.org/) for x86_64. rsync and the MSYS2 runtime are licensed under',
        'the GNU General Public License version 3 or later; the libraries under the licences named',
        'below. The licence texts the packages include are in licenses\<package>\; the others are',
        'published at the upstream address. The complete corresponding source code of each package',
        'is available from the MSYS2 package repository at the "source" address below and from upstream.',
        '')
    foreach ($name in $MsysPackages.Keys) {
        $pkg = Join-Path $BuildDir "downloads\$name-x86_64.pkg.tar.zst"
        Get-VerifiedDownload -Url "https://repo.msys2.org/msys/x86_64/$name-x86_64.pkg.tar.zst" -Path $pkg -Sha256 $MsysPackages[$name]
        $tree = Join-Path $BuildDir "msys2\$name"
        if (Test-Path -LiteralPath $tree) { Remove-Item -LiteralPath $tree -Recurse -Force }
        [void](New-Item -ItemType Directory -Force -Path $tree)
        & $tar -xf $pkg -C $tree
        if ($LASTEXITCODE -ne 0) { throw "$tar could not unpack $pkg (it needs zstd support, as in Windows 11)." }
        $info = @{}
        foreach ($line in Get-Content -LiteralPath (Join-Path $tree '.PKGINFO')) {
            if ($line -match '^(\w+) = (.*)$') { $info[$Matches[1]] = @($info[$Matches[1]] | Where-Object { $_ }) + $Matches[2] }
        }
        $shipped = @($RsyncFiles | Where-Object { Test-Path -LiteralPath (Join-Path $tree "usr\bin\$_") })
        foreach ($file in $shipped) { Copy-Item -LiteralPath (Join-Path $tree "usr\bin\$file") -Destination $Destination }
        $licences = @(Get-ChildItem -LiteralPath (Join-Path $tree 'usr\share') -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
            $_.FullName -like (Join-Path $tree 'usr\share\licenses\*') -or ($_.FullName -like (Join-Path $tree 'usr\share\doc\*') -and $_.Name -match '^COPYING|LICENSE') })
        if ($licences) {
            $licenceDir = Join-Path $Destination "licenses\$($info.pkgname)"
            [void](New-Item -ItemType Directory -Force -Path $licenceDir)
            foreach ($file in $licences) { Copy-Item -LiteralPath $file.FullName -Destination $licenceDir }
        }
        $readme += @(
            "$($info.pkgname) $($info.pkgver)",
            "  licence:  $($info.license -join ', ')",
            "  files:    $(if ($shipped) { $shipped -join ', ' } else { '(none)' })$(if ($licences) { "; licenses\$($info.pkgname)\" })",
            "  upstream: $($info.url)",
            "  binary:   https://repo.msys2.org/msys/x86_64/$name-x86_64.pkg.tar.zst",
            "  source:   https://repo.msys2.org/msys/sources/$($info.pkgbase)-$($info.pkgver).src.tar.zst",
            '')
        Remove-Item -LiteralPath $tree -Recurse -Force
    }
    $missing = @($RsyncFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Destination $_)) })
    if ($missing) { throw "The MSYS2 packages did not provide $($missing -join ', ')." }
    [System.IO.File]::WriteAllLines((Join-Path $Destination 'README.txt'), [string[]]$readme)
    # Self-check: every DLL rsync loads is present (a missing one fails before main).
    $probe = Invoke-Native -FilePath (Join-Path $Destination 'rsync.exe') -Arguments @('--version') -Quiet
    if ($probe.ExitCode -ne 0 -or ($probe.Lines -join ' ') -notmatch 'rsync\s+version') {
        $probe.Lines | Select-Object -Last 10 | Write-Host
        throw "The bundled rsync.exe does not start (exit code $($probe.ExitCode))."
    }
}

function Get-InstallerDefines {
    # The installer shows the structure library's size without network access, from the pack data the app ships.
    $packs = Get-Content -LiteralPath (Join-Path $Root 'src\dara\data\structure_packs.json') -Raw | ConvertFrom-Json
    $pack = @($packs.packs | Where-Object { $_.id -eq 'recommended' })[0]
    if (-not $pack) { throw 'src\dara\data\structure_packs.json has no "recommended" pack.' }
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    $minutes = [math]::Round($pack.minutes.total)
    # MAX_PATH (259 characters + NUL) minus "\" and the longest path inside the app folder.
    $longest = (Get-ChildItem -LiteralPath $App -Recurse -File -Force | ForEach-Object { $_.FullName.Length - $App.Length - 1 } |
        Measure-Object -Maximum).Maximum
    return @(
        "/DLibStructures=$([string]::Format($inv, '{0:N0}', $pack.counts.cod + $pack.counts.mp))",
        "/DLibCodCount=$([string]::Format($inv, '{0:N0}', $pack.counts.cod))",
        "/DLibDownloadMB=$([string]::Format($inv, '{0:N0}', $pack.download_mb.total))",
        "/DLibDiskMB=$([string]::Format($inv, '{0:N0}', $pack.disk_mb.total))",
        "/DLibDiskBytes=$([int64]($pack.disk_mb.total * 1MB))",
        "/DLibTime=$(if ($minutes % 60) { "$([math]::Floor($minutes / 60)) h $($minutes % 60) min" } else { "$($minutes / 60) h" })",
        "/DLibElements=$($pack.elements -join ' ')",
        "/DMaxAppDirLength=$(259 - 1 - $longest)")
}

$version = ([regex]::Match((Get-Content -LiteralPath (Join-Path $Root 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw 'Could not read the version from pyproject.toml.' }
Write-Phase "Building DARA Desktop $version"
$env:PYTHONIOENCODING = 'utf-8'
Set-DaraEnvironment -RepoRoot $Root

# ---- 1. Bundled CPython with every dependency --------------------------------------------------
$appPython = Join-Path $App 'python\python.exe'
if ($ReuseRuntime -and (Test-Path -LiteralPath $appPython)) {
    Write-Phase "Reusing the bundled CPython in $App\python"
} else {
    Write-Phase "Preparing uv $UvVersion and CPython $PythonVersion"
    if ([int](@(Install-Uv -RepoRoot $Root)[-1]) -ne 0) { throw 'uv could not be prepared.' }
    if ([int](@(Install-ManagedPython -RepoRoot $Root)[-1]) -ne 0) { throw "CPython $PythonVersion could not be prepared." }
    $basePython = [System.IO.Path]::GetFullPath($script:BasePython)
    $probe = Invoke-Native -FilePath $basePython -Arguments @('-I', '-c', 'import sys; print(sys.version.split()[0])') -Quiet
    if ($basePython -notlike (Join-Path $Root 'tools\python\*') -or ($probe.Lines -join '') -ne $PythonVersion) {
        # A system Python (the launcher's fallback) is not relocatable; never bundle it.
        throw "Expected the uv-managed CPython $PythonVersion under tools\python, got $basePython."
    }

    Write-Phase "Copying CPython to $App\python"
    if (Test-Path -LiteralPath $App) {
        try { Remove-Item -LiteralPath $App -Recurse -Force }
        catch { throw "Could not remove $App. Close DARA.exe if it runs from there. $($_.Exception.Message)" }
    }
    [void](New-Item -ItemType Directory -Force -Path $App)
    $baseDir = Split-Path -Parent $basePython
    & robocopy.exe $baseDir (Join-Path $App 'python') /E /XD include libs __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE." }
    Remove-Item -LiteralPath (Join-Path $App 'python\Lib\EXTERNALLY-MANAGED') -Force -ErrorAction SilentlyContinue

    Write-Phase 'Installing the DARA dependencies into the bundled CPython'
    $constraints = Join-Path $Root 'scripts\constraints-py312-win.txt'
    # -r pyproject.toml installs the dependencies only; dara itself is found through dara-src.pth.
    $install = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'pip', 'install', '--python', $appPython,
        '--link-mode', 'copy', '--compile-bytecode', '-c', $constraints, '-r', (Join-Path $Root 'pyproject.toml')) -Quiet
    if ($install.ExitCode -ne 0) { $install.Lines | Select-Object -Last 30 | Write-Host; throw 'Dependency installation failed.' }
}

Write-Phase 'Copying the DARA sources'
$stage = Invoke-Native -FilePath $appPython -Arguments @('-I', '-X', 'utf8', (Join-Path $PSScriptRoot 'stage_app.py'), '--repo', $Root, '--app', $App)
if ($stage.ExitCode -ne 0) { throw 'Staging the DARA sources failed.' }
$check = Invoke-Native -FilePath $appPython -Arguments @('-I', '-c',
    'import dara, ray, pymatgen, fastapi, uvicorn, mp_api, psutil, docx; print(dara.__file__)') -Quiet
$expected = (Join-Path $App 'src\dara\__init__.py')
if ($check.ExitCode -ne 0 -or ($check.Lines | Select-Object -Last 1) -ne $expected) {
    $check.Lines | Select-Object -Last 20 | Write-Host
    throw "Import check failed: dara must load from $expected."
}
Write-Phase 'Bundling rsync (MSYS2) for the COD download'
Install-BundledRsync -Destination (Join-Path $App 'tools\rsync')

# ---- 2. DARA.exe (C# WinForms + WebView2, compiled with the csc.exe that ships with Windows) ----
Write-Phase "Building DARA.exe (WebView2 SDK $WebView2Version)"
$nupkg = Join-Path $BuildDir "downloads\microsoft.web.webview2.$WebView2Version.nupkg"
Get-VerifiedDownload -Url "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$WebView2Version/microsoft.web.webview2.$WebView2Version.nupkg" -Path $nupkg -Sha256 $WebView2Sha256
$sdk = Join-Path $BuildDir "webview2-$WebView2Version"
if (-not (Test-Path -LiteralPath (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'))) {
    $zip = Join-Path $BuildDir 'webview2.zip'
    Copy-Item -LiteralPath $nupkg -Destination $zip -Force   # Expand-Archive only accepts .zip
    Expand-Archive -LiteralPath $zip -DestinationPath $sdk -Force
    Remove-Item -LiteralPath $zip -Force
}
$sdkFiles = @((Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'),
              (Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'),
              (Join-Path $sdk 'runtimes\win-x64\native\WebView2Loader.dll'))
foreach ($file in $sdkFiles) {
    Assert-Signed -Path $file -Publisher 'Microsoft Corporation'
    Copy-Item -LiteralPath $file -Destination $App -Force
}
$assemblyInfo = Join-Path $BuildDir 'AssemblyInfo.cs'
$numeric = ($version -replace '[^0-9.].*$', '')
[System.IO.File]::WriteAllText($assemblyInfo, @"
[assembly: System.Reflection.AssemblyTitle("DARA")]
[assembly: System.Reflection.AssemblyProduct("DARA")]
[assembly: System.Reflection.AssemblyDescription("Data-driven automated Rietveld analysis")]
[assembly: System.Reflection.AssemblyVersion("$numeric")]
[assembly: System.Reflection.AssemblyFileVersion("$numeric")]
[assembly: System.Reflection.AssemblyInformationalVersion("$version")]
"@)
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:winexe /platform:x64 /optimize+ "/out:$(Join-Path $App 'DARA.exe')" `
    "/win32icon:$(Join-Path $Root 'logo\dara.ico')" "/win32manifest:$(Join-Path $PSScriptRoot 'launcher\app-manifest.xml')" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll "/r:$($sdkFiles[0])" "/r:$($sdkFiles[1])" `
    (Join-Path $PSScriptRoot 'launcher\DaraLauncher.cs') $assemblyInfo
if ($LASTEXITCODE -ne 0) { throw 'csc failed to build DARA.exe.' }
Write-Host ("dist\DARA: {0} MB" -f (Get-FolderMB $App))
if ($SkipInstaller) { Write-Phase 'Done (installer skipped)'; exit 0 }

# ---- 3. Installer ------------------------------------------------------------------------------
Write-Phase "Preparing Inno Setup $InnoVersion"
$innoDir = Join-Path $BuildDir 'innosetup'
$iscc = Join-Path $innoDir 'ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) {
    $innoSetup = Join-Path $BuildDir "downloads\innosetup-$InnoVersion.exe"
    Get-VerifiedDownload -Url $InnoUrl -Path $innoSetup -Sha256 $InnoSha256
    Assert-Signed -Path $innoSetup -Publisher 'Pyrsys B.V.'
    # Portable per-user copy: no admin rights, nothing registered.
    $process = Start-Process -FilePath $innoSetup -Wait -PassThru -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS', "/DIR=`"$innoDir`"")
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $iscc)) { throw "Inno Setup could not be installed (exit code $($process.ExitCode))." }
}

Write-Phase 'Building the installer'
& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$App" "/DOutputDir=$Dist" "/DRepoRoot=$Root" @(Get-InstallerDefines) (Join-Path $PSScriptRoot 'DARA.iss')
if ($LASTEXITCODE -ne 0) { throw 'ISCC failed to build the installer.' }
$setup = Join-Path $Dist "DARA-Setup-$version.exe"
Write-Host ("{0}: {1:N1} MB" -f $setup, ((Get-Item -LiteralPath $setup).Length / 1MB))
Write-Phase 'Done'
