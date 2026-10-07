# -*- coding: utf-8 -*-
"""
HypeBuddy 语音模块
=========================================================
让"气氛组"真正发出声音。

设计上同样是【两层保险】：
    ① edge-tts（在线，微软 Edge 的语音引擎）
       优点：音质好、音色可选、能调语速和音调（气氛组必备）
       缺点：需要联网

    ② Windows 自带语音 SAPI（离线）
       优点：零依赖、断网也能用
       缺点：音质像十年前的导航仪

主力用 ①，失败了自动回退到 ②，保证"永远有声音"。
"""

import os
import re
import subprocess
import sys
import tempfile
import asyncio

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# 引擎选择 —— 免费的和收费的，你挑
# ============================================================
#   "edge"  = 微软 edge-tts    免费、免注册、开箱即用
#             缺点：没有三汇话音色，只能靠"川普发音层"模拟
#
#   "volc"  = 火山引擎 豆包语音  收费（新用户有试用额度）
#             优点：有【真·四川方言男声】，还能用大白话指挥语气
#             需要：去火山引擎控制台开通，拿 App ID / Access Token
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").strip().lower()

# ============================================================
# 配置区（想调音色/语速改这里）
# ============================================================
# 【总开关】想开声音就把 .env 里的 VOICE_ENABLED 改成 true
#   关掉之后：程序照跑，只是不发声 —— 所有回复都是纯文字
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

# 音色：中文男声（换音色只改这一行）
#     zh-CN-YunjianNeural   激情有劲  ← 默认，配三汇话的冲劲
#     zh-CN-YunxiNeural     活泼阳光，年轻能侃
#     zh-CN-YunyangNeural   稳重厚实
#     zh-CN-YunxiaNeural    少年音，机灵
VOICE_NAME = "zh-CN-YunjianNeural"

VOICE_RATE = "+0%"      # 语速：正常偏快，三汇话节奏紧
VOICE_PITCH = "+0Hz"   # 音调：正常。想更冲可以试 +10Hz

# 怪叫：只比正常说话稍微high一点，别变成尖叫
SHOUT_RATE = "+20%"
SHOUT_PITCH = "+10Hz"


# ============================================================
# 火山引擎（豆包语音）配置 —— 收费引擎
# ============================================================
# 音色（2026 年音色表，均为「四川方言男声」）：
#   zh_male_m191_uranus_bigtts      云舟 2.0   通用场景，声音厚实  ← 默认
#   zh_male_taocheng_uranus_bigtts  小天 2.0   更年轻一点
#
# 【注意】市面上没有商用「三汇话」音色 —— 三汇话属于四川方言区，
#   "四川话"音色就是目前最接近的商用选择，川渝两地听着基本通。
VOICE_NAME_VOLC = os.getenv("VOLC_VOICE", "zh_male_m191_uranus_bigtts")

# 语音指令：火山引擎 2.0 的独门功能 —— 用大白话直接指挥语气和方言
VOLC_INSTRUCTION = os.getenv(
    "VOLC_INSTRUCTION",
    "[#用四川话说，语气耿直火热、语速稍快，带点江湖气]",
)
VOLC_INSTRUCTION_SHOUT = os.getenv(
    "VOLC_INSTRUCTION_SHOUT",
    "[#用四川话大声喊出来，情绪激动、声音洪亮]",
)

# 控制台拿到的凭证（填在 .env 里）
#   新版控制台：一个 API Key 就够了（推荐）
VOLC_API_KEY = os.getenv("VOLC_API_KEY", "").strip()
#   旧版控制台：App ID + Access Token（也兼容）
VOLC_APPID = os.getenv("VOLC_APPID", "").strip()
VOLC_TOKEN = os.getenv("VOLC_ACCESS_TOKEN", "").strip()

VOLC_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
# 模型版本：seed-tts-2.0 = 豆包语音合成大模型 2.0（带方言和语音指令的那个）
VOLC_RESOURCE_ID = os.getenv("VOLC_RESOURCE_ID", "seed-tts-2.0")
VOLC_MODEL = os.getenv("VOLC_MODEL", "seed-tts-2.0-standard")


# ============================================================
# 川普发音层（免费引擎专用）
# ============================================================
# 【为什么要这么绕】
# 免费引擎（edge-tts）的音色库里没有三汇话/四川话，
# 只有普通话 + 东北话 + 陕西话。
#
# 但川渝普通话（"川普"）的发音规律是固定的 —— 可以把要念的字
# 换成"能逼出同样读音"的同音字，让普通话音色念出川渝味。
#
# 【重要】只改【送进喇叭的文本】，屏幕上给你看的正文一个字都不动。
# 【注意】用火山引擎（真方言）时这一层会自动关掉 —— 人家本来就说四川话，
#        再改字反而念错。

