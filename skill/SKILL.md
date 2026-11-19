---
name: douyin-live-interact
display_name: 抖音直播间自动互动
display_name_en: Douyin Live Auto Interaction
version: 1.4.6
author: "jfishpw"
category: content
allowed-tools: Read, Write, Edit, Bash
description: 抖音网页版直播间自动互动。通过 CDP 控制本机 Chrome，定时发送互动话术、自动欢迎新进用户、空闲时按住 Z 键持续点赞；自带本地控制面板，可独立暂停或恢复任一功能、在线修改全部参数、查看运行状态与日志，支持长期挂机（断线自动重连、未开播自动刷新）。当用户需要抖音直播间自动评论、自动欢迎新用户、自动点赞、直播间自动互动脚本，或需要加控制面板与开关、暂停或继续点赞与欢迎、在线调整话术频率、排查不互动不点赞原因时使用本技能。适用于 Windows 本机 Chrome。
description_zh: 抖音网页版直播间自动互动：定时话术、自动欢迎新用户、按住 Z 键持续点赞；自带本地控制面板，可单独暂停或恢复任一功能、在线改参数、看运行状态与日志，支持断线重连与未开播自动刷新。
description_en: Automate a Douyin web live room. Scheduled messages, auto-welcome for new viewers, and continuous likes by holding Z. A local panel can pause or resume each feature, edit settings live and show status and logs; auto-reconnect for long unattended runs.
examples_zh:
  - "帮我在 D:/live 部署抖音直播间自动互动，话术和发送频率我来定"
  - "帮我暂停点赞，只保留欢迎和话术"
  - "抖音直播间一直不互动，帮我排查原因"
examples_en:
  - "Deploy the Douyin live automation to D:/live, I will set the message and interval"
  - "Pause likes but keep welcome and promo running"
  - "The Douyin live room is not interacting at all, help me debug it"
---

# 抖音直播间自动互动（douyin-live-interact）

## Overview

本技能提供一套配置驱动的抖音网页版直播间自动互动脚本：通过 CDP 控制带调试端口的本机 Chrome，同时运行四个循环——定时发送互动话术（promo，支持多条轮换/随机）、欢迎新进直播间用户（welcome）、空闲时按住 Z 键点赞（like）、以及**编码复读（code_echo）**：读取 Windows 实时辅助字幕，听到主播报商品编码时自动发到公屏。参数集中在 `config.json`，并提供**本地控制面板**（`http://127.0.0.1:8765`）在线暂停/继续各功能、修改全部设置、查看实时状态与日志。

## 前置条件

- Windows 本机 + Chrome 浏览器（**安装路径自动探测**：config 指定 → 注册表 App Paths → Program Files / Program Files (x86) / `%LOCALAPPDATA%` 用户级安装 → 仍找不到时回退 Edge；**浏览器版本不限**，新旧 Chrome 都能带调试端口，Edge 用 Edge 自己的默认配置目录）
- Python 3.8+（建议 3.10+），安装依赖：`pip install requests websocket-client`
- **解释器自动挑选**：脚本按「依赖是否装好」选 Python，候选顺序 `runtime.python_path` → PATH 的 `python`/`python3` → `py -3`；会跳过 Microsoft Store 的 `WindowsApps\python.exe` 别名占位符；全不合格时明确报错并给出 pip 命令
- **编码复读额外依赖**：`pip install uiautomation`（内部依赖 comtypes），并且需要 **Windows 11 的实时辅助字幕**（设置 → 辅助功能 → 字幕，或 `Win+Ctrl+L`）。没装该依赖时其余功能不受影响，只会在日志里提示一次
- Chrome 能以 `--remote-debugging-port=9222` 启动（脚本内已处理）
- 抖音网页版已登录（用日常 Chrome 配置目录启动即可带上登录态）
- 点赞依赖抖音 PC 网页默认快捷键 Z（直播间页面聚焦时生效）

## 工作流程

### Step 1：部署脚本

