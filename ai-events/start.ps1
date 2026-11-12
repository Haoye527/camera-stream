$ErrorActionPreference='Stop'
$aiPort=18768
$probe=[Net.Sockets.TcpClient]::new()
try{$probe.Connect('127.0.0.1',$aiPort);Write-Output '事件预览已运行：http://127.0.0.1:18768';exit 0}catch{}finally{$probe.Dispose()}
$python='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$serverScript=Join-Path $PSScriptRoot 'service.py'
$process=Start-Process -FilePath $python -ArgumentList @(('"'+$serverScript+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $PSScriptRoot 'service.log') -PassThru
Write-Output ('事件预览：http://127.0.0.1:18768；PID：'+$process.Id)
