$ErrorActionPreference='Stop'
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'stop.request') -Value 'stop'
Write-Output '已请求停止，当前分段处理完成后退出。'
