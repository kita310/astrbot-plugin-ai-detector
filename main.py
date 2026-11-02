"""可不 AI 鉴别：引用/当前/最近消息里的文字（腾讯朱雀）和图片（Sightengine）是否 AI 生成。

触发：@可不 + “是ai吗”；引用某条消息 + @可不 + “是ai吗”；或在群里单独发“是ai吗”（可在配置里关）。
插件只负责“确认是鉴别请求 + 收集候选 + 调接口”，选哪条、怎么回复由主模型按人设完成。
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.utils.quoted_message_parser import (
    extract_quoted_message_images,
    extract_quoted_message_text,
)

from .detect import image_verdict, load_image, match_trigger, sightengine_image, text_verdict, zhuque_text

TAG = "[kafu-aidetect]"
ARMED = "_kafu_aidetect"


@register("astrbot_plugin_kafu_aidetect", "local", "可不 AI 内容鉴别（朱雀文字 + Sightengine 图片）", "0.1.0")
class KafuAIDetect(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.cfg = config or {}
        self.recent: dict[str, deque] = {}
        self.cooldown: dict[str, float] = {}
        self.data = Path(StarTools.get_data_dir("astrbot_plugin_kafu_aidetect"))
        self.data.mkdir(parents=True, exist_ok=True)
        self.quota_path = self.data / "quota.json"

    # ---------- 小工具 ----------
    def c(self, key, default):
        value = self.cfg.get(key, default)
        return default if value is None or value == "" else value

    @staticmethod
    def plain(chain) -> str:
        return "".join(p.text for p in chain or [] if isinstance(p, Plain) and p.text).strip()

    @staticmethod
    def images(chain) -> list[str]:
        out = []
        for p in chain or []:
            if isinstance(p, Image):
                ref = p.url or p.file or getattr(p, "path", "")
                if ref:
                    out.append(str(ref))
        return list(dict.fromkeys(out))

    @staticmethod
    def reply_of(event):
        return next((p for p in event.get_messages() if isinstance(p, Reply)), None)

    def addressed(self, event) -> bool:
        if event.is_private_chat():
            return True
        me = str(event.get_self_id())
        for p in event.get_messages():
            if isinstance(p, At) and str(p.qq) == me:
                return True
            if isinstance(p, Reply) and str(p.sender_id) == me:
                return True
        return False

    def group_allowed(self, event) -> bool:
        allow = [str(x) for x in self.c("allow_groups", []) or []]
        group = str(event.get_group_id() or "")
        return not allow or not group or group in allow

    def quota(self, kind: str, use: bool = False) -> bool:
        day = time.strftime("%Y-%m-%d")
        try:
            q = json.loads(self.quota_path.read_text())
        except (OSError, ValueError):
            q = {}
        if q.get("day") != day:
            q = {"day": day, "text": 0, "image": 0}
        limit = int(self.c("daily_text_limit", 200) if kind == "text" else self.c("daily_image_limit", 60))
        if q.get(kind, 0) >= limit:
            return False
        if use:
            q[kind] = q.get(kind, 0) + 1
            self.quota_path.write_text(json.dumps(q))
        return True

    # ---------- 记住最近的群消息（只在内存里，最多 30 分钟） ----------
    def remember(self, event, text: str, imgs: list[str]):
        if str(event.get_sender_id()) == str(event.get_self_id()) or not (text or imgs):
            return
        size = int(self.c("recent_size", 15))
        box = self.recent.setdefault(event.unified_msg_origin, deque(maxlen=size))
        box.append({"name": str(event.get_sender_name() or event.get_sender_id())[:20],
                    "uid": str(event.get_sender_id()), "text": text[:3000], "images": imgs[:3], "at": time.time()})

    def recent_candidates(self, origin: str) -> list[dict]:
        now = time.time()
        return [m for m in self.recent.get(origin, []) if now - m["at"] <= 1800][-6:]

    # ---------- 1. 识别鉴别请求 ----------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=300)
    async def watch(self, event: AstrMessageEvent):
        if not self.c("enable", True) or not self.group_allowed(event):
            return
        chain = event.get_messages()
        text = self.plain(chain)
        imgs = self.images(chain)
        addressed = self.addressed(event)
        hit = match_trigger(text, loose=addressed)
        if not hit:
            self.remember(event, text, imgs)
            return
        has_reply = self.reply_of(event) is not None
        candidates = self.recent_candidates(event.unified_msg_origin)
        if not addressed:
            # 没 @ 可不：只认“单独一句是ai吗”，且确实有东西可鉴别
            if not self.c("bare_trigger", True) or not hit["bare"] or event.is_private_chat():
                self.remember(event, text, imgs)
                return
            if not (has_reply or imgs or candidates):
                return
            event.is_at_or_wake_command = True  # 让这条消息正常交给可不回复
        event.set_extra(ARMED, {"content": hit["content"], "images": imgs, "recent": candidates, "calls": 0})
        logger.info(f"{TAG} armed reply={has_reply} images={len(imgs)} content_len={len(hit['content'])} recent={len(candidates)}")

    # ---------- 2. 把候选告诉主模型 ----------
    @filter.on_llm_request(priority=-500)
    async def brief(self, event: AstrMessageEvent, req):
        armed = event.get_extra(ARMED)
        if not armed:
            return
        lines = ["【AI 鉴别请求】用户想知道某条内容是不是 AI 生成的。可选目标："]
        reply = self.reply_of(event)
        if reply is not None:
            who = "可不自己" if str(reply.sender_id) == str(event.get_self_id()) else (reply.sender_nickname or "某人")
            lines.append(f"- quoted：被引用的那条（发送者：{who}）")
        if armed["content"] or armed["images"]:
            lines.append(f"- current：用户这条消息本身（文字 {len(armed['content'])} 字，图片 {len(armed['images'])} 张）")
        for i, m in enumerate(reversed(armed["recent"])):
            preview = m["text"][:30].replace("\n", " ")
            extra = f"，带图 {len(m['images'])} 张" if m["images"] else ""
            ago = int((time.time() - m["at"]) // 60)
            lines.append(f"- recent:{i}：{m['name']} {ago} 分钟前「{preview}」{extra}")
        lines.append("有引用就鉴别被引用的；没有引用时按上下文判断用户指的是哪条，拿不准就先问一句。"
                     "选定后调用 detect_ai_content 一次，然后按工具结果里的回复要求说话。不要自己凭感觉下结论。")
        req.system_prompt = (req.system_prompt or "") + "\n" + "\n".join(lines) + "\n"

    # ---------- 3. 工具 ----------
    @filter.llm_tool(name="detect_ai_content")
    async def detect_ai_content(self, event: AstrMessageEvent, target: str = "quoted", kind: str = "auto") -> str:
        """鉴别一条消息里的文字或图片是不是 AI 生成的。只在用户明确问“是不是 AI”时使用。

        Args:
            target(string): 要鉴别的消息：quoted（被引用的那条）、current（用户这条本身）或 recent:N（系统提示里列出的最近消息编号）。
            kind(string): text（只看文字）、image（只看图片）或 auto（有什么看什么）。
        """
        armed = event.get_extra(ARMED)
        if not armed:
            return "本轮用户没有发起 AI 鉴别请求，不要调用这个工具。"
        if armed["calls"] >= 1:
            return "本轮已经鉴别过一次，直接根据上次结果回复，不要重复调用。"
        armed["calls"] += 1
        uid = str(event.get_sender_id())
        wait = int(self.c("cooldown_seconds", 20)) - (time.time() - self.cooldown.get(uid, 0))
        if wait > 0:
            return f"这位用户刚鉴别过，还要等 {int(wait) + 1} 秒。请让对方稍等一下再问。"

        text, imgs, who = await self.resolve(event, armed, str(target or "").strip())
        if who == "bot":
            return "被引用的是可不自己发的消息，本来就是 AI 生成的，不需要检测，直接如实说。"
        if text is None and not imgs:
            return "找不到这条目标消息。请问用户具体是哪一条，或者让对方引用那条消息再问。"
        kind = (kind or "auto").lower()
        results = []
        if kind in ("auto", "text") and text:
            results.append(await self.check_text(text, who))
        if kind in ("auto", "image") and imgs:
            results.extend(await self.check_images(imgs, who))
        if not results:
            return "目标消息里没有可以鉴别的文字或图片（比如只有表情、语音或很短的一两个字）。如实告诉用户。"
        self.cooldown[uid] = time.time()
        return "\n".join(results) + (
            "\n回复要求：用可不平时的语气口语化地说，先给结论，再给大概概率和一句理由。"
            "必须提醒：这类检测工具会误判，短句、翻译腔、格式化文本、修过的图都容易判错，不能当证据。"
            "不要说得百分百确定，也不要借此指责或嘲讽发消息的人。"
        )

    async def resolve(self, event, armed, target):
        if target.startswith("recent"):
            try:
                idx = int(target.split(":", 1)[1])
                m = list(reversed(armed["recent"]))[idx]
            except (IndexError, ValueError):
                return None, [], ""
            return (m["text"] or None), m["images"], m["name"]
        if target == "current":
            return (armed["content"] or None), armed["images"], "提问者本人"
        reply = self.reply_of(event)
        if reply is None:
            return None, [], ""
        if str(reply.sender_id) == str(event.get_self_id()):
            return None, [], "bot"
        try:
            text = await extract_quoted_message_text(event, reply)
            imgs = await extract_quoted_message_images(event, reply)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{TAG} quoted extract failed: {type(exc).__name__}")
            text, imgs = self.plain(reply.chain), self.images(reply.chain)
        if not text and not imgs:
            text = (reply.message_str or reply.text or "").strip()
        return (text or None), list(dict.fromkeys(imgs or [])), (reply.sender_nickname or "被引用的人")

    async def check_text(self, text: str, who: str) -> str:
        # 去掉引用里常见的占位符，只留真实文字
        text = re.sub(r"\[(?:图片|Image|image|表情|动画表情|语音|视频|文件|合并转发|Forward Message)\]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        n = len(text)
        if n < int(self.c("min_text_chars", 10)):
            return f"【文字】只有 {n} 个字，太短了，检测模型判断不了，没有调用接口。"
        key = str(self.c("zhuque_api_key", "")).strip()
        if not key:
            return "【文字】还没配置朱雀 API Key，文字鉴别暂时用不了。"
        if not self.quota("text"):
            return "【文字】今天的文字鉴别次数用完了，明天再来。"
        try:
            data = await zhuque_text(key, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{TAG} zhuque failed: {exc}")
            return "【文字】检测接口暂时出错了，这次没有结果。"
        self.quota("text", use=True)
        v = text_verdict(data)
        logger.info(f"{TAG} text chars={n} label={v['label']} prob={v['ai_prob']:.3f} tokens={(data.get('makers_models_usage') or {}).get('total_tokens')}")
        short = "（不到 50 字，短文本特别容易误判）" if n < int(self.c("short_text_chars", 50)) else ""
        return (f"【文字】发送者：{who}，{n} 字{short}。腾讯朱雀结论：{v['label']}。"
                f"AI 可能性约 {v['ai_prob']:.0%}；人写 {v['human']:.0%} / AI {v['ai']:.0%} / 疑似 AI {v['suspect']:.0%}。")

    async def check_images(self, imgs: list[str], who: str) -> list[str]:
        user = str(self.c("sightengine_api_user", "")).strip()
        secret = str(self.c("sightengine_api_secret", "")).strip()
        if not (user and secret):
            return ["【图片】图片鉴别还没开通（缺 Sightengine 账号），这次只能看文字。如实告诉用户。"]
        out = []
        for i, ref in enumerate(imgs[: int(self.c("max_images", 2))], 1):
            if not self.quota("image"):
                out.append("【图片】今天的图片鉴别次数用完了。")
                break
            try:
                data = await sightengine_image(user, secret, await load_image(ref), bool(self.c("image_use_proxy", False)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{TAG} image {i} failed: {type(exc).__name__}: {str(exc)[:80]}")
                out.append(f"【图片{i}】这张没检测成功（图片拿不到或接口出错）。")
                continue
            self.quota("image", use=True)
            v = image_verdict(data)
            ops = (data.get("request") or {}).get("operations")
            logger.info(f"{TAG} image {i} label={v['label']} prob={v['ai_prob']:.3f} ops={ops}")
            gen = f"，最像 {v['generator']} 的风格" if v["generator"] else ""
            out.append(f"【图片{i}】发送者：{who}。Sightengine 结论：{v['label']}，AI 生成可能性约 {v['ai_prob']:.0%}{gen}。")
        return out
