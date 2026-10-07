# -*- coding: utf-8 -*-
"""
=========================================================
HypeBuddy 技能库
=========================================================
三汇蔡徐坤的"手和脚"。主程序只管调，怎么实现全在这儿。

【为什么要单独一个文件】
    全部塞进 main.py 的话，光工具就几百行，人格和循环都淹没在里头。
    拆出来之后，想加技能只要在这个文件里写个函数、去 main.py 注册一行。
    这也是毕设可以讲的一点：**工具系统是可扩展的**。

【三类技能】
    一、实用工具   查时间 / 算数 / 读网页 / 帮你选
    二、川渝特色   方言词典 / 歇后语 / 川菜做法 / 赶场日
    三、生活助理   待办清单 / 倒计时 / 记账

【统一约定】
    每个技能都是一个函数：收到一个字符串参数，返回一段字符串结果。
    不许抛异常出去 —— 出错了也要把"为啥子错"当结果返回，
    不然智能体整个循环就断了。
"""

import os
import re
import ast
import json
import html
import random
import operator
import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TODO_FILE = os.path.join(HERE, "todo.json")
MONEY_FILE = os.path.join(HERE, "money.json")

# 跟主程序一样：不走系统代理，否则部分网站直接断连
SESSION = requests.Session()
SESSION.trust_env = False

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ============================================================
# 一、实用工具
# ============================================================
def get_time(arg: str = "") -> str:
    """查现在的日期时间。参数写了也白写，忽略"""
    now = datetime.datetime.now()
    wd = "一二三四五六日"[now.weekday()]
    return (f"{now.year}年{now.month}月{now.day}日 "
            f"{now.hour:02d}:{now.minute:02d}，星期{wd}")


# 只允许这些运算 —— 绝不能拿 eval 直接跑用户输入
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # 防一手：2**9999999 能把机器算死
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("指数太大了，算不动")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("只认得加减乘除和括号")


def calc(expr: str) -> str:
    """
    算数。
    【安全】这里绝不能用 eval()：那个东西会执行任意代码，
    用户随便输一句就能把电脑端了。所以用 ast 解析成语法树，
    只放行白名单里的几种运算。
    """
    expr = (expr or "").strip().replace("×", "*").replace("÷", "/").replace("，", ",")
    if not expr:
        return "你要算啥子？写个式子给我，比如 calc[23*17+5]"
    try:
        v = _safe_eval(ast.parse(expr, mode="eval"))
    except Exception as e:
        return f"这个式子我算不来：{e}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{expr} = {v}"


def read_page(url: str) -> str:
    """
    读一个网页的正文。
    配合联网搜索用：搜到链接 → 打开看里头说啥子。
    """
    url = (url or "").strip()
    if not url:
        return "把网址给我噻"
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = SESSION.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        txt = r.text
        # 先把脚本样式整块干掉，不然正文里全是代码
        txt = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ",
                     txt, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = html.unescape(txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) < 60:
            return f"这个页面没得啥子正文（可能是动态加载的）：{url}"
        return f"来自 {url} 的正文：\n{txt[:2000]}"
    except Exception as e:
        return f"打不开这个网页：{type(e).__name__}: {str(e)[:80]}"


def roll(arg: str) -> str:
    """
    抛硬币 / 掷骰子 / 帮你在几个选项里挑一个。
    参数可以填："硬币" "骰子" "火锅,串串,小面"
    """
    arg = (arg or "").strip()
    if not arg or arg in ("硬币", "抛硬币", "正反面"):
        return f"抛硬币：{'正面' if random.random() < .5 else '反面'}"
    if arg in ("骰子", "掷骰子", "色子"):
        return f"掷骰子：{random.randint(1, 6)} 点"
    if arg in ("大爷",):   # 彩蛋
        return "喊大爷干啥子？要得，大爷说啥子就是啥子"
    opts = [o.strip() for o in re.split(r"[,，、\s|/]+", arg) if o.strip()]
    if len(opts) >= 2:
        return f"我给你挑一个：{random.choice(opts)}"
    n = random.randint(1, 100)
    return f"给你个随机数：{n}"


