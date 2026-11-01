$shared = Get-Counter '\GPU Process Memory(*)\Shared Usage' -ErrorAction SilentlyContinue
$shared.CounterSamples | Where-Object {$_.CookedValue -gt 0} | Sort-Object CookedValue -Descending | Select-Object -First 6 | ForEach-Object { "SHARED  {0}  {1:N0} MB" -f $_.InstanceName, ($_.CookedValue/1MB) }
$eng = Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue
$eng.CounterSamples | Where-Object {$_.CookedValue -gt 1} | ForEach-Object { "ENGINE  {0}  {1:N0}%" -f $_.InstanceName, $_.CookedValue }