将 `scripts/` 下全部文件 + 根目录的 `panel.html` 复制到同一个工作目录（如 `D:\douyin-live\`）：

- `panel.html` — 控制面板页面（放在与 run_loop.py 同目录，缺了面板会 500）
- `cdp_client.py` — CDP 客户端库（被 run_loop.py 引用，勿单独改名）
- `run_loop.py` — 互动主循环 + 面板服务
- `config.example.json` — 配置模板，复制为 `config.json` 后编辑
- `start.ps1` / `stop.ps1` — Windows 一键启动/停止

### Step 2：编辑 config.json

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `live_url` | 直播间地址（start.ps1 用它打开页面） | `https://live.douyin.com/XXXX` |
| `browser.chrome_path` | 浏览器 exe 完整路径。**留空即自动探测**，换电脑一般不用填 | 空 |
| `browser.user_data_dir` | 浏览器配置目录。**留空则用 Chrome 默认配置目录并显式传入**（Chrome M136+ 不传该参数会忽略调试端口；路径由 `%LOCALAPPDATA%` 拼出） | 空 |
| `runtime.python_path` | 指定运行用的 Python（需已装 `requests`/`websocket-client`）。留空则由脚本按依赖自动挑选 | 空 |
| `jitter.enabled` | 间隔随机化：所有间隔在设定值上下浮动，避免固定节奏 | `true` |
| `jitter.ratio` | 浮动比例，`0.1` = ±10%，允许 0~0.5（0%~50%） | `0.1` |
| `promo.enabled` | 是否发送互动话术 | `true` |
| `promo.message` | 互动话术内容。**支持多行，每行一条话术**（单行 ≤200 字，空行自动忽略） | `欢迎光临` |
| `promo.mode` | 多条话术的发送方式：`sequential` 顺序轮流 / `random` 随机（不与上一条重复） | `sequential` |
| `promo.interval` | 话术发送间隔（秒），**最快 10 秒**（下限） | `300` |
| `welcome.enabled` | 是否欢迎新用户 | `true` |
| `welcome.message` | 欢迎文案（自动加 `@用户名` 前缀；**仅支持单行**） | `欢迎光临` |
| `welcome.poll_interval` | 新用户检测频率（秒），最快 1 秒 | `2.5` |
| `welcome.gap` | 两条欢迎最小间隔（秒），0~120 | `6` |
| `welcome.history` | 回看多少条聊天消息（20~500，直播间越忙越要大） | `200` |
| `like.enabled` | 是否自动点赞 | `true` |
| `like.interval` | 每轮点赞间隔（秒），**最快 1 秒**（下限） | `5` |
| `like.key` | 点赞快捷键 | `z` |
| `like.hold` | 每轮按住时长（秒），0.3~60；**不是间隔，不参与随机化** | `2` |
| `code_echo.enabled` | 编码复读开关：读字幕里的编码并发到公屏 | `true` |
| `code_echo.template` | 复读话术，必须含 `{code}` 占位符 | `编码 {code}` |
| `code_echo.cooldown` | 同一编码冷却（秒，10~600） | `30` |
| `code_echo.per_minute` | 每分钟最多发几条（1~10） | `2` |
| `code_echo.per_code` | 同一编码一场最多发几次（1~20） | `2` |
| `panel.enabled` | 是否启用本地控制面板 | `true` |
| `panel.port` | 面板端口（改了要重启） | `8765` |

> 三个 `enabled` 就是"暂停/继续"开关：控制面板上的开关直接写这三个字段，循环每轮重读配置，**改完立即生效、不用重启**。
>
> **间隔随机化 + 频率上限**：所有间隔（发送间隔、检测频率、欢迎间隔、点赞间隔）在设定值上下按 `jitter.ratio` 随机浮动，例如 200 秒 ±10% → 每次 180~220 秒；关闭后严格按设定值。每个间隔还有**最小值（最快限制）**，浮动结果也不会低于它——`10 / 1 / 0 / 1` 秒分别是促销、检测、欢迎间隔、点赞的下限。面板上每个输入框后面会实时显示"允许范围 + 当前实际浮动区间"。

