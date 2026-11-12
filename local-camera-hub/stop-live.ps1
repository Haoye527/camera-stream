Set-Content -LiteralPath (Join-Path $PSScriptRoot 'stop.request') -Value 'stop'
Write-Output 'Stop requested. The local relay will close its camera connection.'
