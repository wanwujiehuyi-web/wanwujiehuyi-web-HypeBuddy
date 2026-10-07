# -*- coding: utf-8 -*-
"""
=========================================================
Skill 加载器 —— 按 Hello-Agents Extra08 的口径实现
=========================================================

【Tool 和 Skill 有什么区别】
    Tool  = 能干的事。一个函数，调了就有结果。（在 tools.py 里）
    Skill = 干某件事的【说明书】。一份 markdown，告诉你分几步、每步注意啥子。

    Tool 解决"能不能"，Skill 解决"会不会"。

【核心机制：渐进式加载】
    技能如果一股脑全塞进提示词，十几个技能就能把上下文撑爆，还费钱。
    所以拆成两级：

      第一级 frontmatter（name + description）
        —— 每次对话【都】加载。很短，就一两行。
        —— 模型靠它判断"这个技能跟当前请求有关系吗"。
        —— 这是技能被触发的【唯一依据】，所以 description 必须写清楚。

      第二级 body（正文：具体步骤）
        —— 【按需】加载。没被触发就永远不读，一分钱不花。

    这就叫 progressive disclosure（渐进式披露）。
    跟人做事一个道理：不需要的说明书就不翻。

【技能长什么样】
    skills/
      三汇话教学/
        SKILL.md          ← 必需：frontmatter + 正文
        references/       ← 可选：参考资料
        scripts/          ← 可选：现成的脚本
        assets/           ← 可选：产出物模板

    SKILL.md 的格式：
        ---
        name: 三汇话教学
        description: >-
          当用户想学川渝方言怎么说时用这个技能。
        ---
        正文：第一步……第二步……
        用户的输入用 $ARGUMENTS 表示。

【说明】
    这里没用 PyYAML。frontmatter 只用到 name 和 description 两个字段，
    自己写个十几行的解析器就够了 —— 少一个依赖，也更好讲清楚原理。
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(HERE, "skills")


# ============================================================
# 一、frontmatter 解析
# ============================================================
def parse_skill_md(text: str):
    """
    把 SKILL.md 拆成 (元数据 dict, 正文 str)。

    处理的是这种格式：
        ---
        name: xxx
        description: >-          ← 这个 >- 是 YAML 的"折叠写法"，
          这一行其实是 description 的续行     表示下面的缩进行要接上去
        ---
        正文……
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text.strip()

    raw_meta, body = m.group(1), m.group(2)
    meta, key = {}, None

    for line in raw_meta.splitlines():
        if not line.strip():
            continue
        # 缩进行 = 上一行的续行（YAML 的折叠/多行写法）
        if re.match(r"^\s+\S", line) and key:
            meta[key] = (meta[key] + " " + line.strip()).strip()
            continue
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if mm:
            key, val = mm.group(1), mm.group(2).strip()
            # >、>-、|、|- 都是"值在下面几行"的意思
            if val in (">", ">-", "|", "|-", ">+", "|+"):
                val = ""
            # 去掉包裹的引号
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            meta[key] = val

    return meta, body.strip()


# ============================================================
# 二、技能加载器
# ============================================================
# 【为什么要有"工具预算"这个东西】
#   主程序默认每轮最多调 2 次工具 —— 防的是模型"查上瘾"。
#   但调研类技能天然要多查几次（搜一路、读几篇、再综合），
#   硬卡 2 次等于把这些技能直接掐死。
#
#   所以给 SKILL.md 的 frontmatter 加了个可选字段 max_tools：
#       max_tools: 8
#   技能被加载时，这一轮的工具预算就抬到这个数。
#   没写就继承默认值 —— 标准格式向后兼容，不写也能跑。
LAST_BUDGET = None      # 最近一次加载的技能要多少预算（None = 不改）


