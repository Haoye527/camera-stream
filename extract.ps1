$ErrorActionPreference = 'Stop'
$base = 'C:\Program Files (x86)\TYKJ'
$assembly = [Reflection.Assembly]::LoadFrom((Join-Path $base 'TY.Domain.dll'))
$decrypt = $assembly.GetType('TY.Domain.Helpers.XXTeaHelper').GetMethod('Decrypt', [type[]]@([string]))
$streams = [ordered]@{}
foreach ($log in Get-ChildItem (Join-Path $base 'logs') -Filter '*.txt' | Sort-Object Name) {
  foreach ($line in [IO.File]::ReadAllLines($log.FullName)) {
    if ($line -match 'TYKJ: ([0-9a-f]+)') {
      try {
        $plain = $decrypt.Invoke($null, @($Matches[1]))
        if ($plain -match '^url:(rtsp://\S+)\s+playUrl:') {
          $url = ($Matches[1] -split '----')[0]
          $uri = [uri]$url
          $streams[$uri.GetLeftPart([UriPartial]::Path)] = $url
        }
      } catch {}
    }
  }
}
$list = @('#EXTM3U')
$i = 0
foreach ($url in $streams.Values) {
  $i++
  $list += "#EXTINF:-1,Camera $i"
  $list += '#EXTVLCOPT:rtsp-tcp'
  $list += $url
}
[IO.File]::WriteAllLines((Join-Path $PSScriptRoot 'camera-live.m3u'), $list, [Text.UTF8Encoding]::new($false))
$streams.Values | ConvertTo-Json -AsArray | Set-Content (Join-Path $PSScriptRoot 'streams.json') -Encoding utf8
Write-Output "Extracted $i streams."

