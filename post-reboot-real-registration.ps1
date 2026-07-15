$ErrorActionPreference = 'Stop'
$TaskName = 'GrokRegister-OneRealRound-AfterReboot'
$WorkDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = 'C:\Users\Meiosis\AppData\Local\Programs\Python\Python312\python.exe'
$AppUrl = 'http://127.0.0.1:5000'
$TaskLog = Join-Path $WorkDir 'data\post-reboot-registration.log'
function Write-TaskLog([string] $Message) { Add-Content -LiteralPath $TaskLog -Value "$(Get-Date -Format o) $Message" -Encoding UTF8 }
try {
    Set-Location -LiteralPath $WorkDir
    Write-TaskLog "Post-reboot helper started; workdir=$WorkDir"
    $app = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'app.py' } | Select-Object -First 1
    if (-not $app) {
        Start-Process -FilePath $Python -ArgumentList 'app.py' -WorkingDirectory $WorkDir -WindowStyle Hidden
        Write-TaskLog 'Started app.py'
    }
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        try { if ((Invoke-RestMethod -Uri "$AppUrl/api/register/status" -TimeoutSec 3).success) { $ready = $true; break } } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'Registration app did not become ready within 120 seconds' }
    $settings = Invoke-RestMethod -Uri "$AppUrl/api/settings" -TimeoutSec 10
    $maxRetries = 3
    if ($settings.success -and $settings.data -and $null -ne $settings.data.max_retries_per_alias) { $maxRetries = [Math]::Max(1, [int]$settings.data.max_retries_per_alias) }
    $body = @{ max_rounds = 1; max_retries = $maxRetries; concurrency = 1 } | ConvertTo-Json
    $result = Invoke-RestMethod -Method Post -Uri "$AppUrl/api/register/start" -ContentType 'application/json' -Body $body -TimeoutSec 15
    Write-TaskLog ("One-round registration request: " + ($result | ConvertTo-Json -Compress))
} catch { Write-TaskLog ("ERROR: " + $_.Exception.Message) }
finally { schtasks.exe /Delete /TN $TaskName /F *> $null }
