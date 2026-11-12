$ErrorActionPreference='Stop'
$clientRoot='C:\Program Files (x86)\TYKJ'
$domain=[Reflection.Assembly]::LoadFrom((Join-Path $clientRoot 'TY.Domain.dll'))
$helper=$domain.GetType('TY.Domain.Helpers.XXTeaHelper')
$decrypt=$helper.GetMethod('Decrypt',[type[]]@([string]))
$decryptKey=$helper.GetMethod('Decrypt',[type[]]@([string],[string]))
$mac=$domain.GetType('TY.Domain.Helpers.ComputerSignHelper').GetMethod('MacId').Invoke($null,@())
$ini=[IO.File]::ReadAllLines((Join-Path $clientRoot 'user.ini'))
$encrypted=($ini | Where-Object {$_ -match '^user='} | Select-Object -First 1) -replace '^user=',''
$session=$decryptKey.Invoke($null,@($encrypted,$mac)) | ConvertFrom-Json
if (-not $session.accessToken) { throw 'No saved access token' }
Write-Output ('Saved login token available; login timestamp present: '+[bool]$session.loginDateTime)
$matchesFound=[Collections.Generic.List[string]]::new()
$log=Get-ChildItem (Join-Path $clientRoot 'logs') -Filter '*.txt' | Where-Object {$_.Name -match '^\d{8}\.txt$'} | Sort-Object Name | Select-Object -Last 1
foreach($line in [IO.File]::ReadLines($log.FullName)) {
 if($line.Contains('getDeviceMediaUrl') -or ($line.Contains('deviceCode') -and $line.Contains('mediaType'))) {$matchesFound.Add($line)}
 if($line -match 'TYKJ: ([0-9a-f]+)') {
  try {
   $plain=$decrypt.Invoke($null,@($Matches[1]))
   if($plain.Contains('getDeviceMediaUrl') -or ($plain.Contains('deviceCode') -and $plain.Contains('mediaType'))) {$matchesFound.Add($plain)}
  }catch{}
 }
}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'tianyi-private-requests.json'),(ConvertTo-Json -InputObject @($matchesFound | Select-Object -Last 14) -Compress),[Text.UTF8Encoding]::new($false))
Write-Output ('Matching media requests retained locally: '+$matchesFound.Count)
