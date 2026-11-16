# 停止抖音直播自动互动（促销 + 欢迎 + 点赞）
#
# 协作式停止：创建 run_loop.stop 信号文件，实例收到后自行退出并清理。
# 判断"是否真的停掉" = 信号文件有没有被实例消费掉（只有 run_loop.py 会删它），
# 所以锁文件丢失/损坏（没有 PID）时同样能停——不依赖进程扫描、不杀进程。
# 历史教训（2026-10-31）：锁文件被外部删成 0 字节后，旧版 stop.ps1 判定
# "没有在运行中的实例"，实例却继续跑了 6 天。现在改为信号文件握手。
$ErrorActionPreference = 'Stop'
$Workspace = $PSScriptRoot
$LogFile   = Join-Path $Workspace 'loop.log'
$LockFile  = Join-Path $Workspace 'run_loop.lock'
$StopFlag  = Join-Path $Workspace 'run_loop.stop'
$HeartbeatMaxSeconds = 90   # 锁文件 mtime 超过这么久视为心跳已停

function Test-InstanceAlive {
    # 仅用于友好提示：锁文件存在 + PID 活着 + mtime 在心跳期内
    if (-not (Test-Path $LockFile)) { return $null }
    $pidText = Get-Content $LockFile -Raw -ErrorAction SilentlyContinue
    if (-not ($pidText -match '\d+')) { return $null }
    $lockPid = [int]$Matches[0]
    if (-not (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) { return $null }
    $age = ((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalSeconds
    if ($age -gt $HeartbeatMaxSeconds) { return $null }
    return $lockPid
}

function Remove-FlagFile {
    # 不用 Remove-Item：某些环境里有回收站包装会失败，残留的信号文件
    # 会让下次启动的实例立刻退出。直接 .NET 删除并校验。
    param([string]$Path)
    try { if (Test-Path $Path) { [System.IO.File]::Delete($Path) } } catch { }
    try { if (Test-Path $Path) { Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue } } catch { }
}

$lockPid = Test-InstanceAlive
if ($lockPid) {
    Write-Output ("找到运行中实例 (PID {0})，发送停止信号..." -f $lockPid)
} else {
    Write-Output '锁文件里没有有效实例信息，仍发送停止信号（在跑的实例都会收到）...'
}

Remove-FlagFile $StopFlag
New-Item -Path $StopFlag -ItemType File -Force | Out-Null

$deadline = (Get-Date).AddSeconds(15)
$consumed = $false
while ((Get-Date) -lt $deadline) {
    if (-not (Test-Path $StopFlag)) { $consumed = $true; break }
    Start-Sleep -Milliseconds 500
}

if ($consumed) {
    Write-Output '已停止'
    if (-not $lockPid) { Write-Output '（该实例的锁文件信息曾丢失，本次通过信号文件停止成功）' }
} else {
    Remove-FlagFile $StopFlag
    if ($lockPid) {
        Write-Output ("实例 15 秒内未响应停止信号 (PID {0})，可能已卡死。" -f $lockPid)
        Write-Output '确认是 run_loop 实例后，可手动在任务管理器结束该 python 进程。'
    } else {
        Write-Output '没有检测到运行中的实例（信号文件已清理）。'
    }
}

# 清理没有主人认领的残留锁文件（有活实例时它会自己写回 PID）
if (-not (Test-Path $StopFlag)) {
    if (-not (Test-InstanceAlive)) { Remove-FlagFile $LockFile }
}

if (Test-Path $LogFile) {
    Write-Output ''
    Write-Output '=== 最近 10 条日志 ==='
    try {
        Get-Content $LogFile -Encoding utf8 -Tail 10
    } catch {
        Write-Output '日志读取失败（可能正在被写入，不影响停止结果）'
    }
}
