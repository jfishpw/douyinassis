"""诊断：当前直播间弹幕 DOM 里有没有"来了"消息，fetch_new_arrivals 逻辑能否拿到。"""
import sys, time, requests
sys.path.insert(0, r'C:\抖音')
from cdp_client import CDPClient

CDP_URL = 'http://127.0.0.1:9222'

def get_ws():
    for t in requests.get(f'{CDP_URL}/json', timeout=5).json():
        if t.get('type') == 'page' and 'douyin.com' in t.get('url', ''):
            return t['webSocketDebuggerUrl']
    raise RuntimeError('Douyin tab not found')

c = CDPClient(get_ws())
c.send('Runtime.enable')

# 1. 基本状态：评论框、发送按钮、聊天消息总数
state = c.eval(r'''(function(){
    const items = document.querySelectorAll('div.webcast-chatroom___bottom_message');
    return {
        input: !!document.querySelector('[data-slate-editor=true]'),
        sendBtn: !!document.querySelector('[data-e2e="live-chatting"] ~ *'),
        msgCount: items.length,
        title: document.title.slice(0, 40)
    };
})()''')
print('page state:', state)

# 2. 最后 15 条消息的文本 + 是否含用户名 span
msgs = c.eval(r'''(function(){
    const items = document.querySelectorAll('div.webcast-chatroom___bottom_message');
    const out = [];
    for (let i = Math.max(0, items.length - 15); i < items.length; i++) {
        const it = items[i];
        const uspan = it.querySelector('span.v8LY0gZF');
        out.push({
            text: (it.innerText || '').replace(/\n/g, ' ').slice(0, 50),
            hasUserSpan: !!uspan,
            user: uspan ? uspan.innerText.trim() : null
        });
    }
    return out;
})()''')
print('\nlast 15 chat messages:')
for m in msgs:
    print(f"  userSpan={str(m['hasUserSpan']):5} user={str(m['user'])[:20]:<22} text={m['text']}")

# 3. 用 run_loop 的真实逻辑跑一遍
import run_loop as rl
users = rl.fetch_new_arrivals(c)
print('\nfetch_new_arrivals ->', users)
print('welcomed set size:', len(rl.welcomed))
c.close()