# ============================================================
# 二、川渝特色
# ============================================================
# 【方言词典】
#   收的都是川渝人日常挂嘴边的词。
#   写成"词 → 解释"的字典，查的时候先精确匹配，再模糊匹配。
FANGYAN = {
    "啥子": "什么。「你说啥子？」",
    "咋个": "怎么。成都那边说得多，川东人讲「啷个」",
    "啷个": "怎么、为什么。重庆和川东都讲这个，三汇人也讲「啷个」。「你啷个不来喃？」",
    "要得": "好的、可以。川渝人答应事情最常用这个词",
    "巴适": "舒服、好、合适。「这个天巴适得很」",
    "安逸": "舒服、爽。「安逸惨了」",
    "晓得": "知道。「我晓得了」",
    "莫": "别、不要。「莫慌」「莫搞」",
    "蛮": "很、挺。「蛮好的」",
    "耍": "玩。「出去耍」",
    "崽儿": "小孩、小伙子。重庆一带叫得多",
    "娃儿": "小孩。川东一带叫得多",
    "孃孃": "阿姨、伯母。读 niáng niang",
    "龙门阵": "聊天、闲谈。「摆龙门阵」就是聊天",
    "好生": "好好地、认真。「你好生走路」",
    "摆": "聊、说。「摆两句嘛」",
    "整": "做、搞、弄。「整碗面」",
    "遭": "碰到、遇上（不好的事）。「遭了」就是完了",
    "扯": "胡扯、乱说。「扯远了」",
    "雄起": "加油、打起精神。球场上喊得最多",
    "扎起": "支持、撑场面。「我给你扎起」",
    "瓜": "傻。「瓜娃子」",
    "哈": "傻。读 hǎ。「哈戳戳的」",
    "宝器": "傻乎乎的人，但多半是打趣，不是真骂",
    "锤子": "表否定。「锤子哦」＝「才怪」、不靠谱",
    "铲铲": "表否定。「说个铲铲」＝「说个屁」",
    "扯把子": "胡说八道、瞎扯",
    "日白": "吹牛、瞎说",
    "哦豁": "糟糕、完了。「哦豁，搞忘带钥匙了」",
    "也": "打招呼的惊喜语气。「也~ 你来啦！」",
    "趴": "软、烂。「煮趴了」就是煮得烂熟",
    "撇脱": "干脆、省事。「这样撇脱得很」",
    "相因": "便宜。「这个好相因」",
    "架势": "起劲、使劲。「吃得架势」",
    "幺儿": "最小的孩子，也是长辈对孩子的昵称",
    "老汉儿": "爸爸。注意不是老公",
    "嘎嘎": "肉。小娃娃话",
    "莽莽": "饭。小娃娃话",
    "茅斯": "厕所",
    "灶房": "厨房",
    "屋头": "家里、屋里",
    "坝坝": "平地、广场。「坝坝舞」",
    "卡卡": "角落。「卡卡角角」＝各个角落",
    "背时": "倒霉、活该",
    "倒拐": "拐弯。「前面倒拐」",
    "归一": "完成、收拾好。「整归一了」",
    "刹角": "结束、收尾。「刹角了」",
    "将才": "刚才",
    "一哈": "一会儿、一下。「等我一哈」",
    "鼓捣": "硬要、非要做。「鼓捣要去」",
    "悬": "危险、差点出事。「好悬哦」",
    "毛": "发脾气、发火。「莫毛」",
    "焦": "着急、愁。「焦得很」",
    "巴": "贴、粘。「巴到墙上」",
    "撇": "差、不好。「这个质量撇」",
    "铁": "关系好、靠得住。「铁哥们」",
    "炯": "聪明、机灵（川渝部分地区用）",
    "靸": "拖（鞋）。读 sǎ。「靸起鞋子」",
    "鬼火冒": "特别生气、火大",
    "鬼画桃符": "字写得乱七八糟",
    "装疯迷窍": "装傻充愣",
    "光胴胴": "光着上身",
    "打平伙": "凑钱一起吃饭，AA制",
    "吃莽莽": "吃饭（对小娃儿说的）",
    "打牙祭": "吃顿好的、改善伙食",
    "摆闲条": "闲聊、说些没用的",
    "扯谎俩白": "撒谎、编瞎话",
    "挑肥拣瘦": "挑三拣四",
    "憨吃哈胀": "吃得又急又多",
    "稀得好": "表示庆幸。「稀得好没遭」＝幸好没中招",
}


