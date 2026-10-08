# -*- coding: utf-8 -*-
"""
=========================================================
HypeBuddy 记忆模块
=========================================================
对应 Hello-Agents 第 8 章 —— 记忆系统。

【为什么要这个模块】
大模型是【无状态】的：每次调用它，它把上一次忘得干干净净。
你问它"我叫啥子"，它一定答不上来 —— 不是它笨，是它真的不知道。

想让搭子"记得住你"，就得在【外面】给它搭一套记忆。
这就是所有 Agent 记忆系统的本质：模型没记忆，脚手架有。

【两层记忆】
  ① 短期记忆 —— 最近几轮对话，原样带回去
     解决"这次聊天别断片"

  ② 长期记忆 —— 从对话里提炼出"值得长期记住的事实"，存盘
     解决"聊了十次它还认得你"

【一句话总结】
短期记忆 = 把最近说的话塞回提示词
长期记忆 = 让模型把对话"消化"成事实清单，下次按相关度捞出来
"""

import os
import re
import sys
import json
import datetime
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 记忆存在哪（和 main.py 同目录）
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")

# 短期记忆保留多少轮对话（一轮 = 一问一答）
SHORT_TERM_TURNS = 6

# ============================================================
# 【多人共用一个实例时】每个访客一份独立记忆
# ============================================================
# 【为什么必须隔离】单机自己用的时候，记忆就一份，天经地义。
#   但把网页版挂到公网之后，所有人共用同一份记忆会出大事：
#     小李说"我叫小李，在三汇开面馆" → 存进记忆
#     张三问"我叫啥子"             → 按相关度一捞 → 答"你叫小李"
#   这不是理论风险：recall() 的关键字重合算法恰好能命中这种问法，
#   百分之百会串。
#
# 【怎么隔离】浏览器生成一个随机 ID 存在自己本地，每次请求带上来。
#   服务端按这个 ID 分文件存：memory.d/<id>.json
#   各人记各人的，谁也不看见谁。
#
# 【为什么不用 IP】同一个 WiFi（比如学校机房）出去是同一个公网 IP，
#   按 IP 分会把一屋子人并成一份记忆，等于没分。
#
# 【终端模式怎么办】不调 use()，key 就是 None，还是用原来的 memory.json ——
#   命令行一个人用，行为跟以前一模一样。
MEMORY_DIR = os.path.join(os.path.dirname(MEMORY_FILE), "memory.d")

_ctx = threading.local()


def use(key):
    """切换当前线程用哪份记忆。key=None 表示用默认的那份。"""
    _ctx.key = key


def _current_key():
    return getattr(_ctx, "key", None)


def _path_for(key) -> str:
    """把访客 id 变成一个安全的文件名 —— 不能让它拼出路径穿越"""
    if not key:
        return MEMORY_FILE
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(key))[:32]
    if not safe:
        return MEMORY_FILE
    os.makedirs(MEMORY_DIR, exist_ok=True)
    return os.path.join(MEMORY_DIR, f"{safe}.json")


# ============================================================
# 一、读写记忆
# ============================================================
def _empty():
    return {"facts": [], "turns": []}


def load(key=None) -> dict:
    """
    读记忆文件。不存在或者坏了，就返回空壳（不能让它把程序搞崩）。
    key 不给就用当前线程切换的那份（见上面的 use()）。
    """
    path = _path_for(_current_key() if key is None else key)
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("facts", [])
        data.setdefault("turns", [])
        return data
    except Exception:
        return _empty()