### Step 3：启动

方式 A（推荐，Windows）：双击 `start.ps1`，输出 `自动互动已启动 (PID xxxx)` 即成功，随后会自动打开控制面板。

> **双击 .ps1 没反应 / 窗口一闪而过？** 这是 PowerShell 执行策略限制，两种解法（任选其一）：
> 1. **右键 `start.ps1` →「使用 PowerShell 运行」**（一次性，最省事，stop.ps1 同样适用）；
> 2. 或先执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`（提示选 Y），之后双击即可正常跑。
>
> 仍看不到输出时，在 PowerShell 里手动执行 `& 'D:\live\start.ps1'` 查看具体报错。

方式 B（手动）：先以调试端口启动 Chrome 并打开直播间，再执行：

```
python run_loop.py
```

启动流程内部逻辑：先起控制面板（端口占用会顺延 10 个）→ 等评论框出现（最长约 1 分钟）→ 依次启动促销、欢迎、点赞三个后台循环 → 主线程保活。

### Step 4：控制面板（在线调整，推荐）

浏览器打开 `http://127.0.0.1:8765`（或 start.ps1 自动打开）：

- **功能开关**：促销 / 欢迎 / 点赞 三个开关各自独立（只改对应 `enabled`，不动其他设置）+ 全部暂停/全部恢复，点一下 1~3 秒内生效
- **详细设置**：直播间地址、话术内容（**多条话术分行填写，可选顺序/随机发送**）与间隔、欢迎文案与频率、点赞参数、面板端口，保存即热生效（端口除外，需重启）
- **编码复读**：复读话术（含 `{code}`）、同码冷却、每分钟上限、同码次数上限，四项都可在线改；状态区显示「复读次数 / 最近编码」
- **节奏随机化与频率限制**：一个开关 + 浮动比例（0%~50%，默认 ±10%）；每个间隔输入框后面实时显示「允许范围、最快限制、按当前设定的实际浮动区间」，所见即所存
- **手改 config.json 同样有效**：脚本每 1~10 秒检查文件修改时间，变了就自动重载并校验；解析失败/内容有误会保留当前配置继续跑，绝不因一次手误清空配置（`utf-8-sig` 读取，带 BOM 的文件也支持）
- **运行状态**：CDP 连接状态、PID、运行时长、已欢迎人数、点赞轮数、促销次数
- **实时日志**：最近 30 条，每 2 秒刷新

面板只监听 `127.0.0.1`，仅本机可访问；`panel.enabled=false` 可整体关闭。

### Step 5：验证

- 查看工作目录下 `loop.log`：应有 `配置加载完成`、`[面板] 控制面板已启动`、`评论框 ok 发送按钮 ok`、`就绪，启动 促销 + 欢迎 + 点赞 三个循环`、`[点赞] 第 1 轮`、`[欢迎]`、`[促销]` 等记录
- 直播间页面能看到话术发出、新用户被 `@`、点赞数增长
- **若一直是 `评论框 -- 发送按钮 --`**：看紧随其后的 `页面实况` 行，`text` 为「直播加载中」就是主播还没开播，放着等即可（脚本会自己刷页面，开播后自动就绪）

### Step 6：停止

双击 `stop.ps1`。**判断是否真的停掉 = `run_loop.stop` 信号文件有没有被实例消费掉**（只有 run_loop.py 会删它），所以锁文件丢失/损坏时同样能停；绝不扫描进程、绝不强杀。停止后 Chrome 和直播间不受影响。15 秒内未被消费会提示"没有检测到运行中的实例"并清理信号文件。

## 实现要点（改代码前必读）

以下点赞机制均为 2026-10 在 live.douyin.com 实测确认，改动前先读：

