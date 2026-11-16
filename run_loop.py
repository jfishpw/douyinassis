"""抖音直播间自动互动循环（配置驱动 + 本地控制面板）
- 定时发送促销/互动话术（promo）
- 检测新进直播间的用户并欢迎（welcome）
- 不发互动的空闲时间按住 Z 键点赞（like，抖音 PC 网页默认快捷键）
- 本地控制面板（http://127.0.0.1:8765）：暂停/继续各功能、在线改全部配置、看实时状态与日志

健壮性设计（2026-10 实战教训）：
- 单实例锁：重复启动会自动退出，避免多个实例互相抢评论框、重复欢迎同一个人
- CDP 发送加锁：三个循环共用一个连接，命令串行执行，避免响应被错线程吞掉
- 断线自动重连：Chrome 被关/页面崩溃后持续重试，Chrome 恢复后自动继续，无需手动重启
- 暂停 = 配置里的 enabled=false：一套状态，面板改了立即生效（循环每轮重读配置），不需重启
"""
import sys, os, time, json, re, random, threading, requests, websocket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.stdout.reconfigure(encoding='utf-8')

CDP_URL = 'http://127.0.0.1:9222'

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(WORKSPACE, 'config.json')
LOG        = os.path.join(WORKSPACE, 'loop.log')
WELCOMED   = os.path.join(WORKSPACE, 'welcomed.json')
LOCK       = os.path.join(WORKSPACE, 'run_loop.lock')
STOP_FLAG  = os.path.join(WORKSPACE, 'run_loop.stop')
PANEL_HTML = os.path.join(WORKSPACE, 'panel.html')

FEATURES = (('promo', '促销话术'), ('welcome', '欢迎新用户'), ('like', '点赞'),
            ('code_echo', '编码复读'))

# 各间隔参数的取值区间：(下限, 上限, 名称)。
# 下限就是"最快允许节奏"——频率上限，随机浮动也绝不会突破它。
# 面板界面直接读这份定义来显示限制，改这里就同时改掉校验和界面提示。
LIMITS = {
    'promo_interval':  (10, 3600, '发送间隔'),
    'welcome_poll':    (1, 60, '检测频率'),
    'welcome_gap':     (0, 120, '两条欢迎最小间隔'),
    'welcome_history': (20, 500, '回看消息数'),
    'like_interval':   (1, 300, '点赞间隔'),
    'like_hold':       (0.3, 60, '点赞按住时长'),
    'code_cooldown':   (10, 600, '同一编码冷却'),
    'code_per_minute': (1, 10, '每分钟发送上限'),
    'code_per_code':   (1, 20, '同一编码最多发送次数'),
}
JITTER_MAX = 0.5          # 随机浮动比例上限（±50%）

# 默认配置：config.json 里缺的项按这里补
DEFAULT_CONFIG = {
    "live_url": "https://live.douyin.com/你的直播间ID",
    "jitter": {
        "enabled": True,          # 间隔随机化：避免固定节奏（更像人工）
        "ratio": 0.1,             # 浮动比例：0.1 = ±10%
    },
    "promo": {
        "enabled": True,
        "message": "",            # 支持多行：每个非空行是一条话术
        "mode": "sequential",     # 发送方式：sequential 顺序轮流 / random 随机（不连续重复）
        "interval": 300,          # 促销话术发送间隔（秒），实际值在此上下 ±ratio 浮动
    },
    "welcome": {
        "enabled": True,
        "message": "有喜欢的可以和主播说哦",
        "poll_interval": 2.5,     # 多久检测一次新用户（秒）
        "gap": 6,                 # 两条欢迎之间至少间隔（秒）
        "history": 200,           # 最多回看多少条聊天消息（直播间越忙越要大）
    },
    "like": {
        "enabled": True,
        "interval": 5,            # 每轮点赞间隔（秒）
        "key": "z",               # 点赞键（抖音 PC 网页默认 Z）
        "hold": 2,                # 每轮按住时长（秒）：按住不放才连续点赞，单击无效
    },
    "code_echo": {
        "enabled": True,          # 读 Windows 实时辅助字幕，命中编码就发公屏
        "template": "编码 {code}",  # 发送模板，{code} 会被替换成编码
        "cooldown": 30,           # 同一编码冷却（秒）
        "per_minute": 2,          # 每分钟最多发几条
        "per_code": 2,            # 同一编码一场最多发几次
    },
    "panel": {
        "enabled": True,          # 是否启用本地控制面板
        "port": 8765,             # 面板端口（改了要重启脚本）
    },
}

# 运行状态（面板读取）
STATE = {
    'started_at': time.time(),
    'cdp': 'disconnected',
    'like_rounds': 0,
    'promo_count': 0,
    'last_promo_at': '',
    'code_count': 0,
    'last_code': '',
    'last_error': '',
}


