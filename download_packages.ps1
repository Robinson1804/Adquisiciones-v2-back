# Download pip packages via Windows proxy (NTLM auth via DefaultNetworkCredentials)
# Run from backend/ directory with the venv activated

param(
    [string]$WheelsDir = ".\wheels"
)

$proxy = New-Object System.Net.WebProxy('http://hitomi.inei.gob.pe:3128', $true)
$proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials

$client = New-Object System.Net.WebClient
$client.Proxy = $proxy

New-Item -ItemType Directory -Force -Path $WheelsDir | Out-Null

function Get-WheelUrl {
    param([string]$Package, [string]$Version, [string]$PythonTag = "py3")
    $html = $client.DownloadString("https://pypi.org/simple/$Package/")
    $lines = $html -split '<br />'
    # Prefer py3-none-any wheel, fallback to any .whl
    $match = $lines | Where-Object { $_ -like "*${Package}-${Version}*-py3-none-any.whl*" } | Select-Object -First 1
    if (-not $match) {
        $match = $lines | Where-Object { $_ -like "*${Package}-${Version}*-cp312*.whl*" } | Select-Object -First 1
    }
    if (-not $match) {
        $match = $lines | Where-Object { $_ -like "*${Package}-${Version}*.whl*" } | Select-Object -First 1
    }
    if ($match -and $match -match 'href="([^"#]+\.whl)') {
        return $matches[1]
    }
    return $null
}

function Download-Wheel {
    param([string]$Url, [string]$DestDir)
    $filename = [System.IO.Path]::GetFileName(($Url -split '#')[0])
    $dest = Join-Path $DestDir $filename
    if (Test-Path $dest) {
        Write-Host "  SKIP (exists): $filename"
        return $dest
    }
    Write-Host "  Downloading: $filename"
    $client.DownloadFile($Url, $dest)
    return $dest
}

$packages = @{
    "fastapi" = "0.115.6"
    "starlette" = "0.41.3"
    "anyio" = "4.7.0"
    "uvicorn" = "0.34.0"
    "uvloop" = $null  # optional, skip
    "sqlalchemy" = "2.0.36"
    "alembic" = "1.14.0"
    "psycopg2-binary" = "2.9.10"
    "pydantic" = "2.10.4"
    "pydantic-settings" = "2.7.0"
    "pydantic-core" = "2.27.2"
    "python-dotenv" = "1.0.1"
    "python-jose" = "3.3.0"
    "passlib" = "1.7.4"
    "pytest" = "8.3.4"
    "httpx" = "0.28.1"
    "httpcore" = "1.0.7"
    "h11" = "0.14.0"
    "annotated-types" = "0.7.0"
    "typing-extensions" = "4.12.2"
    "click" = "8.1.8"
    "sniffio" = "1.3.1"
    "exceptiongroup" = "1.2.2"
    "idna" = "3.10"
    "certifi" = "2024.12.14"
    "iniconfig" = "2.0.0"
    "pluggy" = "1.5.0"
    "packaging" = "24.2"
    "mako" = "1.3.8"
    "greenlet" = "3.1.1"
    "cryptography" = "44.0.0"
    "cffi" = "1.17.1"
    "ecdsa" = "0.19.0"
    "pyasn1" = "0.6.1"
    "rsa" = "4.9"
    "six" = "1.17.0"
    "bcrypt" = "4.2.1"
    "h2" = "4.1.0"
    "hpack" = "4.0.0"
    "hyperframe" = "6.0.1"
    "websockets" = "14.1"
}

foreach ($pkg in $packages.Keys) {
    $ver = $packages[$pkg]
    if ($null -eq $ver) { continue }
    Write-Host "Fetching $pkg $ver ..."
    try {
        $url = Get-WheelUrl -Package $pkg -Version $ver
        if ($url) {
            Download-Wheel -Url $url -DestDir $WheelsDir | Out-Null
        } else {
            Write-Warning "  No wheel found for $pkg $ver"
        }
    } catch {
        Write-Warning "  Error fetching $pkg : $_"
    }
}

Write-Host "`nAll downloads complete. Install with:"
Write-Host "  .venv\Scripts\pip.exe install --no-index --find-links $WheelsDir -r requirements.txt"
