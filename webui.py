# -*- coding: utf-8 -*-
"""
=========================================================
HypeBuddy 网页界面
=========================================================
给三汇蔡徐坤配一个浏览器界面。

【设计原则】只用 Python 标准库（http.server），不引入 Flask / Gradio。
    常见做法是 pip install gradio 三行写出个界面，
    但那样界面长得跟所有人一样，也破坏了本项目「零额外依赖」的卖点。
    自己写 HTTP 服务，好处是：
      · 不装任何东西，python webui.py 就跑
      · 界面完全自己说了算（川东北那套配色是手调的）

【接口】
    GET  /                → 网页本身
    POST /api/chat        → 发一句话，立刻返回一个任务 id
    GET  /api/status?id=  → 查这个任务：还在忙啥 / 好了没 / 结果
    GET  /api/memory      → 看看它记住了啥

【为什么要"任务 id + 轮询"】
    智能体搜一次网要好几秒，直接等会让页面卡死。
    所以拆成两步：先接活，再轮询进度 ——
    这样前端就能实时显示"🌦️ 正在查天气…"，而不是干等一个转圈。
"""

import os
import sys
import json
import uuid
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import main as agent_main      # 主程序：HypeBuddy + 工具 + 人格
import memory                  # 记忆模块

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(HERE, "webui.html")

# ============================================================
# 任务管理
# ============================================================
JOBS = {}                      # job_id -> {status, done, body, shout, error}
JOBS_LOCK = threading.Lock()
AGENT_LOCK = threading.Lock()  # 一次只跑一个任务，免得记忆被写乱

# 【关键】用线程局部变量把"当前任务 id"带给进度回调。
# 这样即使将来多个任务并发，回调也能找到自己的主人。
_local = threading.local()


def _set_status(text):
    jid = getattr(_local, "job_id", None)
    if not jid:
        return
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["status"] = text


# 把进度回调挂到主程序上 —— 工具一有动作，网页就看得见
agent_main.STATUS_HOOK = _set_status


def run_job(job_id, question, visitor=None):
    """在后台线程里跑一次完整对话"""
    _local.job_id = job_id
    # 【隔离】这一轮只认这个访客自己的那份记忆。
    # 不切换的话，所有人的对话会写进同一个 memory.json，
    # 张三问"我叫啥子"，它会把李四的名字答出来。
    memory.use(visitor)
    try:
        with AGENT_LOCK:
            mem = memory.load()
            _set_status("🔍 正在琢磨要不要查点啥…")
            buddy = agent_main.HypeBuddy()
            buddy.reply(question, mem)
            last = buddy.last

        # 怪叫可能在正文前面，也可能在后面，前端要分开上样式
        full, body, shout = last["full"], last["body"], last["shout"]
        shout_after = bool(shout) and full.rstrip().endswith(shout.strip())

        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "",
                "done": True,
                "body": body,
                "shout": shout,
                "shout_after": shout_after,
            })
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id].update({
                "done": True,
                "error": f"{type(e).__name__}: {e}",
            })

    # 任务留着让前端取，10 分钟后清掉，免得内存越吃越多
    def _gc():
        time.sleep(600)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
    threading.Thread(target=_gc, daemon=True).start()


# ============================================================
# 总花费闸 —— 【不限制个人，只限制总共烧多少】
# ============================================================
# 【为什么不做按人限流】我先做了一版"每人每分钟 5 句"，测的时候发现坑：
#   同一个 WiFi（学校机房、宿舍）出去是【同一个公网 IP】。
#   按 IP 算，一屋子人会被并成一个"人" ——
#   一个人多问两句，全班都被拦，演示当场就黄。
#   按访客 id 算又拦不住存心伪造 id 的。
#   干脆不做个人限制：谁都能随便问，只在【总量】上设一道闸。
#
# 【闸设在 token 上，不是句数上】句数跟钱没有直接关系 ——
#   一句"你好"和一次深度调研，差着几十倍。
#   所以直接盯 token：到量自动停止服务。
#   真实用量记在 usage.json 里（main.py 的 llm() 每次调用都记），
#   存盘所以重启不归零。
TOKEN_BUDGET = 500_000_000     # 五亿 tokens，烧到这条线自动停

# 同时排队的活儿最多几个。这不是个人配额，是防机器被压垮 ——
# 智能本来一次只跑一个任务，排太多线程只会把电脑拖死。
MAX_QUEUE = 3


def tokens_used() -> int:
    u = getattr(agent_main, "USAGE", {}) or {}
    return int(u.get("in", 0)) + int(u.get("out", 0))


def budget_left() -> int:
    return max(0, TOKEN_BUDGET - tokens_used())


def queue_len() -> int:
    with JOBS_LOCK:
        return sum(1 for j in JOBS.values() if not j.get("done"))