def fangyan(word: str) -> str:
    """查川渝方言词是啥子意思"""
    word = (word or "").strip().strip("？?。.")
    if not word:
        # 没给词就随机送一个
        w = random.choice(list(FANGYAN))
        return f"「{w}」——{FANGYAN[w]}"
    if word in FANGYAN:
        return f"「{word}」——{FANGYAN[word]}"

    # 【模糊匹配】用户可能问"安逸是啥子意思"这种整句。
    # 【坑】不能返回"第一个命中的词" —— 那样问"安逸"会被答成"啥子"，
    #       因为"啥子"在字典里排在前面。
    # 正确做法：把所有命中的词按它在问句里出现的【位置】排，
    #          谁出现得靠前，问的就是谁。
    hits = [(word.index(k), k, v) for k, v in FANGYAN.items() if k in word]
    if hits:
        hits.sort()
        _, k, v = hits[0]
        return f"「{k}」——{v}"
    sample = "、".join(random.sample(list(FANGYAN), 8))
    return f"「{word}」这个我这儿没收。我晓得的还有：{sample}……"


# 【歇后语】
XIEHOUYU = [
    ("哑巴吃汤圆", "心头有数"),
    ("哑巴吃黄连", "有苦说不出"),
    ("猫抓糍粑", "脱不到爪爪"),
    ("猪八戒照镜子", "里外不是人"),
    ("猪八戒的钉耙", "倒打一耙"),
    ("茅斯头的石头", "又臭又硬"),
    ("癞蛤蟆打哈欠", "好大的口气"),
    ("剃头匠的挑子", "一头热"),
    ("十五个吊桶打水", "七上八下"),
    ("老鼠钻风箱", "两头受气"),
    ("黄泥巴掉裤裆", "不是屎也是屎"),
    ("孔夫子搬家", "净是输（书）"),
    ("外甥打灯笼", "照旧（舅）"),
    ("竹篮打水", "一场空"),
    ("姜太公钓鱼", "愿者上钩"),
    ("泥菩萨过河", "自身难保"),
    ("芝麻开花", "节节高"),
    ("和尚头上的虱子", "明摆着的"),
    ("脚踩两只船", "左右为难"),
    ("擀面杖吹火", "一窍不通"),
    ("茶壶里煮饺子", "有货倒不出"),
    ("兔子的尾巴", "长不了"),
    ("狗咬吕洞宾", "不识好人心"),
    ("大水冲了龙王庙", "一家人不认一家人"),
    ("周瑜打黄盖", "一个愿打一个愿挨"),
    ("刘备借荆州", "有借无还"),
    ("张飞穿针", "大眼瞪小眼"),
    ("关公面前耍大刀", "献丑"),
    ("半天云里挂口袋", "装风（疯）"),
    ("茅斯里打灯笼", "找屎（死）"),
    ("螃蟹过街", "横行霸道"),
    ("麻雀掉进粗糠里", "一场空欢喜"),
    ("猴子掰包谷", "掰一个丢一个"),
    ("黄瓜打锣", "去了半截"),
    ("灯草做拐杖", "借不上力"),
    ("三十晚上的案板", "没得空（闲）"),
    ("剃头的拍巴掌", "完了"),
    ("端公打跟斗", "鬼颠倒"),
    ("老鼠爬秤钩", "自称自"),
    ("阎王爷贴告示", "鬼话连篇"),
]


def xiehouyu(keyword: str) -> str:
    """来句川渝歇后语。参数可填关键词，也可以不填"""
    kw = (keyword or "").strip()
    if kw:
        hits = [f"{a}——{b}" for a, b in XIEHOUYU if kw in a or kw in b]
        if hits:
            return "；".join(hits[:3])
    a, b = random.choice(XIEHOUYU)
    return f"{a}——{b}"