class SkillLoader:
    """扫描 skills/ 目录，管着所有技能"""

    def __init__(self, root: str = SKILLS_DIR):
        self.root = root
        self.skills = {}        # name -> {"desc", "body", "dir", "max_tools"}
        self.scan()

    def scan(self):
        """
        扫一遍目录。每个子目录里找一个 SKILL.md，读出来。
        【注意】这里只把 name 和 description 常驻内存 —— 正文也读了，
        但在没被调用之前【不会】出现在提示词里，所以一样省钱。
        """
        self.skills = {}
        if not os.path.isdir(self.root):
            return
        for entry in sorted(os.listdir(self.root)):
            folder = os.path.join(self.root, entry)
            if not os.path.isdir(folder):
                continue
            md = os.path.join(folder, "SKILL.md")
            if not os.path.isfile(md):
                continue
            try:
                with open(md, "r", encoding="utf-8") as f:
                    meta, body = parse_skill_md(f.read())
            except Exception:
                continue
            # 教程要求：name 和 description 必须是非空字符串
            name = (meta.get("name") or entry).strip()
            desc = (meta.get("description") or "").strip()
            if not desc:
                continue        # 没写 description 的技能等于触发不了，跳过

            # max_tools 是可选扩展字段。写得不对就当没写（不能让一个笔误把技能废掉）
            mt = None
            raw_mt = (meta.get("max_tools") or "").strip()
            if raw_mt.isdigit():
                mt = max(1, min(20, int(raw_mt)))   # 夹在 1~20，防手滑写个 999

            self.skills[name] = {"desc": desc, "body": body,
                                 "dir": folder, "max_tools": mt}

    # ---------- 第一级：给提示词看的清单 ----------
    def descriptions(self) -> str:
        """只返回 name + description。每次对话都带上，所以必须短"""
        if not self.skills:
            return "（没装技能包）"
        return "\n".join(f"- {n}：{s['desc']}" for n, s in self.skills.items())

    # ---------- 第二级：真正要用的时候才加载 ----------
    def load(self, name: str, args: str = "") -> str:
        """把技能的正文取出来，顺便把 $ARGUMENTS 换成用户这次的输入"""
        global LAST_BUDGET
        name = (name or "").strip()
        if name not in self.skills:
            available = "、".join(self.skills) or "（一个都没有）"
            return f"没得叫「{name}」这个技能包。装起的有：{available}"

        sk = self.skills[name]
        # 告诉主程序：这轮的工具预算要抬到多少
        LAST_BUDGET = sk.get("max_tools")
        body = sk["body"]

        # $ARGUMENTS 是教程约定的占位符
        body = body.replace("$ARGUMENTS", args.strip() or "（用户没细说）")

        # 顺便报一下这个技能带了哪些附件，让模型知道可以读
        extras = []
        for sub in ("references", "scripts", "assets"):
            d = os.path.join(sk["dir"], sub)
            if os.path.isdir(d):
                files = [f for f in sorted(os.listdir(d)) if not f.startswith(".")]
                if files:
                    extras.append(f"  {sub}/ —— {', '.join(files)}")
        extra_txt = ("\n\n【这个技能还带了资料】\n" + "\n".join(extras)) if extras else ""

        return (f"已加载技能包「{name}」。按下面的步骤做：\n\n{body}{extra_txt}")

    def names(self):
        return list(self.skills)


# ============================================================
# 三、给主程序调的工具函数
# ============================================================
_LOADER = None


def get_loader() -> SkillLoader:
    """全局只扫一次盘，别每次都重读"""
    global _LOADER
    if _LOADER is None:
        _LOADER = SkillLoader()
    return _LOADER


def use_skill(arg: str) -> str:
    """
    主程序里注册成工具的那个函数。
    参数格式：技能名 或 技能名 + 空格/冒号/竖线 + 这次的具体需求
        例：use_skill("三汇话教学")
            use_skill("三汇话教学 我想学啷个打招呼")
    """
    arg = (arg or "").strip()
    if not arg:
        loader = get_loader()
        return ("没说要加载哪个技能。装起的有：\n"
                + loader.descriptions())

    # 技能名和参数之间，用空白、冒号、竖线、逗号都行
    parts = re.split(r"[\s：:|,，]+", arg, maxsplit=1)
    name = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return get_loader().load(name, rest)


# ============================================================
# 自测：python skill_loader.py
# ============================================================
if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    loader = get_loader()
    print("=== 扫到几个技能包 ===")
    for n in loader.names():
        print(f"  · {n}")
        print(f"    {loader.skills[n]['desc'][:70]}")

    print()
    print("=== 第一级：常驻提示词的清单（短）===")
    print(loader.descriptions())

    print()
    print("=== 第二级：按需加载的正文（长）===")
    out = loader.load(loader.names()[0] if loader.names() else "x",
                      "我想学啷个打招呼")
    print(out[:700])
    print(f"\n（正文共 {len(out)} 字 —— 不触发就不会进提示词）")
