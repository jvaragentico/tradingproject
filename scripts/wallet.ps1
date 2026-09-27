param(
    [ValidateSet('address', 'balance', 'orders', 'buy', 'cancel', 'auto')][string]$Action = 'address',
    [string]$Wallet = '',
    [string]$ExpectedSigner = '',
    [string]$Slug = '',
    [ValidateSet('Up','Down')][string]$Outcome = 'Up',
    [string]$Price = '',
    [string]$Size = '',
    [string]$OrderId = '',
    [double]$Strike = 0,
    [double]$Seconds = 120,
    [double]$OrderDollars = 3,
    [double]$MaxSpend = 10,
    [double]$MaxLoss = 3
)
$ErrorActionPreference = 'Stop'
$taskNode = (Get-Command node -ErrorAction Stop).Source
$taskScript = Join-Path $PSScriptRoot 'wallet-cli.js'
$taskOptions = @{action=$Action;wallet=$Wallet;expectedSigner=$ExpectedSigner;slug=$Slug;outcome=$Outcome;price=$Price;size=$Size;orderId=$OrderId}
if ($Action -eq 'auto') {
    if ($Strike -le 0 -or -not $Slug -or $Seconds -lt 5 -or $Seconds -gt 900 -or $MaxLoss -le 0 -or $MaxLoss -gt $MaxSpend -or $OrderDollars -le 0 -or $OrderDollars -gt [Math]::Min(23.59,$MaxSpend)) { throw 'Provide a current market slug, official strike and valid caps.' }
    Write-Host "AUTOMATIC REAL ORDERS in $Slug. Strike: $Strike. Duration: $Seconds seconds. Per-order cap: $OrderDollars. Session spend cap: $MaxSpend. Worst-case loss cap: $MaxLoss."
    Write-Host 'Maker-only. Do not manually trade this market during the session. No automatic bridge or redemption.'
    if ((Read-Host 'Type START to launch these automatic real-money orders') -cne 'START') { throw 'Automatic session canceled.' }
    $taskOptions.confirmed = $true
    $taskOptions.strike = $Strike
    $taskOptions.seconds = $Seconds
    $taskOptions.orderDollars = $OrderDollars
    $taskOptions.maxSpend = $MaxSpend
    $taskOptions.maxLoss = $MaxLoss
}
if ($Action -eq 'buy') {
    Write-Host "Real BUY: $Size $Outcome shares at `$$Price in $Slug. Maximum cost: $([decimal]$Size * [decimal]$Price)."
    if ((Read-Host 'Type BUY to confirm this real-money order') -cne 'BUY') { throw 'Order canceled.' }
    $taskOptions.confirmed = $true
}
if ($Action -eq 'cancel' -and -not $OrderId) { throw 'Provide -OrderId.' }
$taskSecret = Read-Host 'Wallet private key (hidden)' -AsSecureString
$taskPointer = [IntPtr]::Zero
$taskProcess = $null
try {
    $taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecret)
    $taskInfo = New-Object System.Diagnostics.ProcessStartInfo
    $taskInfo.FileName = $taskNode
    $taskInfo.Arguments = '"' + $taskScript + '"'
    $taskInfo.WorkingDirectory = Split-Path $PSScriptRoot -Parent
    $taskInfo.UseShellExecute = $false
    $taskInfo.RedirectStandardInput = $true
    $taskInfo.CreateNoWindow = $true
    $taskProcess = New-Object System.Diagnostics.Process
    $taskProcess.StartInfo = $taskInfo
    [void]$taskProcess.Start()
    # Anonymous stdin pipe: no key in argv, environment, disk, or console output.
    $taskProcess.StandardInput.WriteLine([Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer))
    $taskProcess.StandardInput.WriteLine(($taskOptions | ConvertTo-Json -Compress))
    $taskProcess.StandardInput.Close()
    $taskProcess.WaitForExit()
    if ($taskProcess.ExitCode -ne 0) { throw 'Wallet command failed. See the public error above.' }
} finally {
    if ($taskPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer) }
    if ($taskProcess) { $taskProcess.Dispose() }
    $taskSecret.Dispose()
}