# 【川菜做法】
CAIPU = {
    "回锅肉": "【回锅肉】二刀肉煮到七分熟，晾冷切薄片。热锅下肉片煸到吐油、卷边成"
              "'灯盏窝'，拨到一边，下郫县豆瓣炒出红油，加甜面酱、豆豉炒香，"
              "最后下蒜苗段断生。要点：肉要晾冷再切，不然切不薄。",
    "麻婆豆腐": "【麻婆豆腐】嫩豆腐切块，盐水里泡五分钟去豆腥。牛肉末炒酥，"
                "下豆瓣、豆豉、辣椒面炒香，加高汤下豆腐，小火煨三分钟。"
                "分三次勾芡（这是个讲究），起锅撒花椒面和蒜苗。要点：麻、辣、烫、香、酥、嫩、鲜、活。",
    "水煮肉片": "【水煮肉片】里脊切薄片，用蛋清、淀粉、料酒抓匀。"
                "锅里下豆瓣、干辣椒、花椒炒香加汤烧开，先烫青菜垫碗底，"
                "再下肉片烫变色就捞。肉上铺干辣椒面、花椒面、蒜末，"
                "滚油一泼。要点：肉片下锅十几秒就好，久了就老。",
    "鱼香肉丝": "【鱼香肉丝】肉丝上浆滑油。泡椒末、姜蒜末、豆瓣炒香，"
                "下木耳丝、笋丝、肉丝翻炒，倒鱼香汁（糖、醋、酱油、淀粉、水，"
                "比例大约 2:2:1:1:3）收汁。要点：鱼香味的灵魂是糖醋比例，"
                "不甜不酸就废了。",
    "宫保鸡丁": "【宫保鸡丁】鸡腿肉切丁上浆。碗汁：糖、醋、酱油、料酒、淀粉。"
                "花椒干辣椒炝锅，下鸡丁炒散，倒碗汁，最后下油酥花生米。"
                "要点：花生米最后放，早了就回软。",
    "夫妻肺片": "【夫妻肺片】牛头皮、牛心、牛舌、牛肚卤熟晾冷切薄片。"
                "红油、花椒面、芝麻、花生碎、芹菜末拌匀。要点：红油要自己炼，"
                "买的不香。",
    "担担面": "【担担面】肉末用甜面酱炒成脆臊。碗底放红油、花椒面、蒜泥、"
              "芽菜末、酱油、醋。面条煮熟捞进去，面上盖脆臊和花生碎。"
              "要点：面要少，味要重，汤要干。",
    "酸辣粉": "【酸辣粉】红薯粉提前泡软。碗底：红油、花椒面、蒜水、"
              "保宁醋、酱油、榨菜末、酥黄豆。粉烫熟捞进碗，浇高汤，撒香菜。"
              "要点：醋一定要用保宁醋，才够川味。",
    "口水鸡": "【口水鸡】三黄鸡整只煮十分钟，关火焖十分钟，捞出来立刻冰水激。"
              "晾冷斩件，浇红油、花椒面、蒜泥、糖、醋、酱油调的料汁，撒花生碎。"
              "要点：煮完过冰水，皮才脆。",
    "蒜泥白肉": "【蒜泥白肉】后腿肉整块煮到断生，晾冷切薄片。"
                "蒜泥、红油、复制酱油、糖调成料汁浇上。要点：刀工是全部，"
                "切得越薄越好吃。",
    "干煸四季豆": "【干煸四季豆】四季豆掰段，宽油下锅煸到表皮起皱捞起。"
                  "肉末、芽菜、干辣椒炒香，回锅同炒。要点：一定要煸透，"
                  "不然四季豆没熟会中毒。",
    "开水白菜": "【开水白菜】老母鸡、火腿、干贝吊汤六小时，用鸡茸扫汤三次"
                "直到汤清如水。白菜心焯水后放进汤里蒸五分钟。"
                "要点：这是川菜里最费功夫的一道，看着清淡，其实最考验手艺。",
}


