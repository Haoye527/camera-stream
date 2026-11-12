$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot 'relay_manager.py'
$recordPath=Join-Path $PSScriptRoot 'relay-manager-process.json'
if(Test-Path -LiteralPath $recordPath){
 $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
 $existing=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$record.pid)
 if($existing -and $existing.CommandLine -and $existing.CommandLine.Contains($scriptPath)){Write-Output 'Relay manager already running';exit 0}
}
$stopFile=Join-Path $PSScriptRoot 'relay-manager-stop.request'
if(Test-Path -LiteralPath $stopFile){Remove-Item -LiteralPath $stopFile}
$python=Join-Path $PSScriptRoot 'object-detection\.venv\Scripts\python.exe'
$child=Start-Process -FilePath $python -ArgumentList @(('"'+$scriptPath+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $PSScriptRoot 'relay-manager.log') -PassThru
Write-Output ('Relay manager started: '+$child.Id)