CHONGQING_ACCENT_ENABLED = True

CHONGQING_ACCENT = {
    # ① n / l 不分 —— 川渝话最标志性的一条
    "你": "李", "那": "辣", "哪": "喇", "年": "连", "男": "兰",
    "难": "兰", "能": "棱", "脑": "老", "牛": "流", "女": "吕",
    "内": "类", "拿": "拉", "宁": "灵",

    # ② 平翘舌不分：zh / ch / sh → z / c / s
    "是": "四", "事": "四", "说": "缩", "吃": "呲", "出": "粗",
    "上": "丧", "少": "扫", "中": "宗", "生": "僧", "谁": "随",
    "成": "层", "长": "藏", "常": "藏", "知": "资", "之": "资",
    "只": "紫", "种": "总", "山": "三", "身": "森", "睡": "岁",
    "书": "苏", "数": "素", "找": "早", "张": "脏",

    # ③ 川渝特有读音（外地人一听就晓得是三汇的）
    "去": "气",      # qù → qì
    "六": "路",      # liù → lù
    "街": "该",      # jiē → gāi
    "鞋": "孩",      # xié → hái
    "咸": "寒",      # xián → hán
    "请": "寝",      # qǐng → qǐn
    "重": "从",      # 三汇 = cóng qìn
    "庆": "沁",
}

_ACCENT_TABLE = str.maketrans(CHONGQING_ACCENT)


def to_chongqing_accent(text: str) -> str:
    """把要念的字换成川普读音（只动语音，不动屏幕上的字）"""
    if not CHONGQING_ACCENT_ENABLED:
        return text
    return text.translate(_ACCENT_TABLE)


# ============================================================
# 三汇腔节奏处理
# ============================================================
# 三汇味儿的大头在【用词】上（啥子、要得、巴适、晓得不、莫得问题），
# 那部分交给大模型去说。这里只做一件事：把长句拆短 ——
# 三汇人说话又快又耿直，一句一句往外蹦，不绕弯子。

FLOW_ENABLED = True


def to_chongqing_flow(text: str) -> str:
    """把长句拆短，念出来更有三汇人耿直的节奏"""
    if not FLOW_ENABLED or not text.strip():
        return text

    # 按标点切短句
    # 【坑】小数点不能被当句号切掉，要排除"两边都是数字"的点
    parts = re.split(r"[，。！？；、,;!?]|(?<!\d)\.|\.(?!\d)", text)
    parts = [p.strip() for p in parts if p.strip()]

    # 合并成 12~20 字的句子（三汇话节奏比普通话紧）
    lines, buf = [], ""
    for p in parts:
        if len(buf) + len(p) <= 20:
            buf = f"{buf}，{p}".strip("，").strip()   # 用逗号接起来，留出换气
        else:
            if buf:
                lines.append(buf)
            buf = p
    if buf:
        lines.append(buf)

    return "\n".join(lines)


# ============================================================
# 文本净化：把"给人看的"文本变成"给嘴念的"文本
# ============================================================
def clean_for_speech(text: str) -> str:
    """
    模型输出的是 Markdown（带 ** 加粗、# 标题、🔗 链接、emoji），
    直接念出来会变成"星号星号"、"井号"之类的噪音。这里清洗一遍。
    """
    t = text

    # 去掉 Markdown 链接，只留文字：[标题](url) -> 标题
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # 去掉裸网址
    t = re.sub(r"https?://\S+", "", t)
    # 去掉加粗/斜体/标题/引用等标记
    t = re.sub(r"[*_`#>~]+", "", t)
    # 去掉 emoji（保留中文、英文、数字、常用标点）
    t = re.sub(r"[^一-鿿　-〿＀-￯"
               r"a-zA-Z0-9\s.,!?;:'\"()\-%+/]", "", t)
    # 折叠超长的重复字母（WOOOOOO -> WOOO），不然会念很久
    t = re.sub(r"([A-Za-z])\1{3,}", r"\1\1\1", t)
    # 压缩空行
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)

    return t.strip()


# ============================================================
# 方案①：edge-tts（主力）
# ============================================================
def _speak_edge(text: str, rate: str, pitch: str) -> bool:
    """用 edge-tts 生成音频并播放。成功返回 True。"""
    try:
        import edge_tts
    except ImportError:
        return False

    tmp = os.path.join(tempfile.gettempdir(), "hypebuddy_voice.mp3")

    async def _gen():
        c = edge_tts.Communicate(text, VOICE_NAME, rate=rate, pitch=pitch)
        await c.save(tmp)

    try:
        asyncio.run(_gen())
    except Exception:
        return False

    if not os.path.exists(tmp) or os.path.getsize(tmp) < 500:
        return False

    # 优先用 MCI（本进程内播放，最稳），不行再退回 PowerShell
    if _play_mp3_mci(tmp):
        return True
    return _play_mp3(tmp)


