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


def run_job(job_id, question):
    """在后台线程里跑一次完整对话"""
    _local.job_id = job_id
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

        if path == "/api/memory":
            mem = memory.load()
            self._json({"facts": mem.get("facts", [])})
            return

        if path == "/api/status":
            from urllib.parse import urlparse, parse_qs
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
        except Exception:
            self._json({"error": "请求格式不对"}, 400)
            return

        if not question:
            self._json({"error": "你啥子都没说噻"}, 400)
            return

        jid = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[jid] = {"status": "🔥 正在起锅…", "done": False}

        threading.Thread(target=run_job, args=(jid, question), daemon=True).start()
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
