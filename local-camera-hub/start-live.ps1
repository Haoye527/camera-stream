$ErrorActionPreference='Stop'
& (Join-Path (Split-Path $PSScriptRoot -Parent) 'start-relay-manager.ps1')
