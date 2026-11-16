"""通过 Chrome DevTools Protocol (CDP) 控制本地 Chrome 实例。
要求 Chrome 启动时带 --remote-debugging-port=9222。
"""
import json
import requests
import websocket
import time
import threading
import sys

CDP_URL = 'http://127.0.0.1:9222'


def list_targets():
    """列出所有 Chrome tab/page target。"""
    r = requests.get(f'{CDP_URL}/json', timeout=5)
    r.raise_for_status()
    return r.json()


def find_tab(url_substr=None, title_substr=None):
    """找匹配 url 或 title 的 tab。返回 (targetId, webSocketDebuggerUrl)。"""
    for t in list_targets():
        if t.get('type') != 'page':
            continue
        if url_substr and url_substr not in t.get('url', ''):
            continue
        if title_substr and title_substr not in t.get('title', ''):
            continue
        return t['id'], t['webSocketDebuggerUrl']
    return None, None


class CDPClient:
    """一个 tab 的 CDP 会话。send 全程加锁：多线程共用连接时，
    不串行化会出现 id 错乱、响应被错线程吞掉导致随机超时。"""

    def __init__(self, ws_url, session_id=None):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self._id = 0
        self._results = {}
        self.session_id = session_id
        self._lock = threading.Lock()

    def send(self, method, params=None, timeout=15):
        with self._lock:
            self._id += 1
            msg_id = self._id
            msg = {'id': msg_id, 'method': method, 'params': params or {}}
            if self.session_id:
                msg['sessionId'] = self.session_id
            self.ws.send(json.dumps(msg))
            deadline = time.time() + timeout
            while time.time() < deadline:
                self.ws.settimeout(max(0.5, deadline - time.time()))
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                data = json.loads(raw)
                if data.get('id') == msg_id:
                    if 'error' in data:
                        raise RuntimeError(f'CDP error: {data["error"]}')
                    return data.get('result', {})
            raise TimeoutError(f'CDP call {method} timed out')

    def eval(self, expr, await_promise=False):
        """在 tab 上下文里执行 JS 表达式，返回 result.value。"""
        r = self.send('Runtime.evaluate', {
            'expression': expr,
            'returnByValue': True,
            'awaitPromise': await_promise,
        })
        if 'exceptionDetails' in r:
            raise RuntimeError(f'JS exception: {r["exceptionDetails"]}')
        return r.get('result', {}).get('value')

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'list':
        for t in list_targets():
            if t.get('type') == 'page':
                print(f"[{t['id'][:8]}] {t.get('title','')}  {t.get('url','')[:80]}")
    elif cmd == 'find-douyin':
        tid, ws = find_tab(url_substr='douyin.com')
        if tid:
            print(f'FOUND: {tid} {ws}')
        else:
            print('NOT FOUND')