def save(mem: dict, key=None) -> None:
    """存记忆"""
    # 短期记忆不能无限长，只留最近 N 轮
    mem["turns"] = mem["turns"][-SHORT_TERM_TURNS * 2:]
    path = _path_for(_current_key() if key is None else key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# 二、短期记忆
# ============================================================
def add_turn(mem: dict, role: str, content: str) -> None:
    """记一轮对话。role 是 'user' 或 'assistant'"""
    mem["turns"].append({"role": role, "content": content, "time": _now()})


def recent_turns(mem: dict) -> str:
    """把最近几轮对话拼成一段文字，塞回提示词"""
    turns = mem.get("turns", [])[-SHORT_TERM_TURNS * 2:]
    if not turns:
        return "（你们是第一次聊天）"
    lines = []
    for t in turns:
        who = "用户" if t["role"] == "user" else "三汇蔡徐坤"
        lines.append(f"{who}：{t['content']}")
    return "\n".join(lines)


# ============================================================
# 三、长期记忆 —— 相关度检索
# ============================================================
def _bigrams(s: str) -> set:
    """
    把一句话切成"相邻两个字"的集合。
    中文没有空格，这个办法简单又管用。
      例：「我喜欢吃火锅」→ {我喜, 喜欢, 欢吃, 吃火, 火锅}
    """
    s = re.sub(r"[^\w一-鿿]+", "", s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


# 这些字哪儿都有，拿来算相关度只会帮倒忙，先滤掉
STOP_CHARS = set("我你他她它的了是在有和与就都也很不没这那你吗呢吧啊把被给让对从到为以于会能要想说做么什")


def _keywords(s: str) -> set:
    """抽出"有信息量"的字（去掉虚词），中文检索里这个办法很实用"""
    return {c for c in s if "一" <= c <= "鿿" and c not in STOP_CHARS}


def _score(query: str, fact: str) -> float:
    """
    算一句话和一条记忆有多相关（0~1）。

    【为什么要两套算法】只有二字切片是不够的 ——
      「我家猫不吃饭」和「用户养了只猫」
      二字切片一个都对不上（家猫 / 的猫），但明显该算相关。
    所以再加一层"关键字重合"，取两者较大的那个。

    这是坑出来的经验：单一相似度指标，在中文短句上一定会漏。
    """
    # ① 二字切片：抓词语级的相似
    a, b = _bigrams(query), _bigrams(fact)
    s1 = len(a & b) / min(len(a), len(b)) if a and b else 0.0

    # ② 关键字重合：抓"猫""火锅"这种单字关键词
    ka, kb = _keywords(query), _keywords(fact)
    s2 = len(ka & kb) / min(len(ka), len(kb)) if ka and kb else 0.0

    return max(s1, s2)


def recall(mem: dict, query: str, top_k: int = 5) -> str:
    """
    按相关度捞出和当前问题有关的记忆。
    【面试可能问】为什么不做"全部塞进去"？
      —— 记忆多了会撑爆提示词、还烧钱、还会稀释重点。
         所以按相关度取 top_k，这跟人脑回忆的机制也像。
    """
    facts = mem.get("facts", [])
    if not facts:
        return "（你对这个用户还一无所知）"

    scored = [(f, _score(query, f["fact"])) for f in facts]
    # 得分高的排前面；一条都没沾边的就不硬塞了
    scored = [x for x in scored if x[1] > 0.08]
    scored.sort(key=lambda x: -x[1])
    top = scored[:top_k]

    if not top:
        return "（这轮问题跟以前记的事没关系）"
    return "\n".join(f"- {f['fact']}" for f, _ in top)


# ============================================================
# 四、长期记忆 —— 从对话里提炼事实
# ============================================================
EXTRACT_PROMPT = """下面是一轮对话。请从中提炼【值得长期记住】的、关于用户的事实。

【值得记】
- 用户的称呼、所在地、职业、身份
- 用户的偏好（爱吃什么、讨厌什么、什么习惯）
- 用户的长期目标、正在做的事
- 用户明确要求你记住的事

【不值得记】
- 一次性的问题（"今天天气咋样"）
- 常识（"三汇是直辖市"）
- 你自己（三汇蔡徐坤）说过的话
- 任何你没把握、需要猜的内容

【输出格式】
每行一条，直接写事实，不要编号、不要解释、不要标点结尾。
如果这轮对话没有任何值得记住的，只输出两个字：无

---
用户：{user}
三汇蔡徐坤：{ai}
---

值得记住的事实："""


def extract_facts(llm_call, user_msg: str, ai_msg: str) -> list:
    """
    让大模型把这一轮对话"消化"成事实清单。

    参数：
        llm_call —— 调用大模型的函数，签名 llm_call(messages) -> str
                    （从外面传进来，避免这个模块和主程序互相 import）
    """
    prompt = EXTRACT_PROMPT.format(user=user_msg, ai=ai_msg)
    try:
        raw = llm_call([{"role": "user", "content": prompt}]).strip()
    except Exception:
        return []

    if not raw or raw.strip() == "无":
        return []

    out = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-•*· ").strip()
        # 过滤掉明显是废话/解释的行
        if not line or line == "无" or len(line) < 4 or len(line) > 60:
            continue
        if line.startswith(("值得记住", "输出", "如果", "---")):
            continue
        out.append(line)
    return out[:5]      # 一轮最多记 5 条，防止刷屏


def remember(mem: dict, fact: str) -> bool:
    """
    记一条长期事实。
    返回 True 表示真的记住了，False 表示这条已经有了（去重）。
    【为什么去重】同一件事反复记，检索时会挤占 top_k 名额，
              而且会让提示词越来越长 —— 记忆也要"瘦身"。
    """
    fact = fact.strip()
    if not fact:
        return False

    # 跟已有记忆比一比，太像就不记了
    for old in mem["facts"]:
        if _score(fact, old["fact"]) > 0.75:
            return False

    mem["facts"].append({"fact": fact, "time": _now()})
    return True


# ============================================================
# 自测：python memory.py
# ============================================================
if __name__ == "__main__":
    print("=== 1. 相关度打分 ===")
    q = "我明天想去吃火锅"
    for f in ["用户喜欢吃火锅", "用户住在三汇", "用户养了只猫"]:
        print(f"  {_score(q, f):.2f}  {f}")

    print()
    print("=== 2. 记事实 + 去重 ===")
    m = _empty()
    print("  记「用户叫小王」    →", remember(m, "用户叫小王"))
    print("  再记「用户名字是小王」→", remember(m, "用户名字是小王"), "（太像了，拒绝）")
    print("  记「用户养了只叫胖虎的猫」→", remember(m, "用户养了只叫胖虎的猫"))

    print()
    print("=== 3. 按相关度回忆 ===")
    print("问「我家猫今天不吃饭」→")
    print(recall(m, "我家猫今天不吃饭"))

    print()
    print("=== 4. 短期记忆 ===")
    add_turn(m, "user", "今天热得很")
    add_turn(m, "assistant", "哦豁，那就莫出门了噻")
    print(recent_turns(m))

    print()
    print("✅ 自测结束")