def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_config():
    """读 config.json，和默认配置合并；文件缺失/损坏时用默认值并记日志。
    用 utf-8-sig 读取：Windows 记事本/PowerShell 保存的 JSON 常带 BOM。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_FILE, encoding='utf-8-sig') as f:
            user_cfg = json.load(f)
        if not isinstance(user_cfg, dict):
            raise ValueError('config.json 顶层必须是 JSON 对象')
        for k, v in user_cfg.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        log('未找到 config.json，使用默认配置')
    except Exception as e:
        log(f'config.json 解析失败（{e}），使用默认配置')
    return normalize_messages(cfg)


def save_config(cfg):
    """原子写 config.json（先写临时文件再替换），避免面板读到半截内容。"""
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, CONFIG_FILE)
    _config_mtime[0] = _file_mtime()      # 自己写的，不触发外部修改重载


_config_mtime = [0.0]


def _file_mtime():
    try:
        return os.path.getmtime(CONFIG_FILE)
    except Exception:
        return 0.0


def reload_if_changed():
    """config.json 被外部（手改/其他程序）修改时自动重载，避免面板写入把手改覆盖掉。
    解析或校验失败时保持旧配置不动——绝不因为一次手误把运行中的配置清成默认值。"""
    global CONFIG
    mt = _file_mtime()
    if mt == 0 or mt <= _config_mtime[0]:
        return
    _config_mtime[0] = mt
    try:
        with open(CONFIG_FILE, encoding='utf-8-sig') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError('顶层必须是 JSON 对象')
    except Exception as e:
        log(f'[控制] config.json 解析失败（{e}），继续使用当前配置')
        return
    merged = merge_config(raw)
    errs = validate_config(merged)
    if errs:
        log(f'[控制] config.json 内容有误（{"; ".join(errs)}），继续使用当前配置')
        return
    CONFIG = merged
    log('[控制] 检测到 config.json 被外部修改，已重新加载：' + '、'.join(
        f'{label}{"启用" if merged[key].get("enabled") else "暂停"}' for key, label in FEATURES))


def validate_config(cfg):
    """校验面板提交的配置，返回错误列表（空表示通过）。"""
    errs = []
    if not isinstance(cfg, dict):
        return ['配置必须是 JSON 对象']

    url = cfg.get('live_url', '')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        errs.append('直播间地址必须是 http(s) 开头的链接')

    promo = cfg.get('promo') or {}
    if not isinstance(promo.get('enabled', True), bool):
        errs.append('促销开关必须是布尔值')
    msg = promo.get('message', '')
    if not isinstance(msg, str):
        errs.append('促销话术必须是字符串')
    else:
        # 多行：每个非空行是一条独立话术，逐行限长；总长度也设上限防误粘贴
        plines = [l for l in re.split(r'[\r\n]+', msg) if l.strip()]
        for i, l in enumerate(plines, 1):
            if len(l) > 200:
                errs.append(f'促销话术第 {i} 行 {len(l)} 字，每行不超过 200 字')
        if len(msg) > 2000:
            errs.append(f'促销话术总长 {len(msg)} 字，不超过 2000 字（约每行 200 字 × 10 行）')
    if promo.get('mode', 'sequential') not in ('sequential', 'random'):
        errs.append('促销发送方式只能是 sequential（顺序轮流）或 random（随机）')
    lo, hi, name = LIMITS['promo_interval']
    try:
        v = float(promo.get('interval', DEFAULT_CONFIG['promo']['interval']))
        if not (lo <= v <= hi):
            errs.append(f'{name}需在 {lo} ~ {hi} 秒之间（{lo} 秒是最快限制）')
    except Exception:
        errs.append(f'{name}必须是数字')

    wc = cfg.get('welcome') or {}
    if not isinstance(wc.get('enabled', True), bool):
        errs.append('欢迎开关必须是布尔值')
    wmsg = wc.get('message', '')
    if not isinstance(wmsg, str) or not wmsg or len(wmsg) > 200:
        errs.append('欢迎文案必须是非空字符串且不超过 200 字')
    for key, limit_key in (('poll_interval', 'welcome_poll'),
                           ('gap', 'welcome_gap'),
                           ('history', 'welcome_history')):
        lo, hi, name = LIMITS[limit_key]
        try:
            v = float(wc.get(key, DEFAULT_CONFIG['welcome'][key]))
            if not (lo <= v <= hi):
                unit = '条' if key == 'history' else '秒'
                errs.append(f'{name}需在 {lo} ~ {hi} {unit}之间')
        except Exception:
            errs.append(f'{name}必须是数字')

    lk = cfg.get('like') or {}
    if not isinstance(lk.get('enabled', True), bool):
        errs.append('点赞开关必须是布尔值')
    lo, hi, name = LIMITS['like_interval']
    try:
        v = float(lk.get('interval', DEFAULT_CONFIG['like']['interval']))
        if not (lo <= v <= hi):
            errs.append(f'{name}需在 {lo} ~ {hi} 秒之间（{lo} 秒是最快限制）')
    except Exception:
        errs.append(f'{name}必须是数字')
    lo, hi, name = LIMITS['like_hold']
    try:
        v = float(lk.get('hold', DEFAULT_CONFIG['like']['hold']))
        if not (lo <= v <= hi):
            errs.append(f'{name}需在 {lo} ~ {hi} 秒之间')
    except Exception:
        errs.append(f'{name}必须是数字')
    keyv = lk.get('key', 'z')
    if not isinstance(keyv, str) or len(keyv) != 1:
        errs.append('点赞按键必须是单个字符')

    ce = cfg.get('code_echo') or {}
    if not isinstance(ce.get('enabled', True), bool):
        errs.append('编码复读开关必须是布尔值')
    tpl = ce.get('template', DEFAULT_CONFIG['code_echo']['template'])
    if not isinstance(tpl, str) or not tpl.strip() or len(tpl) > 100:
        errs.append('编码复读话术必须是非空字符串且不超过 100 字')
    elif '{code}' not in tpl:
        errs.append('编码复读话术必须包含 {code} 占位符（否则发出的内容里没有编码）')
    for key, limit_key in (('cooldown', 'code_cooldown'),
                           ('per_minute', 'code_per_minute'),
                           ('per_code', 'code_per_code')):
        lo, hi, name = LIMITS[limit_key]
        try:
            v = float(ce.get(key, DEFAULT_CONFIG['code_echo'][key]))
            if not (lo <= v <= hi):
                errs.append(f'{name}需在 {lo} ~ {hi} 之间')
        except Exception:
            errs.append(f'{name}必须是数字')

    jc = cfg.get('jitter') or {}
    if not isinstance(jc.get('enabled', True), bool):
        errs.append('间隔随机化开关必须是布尔值')
    try:
        v = float(jc.get('ratio', DEFAULT_CONFIG['jitter']['ratio']))
        if not (0 <= v <= JITTER_MAX):
            errs.append(f'随机浮动比例需在 0 ~ {JITTER_MAX:g}（即 0% ~ {JITTER_MAX * 100:.0f}%）之间')
    except Exception:
        errs.append('随机浮动比例必须是数字')

    pc = cfg.get('panel') or {}
    if not isinstance(pc.get('enabled', True), bool):
        errs.append('面板开关必须是布尔值')
    try:
        p = int(pc.get('port', 8765))
        if not (1024 <= p <= 65535):
            errs.append('面板端口需在 1024 ~ 65535 之间')
    except Exception:
        errs.append('面板端口必须是整数')
    return errs


def merge_config(raw):
    """把提交的配置合并进默认结构，保证字段完整。"""
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in (raw or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return normalize_messages(merged)


def apply_config(raw):
    """面板保存配置：校验 -> 落盘 -> 热替换内存配置。返回错误列表。"""
    global CONFIG
    merged = merge_config(raw)
    errs = validate_config(merged)
    if errs:
        return errs
    try:
        save_config(merged)
    except Exception as e:
        return [f'配置写入失败: {e}']
    CONFIG = merged
    log('[控制] 配置已保存并生效：' + '、'.join(
        f'{label}{"启用" if merged[key].get("enabled") else "暂停"}' for key, label in FEATURES))
    return []


def set_switches(payload):
    """面板开关：payload 里 {promo/welcome/like: true|false} 或 {all: true|false}。"""
    global CONFIG
    cfg = json.loads(json.dumps(CONFIG or DEFAULT_CONFIG))
    changed = []
    if 'all' in payload:
        val = bool(payload['all'])
        for key, _ in FEATURES:
            if cfg[key].get('enabled') != val:
                changed.append((key, val))
            cfg[key]['enabled'] = val
    else:
        for key, _ in FEATURES:
            if key in payload:
                val = bool(payload[key])
                if cfg[key].get('enabled') != val:
                    changed.append((key, val))
                cfg[key]['enabled'] = val
    if not changed:
        return []
    errs = validate_config(cfg)
    if errs:
        return errs
    try:
        save_config(cfg)
    except Exception as e:
        return [f'配置写入失败: {e}']
    CONFIG = cfg
    labels = dict(FEATURES)
    log('[控制] ' + '，'.join(f'{labels[k]}{"已启用" if v else "已暂停"}' for k, v in changed))
    return []


def get_state():
    """面板轮询的完整状态（顺带检查 config.json 是否被外部修改）。"""
    reload_if_changed()
    cfg = CONFIG or DEFAULT_CONFIG
    return {
        'ok': True,
        'pid': os.getpid(),
        'uptime_s': int(time.time() - STATE['started_at']),
        'cdp': STATE['cdp'],
        'last_error': STATE['last_error'],
        'config': cfg,
        # 频率限制与随机化参数：面板界面直接读这里，保证提示与后端校验一致
        'limits': {k: {'min': v[0], 'max': v[1], 'label': v[2]} for k, v in LIMITS.items()},
        'jitter_max': JITTER_MAX,
        'features': [{'key': k, 'label': label, 'enabled': bool(cfg[k].get('enabled'))}
                     for k, label in FEATURES],
        'stats': {
            'welcomed': len(welcomed),
            'like_rounds': STATE['like_rounds'],
            'promo_count': STATE['promo_count'],
            'code_count': STATE.get('code_count', 0),
            'last_code': STATE.get('last_code', ''),
            'last_promo_at': STATE['last_promo_at'],
            'last_welcome_at': (time.strftime('%H:%M:%S', time.localtime(last_welcome_at[0]))
                                if last_welcome_at[0] else ''),
        },
        'log': read_log_tail(30),
    }


def read_log_tail(n=30):
    try:
        with open(LOG, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8000))
            data = f.read().decode('utf-8', 'ignore')
        return [ln for ln in data.splitlines() if ln.strip()][-n:]
    except Exception:
        return []


class CDPClient:
    """一个 tab 的 CDP 会话。send 全程加锁：三个循环线程共用一个连接，
    不串行化会出现 id 错乱、响应被错线程吞掉导致随机超时。"""
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self._id = 0
        self._lock = threading.Lock()
    def send(self, method, params=None, timeout=15):
        with self._lock:
            self._id += 1
            msg_id = self._id
            self.ws.send(json.dumps({'id': msg_id, 'method': method, 'params': params or {}}))
            deadline = time.time() + timeout
            while time.time() < deadline:
                self.ws.settimeout(max(0.5, deadline - time.time()))
                try:
                    data = json.loads(self.ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                if data.get('id') == msg_id:
                    return data.get('result', {})
            raise TimeoutError(f'CDP {method} timeout')
    def eval(self, expr):
        r = self.send('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
        if 'exceptionDetails' in r:
            raise RuntimeError(str(r['exceptionDetails']))
        return r.get('result', {}).get('value')
    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def get_ws():
    """找抖音 tab；找不到时按配置地址自动打开一个（Chrome 在跑但页面被关掉时自愈）。"""
    try:
        tabs = requests.get(f'{CDP_URL}/json', timeout=5).json()
    except Exception as e:
        raise RuntimeError(f'CDP 不可达（Chrome 未启动或没带调试端口）: {e}')
    for t in tabs:
        if t.get('type') == 'page' and 'douyin.com' in t.get('url', ''):
            return t['webSocketDebuggerUrl']
    url = (CONFIG.get('live_url') if isinstance(CONFIG, dict) else None) or 'https://live.douyin.com/'
    try:
        r = requests.put(f'{CDP_URL}/json/new?{url}', timeout=15)
        log(f'未找到抖音页面，已自动打开: {url}')
        return r.json()['webSocketDebuggerUrl']
    except Exception as e:
        raise RuntimeError(f'抖音页面不存在且自动打开失败: {e}')


# ---------- 互动动作 ----------

def normalize_messages(cfg):
    """规范化话术内容（就地修改并返回）：
    - promo.message：允许多行，每个非空行是一条话术；行内多余空白/制表符归一化，空行丢弃
    - welcome.message：仍强制单行（欢迎语要和 @用户名 拼在一行）
    """
    w = cfg.get('welcome')
    if isinstance(w, dict) and isinstance(w.get('message'), str):
        w['message'] = one_line(w['message'])
    p = cfg.get('promo')
    if isinstance(p, dict) and isinstance(p.get('message'), str):
        p['message'] = '\n'.join(promo_lines(p))
    return cfg


def one_line(s):
    """话术/欢迎语只支持单行：换行、回车、制表符压成单个空格，并去掉首尾空白。
    纯文字内容优先（2026-10-31 决定）：多行话术历史上会导致 JS 语法错误，
    且抖音评论是否保留换行不可控，统一在入口归一化，用户粘贴多行也不会丢字。"""
    return re.sub(r'[\r\n\t\u2028\u2029]+', ' ', str(s or '')).strip()


# 促销话术取用游标：顺序模式下指向下一条；随机模式下记上一条用于避重
promo_state = {'i': 0, 'last': ''}


def promo_lines(promo):
    """把 promo.message 拆成"每条一行"的列表（空行丢弃，行内空白归一化）。"""
    raw = promo.get('message') if isinstance(promo, dict) else ''
    if not isinstance(raw, str):
        return []
    return [x for x in (one_line(l) for l in re.split(r'[\r\n]+', raw)) if x]


def pick_promo_line(lines, mode='sequential'):
    """按模式取一条话术，返回 (内容, 第几条/总条数)。空列表返回 ('', 0)。"""
    n = len(lines)
    if n == 0:
        return '', 0
    if mode == 'random':
        # 随机但避免连续两次发同一条（只有一条时自然允许重复）
        pool = [x for x in lines if x != promo_state['last']] or lines
        line = random.choice(pool)
        return line, lines.index(line) + 1
    i = promo_state['i'] % n
    return lines[i], i + 1


def advance_promo(line, mode='sequential'):
    """发送成功后才推进游标——失败时下次仍发同一条，顺序模式不会漏发。"""
    if mode == 'random':
        promo_state['last'] = line
    else:
        promo_state['i'] += 1


def jitter(seconds, lo=None, hi=None):
    """把间隔随机浮动 ±jitter.ratio，并夹在 [lo, hi] 内。
    lo 就是"最快允许节奏"（频率上限）：浮动之后仍要 max(lo, ...)，
    所以随机化永远不会把间隔压得比允许值更短。"""
    base = float(seconds)
    if lo is not None:
        base = max(float(lo), base)
    r = jitter_ratio()
    if r > 0:
        base *= 1 + random.uniform(-r, r)
    if lo is not None:
        base = max(float(lo), base)
    if hi is not None:
        base = min(float(hi), base)
    return base


def jitter_ratio():
    """当前生效的浮动比例（0 = 不浮动）。"""
    jc = (CONFIG or DEFAULT_CONFIG).get('jitter') or {}
    if not jc.get('enabled', True):
        return 0.0
    try:
        return max(0.0, min(JITTER_MAX, float(jc.get('ratio', 0.1))))
    except Exception:
        return 0.0


def js_str(s):
    """把任意字符串转成安全的 JS 字符串字面量（双引号 + 完整转义）。
    历史 bug：以前用 f-string 直接拼进 JS 单引号字面量、只转义反斜杠和单引号，
    话术里带换行（多行话术）就会让 JS 报 SyntaxError: Invalid or unexpected token，
    表现为促销每 30 秒重试一次、永远发不出去（2026-10-31 实际踩到）。"""
    return (json.dumps(s, ensure_ascii=False)
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def brief_error(e, limit=150):
    """把 CDP 抛出的长字典压成一行可读信息，避免日志被几千字刷屏。"""
    s = str(e).replace('\r', ' ').replace('\n', ' ')
    if 'SyntaxError' in s and ('Invalid or unexpected token' in s or 'unterminated string' in s):
        return 'JS 语法错误（话术里的换行/引号/反斜杠没转义）'
    return s if len(s) <= limit else s[:limit] + '…'


def send_comment(client, text):
    """输入 + 发送（调用方需持有 send_lock）"""
    text = one_line(text)          # 兜底：绝不让换行进入 JS 表达式
    client.eval(
        "var el=document.querySelector('[data-slate-editor=true]'); "
        f"if(el){{el.focus();el.innerHTML='';document.execCommand('insertText',false,{js_str(text)});}}"
    )
    client.eval(
        "var b=document.querySelector('[data-e2e=\"live-chatting\"] ~ *'); "
        "if(b)b.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));"
    )
    time.sleep(1.5)
    # 发完把焦点移出评论框，否则后面按 Z 点赞会打进输入框里
    client.eval("var el=document.querySelector('[data-slate-editor=true]'); if(el)el.blur();")


def blur_input(client):
    """焦点在评论框/输入框时先 blur，避免快捷键被输入框吃掉。返回是否执行过 blur。"""
    return bool(client.eval(
        "(function(){const el=document.activeElement;"
        "if(el&&(el.isContentEditable||el.tagName==='INPUT'||el.tagName==='TEXTAREA'))"
        "{el.blur();return true;}return false;})()"
    ))


def focus_player(client):
    """尝试把焦点移到播放器（不可聚焦时静默跳过），保证按键事件打到直播页面上"""
    client.eval(
        "(function(){const p=document.querySelector('[data-e2e=\"living-container\"]');"
        "if(p&&typeof p.focus==='function'){try{p.focus();}catch(e){}}})()"
    )


def hold_like(client, key, hold_seconds, repeat_gap=0.4):
    """按住点赞键持续点赞（抖音规则：按住不放连续点赞，松开停止）。
    实测三个关键点（2026-10 live.douyin.com）：
    1. 发送前必须先 JS focus 播放器激活快捷键，否则按键完全无效；
    2. keyDown 必须带 text 参数（否则不触发 keypress）；
    3. 长按期间要每 repeat_gap 秒补发 autoRepeat  keydown——真实长按有系统自动重复，
       只发单个 keyDown 赞会很少（3 秒约 2 个赞，且服务器计数有延迟）。"""
    key = (key or 'z')[:1]
    vk = ord(key.upper())
    down = {'type': 'keyDown', 'key': key, 'code': 'Key' + key.upper(),
            'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk, 'text': key}
    up = {'type': 'keyUp', 'key': key, 'code': 'Key' + key.upper(),
          'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk}
    client.send('Input.dispatchKeyEvent', down)
    deadline = time.time() + max(0.3, float(hold_seconds))
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(repeat_gap, remaining))
        client.send('Input.dispatchKeyEvent', dict(down, autoRepeat=True))
    client.send('Input.dispatchKeyEvent', up)


def fetch_new_arrivals(client):
    """从聊天区 DOM 拿所有'来了'消息的用户名"""
    history = int(((CONFIG or DEFAULT_CONFIG)['welcome']).get('history', 200))
    js = f'''(function() {{
        const items = document.querySelectorAll('div.webcast-chatroom___bottom_message');
        const out = [];
        const seen = new Set();
        for (let i = Math.max(0, items.length - {history}); i < items.length; i++) {{
            const it = items[i];
            const uspan = it.querySelector('span.v8LY0gZF');
            if (!uspan) continue;
            const u = uspan.innerText.trim();
            if (!u || seen.has(u)) continue;
            seen.add(u);
            out.push(u);
        }}
        return out;
    }})()'''
    r = client.eval(js)
    return r if isinstance(r, list) else []


def load_welcomed():
    """每次启动都从空开始，确保新 session 重新欢迎"""
    try:
        if os.path.exists(WELCOMED):
            os.remove(WELCOMED)
            log('已清空 welcomed.json（新 session 重新欢迎）')
    except Exception as e:
        log(f'清空 welcomed.json 失败: {e}')
    return set()


def save_welcomed(s):
    try:
        with open(WELCOMED, 'w', encoding='utf-8') as f:
            json.dump(sorted(s), f, ensure_ascii=False, indent=0)
    except Exception as e:
        log(f'welcomed.json 写入失败: {e}')


def wait_ready(client):
    for i in range(6):
        if stop_requested():
            return False
        inp = client.eval("!!document.querySelector('[data-slate-editor=true]')")
        btn = client.eval("!!document.querySelector('[data-e2e=\"live-chatting\"] ~ *')")
        log(f"评论框 {'ok' if inp else '--'} 发送按钮 {'ok' if btn else '--'} ({i*10}s)")
        if inp and btn:
            return True
        if i == 1:
            # 20 秒还没出现评论框，打印页面实况：便于区分"未开播/未登录/加载慢/开错页面"
            try:
                info = client.eval(
                    "(function(){return {url: location.href.slice(0,90),"
                    " title: document.title.slice(0,30),"
                    " ready: document.readyState,"
                    " video: document.querySelectorAll('video').length,"
                    " chat: document.querySelectorAll('div.webcast-chatroom___bottom_message').length,"
                    " text: (document.body.innerText || '').replace(/\\n+/g, ' ').slice(0,60)}})()")
                log(f'页面实况: {info}')
            except Exception as e:
                log(f'页面实况读取失败: {e}')
        time.sleep(10)
    log('评论框 60s 内未出现：常见原因是主播未开播、未登录、页面还在加载；'
        '保持 Chrome 打开，连上后会自动重试，无需重启脚本')
    return False


def acquire_single_instance_lock():
    """单实例锁：已有 run_loop.py 在跑就退出。
    多个实例会互相抢评论框、重复欢迎同一个人，是实战中踩过的大坑。
    锁文件里写 PID 并持续 touch（mtime 即心跳），供 start/stop.ps1
    判断实例是否存活——绝不靠命令行匹配进程（本机 AI 助手的执行层
    会产生命令行里带 run_loop.py 字样的无关 python 进程，会误杀）。"""
    try:
        f = open(LOCK, 'a+')
        if os.name == 'nt':
            import msvcrt
            try:
                # 锁加在很远的位置（EOF 之外），不要锁字节 0——
                # 否则其他程序（stop.ps1 / start.ps1 用 Get-Content）读不到里面的 PID
                f.seek(100000)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                log('检测到已有 run_loop.py 在运行，本实例退出（重复启动会互相抢评论框）')
                return None
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                log('检测到已有 run_loop.py 在运行，本实例退出（重复启动会互相抢评论框）')
                return None
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        touch_lock()
        return f  # 必须保持打开，进程退出时操作系统才会释放锁
    except Exception as e:
        log(f'单实例锁创建失败（将继续运行）: {e}')
        return None


def touch_lock():
    """刷新锁文件 mtime = 心跳；顺带确保文件里写的是本进程 PID。
    历史 bug：锁文件被外部删除后，心跳会把文件重建成 0 字节空文件，
    start/stop.ps1 读不到 PID → stop.ps1 误报"没有在运行中的实例"，
    实例却还在跑（2026-10-31 实际踩到，跑了 6 天没停掉）。"""
    try:
        with open(LOCK, 'a+', encoding='utf-8') as f:
            f.seek(0)
            if f.read().strip() != str(os.getpid()):
                f.seek(0)
                f.truncate()
                f.write(str(os.getpid()))
                f.flush()
            os.utime(LOCK, None)
    except Exception:
        pass


def stop_requested():
    """stop.ps1 通过创建 run_loop.stop 文件请求停止（协作式，绝不强杀）。"""
    return os.path.exists(STOP_FLAG)


def cleanup_files():
    """退出时清理锁和信号文件。锁文件句柄要先关，否则 Windows 上删不掉。"""
    global _lock_handle
    if _lock_handle is not None:
        try:
            _lock_handle.close()
        except Exception:
            pass
        _lock_handle = None
    for f in (LOCK, STOP_FLAG):
        try:
            os.remove(f)
        except Exception:
            pass


# ---------- 本地控制面板 ----------

class PanelHandler(BaseHTTPRequestHandler):
    server_version = 'RunLoopPanel/1.0'
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            try:
                with open(PANEL_HTML, 'rb') as f:
                    self._send(200, f.read(), 'text/html; charset=utf-8')
            except Exception as e:
                self._send(500, {'ok': False, 'error': f'panel.html 读取失败: {e}'})
        elif path == '/api/state':
            self._send(200, get_state())
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        path = self.path.split('?')[0]
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 100000:
                raise ValueError('请求体过大')
            raw = self.rfile.read(n) if n else b'{}'
            payload = json.loads(raw or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('请求体必须是 JSON 对象')
        except Exception as e:
            self._send(400, {'ok': False, 'error': f'请求解析失败: {e}'})
            return

        if path == '/api/config':
            errs = apply_config(payload.get('config') or {})
            if errs:
                self._send(400, {'ok': False, 'errors': errs})
            else:
                self._send(200, {'ok': True, 'state': get_state()})
        elif path == '/api/control':
            errs = set_switches(payload)
            if errs:
                self._send(400, {'ok': False, 'errors': errs})
            else:
                self._send(200, {'ok': True, 'state': get_state()})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def log_message(self, fmt, *args):
        # 面板访问日志不写 loop.log（每 2 秒一次太吵）
        pass


def start_panel():
    """启动本地控制面板（只监听 127.0.0.1）。端口被占用时顺延尝试 10 个。"""
    cfg = CONFIG or DEFAULT_CONFIG
    pc = cfg.get('panel') or {}
    if not pc.get('enabled', True):
        log('[面板] 已禁用（panel.enabled=false）')
        return None
    port0 = int(pc.get('port', 8765) or 8765)
    for port in range(port0, port0 + 10):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), PanelHandler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True, name='panel').start()
            log(f'[面板] 控制面板已启动: http://127.0.0.1:{port}')
            return port
        except OSError:
            continue
    log(f'[面板] 启动失败：端口 {port0}~{port0 + 9} 都被占用')
    return None


# ---------- 跨线程共享 ----------

send_lock = threading.Lock()      # 发评论 / 点赞共用，保证点赞不撞上发互动
last_welcome_at = [0.0]
welcomed = set()
CONFIG = None
_lock_handle = None               # 单实例锁的文件句柄（退出时要先关才能删文件）


def thread_safe_send(client, text):
    with send_lock:
        send_comment(client, text)


# ---------- 编码复读：读 Windows 实时辅助字幕 → 提取编码 → 发公屏 ----------

# 数字字符集要包含 ASR 的中文逐位读法（幺零六 = 106）：
# 分隔符必须排除这些字符，否则"幺零六"会被切成"分隔符 + 零六"→ 得到 06（实测踩过）
CE_NUM = r'[0-9零〇幺一二两三四五六七八九]{2,6}'
CE_SEP = r'[^0-9零〇幺一二两三四五六七八九]{0,6}'
CE_SEP_TIGHT = r'[\s的是为:：]{0,2}'
CE_ANCHOR = r'(?:编码|款号|编号|货号|链接|小黄车)'
CE_CN_DIGIT = {'零': '0', '〇': '0', '幺': '1', '一': '1', '二': '2', '两': '2', '三': '3',
               '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}
CE_PAT_PREFIX = re.compile(rf'(?P<pre>[A-Za-z])[ـ\-－]?(?P<num>{CE_NUM})')
CE_PAT_LINK = re.compile(rf'[A-Za-z](?:连接|链接){CE_SEP_TIGHT}(?P<num>{CE_NUM})')
CE_PAT_BEFORE = re.compile(rf'{CE_ANCHOR}{CE_SEP}(?P<num>{CE_NUM})')
CE_PAT_AFTER = re.compile(rf'(?P<num>{CE_NUM}){CE_SEP}{CE_ANCHOR}')
CE_PRICE = re.compile(r'(?:\d+(?:\.\d+)?\s*(?:米|元|块|块钱|万))|(?:价格|优惠|券后|到手价)')

code_state = {'last': {}, 'counts': {}, 'sent': [], 'ui': None, 'no_uia_logged': False}
CAPTION_ID = 'CaptionsTextBlock'


def ce_normalize(s):
    return ''.join(CE_CN_DIGIT.get(ch, ch) for ch in s)


def extract_codes(text):
    """从字幕文本里提取候选编码，返回 [(code, 命中片段)]。

    覆盖三种口播写法：
      · 字母前缀：A123 / B-108 / b幺零八
      · 锚点在前：编码 幺零六 / 编号A123
      · 锚点在后：幺零六的编码
    价格/优惠语境整段丢弃，避免把"16.9米"当编码；"b连接"的 b 属于链接名，只取数字。
    """
    flat = re.sub(r'[\s\u3000]+', '', str(text or ''))
    found = []

    for m in CE_PAT_PREFIX.finditer(text):
        num = ce_normalize(m.group('num'))
        if num.isdigit():
            found.append((m.group('pre').upper() + num, m.group(0)))
    for m in CE_PAT_LINK.finditer(flat):
        num = ce_normalize(m.group('num'))
        if num.isdigit() and 2 <= len(num) <= 6:
            found.append((num, m.group(0)))
    for pat in (CE_PAT_BEFORE, CE_PAT_AFTER):
        for m in pat.finditer(flat):
            if CE_PRICE.search(m.group(0)):
                continue
            num = ce_normalize(m.group('num'))
            if num.isdigit() and 2 <= len(num) <= 6:
                found.append((num, m.group(0)))

    prefixed = {c for c, _ in found if c and c[0].isalpha()}
    seen, out = set(), []
    for code, frag in found:
        if code in seen:
            continue
        if code.isdigit() and any(code == q[1:] for q in prefixed):
            continue
        seen.add(code)
        out.append((code, frag))
    return out


def load_uia():
    """惰性导入 uiautomation；没装就记一次日志，不拖垮整个脚本。"""
    if code_state['ui'] is not None:
        return code_state['ui']
    try:
        import uiautomation as auto
        code_state['ui'] = auto
        return auto
    except Exception as e:
        if not code_state['no_uia_logged']:
            log(f'[复读] 未安装 uiautomation，编码复读不可用（pip install uiautomation）：{e}')
            code_state['no_uia_logged'] = True
        return None


def find_caption_block(auto):
    """定位 Windows 实时辅助字幕的文本控件（AutomationId=CaptionsTextBlock）。"""
    try:
        windows = auto.GetRootControl().GetChildren()
    except Exception:
        return None
    for w in windows:
        try:
            if 'LiveCaptions' not in (w.ClassName or ''):
                continue
        except Exception:
            continue

        def walk(ctrl, depth=0):
            if depth > 12:
                return None
            try:
                children = ctrl.GetChildren()
            except Exception:
                return None
            for ch in children:
                try:
                    if (ch.AutomationId or '') == CAPTION_ID:
                        return ch
                except Exception:
                    pass
                r = walk(ch, depth + 1)
                if r:
                    return r
            return None

        tb = walk(w)
        if tb:
            return tb
    return None


def code_allowed(code, cfg):
    """四道闸门：单码冷却、单码总次数、每分钟总量。"""
    now = time.time()
    cooldown = max(10.0, float(cfg.get('cooldown', 30)))
    per_code = max(1, int(cfg.get('per_code', 2)))
    per_min = max(1, int(cfg.get('per_minute', 2)))

    if now - code_state['last'].get(code, 0) < cooldown:
        return False
    if code_state['counts'].get(code, 0) >= per_code:
        return False
    code_state['sent'] = [t for t in code_state['sent'] if now - t < 60]
    if len(code_state['sent']) >= per_min:
        return False
    return True


def code_record(code):
    code_state['last'][code] = time.time()
    code_state['counts'][code] = code_state['counts'].get(code, 0) + 1
    code_state['sent'].append(time.time())


def code_echo_loop(client, stop_flag):
    """读 Windows 实时辅助字幕，命中编码就发公屏。暂停时轻量空转。"""
    log('[复读] 循环已启动')
    auto = load_uia()
    if auto is None:
        log('[复读] 依赖缺失，本循环不工作（其余功能不受影响）')
        return
    tb = None
    # processed = None 表示"还没建立基线"。字幕文本是**累积**的（整场历史都在里面），
    # 首次启用或从暂停恢复时，必须把当前长度当作基线，只处理之后新增的部分，
    # 否则会把开启前说过的编码全部补发一遍，还会把这些历史编码的配额吃光，
    # 导致当前正在说的编码反而发不出去（2026-11-15 实测踩到）。
    processed = None
    last_resolve = 0.0
    missing_logged = 0.0

    while not stop_flag.is_set():
        cfg = (CONFIG or DEFAULT_CONFIG).get('code_echo') or {}
        if not cfg.get('enabled'):
            processed = None          # 重新启用时重新取基线，不补发历史
            if stop_flag.wait(2):
                return
            continue

        if tb is None or time.time() - last_resolve > 10:
            tb = find_caption_block(auto)
            last_resolve = time.time()
        if tb is None:
            if time.time() - missing_logged > 60:
                log('[复读] 没找到 Windows 实时辅助字幕窗口（设置 → 辅助功能 → 字幕，或 Win+Ctrl+L）')
                missing_logged = time.time()
            if stop_flag.wait(5):
                return
            continue

        try:
            text = tb.Name or ''
        except Exception:
            tb = None
            continue

        if processed is None:
            # 建立基线：跳过已有内容，只处理此刻之后新增的字幕
            processed = len(text)
            log(f'[复读] 开始监听字幕（跳过已有 {processed} 字历史，不补发旧编码）')
            if stop_flag.wait(0.6):
                return
            continue

        if len(text) < processed:      # 文本被清空/重置：同样只从新内容算起
            processed = len(text)
            if stop_flag.wait(0.6):
                return
            continue

        new_part = text[processed:]
        processed = len(text)

        for code, frag in extract_codes(new_part):
            if stop_flag.is_set():
                return
            if not code_allowed(code, cfg):
                continue
            template = str(cfg.get('template') or '编码 {code}')
            msg = template.replace('{code}', code)
            # 随机延迟 0.6~2 秒，避免"说完立刻复制"的机械感
            if stop_flag.wait(random.uniform(0.6, 2.0)):
                return
            try:
                thread_safe_send(client, msg)
                code_record(code)
                STATE['code_count'] = STATE.get('code_count', 0) + 1
                STATE['last_code'] = code
                log(f'[复读] {code} → 已发公屏「{msg}」（原文：{frag[:24]}）')
            except Exception as e:
                if not stop_flag.is_set():
                    log(f'[复读] 发送失败: {brief_error(e)}')

        if stop_flag.wait(0.6):
            return


# ---------- 四个循环（暂停时轻量空转，收到 stop_flag 才退出） ----------

def promotion_loop(client, stop_flag):
    """按配置间隔发促销/互动话术。enabled=false 时暂停但不退出，面板可随时恢复。"""
    _p = (CONFIG or DEFAULT_CONFIG).get('promo') or {}
    _lines = promo_lines(_p)
    _mode = '顺序轮流' if _p.get('mode', 'sequential') != 'random' else '随机'
    log(f'[促销] 循环已启动（{_mode}发送，共 {len(_lines)} 条话术；'
        f'间隔随机浮动 ±{jitter_ratio() * 100:.0f}%，最快 {LIMITS["promo_interval"][0]}s）')
    if stop_flag.wait(10):
        return
    idx = 0
    next_at = 0.0
    while not stop_flag.is_set():
        promo = (CONFIG or DEFAULT_CONFIG).get('promo') or {}
        lines = promo_lines(promo)
        if not promo.get('enabled') or not lines:
            next_at = 0.0                     # 恢复后立即发第一条
            if stop_flag.wait(2):
                return
            continue
        lo, hi, _ = LIMITS['promo_interval']
        interval = max(lo, float(promo.get('interval', 300)))
        now = time.time()
        if next_at == 0.0:
            next_at = now
        if now < next_at:
            if stop_flag.wait(min(2.0, next_at - now)):
                return
            continue
        idx += 1
        mode = promo.get('mode', 'sequential')
        message, pos = pick_promo_line(lines, mode)
        tag = f'（{pos}/{len(lines)}）' if len(lines) > 1 else ''
        log(f'[促销 #{idx}]{tag} {message}')
        try:
            thread_safe_send(client, message)
            advance_promo(message, mode)
            log(f'[促销 #{idx}] ok')
            STATE['promo_count'] = idx
            STATE['last_promo_at'] = time.strftime('%H:%M:%S')
            wait_s = jitter(interval, lo=lo, hi=hi)
            log(f'[促销] 下一条约 {wait_s:.0f}s 后（基准 {interval:.0f}s）')
        except Exception as e:
            if stop_flag.is_set():
                return
            wait_s = jitter(30, lo=lo, hi=hi)
            log(f'[促销 #{idx}] 失败: {brief_error(e)}，约 {wait_s:.0f}s 后重试')
        next_at = time.time() + wait_s


def welcome_loop(client, stop_flag):
    """按配置频率检测新用户并欢迎，每人只欢迎一次。暂停时轻量空转。"""
    log(f'[欢迎] 循环已启动（检测±{jitter_ratio() * 100:.0f}%浮动，最快 {LIMITS["welcome_poll"][0]}s；'
        f'两条欢迎最短 {LIMITS["welcome_gap"][0]}s）')
    while not stop_flag.is_set():
        wc = (CONFIG or DEFAULT_CONFIG).get('welcome') or {}
        if not wc.get('enabled'):
            if stop_flag.wait(2):
                return
            continue
        plo, phi, _ = LIMITS['welcome_poll']
        glo, ghi, _ = LIMITS['welcome_gap']
        poll = jitter(wc.get('poll_interval', 2.5), lo=plo, hi=phi)
        gap = jitter(wc.get('gap', 6), lo=glo, hi=ghi)
        message = wc.get('message', '')
        try:
            users = fetch_new_arrivals(client)
            for u in users:
                if stop_flag.is_set() or u in welcomed:
                    continue
                if time.time() - last_welcome_at[0] < gap:
                    continue
                msg = f'@{u} {message}'
                log(f'[欢迎] {msg}')
                try:
                    thread_safe_send(client, msg)
                    welcomed.add(u)
                    save_welcomed(welcomed)
                    last_welcome_at[0] = time.time()
                    log(f'[欢迎] ok（累计 {len(welcomed)} 人）')
                except Exception as e:
                    if stop_flag.is_set():
                        return
                    log(f'[欢迎] 失败: {brief_error(e)}')
        except Exception as e:
            if not stop_flag.is_set():
                log(f'[欢迎] 检测异常: {brief_error(e)}')
        if stop_flag.wait(poll):
            return


def like_loop(client, stop_flag):
    """不发互动的空闲时间按住 Z 持续点赞。暂停时轻量空转。
    和发评论抢同一把锁，保证只在'不发互动'的时候点赞。"""
    log(f'[点赞] 循环已启动（间隔±{jitter_ratio() * 100:.0f}%浮动，最快 {LIMITS["like_interval"][0]}s）')
    n = 0
    while not stop_flag.is_set():
        lc = (CONFIG or DEFAULT_CONFIG).get('like') or {}
        if not lc.get('enabled'):
            if stop_flag.wait(2):
                return
            continue
        ilo, ihi, _ = LIMITS['like_interval']
        hlo, hhi, _ = LIMITS['like_hold']
        interval = max(ilo, float(lc.get('interval', 5)))
        key = str(lc.get('key', 'z'))
        hold = min(hhi, max(hlo, float(lc.get('hold', 2))))   # 按住时长不是间隔，不做随机化
        with send_lock:
            if stop_flag.is_set():
                return
            try:
                if blur_input(client):
                    time.sleep(0.2)
                focus_player(client)
                hold_like(client, key, hold)
                n += 1
                STATE['like_rounds'] = n
                if n == 1 or n % 20 == 0:
                    log(f'[点赞] 第 {n} 轮')
            except Exception as e:
                if not stop_flag.is_set():
                    log(f'[点赞] 失败: {brief_error(e)}')
        if stop_flag.wait(jitter(interval, lo=ilo, hi=ihi)):
            return


# ---------- 连接管理：连一次跑到底，断了重来 ----------

def run_once():
    """连接直播间并跑三个循环，直到连接断开（异常向上抛给 serve_forever）。"""
    log('连接抖音直播间...')
    ws_url = get_ws()
    client = CDPClient(ws_url)
    client.send('Runtime.enable')

    if not wait_ready(client):
        # 页面停在"直播加载中"（主播未开播）时不会自己恢复，刷新一次让下一轮重新加载
        try:
            client.send('Page.reload', {'ignoreCache': False})
            log('已刷新直播间页面，等下一轮重连再检测')
        except Exception:
            pass
        client.close()
        raise TimeoutError('评论框未就绪（直播间可能未开始/未登录）')

    STATE['cdp'] = 'connected'
    STATE['last_error'] = ''
    log('就绪，启动 促销 + 欢迎 + 点赞 + 编码复读 四个循环')
    stop_flag = threading.Event()
    threads = [
        threading.Thread(target=promotion_loop, args=(client, stop_flag), daemon=True),
        threading.Thread(target=welcome_loop,   args=(client, stop_flag), daemon=True),
        threading.Thread(target=like_loop,      args=(client, stop_flag), daemon=True),
        threading.Thread(target=code_echo_loop, args=(client, stop_flag), daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        # 守护线程职责：每 10s ping 一次 CDP，断开就通知各循环退出；
        # 同时刷新锁文件心跳，并检查停止信号文件
        while not stop_flag.is_set():
            if stop_flag.wait(10):
                break
            touch_lock()
            reload_if_changed()
            if stop_requested():
                log('收到停止信号（stop.ps1），正在退出')
                stop_flag.set()
                break
            try:
                client.eval('1+1')
            except Exception as e:
                log(f'CDP 连接断开: {e}')
                stop_flag.set()
                break
    finally:
        stop_flag.set()
        STATE['cdp'] = 'disconnected'
        log('正在停止各循环...')
        for t in threads:
            t.join(timeout=10)
        client.close()


def serve_forever():
    """外层重连循环：Chrome 被关、页面崩溃、直播间未开始都会自动重试。
    Chrome 恢复后自动继续，无需手动重启脚本。
    停止方式：stop.ps1 创建 run_loop.stop 文件，这里协作式退出并清理锁文件。"""
    backoff = 10
    while True:
        if stop_requested():
            log('收到停止信号（stop.ps1），退出')
            cleanup_files()
            return
        try:
            run_once()
            log('连接已断开')
            backoff = 10
        except Exception as e:
            STATE['cdp'] = 'disconnected'
            STATE['last_error'] = str(e)[:200]
            log(f'连接失败: {brief_error(e, 200)}')
        # 退避等待也要响应停止信号 + 维持心跳
        waited = 0.0
        while waited < backoff and not stop_requested():
            time.sleep(min(1.0, backoff - waited))
            waited += 1.0
            touch_lock()
            reload_if_changed()
        if stop_requested():
            log('收到停止信号（stop.ps1），退出')
            cleanup_files()
            return
        log(f'{backoff}s 后重连（Chrome 恢复后会自动继续，无需手动重启）')
        backoff = min(backoff * 2, 60)


def main():
    global CONFIG, welcomed, _lock_handle
    lock = acquire_single_instance_lock()
    if lock is None:
        return
    _lock_handle = lock
    CONFIG = load_config()
    _config_mtime[0] = _file_mtime()
    welcomed = load_welcomed()
    STATE['started_at'] = time.time()
    log(f'配置加载完成：promo={CONFIG["promo"]["enabled"]} welcome={CONFIG["welcome"]["enabled"]} '
        f'like={CONFIG["like"]["enabled"]}（已欢迎列表 {len(welcomed)} 人）')
    start_panel()
    try:
        serve_forever()
    except KeyboardInterrupt:
        log('已停止')
    finally:
        cleanup_files()


if __name__ == '__main__':
    main()