def caipu(name: str) -> str:
    """川菜做法。参数填菜名"""
    name = (name or "").strip()
    if not name:
        return "你要学哪道菜？我这儿有：" + "、".join(CAIPU.keys())
    if name in CAIPU:
        return CAIPU[name]
    for k, v in CAIPU.items():
        if k in name or name in k:
            return v
    return ("我这儿没得这道菜的做法。有的：" + "、".join(CAIPU.keys()))


# 【赶场日】
#   四川乡镇赶场一般是"逢几赶"，常见的是逢1、4、7 或 2、5、8 或 3、6、9。
#   【重要提醒】实际是按【农历】算的，这里按公历近似，只能当参考。
#   想知道三汇镇的确切日子，得问当地人 —— 别拿这个当准数。
GANCHANG_CYCLE = (3, 6, 9)


def ganchang(arg: str = "") -> str:
    """算这个月哪几天赶场（按公历逢3、6、9近似，实际以当地为准）"""
    now = datetime.datetime.now()
    m = now.month
    if arg.strip().isdigit():
        m = int(arg.strip())
        if not 1 <= m <= 12:
            return "月份要在 1~12 之间噻"
    days = []
    y = now.year
    d = 1
    while True:
        try:
            dt = datetime.date(y, m, d)
        except ValueError:
            break
        if dt.day in GANCHANG_CYCLE:
            days.append(f"{dt.day}号")
        d += 1
    cyc = "、".join(str(c) for c in GANCHANG_CYCLE)
    return (f"{y}年{m}月，按逢{cyc}算，赶场天是：{'、'.join(days)}\n"
            f"（注意：实际赶场是按【农历】排的，这里按公历近似，"
            f"只能当参考，准确的日子问一下当地人）")


# ============================================================
# 三、生活助理
# ============================================================
def _load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def todo(arg: str) -> str:
    """
    待办清单。
    用法：todo[加 明天买醋] / todo[看] / todo[删 2] / todo[清空]
    """
    arg = (arg or "").strip()
    items = _load_json(TODO_FILE)

    if not arg or arg in ("看", "列表", "查看"):
        if not items:
            return "待办清单是空的，安逸"
        return "你还有这些没办：\n" + "\n".join(
            f"  {i+1}. {it['text']}（{it['time']}）" for i, it in enumerate(items))

    if arg.startswith(("加", "记", "添", "新增")):
        text = re.sub(r"^(加|记到起|记|添|新增)[:：\s]*", "", arg).strip()
        if not text:
            return "要记啥子？比如 todo[加 明天买醋]"
        items.append({"text": text,
                      "time": datetime.datetime.now().strftime("%m-%d %H:%M")})
        _save_json(TODO_FILE, items)
        return f"记到了：{text}。现在有 {len(items)} 条"

    if arg.startswith(("删", "完成", "搞定", "划掉")):
        num = re.search(r"\d+", arg)
        if not num:
            return "要删第几条？比如 todo[删 2]"
        i = int(num.group()) - 1
        if not 0 <= i < len(items):
            return f"只有 {len(items)} 条，没得第 {i+1} 条"
        done = items.pop(i)
        _save_json(TODO_FILE, items)
        return f"搞定：{done['text']}，还剩 {len(items)} 条"

    if arg in ("清空", "全删", "清"):
        _save_json(TODO_FILE, [])
        return "清单清空了"

    return "没听懂，你可以说：todo[加 明天买醋] / todo[看] / todo[删 1] / todo[清空]"


