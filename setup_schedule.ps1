# My Little Lab - Windows 작업 스케줄러 등록
#   .\setup_schedule.ps1                    # 매일 설정 시각에 자동 수집 (서버가 꺼져 있어도 동작)
#   .\setup_schedule.ps1 -StartServerAtLogon # + 로그인할 때 대시보드 서버 자동 실행
#   .\setup_schedule.ps1 -Remove            # 등록 해제
param([switch]$StartServerAtLogon, [switch]$Remove)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $dir ".venv\Scripts\pythonw.exe"

if ($Remove) {
  foreach ($n in "MyLittleLab-Collect", "MyLittleLab-Server") {
    try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop; Write-Host "해제: $n" } catch {}
  }
  return
}
if (-not (Test-Path $pyw)) { throw "가상환경이 없습니다. 먼저 start_lab.bat 을 한 번 실행하세요." }

$cfgPath = Join-Path $dir "config.json"
$time = "08:00"
if (Test-Path $cfgPath) { $time = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).schedule.time }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\collect.py`" --scheduled" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At $time
Register-ScheduledTask -TaskName "MyLittleLab-Collect" -Action $action -Trigger $trigger -Settings $settings -Description "My Little Lab 매일 자동 수집" -Force | Out-Null
Write-Host "등록 완료: 매일 $time 자동 수집 (PC가 꺼져 있었다면 켜진 뒤 실행)"

if ($StartServerAtLogon) {
  $a2 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c set LAB_NO_BROWSER=1 && `"$pyw`" `"$dir\app.py`"" -WorkingDirectory $dir
  $t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  Register-ScheduledTask -TaskName "MyLittleLab-Server" -Action $a2 -Trigger $t2 -Settings $settings -Description "My Little Lab 대시보드 서버" -Force | Out-Null
  Write-Host "등록 완료: 로그인 시 대시보드 서버 자동 실행 → http://localhost:5050"
}
Write-Host "※ 수집 시각을 바꿨다면 이 스크립트를 다시 실행하세요."
