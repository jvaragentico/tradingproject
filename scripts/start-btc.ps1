param([switch]$CheckOnly,[string]$Wallet = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python environment is missing. See README.md setup.' }
Push-Location $taskRoot
try {
    $taskLines = & $taskPython (Join-Path $PSScriptRoot 'live_btc_market.py')
    if ($LASTEXITCODE -ne 0 -or $taskLines.Count -ne 1) { throw 'Live BTC market check failed. No wallet was opened and no orders were placed.' }
    $taskMarket = $taskLines | ConvertFrom-Json
    if ($taskMarket.slug -notmatch '^btc-updown-5m-\d+$' -or [double]$taskMarket.strike -le 0) { throw 'Invalid live market response.' }
    Write-Host "Live market: $($taskMarket.slug)"
    Write-Host "Polymarket opening reference: $($taskMarket.strike)"
    Write-Host 'Test session: maximum $3 spend and $3 market loss; $50 account-value floor; 90 seconds.'
    if ($CheckOnly) { Write-Host 'Read-only check complete. No wallet opened or orders placed.'; return }
    & (Join-Path $PSScriptRoot 'wallet.ps1') -Action auto -Wallet $Wallet -Slug $taskMarket.slug -Strike ([double]$taskMarket.strike) -Seconds 90 -OrderDollars 3 -MaxSpend 3 -MaxLoss 3 -StopFloor 50 -StartAfterKey
    if ($LASTEXITCODE -ne 0) { throw 'Trading session failed. Check the account before retrying.' }
} finally {
    Pop-Location
}
