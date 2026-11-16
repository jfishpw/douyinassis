# 启动抖音直播自动互动（促销 + 欢迎 + 点赞）
$ErrorActionPreference = 'Stop'
$Workspace = $PSScriptRoot
$ConfigFile = Join-Path $Workspace 'config.json'

# 1. 读取配置里的直播间地址
if (Test-Path $ConfigFile) {
    $Config = Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $Url = $Config.live_url
} else {
    $Url = 'https://live.douyin.com/XXXX'
    Write-Output '未找到 config.json，使用内置默认地址'
}
if ([string]::IsNullOrWhiteSpace($Url)) {
    Write-Output 'config.json 里 live_url 为空，请先配置'
    exit 1
}

# 2. 启动 Chrome（带 CDP）
#    注意：不能按进程名判断 Chrome 是否在运行——WorkBuddy 自带的 chromium 也叫 chrome.exe，
#    按名字判断会误以为"Chrome 已在运行"而永不启动真正的 Chrome（2026-11-02 实际踩到）。
#    正确做法：按可执行文件路径识别，并优先看调试端口是否已就绪。
# 依次尝试：config 指定 → 注册表 App Paths → 常见安装位置 → Edge 兜底
# 换电脑最容易失效的就是"写死安装路径"，所以这里全部走探测
function Resolve-BrowserPath {
    param([string]$ExeName, $Cfg, [string]$CfgKey)
    if ($Cfg -and $Cfg.browser -and $Cfg.browser.$CfgKey) {
        $p = [string]$Cfg.browser.$CfgKey
        if (Test-Path $p) { return $p }
        Write-Warning ('config.json 里的 ' + $CfgKey + ' 无效，已忽略：' + $p)
    }
    foreach ($hive in @('HKLM:', 'HKCU:')) {
        $reg = $hive + '\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\' + $ExeName
        if (Test-Path $reg) {
            $v = (Get-ItemProperty -Path $reg -ErrorAction SilentlyContinue).'(default)'
            if ($v -and (Test-Path $v)) { return $v }
        }
    }
    if ($ExeName -eq 'chrome.exe') {
        $cands = @(
            (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
            (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
        )
    } else {
        $cands = @(
            (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
            (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
        )
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

$Chrome = Resolve-BrowserPath -ExeName 'chrome.exe' -Cfg $Config -CfgKey 'chrome_path'
if (-not $Chrome) {
    $Chrome = Resolve-BrowserPath -ExeName 'msedge.exe' -Cfg $Config -CfgKey 'edge_path'
    if ($Chrome) { Write-Output ('未找到 Chrome，改用 Edge：' + $Chrome) }
}
if (-not $Chrome) {
    Write-Output '未找到 Chrome 或 Edge。请安装 Chrome，或在 config.json 里加一行指定路径：'
    Write-Output '  "browser": { "chrome_path": "你的 chrome.exe 完整路径" }'
    exit 1
}
$BrowserVer = ''
try { $BrowserVer = (Get-Item $Chrome).VersionInfo.ProductVersion } catch {}
if ($BrowserVer) { Write-Output ('浏览器: ' + $Chrome + ' (v' + $BrowserVer + ')') }
else { Write-Output ('浏览器: ' + $Chrome) }

# 默认配置目录要**按浏览器区分**：Chrome 与 Edge 的用户数据目录不同。
# （早前 Edge 兜底时仍用 Chrome 的目录，等于给 Edge 开了个空配置，登录态全丢。）
function Get-DefaultUserData {
    param([string]$ExeLeaf)
    if ($ExeLeaf -eq 'msedge.exe') {
        return (Join-Path $env:LOCALAPPDATA (Join-Path 'Microsoft\Edge' 'User Data'))
    }
    return (Join-Path $env:LOCALAPPDATA (Join-Path 'Google\Chrome' 'User Data'))
}

$ExeLeaf = Split-Path $Chrome -Leaf
$UserData = Get-DefaultUserData -ExeLeaf $ExeLeaf
if ($Config -and $Config.browser -and $Config.browser.user_data_dir) {
    $custom = [string]$Config.browser.user_data_dir
    if (Test-Path $custom) { $UserData = $custom }
    else { Write-Warning ('config.json 里的 user_data_dir 不存在，改用默认配置目录：' + $custom) }
}
Write-Output ('配置目录: ' + $UserData)

# --user-data-dir 必须**显式传**：Chrome M136+ 用默认目录时会静默忽略 --remote-debugging-port；
# 老版本 Chrome 与 Edge 传了也无害，所以统一都传，一套参数兼容所有版本。
$LaunchArgs = @('--remote-debugging-port=9222', '--remote-allow-origins=*', ('--user-data-dir=' + $UserData))
$ManualCmd = '"' + $Chrome + '" ' + ($LaunchArgs -join ' ')

function Get-RunningBrowser {
    @(Get-CimInstance Win32_Process -Filter ("Name='" + $ExeLeaf + "'") -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $Chrome })
}

$cdpOk = $false
try { Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/version' -UseBasicParsing -TimeoutSec 3 | Out-Null; $cdpOk = $true } catch {}

if ($cdpOk) {
    Write-Output '浏览器已在运行，调试端口可用'
} else {
    $real = Get-RunningBrowser
    if ($real.Count -gt 0) {
        Write-Output ('检测到浏览器已运行（' + $real.Count + ' 个进程）但没开调试端口 9222')
        Write-Output ('请先完全退出浏览器（任务管理器里结束 ' + $ExeLeaf + '，或右键托盘图标退出）后再运行本脚本')
        Write-Output '或手动这样启动：'
        Write-Output ('  ' + $ManualCmd)
        exit 1
    }
    Start-Process -FilePath $Chrome -ArgumentList $LaunchArgs
    Write-Output '已启动浏览器（带 CDP 调试端口），等待就绪…'
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/version' -UseBasicParsing -TimeoutSec 2 | Out-Null; $cdpOk = $true; break } catch {}
    }
}

# 3. 检查 CDP
if (-not $cdpOk) {
    Write-Output 'CDP 失败：调试端口 9222 在 15 秒内没有就绪'
    Write-Output '常见原因：浏览器启动被安全软件拦截；或用了受限的配置目录。可手动执行下面这条命令排查：'
    Write-Output ('  ' + $ManualCmd)
    exit 1
}
Write-Output 'CDP OK: 9222'

# 4. 打开抖音直播间（已有抖音 tab 则不重复开）
$tabs = Invoke-RestMethod -Uri 'http://127.0.0.1:9222/json' -UseBasicParsing -TimeoutSec 5
$dtab = @()
foreach ($t in $tabs) { if ($t.type -eq 'page' -and $t.url -like '*douyin*') { $dtab = $t; break } }
if ($dtab.Count -eq 0) {
    Invoke-RestMethod -Uri ('http://127.0.0.1:9222/json/new?' + $Url) -Method Put -UseBasicParsing -TimeoutSec 15 | Out-Null
    Write-Output ('已打开抖音直播间: ' + $Url)
}
else { Write-Output ('抖音直播间已打开: ' + $dtab.title) }

# 5. 启动互动循环（促销 + 欢迎 + 点赞）；已有实例在跑就只确保环境就绪
# 存活判断只看锁文件：PID 活着 + mtime 心跳新鲜（实例每 10s touch 一次）。
# 绝不按命令行匹配进程——AI 助手执行层产生的 python 包装进程命令行里
# 可能带有 run_loop.py 字样，会误判/误杀（2026-10-24 实战踩坑）。
$LockFile = Join-Path $Workspace 'run_loop.lock'
$lockPid = $null
if (Test-Path $LockFile) {
    $pidText = Get-Content $LockFile -Raw -ErrorAction SilentlyContinue
    if ($pidText -match '\d+') {
        $candidate = [int]$Matches[0]
        $age = ((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalSeconds
        if ((Get-Process -Id $candidate -ErrorAction SilentlyContinue) -and ($age -lt 90)) {
            $lockPid = $candidate
        }
    }
}
# 控制面板地址（config.json 的 panel 段）
$PanelUrl = 'http://127.0.0.1:8765'
$PanelEnabled = $true
if ($Config.panel) {
    if ($Config.panel.port) { $PanelUrl = 'http://127.0.0.1:' + $Config.panel.port }
    if ($Config.panel.enabled -eq $false) { $PanelEnabled = $false }
}

if ($lockPid) {
    Write-Output ('自动互动已在运行中 (PID ' + $lockPid + ')，已确保 Chrome 和直播间就绪')
    Write-Output '连接如中断会自动恢复；需要重启请先运行 stop.ps1'
    if ($PanelEnabled) {
        Write-Output ('控制面板: ' + $PanelUrl)
        Start-Process $PanelUrl
    }
    exit 0
}
# 选一个**真正装了依赖**的 Python，而不是只看命令名是否存在。兼容这几类差异：
#   · PATH 里的 python 可能是"干净"的（没 requests）→ 逐个验依赖
#   · 可能是 Microsoft Store 的应用别名占位符（WindowsApps\python.exe，运行只会弹商店）→ 跳过
#   · 有的机器只有 py 启动器（py -3），PATH 里没有 python
#   · 有的机器 python 版本过老（<3.8）→ 给提示
function Test-PythonDeps {
    param([string]$Exe, [string[]]$PreArgs = @())
    if (-not $Exe) { return $false }
    if ($Exe -like '*\WindowsApps\*') { return $false }
    try {
        & $Exe @PreArgs -c "import requests, websocket" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

$PyCandidates = @()
if ($Config -and $Config.runtime -and $Config.runtime.python_path) {
    $PyCandidates += , @([string]$Config.runtime.python_path)
}
foreach ($name in @('python', 'python3')) {
    $src = (Get-Command $name -ErrorAction SilentlyContinue).Source
    if ($src) { $PyCandidates += , @($src) }
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PyCandidates += , @('py', '-3')
}

$PythonExe = $null
$PythonArgs = @()
$seen = @()
foreach ($c in $PyCandidates) {
    $key = ($c -join ' ')
    if ($seen -contains $key) { continue }
    $seen += $key
    $exe = $c[0]
    $pre = @()
    if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
    if (Test-PythonDeps -Exe $exe -PreArgs $pre) {
        $PythonExe = $exe
        $PythonArgs = $pre
        break
    }
    Write-Output ('  · 跳过（未装依赖或不可用）: ' + $key)
}
if (-not $PythonExe) {
    Write-Output '没有找到装好依赖的 Python，主程序无法启动。请任选一种方式：'
    Write-Output '  1) 给现有 Python 装依赖：  pip install requests websocket-client'
    Write-Output '  2) 在 config.json 里指定 Python 路径（需已装依赖）：'
    Write-Output '     "runtime": { "python_path": "C:\Path\To\python.exe" }'
    exit 1
}

$PyVer = ''
try { $PyVer = (& $PythonExe @PythonArgs -c "import sys;print(sys.version.split()[0])").Trim() } catch {}
if ($PyVer) { Write-Output ('Python: ' + $PythonExe + ' (v' + $PyVer + ')') }
else { Write-Output ('Python: ' + $PythonExe) }
try {
    if ($PyVer -and ([version]$PyVer -lt [version]'3.8')) {
        Write-Output '⚠ 该 Python 版本低于 3.8，可能跑不起来，建议升级到 3.10+'
    }
} catch {}
$py = Start-Process -FilePath $PythonExe -ArgumentList (@() + $PythonArgs + @('-X', 'utf8', (Join-Path $Workspace 'run_loop.py'))) -WindowStyle Hidden -PassThru
Write-Output ('自动互动已启动 (PID ' + $py.Id + ')')
Write-Output ('日志: ' + $Workspace + '\loop.log')
Write-Output ('改配置: ' + $Workspace + '\config.json（也可用控制面板在线改）')
Write-Output ('停止: ' + $Workspace + '\stop.ps1')
if ($PanelEnabled) {
    Write-Output ('控制面板: ' + $PanelUrl)
    Start-Sleep -Seconds 2
    Start-Process $PanelUrl
}