def _play_mp3_mci(path: str) -> bool:
    """
    用 Windows 自带的 MCI 接口（winmm.dll）播放 mp3 —— 全程在【本进程内】。

    【为什么要有这个】
    原来用 PowerShell 起 MediaPlayer 播放，命令返回成功但听不到声音：
    声音是在那个"子进程"的音频会话里放的，未必送到你的扬声器上。
    MCI 直接在 Python 进程里调系统音频，路径短、打扰少，是最稳的一条。
    """
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm")
    except Exception:
        return False

    # mciSendStringW：给 MCI 发命令字符串，W 版支持中文路径
    def mci(cmd: str) -> int:
        return winmm.mciSendStringW(cmd, None, 0, 0)

    alias = "hypebuddy"
    mci(f"close {alias}")                       # 清掉可能残留的上一次
    if mci(f'open "{path}" alias {alias}') != 0:
        return False
    try:
        # play + wait：等它自然放完才返回（阻塞是故意的，不然回答会叠音）
        return mci(f"play {alias} wait") == 0
    finally:
        mci(f"close {alias}")


def _play_mp3(path: str) -> bool:
    """兜底：用 PowerShell 调 Windows 的播放器放 mp3（零额外依赖）"""
    ps = (
        "Add-Type -AssemblyName presentationCore; "
        "$p = New-Object System.Windows.Media.MediaPlayer; "
        f"$p.Open([uri]'{path}'); $p.Volume = 1.0; $p.Play(); "
        # 等音频自然放完：用 NaturalDuration，取不到就按文本长度估
        "for ($i=0; $i -lt 200; $i++) { "
        "  Start-Sleep -Milliseconds 100; "
        "  if ($p.NaturalDuration.HasTimeSpan -and "
        "      $p.Position -ge $p.NaturalDuration.TimeSpan) { break } "
        "}; $p.Stop(); $p.Close()"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=180,
        )
        return r.returncode == 0
    except Exception:
        return False


# ============================================================
# 方案②：Windows 自带语音（兜底，离线可用）
# ============================================================
def _speak_sapi_wav(text: str) -> bool:
    """
    中间方案：SAPI 只负责"合成 WAV 文件"，播放交给 Python 自带的 winsound。

    【思路】把"合成"和"播放"拆开 ——
      · 合成不碰声卡，谁来做都行
      · 播放用 winsound，在 Python 进程内直接调系统音频，路径最短
    这样即便 PowerShell 那条路出不了声，也还有一条能出声的。
    """
    try:
        import winsound
    except ImportError:
        return False

    tmp_txt = os.path.join(tempfile.gettempdir(), "hypebuddy_sapi.txt")
    tmp_wav = os.path.join(tempfile.gettempdir(), "hypebuddy_sapi.wav")
    try:
        with open(tmp_txt, "w", encoding="utf-8") as f:
            f.write(text)
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 2; "
            f"$s.SetOutputToWaveFile('{tmp_wav}'); "
            f"$s.Speak([System.IO.File]::ReadAllText('{tmp_txt}', "
            "[System.Text.Encoding]::UTF8)); "
            "$s.Dispose()"
        )
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(tmp_wav):
            return False

        # SND_FILENAME 是阻塞的，等念完才返回
        winsound.PlaySound(tmp_wav, winsound.SND_FILENAME)
        return True
    except Exception:
        return False


def _speak_sapi(text: str) -> bool:
    """用 Windows SAPI 直接朗读。零依赖、离线可用。"""
    # 文本写进临时文件，避免引号转义地狱
    tmp = os.path.join(tempfile.gettempdir(), "hypebuddy_sapi.txt")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 2; "
            f"$s.Speak([System.IO.File]::ReadAllText('{tmp}', [System.Text.Encoding]::UTF8))"
        )
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=180,
        )
        return r.returncode == 0
    except Exception:
        return False


