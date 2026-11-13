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
    [switch]$SkipInstaller   # stop after dist\DARA (faster when only testing the app)
)

$ErrorActionPreference = 'Stop'

# Pinned build tools; update version and hash together.
$WebView2Version = '1.0.4314.35'
$WebView2Sha256  = '90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4'
$InnoVersion     = '6.7.4'
$InnoSha256      = 'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'
$InnoUrl         = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe'  # linked from jrsoftware.org/isdl.php

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

$version = ([regex]::Match((Get-Content -LiteralPath (Join-Path $Root 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw 'Could not read the version from pyproject.toml.' }
Write-Phase "Building DARA Desktop $version"
$env:PYTHONIOENCODING = 'utf-8'
Set-DaraEnvironment -RepoRoot $Root

# ---- 1. Bundled CPython with every dependency --------------------------------------------------
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
$appPython = Join-Path $App 'python\python.exe'

Write-Phase 'Installing the DARA dependencies into the bundled CPython'
$constraints = Join-Path $Root 'scripts\constraints-py312-win.txt'
# -r pyproject.toml installs the dependencies only; dara itself is found through dara-src.pth.
$install = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'pip', 'install', '--python', $appPython,
    '--link-mode', 'copy', '--compile-bytecode', '-c', $constraints, '-r', (Join-Path $Root 'pyproject.toml')) -Quiet
if ($install.ExitCode -ne 0) { $install.Lines | Select-Object -Last 30 | Write-Host; throw 'Dependency installation failed.' }

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
& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$App" "/DOutputDir=$Dist" "/DRepoRoot=$Root" (Join-Path $PSScriptRoot 'DARA.iss')
if ($LASTEXITCODE -ne 0) { throw 'ISCC failed to build the installer.' }
$setup = Join-Path $Dist "DARA-Setup-$version.exe"
Write-Host ("{0}: {1:N1} MB" -f $setup, ((Get-Item -LiteralPath $setup).Length / 1MB))
Write-Phase 'Done'