- **点赞是"按住"不是"单击"**：抖音网页版规则为按住 Z 不放持续点赞、松开才停，单击基本无效。
- **发送前必须先激活播放器**：快捷键监听器挂在播放器上，未激活时按键完全无效。用 JS `living-container.focus()` 激活即可，无需真实鼠标点击（点击也不影响直播播放，但没必要）。
- **keyDown 必须带 text 参数**：不带 `text` 不触发 keypress，实测收不到点赞。
- **长按期间要补发 autoRepeat 按键**：真实长按时操作系统会持续发送重复 keydown；只发单个 keyDown 赞会很少。实现为每 0.4 秒补发一次 `autoRepeat` keydown，实测 3 秒按住约 2 个赞。
- **点赞计数有延迟**：赞发出后直播间"本场点赞"计数约 1-2 秒后才跳，属正常现象，不要因此重复按键。
- **单实例锁**：重复启动第二个实例会自动退出——多个实例会互相抢评论框、重复欢迎同一个人（实战踩坑）。`start.ps1` 检测到已在运行时就只确保 Chrome/直播间就绪。存活判断只看锁文件（PID + mtime 心跳），不扫描进程。
  - **锁必须加在远离字节 0 的位置**（本实现用偏移 100000）：msvcrt 若锁住字节 0，外部 `Get-Content` 读不到里面的 PID。
  - **心跳要顺带重写 PID**：锁文件被外部删成 0 字节后，如果心跳只 touch 不写 PID，`stop.ps1` 会永远以为"没在运行"（2026-10-31 实例因此空转 6 天没停掉）。
  - **退出时先关锁句柄再删文件**，否则 Windows 上 `os.remove` 删不掉，留下 5 字节残留锁。