# ============================================================
# 方案③：火山引擎（收费引擎，真·四川方言男声）
# ============================================================
def _speak_volc(text: str, is_shout: bool = False) -> bool:
    """
    调火山引擎「豆包语音合成大模型 2.0」合成语音。

    接口：POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
    鉴权：X-Api-App-Id / X-Api-Access-Key / X-Api-Resource-Id
    返回：分块流式，每个块是一段 JSON，音频数据是 base64
    """
    import base64
    import json
    import uuid

    if not (VOLC_API_KEY or (VOLC_APPID and VOLC_TOKEN)):
        return False

    try:
        import requests
    except ImportError:
        return False

    instruction = VOLC_INSTRUCTION_SHOUT if is_shout else VOLC_INSTRUCTION
    payload = {
        "user": {"uid": "hypebuddy"},
        "req_params": {
            "text": text,
            "speaker": VOICE_NAME_VOLC,
            "model": VOLC_MODEL,
            # 语音指令：控制方言、情绪、语速、音调（这是 2.0 的独门功能）
            "context_texts": [instruction],
            "audio_params": {"format": "mp3", "sample_rate": 24000},
        },
    }
    headers = {
        "X-Api-Resource-Id": VOLC_RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    # 两种鉴权方式，填了哪个用哪个
    if VOLC_API_KEY:
        headers["X-Api-Key"] = VOLC_API_KEY
    else:
        headers["X-Api-App-Id"] = VOLC_APPID
        headers["X-Api-Access-Key"] = VOLC_TOKEN

    tmp = os.path.join(tempfile.gettempdir(), "hypebuddy_volc.mp3")
    try:
        # 【坑】不能走系统代理，否则连不上（跟主程序里同一套处理）
        s = requests.Session()
        s.trust_env = False
        r = s.post(VOLC_URL, headers=headers, json=payload, timeout=60)

        if r.status_code != 200:
            print(f"      ⚠️  火山引擎 HTTP {r.status_code}：{r.text[:300]}")
            return False

        # 接口是 chunked 流式返回，每个块是一段 JSON，音频数据 base64 编码。
        # 格式可能是「每行一个 JSON」也可能拼在一起，所以不按行切 ——
        # 直接用正则把所有 "data":"<base64>" 抠出来拼起来，最稳。
        audio = bytearray()
        for m in re.finditer(rb'"data"\s*:\s*"([A-Za-z0-9+/=]+)"', r.content):
            try:
                audio += base64.b64decode(m.group(1))
            except Exception:
                continue

        if len(audio) < 500:
            print(f"      ⚠️  火山引擎没返回音频：{r.content[:400]}")
            return False

        with open(tmp, "wb") as f:
            f.write(audio)

        return _play_mp3_mci(tmp)

    except Exception as e:
        print(f"      ⚠️  火山引擎调用失败：{type(e).__name__}: {str(e)[:150]}")
        return False


# ============================================================
# 对外接口：speak()
# ============================================================
def speak(text: str, is_shout: bool = False) -> None:
    """
    把文字念出来。
        text     要念的内容
        is_shout 是不是"怪叫"（是的话用更夸张的语速和音调）
    """
    if not VOICE_ENABLED or not text.strip():
        return

    spoken = clean_for_speech(text)
    if not spoken:
        return

    # ── 收费引擎：火山引擎（真·四川方言，语气用"语音指令"控制）──
    if TTS_ENGINE == "volc":
        if _speak_volc(spoken, is_shout):
            return
        # 火山挂了也别哑巴，往下走免费方案
        print("      ↪️  火山引擎没成功，自动退回免费引擎")

    # ── 免费引擎：微软 edge-tts ──
    # 【注意】川普发音层只在免费引擎上用！
    #   因为 edge 没有方言音色，只能靠"改写同音字"骗出川渝读音；
    #   火山引擎本身就说四川话，再改字反而念错了。
    if not is_shout:
        spoken = to_chongqing_accent(spoken)   # 川普化
        spoken = to_chongqing_flow(spoken)     # 拆短句
    else:
        spoken = to_chongqing_accent(spoken)   # 怪叫也川普化（"巴适得板"→"巴四得板"）

    rate = SHOUT_RATE if is_shout else VOICE_RATE
    pitch = SHOUT_PITCH if is_shout else VOICE_PITCH

    # 三级保险，谁先成功算谁的：
    #   ① edge-tts 合成 mp3 → MCI 本进程播放（音质最好）
    #   ② SAPI 合成 WAV  → winsound 本进程播放
    #   ③ SAPI 直接朗读  → PowerShell（最后的老办法）
    if _speak_edge(spoken, rate, pitch):
        return
    if _speak_sapi_wav(spoken):
        return
    _speak_sapi(spoken)


# ============================================================
# 自测：python voice.py
# ============================================================
if __name__ == "__main__":
    print("测试 1：普通说话")
    speak("兄弟，今天这波消息我给你盘一下，保真。")
    print("\n测试 2：怪叫")
    speak("WOOOOOO！！！", is_shout=True)
    print("\n测试 3：带 Markdown 的文本（会先净化再念）")
    speak("**重点来了**！详情看 https://example.com ，直接起飞 🔥")
    print("\n✅ 测试结束")
