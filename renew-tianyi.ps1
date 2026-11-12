param([switch]$TestOnly,[string]$Camera='all')
$ErrorActionPreference='Stop'
$renewStage='load_official_client'
trap {
 $reason='renewal_failed'
 if($_.Exception.Message -match 'sign-in required|No saved access token'){$reason='official_sign_in_required'}
 if($_.Exception.Message -match 'Returned stream does not match target camera'){$reason='stream_path_changed'}
 if($_.Exception.Message -match 'Official playback renewal refused'){$reason='official_media_refused'}
 $failedCameras=if($Camera -eq 'all'){@((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'relay-config.json') -Raw | ConvertFrom-Json).relays | Where-Object {$_.type -eq 'tianyi' -and $_.key -match '^[A-Za-z0-9_-]+$'} | ForEach-Object {$_.key})}else{@($Camera)}
 foreach($failedCamera in $failedCameras){
  $failureStatus=@{camera=$failedCamera;ok=$false;reason=$reason;stage=$renewStage;errorType=$_.Exception.GetType().FullName;at=[DateTimeOffset]::Now.ToString('o')}
  if($_.Exception.Response){try{$failureStatus.http=[int]$_.Exception.Response.StatusCode}catch{}}
  if($_.Exception.InnerException){$failureStatus.innerErrorType=$_.Exception.InnerException.GetType().FullName}
  $failureStatus.line=$_.InvocationInfo.ScriptLineNumber
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot ($failedCamera+'-renew-status.json')),($failureStatus | ConvertTo-Json -Compress))
 }
 Write-Output ('Automatic signature renewal failed: '+$reason+'; stage='+$renewStage+'; type='+$_.Exception.GetType().FullName)
 exit 1
}
$clientRoot='C:\Program Files (x86)\TYKJ'
$domain=[Reflection.Assembly]::LoadFrom((Join-Path $clientRoot 'TY.Domain.dll'))
$helper=$domain.GetType('TY.Domain.Helpers.XXTeaHelper')
$decrypt=$helper.GetMethod('Decrypt',[type[]]@([string]))
$decryptKey=$helper.GetMethod('Decrypt',[type[]]@([string],[string]))
$mac=$domain.GetType('TY.Domain.Helpers.ComputerSignHelper').GetMethod('MacId').Invoke($null,@())
$ini=[IO.File]::ReadAllLines((Join-Path $clientRoot 'user.ini'))
$encrypted=($ini | Where-Object {$_ -match '^user='} | Select-Object -First 1) -replace '^user=',''
if(-not $mac){throw 'Cannot obtain official client machine identifier'}
$session=$decryptKey.Invoke($null,@($encrypted,$mac)) | ConvertFrom-Json
if(-not $session.accessToken){throw 'Official client sign-in required'}
$domain.GetType('TY.Domain.Net.HttpMgr').GetField('accessToken').SetValue($null,[string]$session.accessToken)
$renewStage='resolve_device_mapping'
$targets=@{}
$mappingFile=Join-Path $PSScriptRoot 'tianyi-private-device-map.json'
$oldMapping=@()
if(Test-Path -LiteralPath $mappingFile){try{$oldMapping=Get-Content -LiteralPath $mappingFile -Raw | ConvertFrom-Json}catch{}}
foreach($name in @((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'relay-config.json') -Raw | ConvertFrom-Json).relays | Where-Object {$_.type -eq 'tianyi' -and $_.key -match '^[A-Za-z0-9_-]+$'} | ForEach-Object {$_.key})) {
 $file=Join-Path $PSScriptRoot ($name+'-live.m3u')
 $url=[IO.File]::ReadAllLines($file) | Where-Object {$_.StartsWith('rtsp://')} | Select-Object -First 1
 $expectedPath=([uri]$url).AbsolutePath
 # Device identity is the saved official deviceCode; media paths may rotate remotely.
 $saved=$oldMapping | Where-Object {$_.name -eq $name -and $_.deviceCode} | Select-Object -First 1
 $targets[$expectedPath]=@{name=$name;file=$file;deviceCode=$saved.deviceCode}
}
$pending=$null
foreach($log in Get-ChildItem (Join-Path $clientRoot 'logs') -Filter '*.txt' | Where-Object {$_.Name -match '^\d{8}\.txt$'} | Sort-Object Name) {
 foreach($line in [IO.File]::ReadLines($log.FullName,[Text.Encoding]::UTF8)) {
  if($line.Contains('getDeviceMediaUrl') -and $line -match 'Info\s*:\s*([A-Za-z0-9_-]{12,})') {$pending=$Matches[1]}
  if($pending -and $line -match 'TYKJ: ([0-9a-f]+)') {
   try {
    $plain=$decrypt.Invoke($null,@($Matches[1]))
    if($plain -match '^url:(rtsp://\S+)\s+playUrl:') {
     $url=($Matches[1] -split '----')[0];$key=([uri]$url).AbsolutePath
     if($targets.ContainsKey($key) -and -not $targets[$key].deviceCode){$targets[$key].deviceCode=$pending}
    }
   }catch{}
  }
 }
}
$newMapping=@($targets.Keys | ForEach-Object {@{name=$targets[$_].name;path=$_;deviceCode=$targets[$_].deviceCode}})
if(@($newMapping | Where-Object {-not $_.deviceCode}).Count -eq 0){[IO.File]::WriteAllText($mappingFile,(ConvertTo-Json -InputObject $newMapping -Compress),[Text.UTF8Encoding]::new($false))}
$askType=$domain.GetType('TY.Domain.Net.HttpAsk')
$dictType=[Collections.Generic.Dictionary[string,string]]
$ctor=$askType.GetConstructor([type[]]@([string],[string],[string],$dictType,[object[]]))
$build=$askType.GetMethod('GetAllParam',[Reflection.BindingFlags]'Instance,NonPublic')
$results=@()
foreach($target in $targets.Values) {
 if($Camera -ne 'all' -and $target.name -ne $Camera){continue}
 if(-not $target.deviceCode){throw ('Target device mapping unavailable: '+$target.name)}
 $dict=[Collections.Generic.Dictionary[string,string]]::new()
 $dict.Add('deviceCode',$target.deviceCode);$dict.Add('mediaType','0')
 $url='https://ehome.21cn.com/gw/accountservice/getDeviceMediaUrl'
 $ask=$ctor.Invoke([object[]]@('getDeviceMediaUrl','POST',$url,$dict,$null))
 $body=$build.Invoke($ask,@())
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 $renewStage='official_media_request'
 $reply=Invoke-WebRequest -UseBasicParsing -Uri $url -Method Post -ContentType 'application/x-www-form-urlencoded' -Body $body -TimeoutSec 20
 $raw=$reply.Content
 [IO.File]::WriteAllText((Join-Path $PSScriptRoot ('tianyi-private-'+$target.name+'-renew.json')),$raw,[Text.UTF8Encoding]::new($false))
 $renewStage='parse_official_response'
 $obj=$raw | ConvertFrom-Json
 if($obj.result -ne 0 -or -not ([string]$obj.url).StartsWith('rtsp://')){
  $failure=@{camera=$target.name;ok=$false;result=$obj.result;at=[DateTimeOffset]::Now.ToString('o')}
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot ($target.name+'-renew-status.json')),($failure | ConvertTo-Json -Compress))
  throw ('Official playback renewal refused; camera='+$target.name+' result='+$obj.result+'; client login may need refreshing')
 }
 $newUrl=([string]$obj.url -split '----')[0]
 # The HTTPS response is for the explicit deviceCode just submitted. A signed
 # RTSP path is a temporary locator, not an immutable camera identifier.
 $mediaUri=[uri]$newUrl
 if(-not $mediaUri.IsAbsoluteUri -or $mediaUri.Scheme -ne 'rtsp' -or -not $mediaUri.Host){throw 'Invalid official media URL'}
 $renewStage='save_playback_address'
 if(-not $TestOnly){
  $pendingFile=$target.file+'.pending'
  [IO.File]::WriteAllLines($pendingFile,@('#EXTM3U',('#EXTINF:-1,'+$target.name),'#EXTVLCOPT:rtsp-tcp',$newUrl),[Text.UTF8Encoding]::new($false))
  [IO.File]::Replace($pendingFile,$target.file,($target.file+'.backup'))
  $mapping=@($targets.Keys | ForEach-Object {
   $entry=$targets[$_]
   $path=if($entry.name -eq $target.name){$mediaUri.AbsolutePath}else{
    $line=[IO.File]::ReadAllLines($entry.file) | Where-Object {$_.StartsWith('rtsp://')} | Select-Object -First 1
    ([uri]$line).AbsolutePath
   }
   @{name=$entry.name;path=$path;deviceCode=$entry.deviceCode}
  })
  [IO.File]::WriteAllText($mappingFile,(ConvertTo-Json -InputObject $mapping -Compress),[Text.UTF8Encoding]::new($false))
 }
 $success=@{camera=$target.name;ok=$true;http=[int]$reply.StatusCode;result=$obj.result;applied=(-not $TestOnly);at=[DateTimeOffset]::Now.ToString('o')}
 [IO.File]::WriteAllText((Join-Path $PSScriptRoot ($target.name+'-renew-status.json')),($success | ConvertTo-Json -Compress))
 $results+=$success
}
$results | ConvertTo-Json -Depth 4 -Compress