# ============================================================
# 来访统计 —— 想知道有几个人点进来
# ============================================================
# 【为什么单独记一笔】页面加载（GET /）本身不带访客 id，
#   光数请求次数分不清"一个人刷了十次"和"十个人各来一次"。
#   所以前端加载完会打一个信标过来（/api/visit），带上自己的随机 id。
#   按 id 去重，才是真的"几个人"。
#
# 【隐私注意】这个文件里存着人家的 IP，跟 memory.json 一个性质，
#   已经写进 .gitignore，不会传到仓库。
VISIT_FILE = os.path.join(HERE, "visits.jsonl")
_visit_lock = threading.Lock()


def _peer_ip(handler) -> str:
    """访客的真实 IP。走隧道时请求是从本机进来的，
    client_address 永远是 127.0.0.1，真 IP 在 CF-Connecting-IP 头里"""
    for h in ("CF-Connecting-IP", "X-Real-IP"):
        v = handler.headers.get(h)
        if v:
            return v.strip()
    xff = handler.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return handler.client_address[0]


def record_visit(visitor: str, ip: str, ua: str) -> None:
    """记一笔来访。一行一个 JSON —— 追加写，某一行坏了不影响别的行"""
    rec = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "day": time.strftime("%Y-%m-%d"),
        "v": (visitor or "").strip()[:32],
        "ip": ip,
        "ua": (ua or "")[:70],
    }
    try:
        with _visit_lock:
            with open(VISIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # 统计失败绝不能影响人家聊天


def visit_stats() -> dict:
    """统计：今天几个、累计几个。按访客 id 去重，拿不到 id 的退回按 IP"""
    today = time.strftime("%Y-%m-%d")
    days, every, today_set = {}, set(), set()
    try:
        with open(VISIT_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                key = r.get("v") or ("ip:" + r.get("ip", ""))
                d = r.get("day", "")
                days.setdefault(d, set()).add(key)
                every.add(key)
                if d == today:
                    today_set.add(key)
    except FileNotFoundError:
        pass
    return {"today": len(today_set), "total": len(every), "days": days}


def _stats_html() -> str:
    """一个给自己看的小页面。做得糙一点没关系，能看懂就行"""
    s = visit_stats()
    rows = "".join(
        f"<tr><td>{d}</td><td>{len(s['days'][d])}</td></tr>"
        for d in sorted(s["days"], reverse=True)[:14]
    ) or "<tr><td colspan=2>还没有人来过</td></tr>"
    return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><title>来访统计</title>
<style>
 body{{font-family:"Microsoft YaHei",sans-serif;background:#faf6ef;color:#33281a;
      max-width:620px;margin:44px auto;padding:0 20px}}
 h1{{font-size:20px;letter-spacing:1px}}
 .big{{display:flex;gap:18px;margin:26px 0}}
 .card{{flex:1;background:#fff;border-radius:14px;padding:22px 10px;
       box-shadow:0 4px 18px rgba(70,52,26,.10);text-align:center}}
 .card .n{{font-size:40px;font-weight:700;color:#a86c15;line-height:1.1}}
 .card .t{{font-size:13px;color:#7d6b53;margin-top:8px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;
       overflow:hidden;box-shadow:0 4px 18px rgba(70,52,26,.08)}}
 th,td{{padding:11px 18px;text-align:left;font-size:14px;
       border-bottom:1px solid #f0e9dd}}
 th{{background:#f6f0e6;color:#7d6b53;font-size:13px}}
 .tip{{color:#a1917a;font-size:12.5px;margin-top:20px;line-height:1.9}}
</style></head><body>
<h1>三汇蔡徐坤 · 来访统计</h1>
<div class="big">
  <div class="card"><div class="n">{s['today']}</div><div class="t">今天来的</div></div>
  <div class="card"><div class="n">{s['total']}</div><div class="t">累计来过</div></div>
</div>
<table><tr><th>日期</th><th>来了几个</th></tr>{rows}</table>
<p class="tip">
按浏览器去重 —— 同一个人换台设备算两个，清了浏览器数据也算新的。<br>
这个页面【只在你自己电脑上打得开】，公网地址上来的人看不到。
</p>
</body></html>"""


# ============================================================
# HTTP 服务
# ============================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "HypeBuddy/1.0"

    # 默认的日志太吵，压掉
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            if not os.path.exists(HTML_FILE):
                self._send(500, "找不到 webui.html".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            with open(HTML_FILE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return

        # 头像：本地放了照片就用照片，没放就用自带的卡通版。
        # 【为什么这么绕】卡通版是手绘的原创角色，可以随仓库公开；
        #   照片版可能涉及肖像权/版权，所以文件名带 .local 并被 .gitignore 排除。
        #   这样：你自己的机器上看到的是照片，别人 clone 下来拿到的是合规的卡通版。
        if path == "/assets/avatar":
            for name, ctype in (("avatar.jpg", "image/jpeg"),
                                ("avatar.png", "image/png"),
                                ("avatar.local.jpg", "image/jpeg"),
                                ("avatar.local.png", "image/png"),
                                ("avatar.svg", "image/svg+xml")):
                fp = os.path.join(HERE, "assets", name)
                if os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ctype)
                    return
            self._send(404, b"no avatar", "text/plain")
            return

        if path.startswith("/assets/"):
            # 【安全】必须防目录穿越：有人请求 /assets/../../.env 就完蛋了。
            # 做法是把路径规范化后，确认它还在 assets 目录里面。
            rel = path[len("/assets/"):]
            base = os.path.realpath(os.path.join(HERE, "assets"))
            full = os.path.realpath(os.path.join(base, rel))
            if full.startswith(base + os.sep) and os.path.isfile(full):
                ext = os.path.splitext(full)[1].lower()
                ctype = {
                    ".svg": "image/svg+xml",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                }.get(ext, "application/octet-stream")
                with open(full, "rb") as f:
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
            return

        if path == "/api/visit":
            # 前端一加载完就打这个信标过来，带上自己的随机 id。
            # 【为什么用 204 空响应】它是个"打个招呼就走"的请求，
            #   不需要任何返回值，越轻越好。
            qs = parse_qs(urlparse(self.path).query)
            v = (qs.get("v") or [""])[0]
            record_visit(v, _peer_ip(self), self.headers.get("User-Agent", ""))
            self._send(204, b"", "text/plain")
            return

        if path == "/stats":
            # 【只给本机看】走公网隧道进来的请求，一定带着 CF-Connecting-IP。
            #   带着，就说明是外头的人 —— 不给看，免得来访记录被别人翻。
            if (self.headers.get("CF-Connecting-IP")
                    or self.headers.get("X-Forwarded-For")):
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, _stats_html().encode("utf-8"),
                       "text/html; charset=utf-8")
            return

        if path == "/api/memory":
            # 只给这个访客自己的记忆 —— 别人的看不见
            qs = parse_qs(urlparse(self.path).query)
            v = (qs.get("v") or [""])[0].strip() or None
            mem = memory.load(v)
            self._json({"facts": mem.get("facts", [])})
            return

        if path == "/api/status":
            qs = parse_qs(urlparse(self.path).query)
            jid = (qs.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(jid)
            if not job:
                self._json({"done": True, "error": "任务不存在或已过期"}, 404)
                return
            self._json(job)
            return

        self._send(404, b"not found", "text/plain")

    # ---------- POST ----------
    def do_POST(self):
        if self.path.split("?")[0] != "/api/chat":
            self._send(404, b"not found", "text/plain")
            return

        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            question = (data.get("q") or "").strip()
            visitor = (data.get("v") or "").strip() or None   # 访客自己的随机 id
        except Exception:
            self._json({"error": "请求格式不对"}, 400)
            return

        if not question:
            self._json({"error": "你啥子都没说噻"}, 400)
            return

        # 【总花费闸】拦在开线程之前 —— 到量了一分钱都不再花
        if tokens_used() >= TOKEN_BUDGET:
            self._json({"error": "这台搭子先歇了，额度用完咯 🙏"}, 503)
            return
        if queue_len() >= MAX_QUEUE:
            self._json({"error": "前头还有人在问，缓一下再发嘛 😅"}, 429)
            return

        jid = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[jid] = {"status": "🔥 正在起锅…", "done": False}

        threading.Thread(target=run_job, args=(jid, question, visitor),
                         daemon=True).start()
        self._json({"id": jid})


# ============================================================
# 跑起来
# ============================================================
def main():
    # 说明：API Key 的检查在 main.py 里，import 的时候已经做过了
    port = 8848
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue

    if httpd is None:
        print("\n❌ 端口都被占用了，换个环境再试\n")
        return

    url = f"http://127.0.0.1:{port}/"
    print()
    print("=" * 60)
    print("  🎤 三汇蔡徐坤 · 摆龙门阵".center(52, " "))
    print("=" * 60)
    print(f"  界面跑起来了： {url}")
    print(f"  要关掉就按     Ctrl + C")
    used, left = tokens_used(), budget_left()
    pct = (used / TOKEN_BUDGET * 100) if TOKEN_BUDGET else 0
    print("-" * 60)
    print(f"  💰 已烧 {used:,} tokens（占额度 {pct:.2f}%）")
    print(f"     还剩 {left:,}，烧完自动停止服务")
    vs = visit_stats()
    print(f"  👀 今天来过 {vs['today']} 个人，累计 {vs['total']} 个")
    print(f"     看细节： {url}stats")
    print("=" * 60)
    print()

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  走了哈，有事随时喊我！\n")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
