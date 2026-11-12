$ErrorActionPreference = 'Stop'
$clientRoot = 'C:\Program Files (x86)\TYKJ'
$domain = [Reflection.Assembly]::LoadFrom((Join-Path $clientRoot 'TY.Domain.dll'))
$decrypt = $domain.GetType('TY.Domain.Helpers.XXTeaHelper').GetMethod('Decrypt', [type[]]@([string]))
$targets = @{}
foreach ($name in @((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'relay-config.json') -Raw | ConvertFrom-Json).relays | Where-Object {$_.type -eq 'tianyi' -and $_.key -match '^[A-Za-z0-9_-]+$'} | ForEach-Object {$_.key})) {
    $file = Join-Path $PSScriptRoot ($name + '-live.m3u')
    $line = [IO.File]::ReadAllLines($file) | Where-Object { $_.StartsWith('rtsp://') } | Select-Object -First 1
    $targets[([uri]$line).AbsolutePath] = @{Name=$name;File=$file;Old=$line;Latest=$line;UpdatedAt=([IO.File]::GetLastWriteTime($file))}
}
foreach ($log in Get-ChildItem (Join-Path $clientRoot 'logs') -Filter '*.txt' | Sort-Object Name) {
    foreach ($line in [IO.File]::ReadAllLines($log.FullName)) {
        if ($line -match 'TYKJ: ([0-9a-f]+)') {
            try {
                $plain = $decrypt.Invoke($null,@($Matches[1]))
                if ($plain -match '^url:(rtsp://\S+)\s+playUrl:') {
                    $candidate = ($Matches[1] -split '----')[0]
                    $key = ([uri]$candidate).AbsolutePath
                    if ($targets.ContainsKey($key)) {
                        $eventTime=[DateTime]::ParseExact($line.Substring(0,23),'yyyy-MM-dd HH:mm:ss.fff',[Globalization.CultureInfo]::InvariantCulture)
                        if($eventTime -gt $targets[$key].UpdatedAt){$targets[$key].Latest = $candidate;$targets[$key].UpdatedAt=$eventTime}
                    }
                }
            } catch {}
        }
    }
}
foreach ($target in $targets.Values) {
    if ($target.Latest -ne $target.Old) {
        [IO.File]::WriteAllLines($target.File,@('#EXTM3U',('#EXTINF:-1,'+$target.Name),'#EXTVLCOPT:rtsp-tcp',$target.Latest),[Text.UTF8Encoding]::new($false))
        Write-Output ($target.Name + ': updated from client log')
    } else { Write-Output ($target.Name + ': no newer address') }
}
