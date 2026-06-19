$proxy = New-Object System.Net.WebProxy('http://hitomi.inei.gob.pe:3128', $true)
$proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
$client = New-Object System.Net.WebClient
$client.Proxy = $proxy
$wheelsDir = 'D:\DATA ROBINSON\OTIN-2026\Sistemas Internos\Adquisiciones-OTIN\backend\wheels'

function Get-WheelUrl {
    param([string]$Package, [string]$Version)
    $html = $client.DownloadString("https://pypi.org/simple/$Package/")
    $lines = $html -split '<br />'
    $candidates = @(
        ($lines | Where-Object { $_ -like "*${Version}*cp312*win_amd64*.whl*" } | Select-Object -First 1),
        ($lines | Where-Object { $_ -like "*${Version}*cp311*win_amd64*.whl*" } | Select-Object -First 1),
        ($lines | Where-Object { $_ -like "*${Version}*abi3*win_amd64*.whl*" } | Select-Object -First 1),
        ($lines | Where-Object { $_ -like "*${Version}*win_amd64*.whl*" } | Select-Object -First 1),
        ($lines | Where-Object { $_ -like "*${Version}*py3-none-any*.whl*" } | Select-Object -First 1),
        ($lines | Where-Object { $_ -like "*${Version}*py2.py3*none-any*.whl*" } | Select-Object -First 1)
    )
    foreach ($c in $candidates) {
        if ($c -and $c -match 'href="([^"#]+\.whl)') {
            return $Matches[1]
        }
    }
    return $null
}

function Download-File {
    param([string]$Url, [string]$DestDir)
    $filename = [System.IO.Path]::GetFileName(($Url -split '#')[0])
    $dest = Join-Path $DestDir $filename
    if (Test-Path $dest) { Write-Host "  SKIP: $filename"; return }
    Write-Host "  GET: $filename"
    $client.DownloadFile($Url, $dest)
}

$packages = @{
    "annotated-types" = "0.7.0"
    "typing-extensions" = "4.12.2"
    "pydantic-core" = "2.27.2"
    "pydantic-settings" = "2.7.0"
    "python-dotenv" = "1.0.1"
    "psycopg2-binary" = "2.9.10"
    "python-jose" = "3.3.0"
    "bcrypt" = "4.2.1"
    "greenlet" = "3.1.1"
    "cryptography" = "44.0.0"
    "cffi" = "1.17.1"
    "pycparser" = "2.22"
    "python-multipart" = "0.0.20"
    # C5 export deps
    "openpyxl" = "3.1.5"
    "et-xmlfile" = "2.0.0"
    "reportlab" = "4.2.5"
    "pillow" = "11.0.0"
}

foreach ($pkg in $packages.Keys) {
    $ver = $packages[$pkg]
    Write-Host "Fetching $pkg $ver ..."
    try {
        $url = Get-WheelUrl -Package $pkg -Version $ver
        if ($url) {
            Download-File -Url $url -DestDir $wheelsDir
        } else {
            Write-Warning "  NOT FOUND: $pkg $ver"
        }
    } catch {
        Write-Warning "  ERROR $pkg : $_"
    }
}

Write-Host "Done"