def countdown(arg: str) -> str:
    """
    倒计时。
    用法：countdown[2027-01-01] 或 countdown[过年]（只认几个固定日子）
    """
    arg = (arg or "").strip()
    today = datetime.date.today()

    # 【注意】这里只放【公历】固定的节日。
    #   春节、端午、中秋都是农历，日子每年在变，我这没有农历转换 ——
    #   所以"过年"这种词不能硬映射到 01-01，那是元旦，不是过年。
    #   宁可说"算不了"，也不能给个错日子。
    for k in ("过年", "春节", "除夕", "端午", "中秋", "七夕"):
        if k in arg:
            return (f"「{k}」这些是按【农历】算的，日子每年都不一样，"
                    f"我这儿没得农历转换，算不准 —— 你直接告诉我具体日期，"
                    f"比如 countdown[2027-02-06]")

    fixed = {
        "元旦": "01-01", "五一": "05-01", "劳动节": "05-01",
        "国庆": "10-01", "国庆节": "10-01", "儿童节": "06-01",
        "六一": "06-01", "三八": "03-08", "妇女节": "03-08",
    }
    for k, md in fixed.items():
        if k in arg:
            m, d = map(int, md.split("-"))
            target = datetime.date(today.year, m, d)
            if target < today:
                target = datetime.date(today.year + 1, m, d)
            delta = (target - today).days
            return (f"离{k}还有 {delta} 天"
                    + ("（就是今天！）" if delta == 0 else ""))

    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", arg)
    if m:
        try:
            target = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return "这个日期不对头"
        delta = (target - today).days
        if delta > 0:
            return f"离 {target} 还有 {delta} 天"
        if delta == 0:
            return f"{target} 就是今天"
        return f"{target} 已经过去 {abs(delta)} 天了"

    return "你要倒计时啥子？可以填日期（countdown[2027-01-01]）或者 countdown[过年]"


def money(arg: str) -> str:
    """
    记流水账。
    用法：money[花 30 吃饭] / money[收 5000 工资] / money[看] / money[清空]
    """
    arg = (arg or "").strip()
    records = _load_json(MONEY_FILE)

    if not arg or arg in ("看", "账", "统计", "查"):
        if not records:
            return "还没记过账"
        total_out = sum(r["amount"] for r in records if r["amount"] < 0)
        total_in = sum(r["amount"] for r in records if r["amount"] > 0)
        lines = "\n".join(
            f"  {r['time']}  {'支出' if r['amount']<0 else '收入'} "
            f"{abs(r['amount']):g}  {r['note']}" for r in records[-15:])
        return (f"最近这几笔：\n{lines}\n"
                f"合计：花出去 {abs(total_out):g}，收进来 {total_in:g}，"
                f"净 {total_in+total_out:g}")

    if arg in ("清空", "全删"):
        _save_json(MONEY_FILE, [])
        return "账本清空了"

    m = re.match(r"(花|花销|支出|出|收|收入|进)\s*([\d.]+)\s*(.*)", arg)
    if m:
        kind, num, note = m.group(1), float(m.group(2)), m.group(3).strip()
        amount = -num if kind in ("花", "花销", "支出", "出") else num
        records.append({"amount": amount, "note": note or "没写备注",
                        "time": datetime.datetime.now().strftime("%m-%d %H:%M")})
        _save_json(MONEY_FILE, records)
        word = "花" if amount < 0 else "进"
        return f"记下了：{word} {num:g}，{note or '没写备注'}"

    return "没听懂。你可以说：money[花 30 吃饭] / money[收 5000 工资] / money[看]"


# ============================================================
# 自测：python skills.py
# ============================================================
if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    tests = [
        ("查时间", get_time, ""),
        ("算数", calc, "23*17+5"),
        ("算数(乱写)", calc, "import os"),
        ("方言", fangyan, "巴适"),
        ("方言(模糊)", fangyan, "安逸是啥子意思"),
        ("歇后语", xiehouyu, ""),
        ("歇后语(查)", xiehouyu, "汤圆"),
        ("川菜", caipu, "回锅肉"),
        ("赶场", ganchang, ""),
        ("待办-加", todo, "加 明天买醋"),
        ("待办-看", todo, "看"),
        ("待办-删", todo, "删 1"),
        ("倒计时", countdown, "过年"),
        ("记账-花", money, "花 30 吃饭"),
        ("记账-看", money, "看"),
        ("记账-清", money, "清空"),
    ]
    for name, fn, arg in tests:
        try:
            out = fn(arg)
        except Exception as e:
            out = f"!! 报错了: {type(e).__name__}: {e}"
        print(f"【{name}】{arg!r}")
        print(f"   {out[:160]}")
        print()
