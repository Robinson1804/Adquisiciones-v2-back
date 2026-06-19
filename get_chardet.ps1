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

Write-Host "Fetching chardet 5.2.0 ..."
$url = Get-WheelUrl -Package 'chardet' -Version '5.2.0'
if ($url) {
    Download-File -Url $url -DestDir $wheelsDir
} else {
    Write-Warning "chardet 5.2.0 not found, trying without version..."
    $html = $client.DownloadString("https://pypi.org/simple/chardet/")
    $lines = $html -split '<br />'
    $line = $lines | Where-Object { $_ -like "*py3-none-any*.whl*" } | Select-Object -Last 1
    if ($line -and $line -match 'href="([^"#]+\.whl)') {
        Download-File -Url $Matches[1] -DestDir $wheelsDir
    } else {
        Write-Warning "Could not find chardet wheel"
    }
}
Write-Host "Done"