- **停止是协作式的**：`stop.ps1` 只创建 `run_loop.stop` 信号文件，实例收到后自行退出并清理。**绝不按命令行匹配进程、绝不强杀**——Windows 上 AI 助手/编辑器的执行层会产生命令行里带 `run_loop.py` 字样的无关 python 进程，按命令行匹配会误杀（2026-10-24 实战踩坑，曾误杀助手进程）。
- **断线自动重连**：`serve_forever()` 外层循环守护连接，Chrome 被关/页面崩溃/直播间未开始都会按 10s→60s 退避重试；Chrome 恢复后自动继续，无需手动重启。抖音页面被关时会按 `live_url` 自动重新打开。
- **未开播会自动等待并自愈**：主播未开播时直播间只有"直播加载中"的空壳页，评论框/发送按钮根本不存在（任何选择器都找不到，不是脚本问题）。`wait_ready` 60 秒仍不就绪会 `Page.reload` 刷一次页面再进退避重连，开播后最多 1~2 分钟自动就绪，无需手动刷新或重启。
- **页面实况诊断行**：`wait_ready` 在 20 秒时打印一行 `页面实况: {url, title, ready, video, chat, text}`。`text` 含「直播加载中」= 未开播；`video` 为 1 也不代表开播（播放器容器一直在），要看 `chat` 是否 > 0。
- **别用 `document.cookie` 判断登录**：抖音的 `sessionid` 是 HttpOnly，JS 读不到，会误报"未登录"。要确认登录态用 CDP `Network.getCookies` 或直接看页面头像。
- **CDP 命令串行**：三个循环线程共用一个 CDP 连接，`CDPClient.send` 全程加锁，避免 id 错乱、响应被错线程吞掉导致随机超时。
- **点赞与发评论互斥**：两者共用 `send_lock`，保证只在"不发互动"的空闲时点赞；发完评论会 `blur()` 输入框，防止 Z 键打进评论框。
- **DOM 选择器会随抖音改版失效**：评论框 `[data-slate-editor=true]`、发送按钮 `[data-e2e="live-chatting"] ~ *`、进入消息 `div.webcast-chatroom___bottom_message` 内的 `span.v8LY0gZF`、播放器 `[data-e2e="living-container"]`。若欢迎/发送/点赞失效，先用 Chrome DevTools 重新定位选择器，再改 `run_loop.py` 对应位置。详见 `references/troubleshooting.md`。
- **促销话术支持多行（每行一条），欢迎语仍单行**：`promo_lines()` 按行拆分、逐行 `one_line()` 归一化、丢弃空行；`send_comment()` 发送前仍把行内换行压成空格（抖音评论不保留换行，且历史上有过多行导致的 JS 语法错误事故）。`promo.mode` 决定取用方式：`sequential` 按游标循环、`random` 随机且避开上一条；**游标只在发送成功后推进**，失败重发仍是同一条，不会漏发。`welcome.message` 必须单行（要和 `@用户名` 拼一行）。
- **话术必须用 `js_str()` 转成 JS 字面量**：`send_comment` 把话术拼进 `document.execCommand('insertText', ...)` 的 JS 里，**绝不能手工用 f-string + 单引号拼接**。旧实现只转义 `\` 和 `'`，话术带换行（多行话术）会让 JS 报 `SyntaxError: Invalid or unexpected token`，表现为促销每 30 秒重试一次永远发不出（2026-10-31 实战踩到）。现在统一用 `json.dumps` 生成字面量，换行/引号/反斜杠全部安全。
- **改选择器时引号必须成对**：JS 字符串里少一个 `"` 会让脚本启动即报 `SyntaxError: missing ) after argument list`，表现为无限重试、完全不互动（历史事故）。改完先用 CDP eval 一遍确认能返回 `true`。
- **间隔随机化 + 频率下限**：`jitter(seconds, lo, hi)` 把间隔按 `jitter.ratio` 浮动后**再次夹到 `lo` 以上**，所以随机化永远不会突破"最快限制"；`LIMITS` 是唯一真源，校验（`validate_config`）、右侧提示（`/api/state` 的 `limits`）、面板渲染都读它，改一处即全局同步。按住时长不属于间隔，不参与随机化。
- **编码复读的数据源是 Windows 实时辅助字幕，不是网页字幕**（2026-11-11 实测）：`LiveCaptionsDesktopWindow` 里的 `TextControl`（`AutomationId=CaptionsTextBlock`）的 `Name` 就是逐句转录文本，**累积增长**（每次 +N 字），只能读、不能写。Chrome 自带实时字幕是另一个窗口（`CaptionBubbleLabel`），同样可读但延迟更大；网页 DOM 里**没有**字幕节点（已实测）。
- **编码提取必须带上下文锚点**：只在「编码 / 款号 / 编号 / 货号 / 链接 / 小黄车 / 号」附近取 2~8 位数字，否则会把"16.9 米""121 本场点赞"当编码。中文读法两种都要支持：**逐位**（幺零六 → 106）与**数值**（一百零六 → 106、一千零八十 → 1080）。分隔符字符类必须排除中文数字，否则"幺零六"会被切成"分隔符+零六"→ 06（实测踩过）。`b连接`/`B链接` 里的字母属于链接名，只取数字；`A123`/`B-108` 才当字母前缀。
- **识别要用"重叠窗口"而不是纯增量**：字幕每 0.6 秒轮询一次，编码和锚词很容易被切在两次轮询之间（第一次拿到"…幺零"、第二次拿到"六的编码…"），纯增量 diff 会**永久漏掉**这类命中——这是"字幕明明正常、程序却没识别"的头号原因（2026-11-16 实测复现）。做法：每次回看已处理文本尾部 `CE_OVERLAP`（40 字），只接受**结束位置跨过上次边界**的匹配，既补全被切断的编码，又不会重复发送。
- **被闸门拦下必须留日志**：冷却/同码次数/每分钟上限拦掉时，打印 `[复读] 106 未发送：30 秒冷却中`（同码 20 秒内只报一次）。否则用户只会以为"没识别"，排查方向全错。
- **四道闸门防刷屏**：同码冷却、同码次数上限、每分钟总量上限，三者同时生效；发送前再随机延迟 0.6~2 秒，避免"说完立刻复读"的机械感。发送走同一个 `send_lock`，不会和促销/欢迎撞车。
- **字幕游标必须"先取基线"再增量**（2026-11-15 修的真实 bug）：`CaptionsTextBlock` 的文本是**整场累积**的，不是"当前一句"。所以首次启用、以及从暂停恢复时，都必须把**当前文本长度记为基线**、只处理之后新增的部分。早期实现关闭时把游标重置为 0，导致重新开启后把开启前说过的编码全部补发一遍，还把每分钟配额吃光、当前正在说的编码反而发不出去。改游标相关的代码时务必保持这个顺序：取基线 → 增量 diff → 文本变短时同样只从新内容算起。
- **换电脑可移植性**（2026-11-11 加固）：启动脚本不再写死浏览器路径与配置目录——依次尝试 `browser.chrome_path` → 注册表 `App Paths\chrome.exe`（HKLM/HKCU）→ 三个常见安装位置 → Edge 兜底；`user_data_dir` 留空时用 `%LOCALAPPDATA%\Google\Chrome\User Data` 拼出默认路径并**显式传 `--user-data-dir`**——**Chrome M136+ 不传该参数会静默忽略 `--remote-debugging-port`**，表现为"浏览器起来了但 9222 连不上"（2026-11-16 实测：不传 11 秒超时，传默认目录 1 秒就绪）。判断"浏览器是否已运行"用 `ExecutablePath` 全等比较，不受同名进程干扰。ps1 保存为 **UTF-8 with BOM**，避免 PowerShell 5.1 在非中文区域按 ANSI 解析导致中文乱码。**选 Python 必须验依赖**：只按命令名去找解释器会踩空——PATH 里可能是个没装 `requests` 的干净 python，启动后主程序秒退，表现为"双击没反应、面板打不开"（2026-11-16 实测）。正确做法是对候选逐个跑 `import requests, websocket` 验通再启动。
- **暂停 = enabled=false，不是杀线程**：三个循环在 enabled=false 时轻量空转（2 秒轮询一次配置），面板随时能恢复；循环每轮重读 `CONFIG` 全局对象，所以面板保存后下个周期即生效，无需重启。新增功能时也要遵守：循环体内的参数一律从 CONFIG 现读，不要提到循环外缓存。
- **控制面板只监听 127.0.0.1**：`ThreadingHTTPServer` 绑定本地回环，端口占用时自动顺延 10 个；`/api/state`、`/api/control`、`/api/config` 三个接口，POST 请求体上限 100KB。config 写入走临时文件 + `os.replace` 原子替换，避免读到半截 JSON。
- **开关只动一个字段**：`set_switches` 深拷贝当前内存配置 → 只改目标 `enabled` → 校验 → 落盘，其他话术/间隔/开关原样保留；`{"all": false}` 只是批量入口。
- **外部修改保护**：`reload_if_changed()` 在面板轮询、守护 tick、重连退避三处调用，按 mtime 判断；解析失败或校验不过一律保留当前配置（教训：曾用 `encoding='utf-8'` 读带 BOM 的 config.json 直接解析失败并回退默认值，等于把用户配置清空——必须用 `utf-8-sig` 且失败不替换）。
- **welcomed.json 每次启动清空**：确保新 session 重新欢迎，属预期行为。
- **CDP 连接中断**（日志出现 WinError 10053）：通常是 Chrome 被关或页面崩溃，重启 `start.ps1` 即可。

## Resources

- `scripts/run_loop.py` — 互动主循环 + 控制面板服务
- `panel.html` — 控制面板页面（必须与 run_loop.py 同目录）
- `scripts/cdp_client.py` — CDP 客户端库（线程安全）
- `scripts/start.ps1` / `scripts/stop.ps1` — Windows 一键启动/停止
- `scripts/config.example.json` — 配置模板
- `scripts/diag_welcome.py` — 诊断脚本：dump 弹幕 DOM 状态，排查欢迎失效/选择器变更
- `references/troubleshooting.md` — 常见故障排查（选择器失效、CDP 连接、点赞无反应、重复欢迎、ps1 双击无法运行等）

> 说明：本包**不含任何二进制文件**（SkillHub 禁止上传二进制，图标 `icon.png` 会作为独立素材通过商店界面上传，不打进包里）。
