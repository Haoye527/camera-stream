param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$config = 'C:\EasyNVR\configs\config.toml'
$currentBytes = [IO.File]::ReadAllBytes($config)
$bom = $currentBytes.Length -ge 3 -and $currentBytes[0] -eq 239 -and $currentBytes[1] -eq 187 -and $currentBytes[2] -eq 191
$encoding = [Text.UTF8Encoding]::new($bom, $true)
$currentText = [IO.File]::ReadAllText($config, $encoding)
$sections = [regex]::Matches($currentText, '(?ms)^\[RecordInfo\][^\r\n]*\r?\n.*?(?=^\[|\z)')
if ($sections.Count -ne 1) { throw 'Expected one RecordInfo section' }
$section = $sections[0]
$setting = [regex]::Matches($section.Value, '(?m)^[ \t]*DiskUsageRatio[ \t]*=[ \t]*(?<ratio>85\.95)(?=[ \t]*(?:#.*)?\r?$)')
if ($setting.Count -ne 1) { throw 'Native threshold must be 85.95 before switching' }
$value = $setting[0].Groups['ratio']
$offset = $section.Index + $value.Index
$updatedText = $currentText.Substring(0, $offset) + '95.95' + $currentText.Substring($offset + $value.Length)
$expected = '(?m)^[ \t]*DiskUsageRatio[ \t]*=[ \t]*95\.95(?=[ \t]*(?:#.*)?\r?$)'
if ([regex]::Matches($updatedText, $expected).Count -ne 1 -or $updatedText.Length -ne $currentText.Length -or
    $updatedText.Remove($offset, 5) -cne $currentText.Remove($offset, 5)) {
    throw 'Dry-run validation failed: unexpected configuration change'
}

$before = 'RecordInfo.DiskUsageRatio = 85.95'
$after = 'RecordInfo.DiskUsageRatio = 95.95'
if (!$Apply) {
    [pscustomobject]@{ path = $config; before = $before; after = $after; changed_values = 1; effective_rotation = 85.95; applied = $false } | ConvertTo-Json -Compress
    return
}

$installed = Get-CimInstance Win32_Service -Filter "Name='easynvr'"
if ($installed.State -ne 'Running' -or $installed.PathName -ne 'C:\EasyNVR\easynvr.exe') { throw 'Unexpected EasyNVR service state or path' }
$storage = Invoke-RestMethod -Uri 'http://127.0.0.1:18768/api/storage' -TimeoutSec 4
if ($storage.retention.status -ne 'waiting_for_native_config' -or $storage.retention.limit_percent -ne 85.95) { throw 'Rotation worker is not staged' }
if (10000 * [decimal]$storage.disk.used -ge 8595 * [decimal]$storage.disk.total) { throw 'Disk is already at the rotation threshold' }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path (Split-Path $config) "config.before-file-rotation-$stamp.toml"
$staged = Join-Path (Split-Path $config) "config.file-rotation-$stamp.tmp"
[IO.File]::WriteAllText($staged, $updatedText, $encoding)
$configReplaced = $false
try {
    Stop-Service -Name easynvr
    (Get-Service easynvr).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
    if ([IO.File]::ReadAllText($config, $encoding) -cne $currentText) { throw 'Configuration changed during restart' }
    [IO.File]::Replace($staged, $config, $backup)
    $configReplaced = $true
    Start-Service -Name easynvr
    (Get-Service easynvr).WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
    $verified = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $nvr = Invoke-WebRequest -Uri 'http://127.0.0.1:10000/cloud/' -TimeoutSec 2
            $storage = Invoke-RestMethod -Uri 'http://127.0.0.1:18768/api/storage' -TimeoutSec 2
            if ($nvr.StatusCode -eq 200 -and $storage.retention.status -eq 'ready' -and $storage.retention.limit_percent -eq 85.95) {
                $verified = $true
                break
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (!$verified) { throw 'Could not verify the EasyNVR site and active rotation' }
    [pscustomobject]@{ native_fallback = 95.95; backup = $backup; retention = $storage.retention } | ConvertTo-Json -Compress -Depth 4
} catch {
    if ($configReplaced) {
        if ((Get-Service easynvr).Status -ne 'Stopped') { Stop-Service -Name easynvr }
        (Get-Service easynvr).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
        Copy-Item -LiteralPath $backup -Destination $config -Force
    }
    if ((Get-Service easynvr).Status -eq 'Stopped') { Start-Service -Name easynvr }
    throw
} finally {
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged -Force }
}
