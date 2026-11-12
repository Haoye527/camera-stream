$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-relay-manager.ps1')
if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'ai-events\start.ps1')) { & (Join-Path $PSScriptRoot 'ai-events\start.ps1') }
if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'object-detection\start.ps1')) { & (Join-Path $PSScriptRoot 'object-detection\start.ps1') }
if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'adaptive-recording\start.ps1')) { & (Join-Path $PSScriptRoot 'adaptive-recording\start.ps1') }