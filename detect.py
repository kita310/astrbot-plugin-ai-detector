"""AI 生成内容鉴别：触发词、朱雀文字检测、Sightengine 图片检测、结论映射。纯函数尽量不依赖 AstrBot。"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import aiohttp

ZHUQUE_URL = "https://ai-gateway.edgeone.link/v1/providers/zhuque-text/classify"
SIGHTENGINE_URL = "https://api.sightengine.com/1.0/check.json"
DATA_ROOT = Path("/AstrBot/data").resolve()
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# “是ai吗 / 是不是ai / ai写的吗 / 鉴定一下是不是ai / 这图是ai画的吧”这类问法
_AI = r"(?:ai|AI|Ai|aI|人工智能|机器人)"
TRIGGER = re.compile(
    r"(?:(?:是不是|是否|是|有没有|有无|用|像)\s*" + _AI + r"(?:写|画|生成|做|弄|搞|p|P|改)?(?:的)?(?:吗|嘛|么|吧|啊|呢|不)?"
    r"|" + _AI + r"\s*(?:写|画|生成|做|弄|搞)的?(?:吗|嘛|么|吧|不)"
    r"|" + _AI + r"\s*(?:生成|写作|作图|画)?(?:鉴定|鉴别|检测|识别)"
    r"|(?:鉴定|鉴别|检测|识别)(?:一下)?(?:是不是|是否)?\s*" + _AI + r")"
    r"\s*[?？!！。.~～:：]*\s*$"
)
# 问可不本人是不是 AI，不是鉴别请求
ASK_BOT = re.compile(r"(?:你|您|可不|kafu|机器人你)\s*(?:到底|真的|难道)?\s*$", re.I)
TRIGGER_ONLY_FILLER = re.compile(
    r"^[\s，,。.、:：~～!！?？]*(?:帮我|帮忙|请)?(?:看看|看下|看一下)?"
    r"(?:下面|以下|下列|上面|楼上)?(?:的)?(?:这|那)?(?:个|条|张|段|句|篇|些)?"
    r"(?:图|图片|照片|文字|文本|消息|话|内容|东西|作文|文章)?[\s，,。.、:：~～!！?？]*$"
)


def clean(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", str(text or ""))
    text = re.sub(r"\[CQ:at,qq=\d+\]|\[At:\d+\]", " ", str(text or ""), flags=re.I)
    return re.sub(r"^\s*@\S+\s*", "", text).strip()


def match_trigger(text: str, loose: bool = False):
    """返回 None（不是鉴别请求）或 {"content": 要鉴别的文字, "bare": 是否只有触发词}。
    loose=True（已 @可不 或私聊）时，触发词可以单独占一行在正文前面或中间。"""
    text = clean(text)
    m = TRIGGER.search(text)
    if not m:
        return _match_line(text) if loose else None
    before = text[: m.start()]
    if ASK_BOT.search(before):
        return None
    bare = bool(TRIGGER_ONLY_FILLER.match(before))
    content = "" if bare else before.strip(" \t，,。.、:：~～")
    return {"content": content, "bare": bare}


def _match_line(text: str):
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        m = TRIGGER.search(line)
        if not m:
            continue
        before = line[: m.start()]
        if ASK_BOT.search(before):
            return None
        lead = "" if TRIGGER_ONLY_FILLER.match(before) else before.strip(" \t，,。.、:：~～")
        rest = "\n".join(l for j, l in enumerate(lines) if j != i).strip()
        content = "\n".join(x for x in (lead, rest) if x).strip()
        return {"content": content, "bare": not content}
    return None


def text_verdict(data: dict) -> dict:
    ratio = data.get("labels_ratio") or {}
    human, ai, suspect = (float(ratio.get(k, 0) or 0) for k in ("0", "1", "2"))
    prob = float(data.get("softmax_confidence") or 0)
    if ai >= 0.6:
        label = "大概率是 AI 写的"
    elif ai + suspect >= 0.6:
        label = "疑似有 AI 参与，不能确定"
    elif human >= 0.6:
        label = "更像是人写的"
    else:
        label = "判断不了，人写和 AI 的特征都有"
    return {"label": label, "ai_prob": prob, "human": human, "ai": ai, "suspect": suspect}


def image_verdict(data: dict) -> dict:
    t = data.get("type") or {}
    p = float(t.get("ai_generated") or 0)
    if p >= 0.8:
        label = "大概率是 AI 生成的"
    elif p >= 0.5:
        label = "有可能是 AI 生成的"
    elif p >= 0.2:
        label = "更像真实照片或人工作品，但不能排除 AI"
    else:
        label = "基本是真实照片或人工作品"
    gens = t.get("ai_generators") or {}
    top = max(gens.items(), key=lambda kv: kv[1], default=(None, 0))
    return {"label": label, "ai_prob": p, "generator": top[0] if top[1] >= 0.5 else None}


async def zhuque_text(key: str, text: str, timeout: int = 20) -> dict:
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.post(ZHUQUE_URL, json={"text": text[:5000], "is_merge": True},
                          headers={"Authorization": "Bearer " + key}) as r:
            body = await r.json(content_type=None)
            if r.status != 200 or body.get("status") != "success":
                raise RuntimeError("zhuque_http_%s:%s" % (r.status, str(body.get("msg") or body.get("Message") or "")[:60]))
            return body


async def sightengine_image(user: str, secret: str, data: bytes, use_proxy: bool = False, timeout: int = 30) -> dict:
    form = aiohttp.FormData()
    form.add_field("models", "genai")
    form.add_field("api_user", user)
    form.add_field("api_secret", secret)
    form.add_field("media", data, filename="image.jpg", content_type="application/octet-stream")
    async with aiohttp.ClientSession(trust_env=use_proxy, timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.post(SIGHTENGINE_URL, data=form) as r:
            body = await r.json(content_type=None)
            if r.status != 200 or body.get("status") != "success":
                err = body.get("error") or {}
                raise RuntimeError("sightengine_http_%s:%s" % (r.status, str(err.get("message") or "")[:80]))
            return body


async def load_image(ref: str) -> bytes:
    """读取图片：http(s) 链接、base64://、或 /AstrBot/data 下的本地文件。其余一律拒绝。"""
    ref = str(ref or "")
    if ref.startswith("base64://"):
        data = base64.b64decode(ref[9:])
    elif ref.startswith(("http://", "https://")):
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(ref) as r:
                if r.status != 200:
                    raise RuntimeError("image_http_%s" % r.status)
                data = await r.content.read(MAX_IMAGE_BYTES + 1)
    else:
        path = Path(ref.removeprefix("file://")).resolve()
        if not path.is_relative_to(DATA_ROOT) or not path.is_file():
            raise PermissionError("image_path_not_allowed")
        data = path.read_bytes()[: MAX_IMAGE_BYTES + 1]
    if not data:
        raise ValueError("image_empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image_too_large")
    return data
