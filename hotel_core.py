"""Business state machine. Python 3.10+, no dependency on Streamlit."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from agent_protocol import AgentConflict, TaskInterrupted, parse_delivery_command, validate_department_ticket

ROOT = Path(__file__).resolve().parent
DEPARTMENTS = {"送物": "楼层保洁", "工程报修": "工程部", "客诉紧急": "大堂经理", "续住申请": "前台接待", "票券服务": "礼宾部"}
SERVICE_INTENTS = set(DEPARTMENTS) | {"延迟退房"}
def _load_pms_rooms() -> dict[str, dict[str, str]]:
    configured = os.getenv("AEGIS_PMS_FILE")
    pms_file = Path(configured) if configured else ROOT / "data" / "pms_rooms_240.json"
    if configured:
        rooms = json.loads(pms_file.read_text(encoding="utf-8"))
        if not isinstance(rooms, dict) or not rooms or any(
            not isinstance(r, dict) or r.get("room_no") != number or not re.fullmatch(r"\d{3,4}", number)
            or "room_status" not in r or "checkout_time" not in r for number, r in rooms.items()
        ):
            raise ValueError("PMS 初始数据字段不完整或房号不合法")
        return rooms
    if pms_file.exists():
        try:
            return json.loads(pms_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "1128": {"room_no": "1128", "floor": "11", "guest_name": "张先生", "member_level": "携程黑金会员", "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕"},
        "1106": {"room_no": "1106", "floor": "11", "guest_name": "赵女士", "member_level": "携程白金会员", "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕"},
        "808": {"room_no": "808", "floor": "8", "guest_name": "李女士", "member_level": "普通会员", "checkout_time": "12:00", "room_status": "在住", "inventory_status": "紧张"},
        "902": {"room_no": "902", "floor": "9", "guest_name": "王先生", "member_level": "普通会员", "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕"},
    }


PMS_ROOMS = _load_pms_rooms()


def normalized(text: str) -> str:
    return re.sub(r"[\s，。？！?！,.：:、]", "", text.casefold())


def _format_greeting(name: str | None, reply: str) -> str:
    """Prefix a guest name exactly once, preserving model-generated greetings."""
    if not name:
        return reply
    prefixes = (f"{name}，", f"{name},", f"{name} ")
    if reply.startswith(prefixes):
        return reply
    return f"{name}，{reply}"


class HotelKnowledge:
    """Synonym/phrase retrieval with a rejection threshold, supporting multi-topic matching."""
    def __init__(self, path: Path | None = None) -> None:
        academic = os.getenv("AEGIS_SITE_PROFILE") == "academic"
        configured = os.getenv("AEGIS_KNOWLEDGE_FILE")
        self.source = path or (Path(configured) if configured else ROOT / "data" / (
            "academic_hotel_knowledge.json" if academic else "hotel_knowledge.json"))
        payload = json.loads(self.source.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or not isinstance(payload.get("fallback"), str)
                or not isinstance(payload.get("entries"), list) or not payload["entries"]):
            raise ValueError("酒店知识文件需要 fallback 与非空 entries")
        identifiers = set()
        for entry in payload["entries"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]
                    or entry["id"] in identifiers or not isinstance(entry.get("answer"), str)
                    or not isinstance(entry.get("keywords"), list) or not entry["keywords"]
                    or any(not isinstance(word, str) or not word for word in entry["keywords"])
                    or not isinstance(entry.get("specificity", 0), (int, float))
                    or not isinstance(entry.get("semantic_phrases", []), list)
                    or any(not isinstance(phrase, str) for phrase in entry.get("semantic_phrases", []))):
                raise ValueError("酒店知识条目需要唯一 id、回答和有效关键词")
            identifiers.add(entry["id"])
        self.entries = payload["entries"]
        self.fallback = payload["fallback"]

    def match_all(self, text: str) -> list[dict[str, Any]]:
        query = normalized(text)
        ranked = []
        for entry in self.entries:
            hits = [k for k in entry["keywords"] if normalized(k) in query]
            examples = entry.get("semantic_phrases", [])
            fuzzy = max((SequenceMatcher(None, query, normalized(p)).ratio() for p in examples), default=0)
            score = 1 + entry.get("specificity", 0) + max(map(len, hits)) / 100 if hits else fuzzy
            if hits or fuzzy >= 0.72:
                ranked.append((score, entry))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked:
            return []
        if ranked[0][0] < 1.0 and len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            return []

        matched = []
        seen_ids = set()
        for score, entry in ranked:
            if entry["id"] in seen_ids:
                continue
            if score >= 1.0 or score >= 0.72:
                matched.append(entry)
                seen_ids.add(entry["id"])
        return matched

    def match(self, text: str) -> dict[str, Any] | None:
        matches = self.match_all(text)
        return matches[0] if matches else None


@dataclass
class HotelState:
    messages: list[dict[str, Any]] = field(default_factory=list)
    room_no: str | None = None
    intent: str = "未识别"
    sentiment: str = "正常"
    active_agent: str = "ReceptionAgent"
    request_detail: str | None = None
    tickets: list[dict[str, Any]] = field(default_factory=list)
    last_trace: list[dict[str, str]] = field(default_factory=list)
    model_mode: str = "规则降级"
    pms_rooms: dict[str, dict[str, str]] = field(default_factory=lambda: deepcopy(PMS_ROOMS))
    guest_profile: dict[str, str] | None = None
    pending: dict[str, Any] | None = None
    late_checkout: dict[str, Any] | None = None
    checkout_requests: list[dict[str, Any]] = field(default_factory=list)
    voucher_ledger: list[dict[str, Any]] = field(default_factory=list)
    current_date: str = "2026-10-01"
    borrowed_items: list[dict[str, Any]] = field(default_factory=list)
    audit_logs: list[dict[str, Any]] = field(default_factory=list)
    rolling_summary: str | None = None
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_conflicts: list[dict[str, Any]] = field(default_factory=list)
    authorized_room: str | None = None
    stay_id: str | None = None
    session_id: str | None = None


def _load_env_or_config() -> None:
    config_file = ROOT / "config.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            if "api_key" in cfg and not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = str(cfg["api_key"]).strip()
                os.environ["SILICONFLOW_API_KEY"] = str(cfg["api_key"]).strip()
            if "base_url" in cfg and not os.getenv("OPENAI_BASE_URL"):
                os.environ["OPENAI_BASE_URL"] = str(cfg["base_url"]).strip()
                os.environ["SILICONFLOW_BASE_URL"] = str(cfg["base_url"]).strip()
            if "model" in cfg and not os.getenv("OPENAI_MODEL"):
                os.environ["OPENAI_MODEL"] = str(cfg["model"]).strip()
        except Exception:
            pass

    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        except Exception:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and not os.getenv(k):
                        os.environ[k] = v


_load_env_or_config()


class _HttpxOpenAICompatClient:
    """Small OpenAI-compatible transport used when the optional SDK is unavailable."""

    def __init__(self, *, api_key: str, base_url: str, timeout: float = 12.0) -> None:
        import httpx

        self._httpx = httpx
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.models = SimpleNamespace(list=self._list_models)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_completion))

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._httpx.get if method == "GET" else self._httpx.post
        kwargs: dict[str, Any] = {"headers": self._headers, "timeout": self._timeout}
        if payload is not None:
            kwargs["json"] = payload
        response = request(f"{self._base_url}{path}", **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("model_response_not_object")
        return data

    def _list_models(self) -> SimpleNamespace:
        payload = self._request("GET", "/models")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            rows = []
        return SimpleNamespace(data=[SimpleNamespace(id=str(row.get("id", ""))) for row in rows if isinstance(row, dict)])

    def _create_completion(self, **kwargs: Any) -> SimpleNamespace:
        payload = {key: value for key, value in kwargs.items() if value is not None}
        response = self._request("POST", "/chat/completions", payload)
        choices: list[SimpleNamespace] = []
        for raw_choice in response.get("choices", []) if isinstance(response.get("choices", []), list) else []:
            if not isinstance(raw_choice, dict):
                continue
            raw_message = raw_choice.get("message", {})
            if not isinstance(raw_message, dict):
                raw_message = {}
            raw_calls = raw_message.get("tool_calls", [])
            calls: list[SimpleNamespace] = []
            for raw_call in raw_calls if isinstance(raw_calls, list) else []:
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function", {})
                if not isinstance(function, dict):
                    function = {}
                calls.append(SimpleNamespace(function=SimpleNamespace(
                    name=function.get("name"), arguments=function.get("arguments", "")
                )))
            choices.append(SimpleNamespace(message=SimpleNamespace(
                content=raw_message.get("content"), tool_calls=calls
            )))
        return SimpleNamespace(choices=choices)


class OpenAICompatibleClient:
    def __init__(self, *, disabled: bool = False) -> None:
        self.client: Any = None
        self.initialization_error: str | None = None
        self.last_probe: dict[str, Any] | None = None
        self.transport: str | None = None
        self.disabled = disabled or os.getenv("AEGIS_FORCE_RULES", "").lower() in {"1", "true", "yes"}
        if self.disabled:
            self.model = "offline_rules"
            self.transport = "offline"
            return
        _load_env_or_config()
        key = os.getenv("OPENAI_API_KEY") or os.getenv("SILICONFLOW_API_KEY")
        base = os.getenv("OPENAI_BASE_URL") or os.getenv("SILICONFLOW_BASE_URL") or os.getenv("SILICONFLOW_API_URL") or "https://api.siliconflow.cn/v1"
        self.model = os.getenv("OPENAI_MODEL") or ("Qwen/Qwen2.5-7B-Instruct" if "siliconflow" in (base or "") else "Qwen/Qwen2.5-7B-Instruct")
        if base:
            base = base.rstrip("/").removesuffix("/chat/completions")
        if key:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=key, base_url=base, timeout=12.0, max_retries=0)
                self.transport = "openai"
            except ImportError:
                try:
                    self.client = _HttpxOpenAICompatClient(api_key=key, base_url=base, timeout=12.0)
                    self.transport = "httpx"
                except ImportError:
                    self.initialization_error = "openai_sdk_missing"
            except ValueError:
                self.initialization_error = "invalid_client_config"
            except Exception as error:
                self.initialization_error = self._error_code(error)
        else:
            self.initialization_error = "api_key_missing"

    @staticmethod
    def _error_code(error: Exception) -> str:
        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return f"http_{status_code}"
        if isinstance(error, (TimeoutError,)) or error.__class__.__name__.lower().endswith("timeout"):
            return "timeout"
        return error.__class__.__name__.lower()

    def probe(self) -> dict[str, Any]:
        """Check credentials and selected model without sending a chat completion."""
        started = time.perf_counter()

        def finish(ok: bool, code: str, *, model_listed: bool | None = None) -> dict[str, Any]:
            result = {
                "ok": ok,
                "code": code,
                "model": self.model,
                "model_listed": model_listed,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            self.last_probe = result
            return result

        if self.disabled:
            return finish(False, "offline_rules")
        if self.initialization_error:
            return finish(False, self.initialization_error)
        if not self.client:
            return finish(False, "client_unavailable")
        try:
            response = self.client.models.list()
            rows = getattr(response, "data", []) or []
            model_ids = {str(getattr(row, "id", "")) for row in rows}
            if not model_ids:
                return finish(False, "model_list_empty", model_listed=False)
            if self.model not in model_ids:
                return finish(False, "model_not_listed", model_listed=False)
            return finish(True, "ok", model_listed=True)
        except Exception as error:
            return finish(False, self._error_code(error))

    def json_extract(self, system: str, user: str) -> dict[str, Any] | None:
        if self.disabled or not self.client:
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"}, temperature=0,
            )
            result = json.loads(response.choices[0].message.content or "{}")
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 100) -> str | None:
        """Return a short text completion for tightly scoped persona replies."""
        if self.disabled or not self.client:
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.4,
            )
            content = response.choices[0].message.content if response.choices else None
            return content.strip() if content else None
        except Exception:
            return None

    def dispatch_tool_call(self, prompt: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.client:
            return None
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": "只调用 create_hotel_ticket，遵循给定工单字段，不得改变房号、类别和部门。"}, {"role": "user", "content": prompt}],
                tools=tools, tool_choice={"type": "function", "function": {"name": "create_hotel_ticket"}}, temperature=0,
            )
            calls = response.choices[0].message.tool_calls or []
            if len(calls) == 1 and calls[0].function.name == "create_hotel_ticket":
                result = json.loads(calls[0].function.arguments)
                return result if isinstance(result, dict) else None
        except Exception:
            pass
        return None


def _room_no(text: str) -> str | None:
    corr_pattern = r"(?:不对|记错|搞错|其实是|更正|纠正|换成|改为|变成)\D*?(\d{3,4})(?!\d)"
    corrections = list(re.finditer(corr_pattern, text))
    if corrections:
        return str(int(corrections[-1].group(1)))

    correction = re.search(r"(?:修改|实际上是|不是\d{3,4}\D*?是|住|房号|房间)\D*?(\d{3,4})(?!\d)", text)
    if correction:
        val = correction.group(1)
        end_pos = correction.end(1)
        if not (end_pos < len(text) and text[end_pos] in (":", "：", "点", "时")):
            return str(int(val))

    patterns = [
        r"(?:房间号?|房号|room)\s*[:：#]?\s*(\d{3,4})(?!\d)",
        r"(?<!\d)(\d{3,4})\s*(?:号?房|室)",
        r"(?<!\d)(\d{3,4})(?!\d)",
    ]
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            val = match.group(1)
            end_pos = match.end(1)
            if end_pos < len(text) and text[end_pos] in (":", "：", "点", "时"):
                continue
            matches.append(val)
    if matches:
        return str(int(matches[-1]))
    return None


def requested_checkout_time(text: str) -> str | None:
    clock = re.search(r"(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)", text)
    cn = re.search(r"(\d{1,2}|十二|十三|十四|十五|十六|十一|十|一|二|两|三|四|五|六|七|八|九)\s*(?:点|时)(半)?", text)
    if clock:
        hour, minute = map(int, clock.groups())
    elif cn:
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16}
        hour = int(cn[1]) if cn[1].isdigit() else digits[cn[1]]
        minute = 30 if cn[2] else 0
    else:
        return None
    if 1 <= hour <= 6 and "上午" not in text and "凌晨" not in text:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def requested_extend_nights(text: str) -> int:
    m = re.search(r"(\d+|一|两|二|三|四|五|半)?\s*(?:天|晚|日|夜)", text)
    if m and m.group(1):
        digits = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}
        val = m.group(1)
        return int(val) if val.isdigit() else digits.get(val, 1)
    return 1


def _academic_voucher_request(text: str) -> bool:
    return os.getenv("AEGIS_SITE_PROFILE") == "academic" and any(
        word in text for word in ("早餐券", "早饭券", "餐券", "体验券")
    )


def _rules_extract(text: str, knowledge: HotelKnowledge) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", text)
    resolution_phrases = (
        "修好了", "维修好了", "师傅修好了", "搞定了", "弄好了", "解决了",
        "已经好了", "送到了", "收到了", "拿到了", "处理好了", "弄妥了", "不用修了",
    )
    unresolved_phrases = (
        "没修好", "还没修好", "没弄好", "还没送到", "没收到", "没搞定", "没解决",
    )
    is_resolution = any(phrase in compact for phrase in resolution_phrases) and not any(
        phrase in compact for phrase in unresolved_phrases
    )
    angry_terms = (
        "投诉", "客诉", "生气", "愤怒", "太差", "差评", "赔偿", "无法忍受", "一星", "垃圾", "气死",
        "臭死了", "怎么做卫生", "卫生怎么", "找经理", "大堂经理", "经理给我解释", "恶心", "脏死了",
    )
    strong_complaint_terms = (
        "投诉", "客诉", "太差", "差评", "赔偿", "无法忍受", "一星", "垃圾", "气死",
        "臭死了", "怎么做卫生", "卫生怎么", "找经理", "大堂经理", "经理给我解释", "恶心", "脏死了",
    )
    angry = any(word in compact for word in angry_terms)
    strong_complaint = any(word in compact for word in strong_complaint_terms)
    negate_checkout = any(w in compact for w in ("没说退房", "不是退房", "不要退房", "不用退房", "不退房", "我不退", "不打算退"))
    extend_stay = any(w in compact for w in ("续住", "续订", "续房", "多住", "再住", "延住", "延期住", "加订一天", "续一天", "续订一天", "续住一天", "想续"))
    request_time = requested_checkout_time(text)
    late = (not negate_checkout) and (not extend_stay) and (
        any(w in compact for w in ("延迟退房", "延时退房", "晚点退房", "晚退房", "推迟退房")) or (request_time is not None and any(w in text for w in ("退房", "离店")))
    )
    checkout_policy_question = any(w in text for w in ("退房时间", "几点退房", "何时退房", "退房政策"))
    explicit_delay = any(w in compact for w in ("延迟", "延时", "推迟", "晚点", "晚退"))
    if checkout_policy_question and not explicit_delay:
        late = False
    broken = any(w in text for w in ("坏", "漏水", "不制冷", "不亮", "没热水", "没有热水", "故障", "报修", "维修", "停电", "断电", "水管爆", "门锁"))
    delivery_command = parse_delivery_command(text)
    delivery = (delivery_command.item is not None or any(w in text for w in (
        "浴巾", "毛巾", "矿泉水", "瓶水", "送水", "吹风机", "枕头", "被子", "牙刷",
        "早餐券", "早饭券", "餐券", "房卡", "拖鞋", "洗发水", "沐浴露", "梳子", "剃须刀", "雨伞", "充电器"
    ))) and any(w in text for w in ("送", "要", "需要", "借", "来", "拿", "发", "补", "加", "给", "换", "改"))
    delivery = delivery and delivery_command.action != "cancel"
    voucher = any(w in text for w in ("早餐券", "早饭券", "餐券", "体验券"))
    voucher_delivery = voucher and (
        any(w in compact for w in ("送到", "送来", "发给", "给我发", "给房间发"))
        or ("给" in compact and "发" in compact)
    )
    explicit_complaint = strong_complaint

    faq_matches = knowledge.match_all(text)
    # If requesting late checkout, stay extension, or explicitly denying checkout, avoid giving the default 12:00 checkout FAQ
    if (late or negate_checkout or extend_stay) and not any(w in text for w in ("退房时间", "几点退房", "何时退房", "几点离店")):
        faq_matches = [e for e in faq_matches if e["id"] != "checkout"]

    # A completion acknowledgement is a state transition, never a new service request.
    if is_resolution:
        return {
            "room_no": _room_no(text),
            "intent": "服务完结",
            "service_intent": None,
            "faq_entry": None,
            "faq_entries": [],
            "sentiment": "愤怒" if angry else "正常",
            "item_detail": None,
            "requested_time": request_time,
            "nights": 1,
            "is_resolution": True,
            "is_strong_complaint": strong_complaint,
        }

    # Determine service intent if any
    service_intent = None
    if explicit_complaint or (angry and not broken and not delivery and not late and not extend_stay):
        service_intent = "客诉紧急"
    elif extend_stay:
        service_intent = "续住申请"
    elif late:
        service_intent = "延迟退房"
    elif broken:
        service_intent = "工程报修"
    elif voucher and (not voucher_delivery or _academic_voucher_request(text)):
        service_intent = "票券服务"
    elif delivery:
        service_intent = "送物"

    # Primary intent classification
    if service_intent:
        intent = service_intent
    elif faq_matches:
        intent = "酒店咨询"
    else:
        intent = "其他"

    return {
        "room_no": _room_no(text),
        "intent": intent,
        "service_intent": service_intent,
        "faq_entry": faq_matches[0] if faq_matches else None,
        "faq_entries": faq_matches,
        "sentiment": "愤怒" if angry else "正常",
        "item_detail": text if service_intent in SERVICE_INTENTS else None,
        "requested_time": request_time,
        "nights": requested_extend_nights(text) if extend_stay else 1,
        "is_resolution": False,
        "is_strong_complaint": strong_complaint,
    }


class ReceptionAgent:
    def __init__(
        self,
        model: OpenAICompatibleClient,
        knowledge: HotelKnowledge,
        pms_rooms: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.model, self.knowledge = model, knowledge
        self.pms_rooms = pms_rooms if pms_rooms is not None else PMS_ROOMS

    def understand(self, text: str) -> tuple[dict[str, Any], str]:
        result = _rules_extract(text, self.knowledge)
        # Completion acknowledgements are deterministic workflow signals. They
        # must never be reinterpreted as a new dispatch request by the model.
        if result.get("is_resolution"):
            return result, "规则护栏 · 服务完结"
        prompt = (
            "提取酒店住客意图 JSON：intent(酒店咨询/送物/工程报修/客诉紧急/延迟退房/续住申请/票券服务/闲聊/其他)、"
            "sentiment(正常/愤怒)、room_no(住客的有效房间号，纯数字字符串或null)。"
            "不能替客人猜房号、会员身份或批准权益。"
            "如果住客存在口误纠错、换房或提到多个房号（如‘我是1102……不对我记错了，其实我是1106’），"
            "请理解上下文逻辑，提取住客最终明确认定的当前有效房间号（仅提取纯数字，若未确定则为null）。"
            "询问酒店设施政策、退房时间/退房政策（如‘退房时间是几点’、‘几点退房’）一律属于‘酒店咨询’；"
            "只有明确提出推迟/延后退房（如‘想延迟退房’、‘延迟到两点’）才属于‘延迟退房’。"
            "想多住几天/延长入住属于‘续住申请’；索要早餐券/优惠券属于‘票券服务’；"
            "天文学、哲学、诗词、天气等泛话题属于‘闲聊’。"
            "设备故障、水管漏水、电器损坏（如‘空调坏了’、‘花洒漏水’）主意图属于【工程报修】；若语气生气则 sentiment 标为‘愤怒’，但 intent 仍然是【工程报修】；"
            "只有住客表达严重恶劣指责、卫生极差（如‘臭死了’、‘怎么做卫生的’）或明确要求管理层介入（如‘让大堂经理给我解释’、‘找经理’）主意图才属于【客诉紧急】；"
            "确认服务已完成（如‘修好了’、‘送到了’、‘搞定了’）不属于工程报修或送物。"
        )
        try:
            from harness.self_evolution import DynamicExemplarCurator
            exemplar_block = DynamicExemplarCurator().retrieve_prompt_block(text, top_k=2)
            if exemplar_block:
                prompt += "\n" + exemplar_block
        except Exception:
            pass
        cloud = self.model.json_extract(prompt, text)
        if not cloud:
            return result, "规则降级"
        cloud_room = cloud.get("room_no")
        if cloud_room is not None:
            candidate_room = str(cloud_room)
            if re.fullmatch(r"\d{3,4}", candidate_room) and candidate_room in self.pms_rooms:
                result["room_no"] = candidate_room
        cloud_intent = cloud.get("intent")
        if isinstance(cloud_intent, list):
            cloud_intent = cloud_intent[0] if cloud_intent else None
        if cloud_intent is not None:
            cloud_intent = str(cloud_intent).strip()

        cloud_sentiment = cloud.get("sentiment")
        if isinstance(cloud_sentiment, list):
            cloud_sentiment = cloud_sentiment[0] if cloud_sentiment else None
        if cloud_sentiment is not None:
            cloud_sentiment = str(cloud_sentiment).strip()

        checkout_faq_terms = ("退房时间", "几点退房", "何时退房", "退房政策")
        delay_terms = ("延迟", "延时", "推迟", "晚点", "晚退")
        if cloud_intent == "延迟退房":
            # H13: a policy question cannot become a write operation because
            # the cloud classifier guessed late checkout.
            is_checkout_policy = any(w in text for w in checkout_faq_terms)
            has_delay_term = any(w in text for w in delay_terms)
            if (is_checkout_policy and not has_delay_term) or not (result.get("requested_time") is not None or has_delay_term):
                result["intent"] = "酒店咨询"
                result["service_intent"] = None
                cloud_intent = None

        if result.get("intent") in {"工程报修", "送物"} and not result.get("is_strong_complaint"):
            if cloud_intent == "客诉紧急":
                cloud_intent = result["intent"]

        if cloud_intent in SERVICE_INTENTS | {"酒店咨询", "闲聊", "其他"}:
            if result["intent"] in {"其他", "酒店咨询"} and cloud_intent in SERVICE_INTENTS:
                result["intent"] = cloud_intent
                result["service_intent"] = cloud_intent
            elif result["intent"] == "其他":
                result["intent"] = cloud_intent

        if result.get("is_strong_complaint") or (cloud_intent == "客诉紧急" and not (result.get("intent") in {"工程报修", "送物"} and not result.get("is_strong_complaint"))):
            result["intent"] = "客诉紧急"
            result["service_intent"] = "客诉紧急"
            result["sentiment"] = "愤怒"
        elif cloud_sentiment == "愤怒":
            result["sentiment"] = "愤怒"
            if result.get("intent") not in {"工程报修", "送物", "延迟退房"}:
                result["intent"] = "客诉紧急"
                result["service_intent"] = "客诉紧急"
        result["item_detail"] = text if result["intent"] in SERVICE_INTENTS else None
        return result, "云模型 + 规则校验"

    def generate_chitchat_reply(self, text: str, guest_name: str | None = None) -> str:
        fallback = "我可以帮您办理酒店客房服务或解答设施政策，请告诉我您的具体需求。"
        if getattr(self.model, "disabled", False) or not self.model.client:
            return fallback
        prompt = (
            "你是 AegisStay 五星级智慧酒店的贴身管家。"
            f"住客提了一个范围外话题：‘{text}’。请按规则回答："
            "1. 用 1 句话（30字内）简明准确、有趣地回答；"
            "2. 用 1 句话自然转回酒店客房关怀（必须明确包含‘酒店’或‘客房’，如询问客房温度或是否需要酒店服务）；"
            "3. 总字数严格控制在 70 字以内，优雅体贴，禁止啰嗦。"
        )
        raw = self.model.chat([{"role": "user", "content": prompt}], max_tokens=100)
        if not raw:
            return fallback
        return raw.strip()[:70]

    def reply_faq(self, text: str) -> str:
        entry = self.knowledge.match(text)
        return entry["answer"] if entry else self.knowledge.fallback


def create_hotel_ticket(room_no: str, category: str, item_detail: str, priority: str, assigned_dept: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{3,4}", room_no):
        raise ValueError("房间号必须为 3–4 位数字")
    if category not in DEPARTMENTS or assigned_dept != DEPARTMENTS[category] or priority not in {"高", "中", "低"}:
        raise ValueError("工单路由或优先级不合法")
    if not item_detail.strip():
        raise ValueError("工单需要具体事项")
    risk_level = "红色" if (category == "客诉紧急" or priority == "高") else "普通"
    return {"ticket_id": f"HT-{uuid.uuid4().hex[:10].upper()}", "room_no": room_no, "category": category, "item_detail": item_detail, "priority": priority, "assigned_dept": assigned_dept, "status": "处理中", "created_at": datetime.now().isoformat(timespec="seconds"), "risk_level": risk_level}


def apply_late_checkout(room_no: str, requested_time: str, profile: dict[str, str] | None) -> dict[str, Any]:
    """Approve only confirmed occupied Black Gold rooms, through 14:00 inclusive."""
    if not re.fullmatch(r"\d{3,4}", room_no) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", requested_time):
        raise ValueError("房号或退房时间格式不正确")
    if requested_time <= "12:00":
        raise ValueError("延迟退房时间应晚于 12:00")
    reason = "会员等级不满足自动审批，需前台核实费用"
    if os.getenv("AEGIS_SITE_PROFILE") == "academic":
        reason = "学术接待延退需由前台核对接待订单与会务排期"
    approved = False
    if not profile or profile.get("room_no") != room_no or profile.get("room_status") != "在住":
        reason = "未找到有效在住档案，请前台核实"
    elif requested_time > "14:00":
        reason = "超出 14:00 免费权益时限，需前台审核费用"
    elif profile.get("inventory_status") != "充裕":
        reason = "房态紧张，需前台协调"
    elif profile.get("next_booking") and profile["next_booking"].get("expected_checkin", "15:00") < "15:00" and requested_time >= "14:00":
        reason = f"次日新客预计 {profile['next_booking']['expected_checkin']} 到店，为预留消杀保洁排期需前台人工核准"
    elif profile.get("member_level") == "携程黑金会员":
        if os.getenv("AEGIS_SITE_PROFILE") == "academic":
            return {"room_no": room_no, "requested_time": requested_time, "status": "需前台审核",
                    "checkout_time": profile["checkout_time"], "reason": "学术接待按订单与会务排期核验，不自动套用商业会员权益", "updated_pms": False}
        profile["checkout_time"] = max(profile["checkout_time"], requested_time)
        approved, reason = True, "黑金会员且房态充裕，权益内免费延迟退房"
    return {"room_no": room_no, "requested_time": requested_time, "status": "已批准" if approved else "需前台审核", "checkout_time": profile["checkout_time"] if profile else None, "reason": reason, "updated_pms": approved}


def run_pms_night_audit(pms_rooms: dict[str, dict[str, Any]], current_date: str) -> tuple[str, list[dict[str, Any]]]:
    """Execute hotel night audit, advancing date and rolling over occupied/overdue rooms."""
    from datetime import date, timedelta
    try:
        cur = date.fromisoformat(current_date)
    except Exception:
        cur = date(2026, 10, 1)
    next_date = (cur + timedelta(days=1)).isoformat()

    actions = []
    for r_no, room in pms_rooms.items():
        status = room.get("room_status", "在住")
        if status == "在住":
            chk_date = room.get("checkout_date", current_date)
            if chk_date <= current_date:
                room["room_status"] = "超时待结"
                actions.append({"room_no": r_no, "action": "标记为超时待结", "detail": f"退房日 {chk_date} 已过，需前台核对结账"})
            room["daily_voucher_used"] = 0
        elif status == "空房清洁中":
            room["room_status"] = "空房可用"
            actions.append({"room_no": r_no, "action": "夜审保洁归档", "detail": "保洁夜审归档，房间可重新安排入住"})

    return next_date, actions


def _requested_quantity(detail: str) -> int:
    return parse_delivery_command(detail).quantity or 1


class HousekeepingAgent:
    """专业处理送物，支持线程安全原子库存扣减、借还类物资登记与动态派送 ETA。"""

    name = "HousekeepingAgent"
    display_name = "客房保洁管家"
    BORROWABLE_ITEMS = {"雨伞", "充电器", "吹风机"}

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.inventory = {
            "浴巾": 24, "毛巾": 24, "矿泉水": 36, "吹风机": 6, "枕头": 12,
            "被子": 8, "牙刷": 30, "早餐券": 40, "拖鞋": 24,
            "洗发水": 18, "沐浴露": 18, "梳子": 20, "剃须刀": 12,
            "雨伞": 10, "充电器": 8,
        }

    def _item(self, detail: str) -> str:
        item = parse_delivery_command(detail).item
        return item if item in self.inventory else "综合物料"

    def dispatch(self, room_no: str, detail: str, state: HotelState) -> dict[str, Any]:
        if _academic_voucher_request(detail):
            raise AgentConflict("学术接待餐券需前台核验，不能按普通送物派单")
        with self.lock:
            active = sum(
                ticket.get("category") == "送物" and ticket.get("status") == "处理中"
                for ticket in state.tickets
            )
            eta_minutes = 5 if active <= 1 else min(10, 8 + active - 2)
            item = self._item(detail)
            quantity = _requested_quantity(detail)
            remaining = None
            reserved = 0
            inventory_status = "无需库存扣减"
            is_borrow = item in self.BORROWABLE_ITEMS
            if item != "综合物料":
                before = self.inventory[item]
                reserved = quantity if before >= quantity else 0
                remaining = before - reserved
                self.inventory[item] = remaining
                inventory_status = "库存充足" if before >= quantity else "库存不足，已转前台协调"
            ticket = create_hotel_ticket(room_no, "送物", detail, "低", DEPARTMENTS["送物"])
            ticket.update(
                agent=self.name,
                department_agent=self.name,
                eta_minutes=eta_minutes,
                inventory_item=item,
                inventory_remaining=remaining,
                inventory_status=inventory_status,
                is_borrowable=is_borrow,
                quantity=quantity,
                reserved_quantity=reserved,
                delivery_status="待派送",
            )
            if is_borrow and reserved:
                state.borrowed_items.append({
                    "ticket_id": ticket["ticket_id"],
                    "room_no": room_no,
                    "item": item,
                    "quantity": quantity,
                    "status": "借用在房",
                    "borrowed_at": datetime.now().isoformat(timespec="seconds"),
                })
            return ticket

    def revise(self, ticket: dict[str, Any], command: Any, quantity: int | None = None) -> str:
        """Amend a queued demo delivery; dispatched effects require staff acknowledgement."""
        if command.action != "cancel" and _academic_voucher_request(command.item or ""):
            return "早餐券需前台核验，暂未修改原送物工单，请联系前台办理餐券申请。"
        with self.lock:
            desired_quantity = command.quantity if command.quantity is not None else (quantity or ticket.get("quantity", 1))
            if command.action != "cancel" and desired_quantity <= 0:
                return "物品数量需要大于零，暂未修改原工单。"
            if ticket.get("delivery_status") in {"配送中", "已送达"} or ticket.get("status") == "已结单":
                ticket["change_request"] = {
                    "change_id": "CHANGE-" + uuid.uuid4().hex,
                    "action": command.action, "item": command.item, "quantity": desired_quantity,
                    "status": "待部门确认",
                }
                return "已收到修改请求，原送物已派出或送达，正在等待保洁确认撤回或更换；目前尚未确认取消成功。"
            old_item = ticket.get("inventory_item")
            old_reserved = ticket.get("reserved_quantity", 0)
            if command.action != "cancel" and command.item not in self.inventory:
                return "请确认需要更换成哪种物品，暂未修改原工单。"
            if command.action != "cancel":
                available = self.inventory[command.item] + (old_reserved if old_item == command.item else 0)
                if available < desired_quantity:
                    return "更换物品的库存不足，原工单暂未修改，请联系前台协调。"
            if old_item in self.inventory:
                self.inventory[old_item] += old_reserved
            ticket["reserved_quantity"] = 0
            if command.action == "cancel":
                ticket.update(status="已取消", delivery_status="已取消", cancelled_at=datetime.now().isoformat(timespec="seconds"))
                return "已取消尚未派送的送物工单，并释放预留库存。"
            self.inventory[command.item] -= desired_quantity
            ticket.update(
                inventory_item=command.item, quantity=desired_quantity, reserved_quantity=desired_quantity,
                inventory_remaining=self.inventory[command.item], inventory_status="库存充足",
                item_detail=f"送 {desired_quantity} 件{command.item}",
                is_borrowable=command.item in self.BORROWABLE_ITEMS,
                revision=ticket.get("revision", 1) + 1,
            )
            return f"已将原送物工单改为 {desired_quantity} 件{command.item}，原物品不再派送。"


class EngineeringAgent:
    """诊断工程故障，并把重大险情提升为红色高危工单。"""

    name = "EngineeringAgent"
    display_name = "工程安防工程师"
    MAJOR_INCIDENTS = ("漏水", "水管爆", "断电", "停电", "门锁", "打不开门", "被困", "受伤", "着火", "火警")

    def diagnose(self, room_no: str, detail: str, state: HotelState, sentiment: str = "正常") -> dict[str, Any]:
        major = any(word in detail for word in self.MAJOR_INCIDENTS)
        priority = "高" if major or sentiment == "愤怒" else "中"
        ticket = create_hotel_ticket(room_no, "工程报修", detail, priority, DEPARTMENTS["工程报修"])
        ticket.update(
            agent=self.name,
            department_agent=self.name,
            incident_level="重大险情" if major else "一般故障",
            risk_level="红色" if major else ticket["risk_level"],
            safety_guidance=(
                "请立即远离漏水/带电区域，关闭房内电源并等待工程部确认安全。"
                if major else "请先停止使用故障设备，工程部将按排查单逐项检查。"
            ),
        )
        return ticket


class ConciergeAgent:
    """处理续住审批与早餐券等礼宾票务动作。"""

    name = "ConciergeAgent"
    display_name = "礼宾接待管家"

    def apply_stay_extension(self, room_no: str, nights: int, profile: dict[str, str] | None, state: HotelState) -> dict[str, Any]:
        if not profile or profile.get("room_no") != room_no or profile.get("room_status") != "在住":
            raise ValueError("续住申请需要有效的在住档案")
        inventory = profile.get("inventory_status", "充裕")
        detail = f"申请续住 {max(1, nights)} 晚；房态{'紧张需协调' if inventory == '紧张' else '充裕'}"
        ticket = create_hotel_ticket(room_no, "续住申请", detail, "高" if inventory == "紧张" else "中", DEPARTMENTS["续住申请"])
        ticket.update(agent=self.name, department_agent=self.name, nights=max(1, nights), inventory_status=inventory)
        return ticket

    def issue_breakfast_vouchers(self, room_no: str, quantity: int, profile: dict[str, str] | None, state: HotelState) -> dict[str, Any]:
        quantity = max(1, quantity)
        if os.getenv("AEGIS_SITE_PROFILE") == "academic":
            record = {"voucher_id": f"BV-{uuid.uuid4().hex[:8].upper()}", "room_no": room_no, "quantity": quantity,
                      "status": "需前台核验", "agent": self.name,
                      "reason": "学术接待是否含早以核验的接待订单为准，实际餐券由前台办理，不自动发放"}
            state.voucher_ledger.append(record)
            return record
        if not profile or profile.get("room_no") != room_no or profile.get("room_status") != "在住":
            record = {"voucher_id": f"BV-{uuid.uuid4().hex[:8].upper()}", "room_no": room_no, "quantity": quantity, "status": "需前台核验", "agent": self.name, "reason": "未找到有效在住档案"}
            state.voucher_ledger.append(record)
            return record
        issued = sum(v.get("quantity", 0) for v in state.voucher_ledger if v.get("room_no") == room_no and v.get("status") == "已发放")
        status = "已发放" if issued + quantity <= 4 else "需前台核验"
        record = {
            "voucher_id": f"BV-{uuid.uuid4().hex[:8].upper()}",
            "room_no": room_no,
            "quantity": quantity,
            "status": status,
            "agent": self.name,
            "quota_remaining": max(0, 4 - issued - quantity),
            "reason": "每日早餐券配额已核销" if status == "已发放" else "超过每日早餐券配额，需前台核验",
        }
        state.voucher_ledger.append(record)
        return record


class ManagerEscalationAgent:
    """处理高危客诉，给出情绪降温和人工补偿建议。"""

    name = "ManagerEscalationAgent"
    display_name = "大堂经理危机响应"

    def escalate(self, room_no: str, detail: str, state: HotelState, sentiment: str = "愤怒") -> dict[str, Any]:
        ticket = create_hotel_ticket(room_no, "客诉紧急", detail, "高", DEPARTMENTS["客诉紧急"])
        ticket.update(
            agent=self.name,
            department_agent=self.name,
            risk_level="红色",
            empathy_response="非常抱歉这次入住让您感到不适，我已为您升级大堂经理优先处理。",
            compensation_suggestion="建议大堂经理根据核查结果赠送果盘或协调相应补偿。",
            manager_action="置顶督办并在 10 分钟内回访",
        )
        return ticket


class DispatchAgent:
    """Backward-compatible tool facade; business routing lives in vertical agents."""
    TOOL_SCHEMA = [{"type": "function", "function": {"name": "create_hotel_ticket", "description": "创建本地酒店内部工单", "parameters": {"type": "object", "additionalProperties": False, "required": ["room_no", "category", "item_detail", "priority", "assigned_dept"], "properties": {"room_no": {"type": "string"}, "category": {"type": "string", "enum": list(DEPARTMENTS)}, "item_detail": {"type": "string"}, "priority": {"type": "string", "enum": ["高", "中", "低"]}, "assigned_dept": {"type": "string", "enum": list(DEPARTMENTS.values())}}}}}]

    def __init__(self, model: OpenAICompatibleClient) -> None:
        self.model = model

    def dispatch(self, room_no: str, intent: str, detail: str, sentiment: str) -> tuple[dict[str, Any], str]:
        category = intent if intent in set(DEPARTMENTS) else "客诉紧急" if sentiment == "愤怒" else intent
        major_incident = any(w in detail for w in ("漏水", "水管爆", "没热水", "停电", "断电", "打不开门", "门锁", "被困", "受伤", "着火", "火警", "危险"))
        priority = "高" if category == "客诉紧急" or (category == "工程报修" and (sentiment == "愤怒" or major_incident)) else "中" if category in {"工程报修", "续住申请"} else "低"
        args = {"room_no": room_no, "category": category, "item_detail": detail, "priority": priority, "assigned_dept": DEPARTMENTS[category]}
        cloud = self.model.dispatch_tool_call(json.dumps(args, ensure_ascii=False), self.TOOL_SCHEMA)
        # Verify valid tool invocation
        is_valid_tool_call = bool(
            isinstance(cloud, dict)
            and str(cloud.get("room_no", "")) == str(room_no)
            and cloud.get("category") == category
        )
        mode = "云模型 Tool Calling" if is_valid_tool_call else "规则降级 · 本地工具调用"
        return create_hotel_ticket(**args), mode


class HotelOrchestrator:
    def __init__(self, state: HotelState | None = None, *, force_rules: bool = False) -> None:
        self.state = state or HotelState()
        self.force_rules = force_rules
        self.model = OpenAICompatibleClient(disabled=force_rules)
        self.knowledge = HotelKnowledge()
        self.reception = ReceptionAgent(self.model, self.knowledge, self.state.pms_rooms)
        self.housekeeping = HousekeepingAgent()
        self.engineering = EngineeringAgent()
        self.concierge = ConciergeAgent()
        self.manager_escalation = ManagerEscalationAgent()
        self.vertical_agents = {
            agent.name: agent
            for agent in (self.housekeeping, self.engineering, self.concierge, self.manager_escalation)
        }
        # Kept as a compatibility facade for callers that imported the old
        # dispatcher. New orchestration routes through vertical_agents below.
        self.dispatcher = DispatchAgent(self.model)

    def _bind_room(self, room_no: str) -> None:
        if self.state.authorized_room and room_no != self.state.authorized_room:
            raise ValueError("已认证住客不能切换到其他房间，请联系前台变更入住绑定")
        self.state.room_no = room_no
        self.state.guest_profile = self.state.pms_rooms.get(room_no)
        self.state.late_checkout = next((r for r in reversed(self.state.checkout_requests) if r["room_no"] == room_no
                                         and (not self.state.stay_id or r.get("stay_id") == self.state.stay_id)
                                         and (not self.state.session_id or r.get("owner_session_id") == self.state.session_id)), None)

    def _verified_department_ticket(self, intent: str, detail: str) -> dict[str, Any]:
        """Departments propose against copies; only a validated proposal changes this state."""
        state = self.state
        child_state = deepcopy(state)
        delivery = parse_delivery_command(detail) if intent == "送物" else None
        minimum = "低"
        if intent == "送物":
            child = copy(self.housekeeping)
            child.lock = threading.RLock()
            child.inventory = deepcopy(self.housekeeping.inventory)
            ticket = child.dispatch(state.room_no, detail, child_state)
        elif intent == "工程报修":
            minimum = "高" if state.sentiment == "愤怒" or any(w in detail for w in self.engineering.MAJOR_INCIDENTS) else "中"
            ticket = self.engineering.diagnose(state.room_no, detail, child_state, state.sentiment)
        elif intent == "客诉紧急":
            minimum = "高"
            ticket = self.manager_escalation.escalate(state.room_no, detail, child_state, state.sentiment)
        elif intent == "续住申请":
            ticket = self.concierge.apply_stay_extension(
                state.room_no, state.pending.get("nights", 1), child_state.guest_profile, child_state,
            )
        else:
            ticket, _ = self.dispatcher.dispatch(state.room_no, intent, detail, state.sentiment)
        validate_department_ticket(
            ticket, room_no=state.room_no, intent=intent, department=DEPARTMENTS[intent],
            minimum_priority=minimum, delivery=delivery,
        )
        if any(existing["ticket_id"] == ticket["ticket_id"] for existing in state.tickets):
            raise AgentConflict("部门结果复用了已有工单标识")
        if intent == "送物":
            item, quantity = ticket.get("inventory_item"), ticket.get("quantity", 1)
            expected_item = delivery.item if delivery.item in self.housekeeping.inventory else "综合物料"
            if item != expected_item or set(child.inventory) != set(self.housekeeping.inventory):
                raise AgentConflict("部门物品或库存字段不符合任务约定")
            if item in self.housekeeping.inventory:
                before = self.housekeeping.inventory[item]
                reserved = quantity if before >= quantity else 0
                if (ticket.get("reserved_quantity") != reserved
                        or ticket.get("inventory_remaining") != before - reserved
                        or child.inventory.get(item) != before - reserved
                        or ticket.get("inventory_status") != ("库存充足" if before >= quantity else "库存不足，已转前台协调")
                        or ticket.get("delivery_status") != "待派送"):
                    raise AgentConflict("部门库存判断与库存账本不一致")
                if any(child.inventory.get(other) != value for other, value in self.housekeeping.inventory.items() if other != item):
                    raise AgentConflict("部门结果修改了任务以外的物品库存")
            elif child.inventory != self.housekeeping.inventory:
                raise AgentConflict("未识别的物品不得修改库存")
            borrowed_changes = child_state.borrowed_items[len(state.borrowed_items):]
            expected_borrow = item in self.housekeeping.BORROWABLE_ITEMS and bool(ticket.get("reserved_quantity"))
            if ticket.get("is_borrowable") != (item in self.housekeeping.BORROWABLE_ITEMS) or len(borrowed_changes) != int(expected_borrow):
                raise AgentConflict("借用标识或记录数量与库存预留不一致")
            for borrowed in borrowed_changes:
                if (borrowed.get("ticket_id") != ticket["ticket_id"] or borrowed.get("room_no") != state.room_no
                        or borrowed.get("item") != item or borrowed.get("quantity") != quantity):
                    raise AgentConflict("借用记录与已核验工单不一致")
            self.housekeeping.inventory.clear()
            self.housekeeping.inventory.update(child.inventory)
            state.borrowed_items.extend(borrowed_changes)
        return ticket

    def _record_agent_conflict(self, error: AgentConflict, trace: list[dict[str, str]]) -> str:
        state = self.state
        conflict = {
            "conflict_id": f"CF-{uuid.uuid4().hex[:10]}", "room_no": state.room_no,
            "intent": state.intent, "detail": state.pending.get("detail") if state.pending else None,
            "reason": str(error), "status": "需人工核验",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        state.agent_conflicts.append(conflict)
        state.active_agent = "Supervisor · 需人工核验"
        trace.append({"agent": "Supervisor", "event": "agent_conflict", "detail": str(error)})
        return "部门处理结果与您的需求或业务规则不一致，暂未派单，已记录待核验事项，请联系前台确认。"

    def delivery_target(self, text: str, command: Any = None) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve one business order consistently for both scheduling and execution."""
        command = command or parse_delivery_command(text)
        if command.unresolved_target:
            return None, "请确认要取消或修改的具体送物物品，暂未修改任何工单。"
        if command.ambiguous:
            return None, "您提到了多种物品，请分别说明要修改或取消哪一种；暂未修改工单。"
        if self.state.pending and self.state.pending.get("uncommitted_delivery"):
            return None, None
        tickets = [t for t in self.state.tickets if t.get("room_no") == self.state.room_no and t.get("category") == "送物"
                   and (not self.state.authorized_room or t.get("stay_id") == self.state.stay_id
                        and t.get("owner_session_id") == self.state.session_id)]
        active = [t for t in tickets if t["status"] == "处理中"]
        completed = [t for t in tickets if t["status"] == "已结单"]
        old_items = set(command.references) - {command.item}
        if old_items:
            matching = [t for t in active if t.get("inventory_item") in old_items]
            if len(matching) == 1:
                return matching[0], None
            if len(matching) > 1:
                return None, "同一种物品有多张送物工单，请联系前台确认要修改哪一单。"
            historical = [t for t in completed if t.get("inventory_item") in old_items]
            if historical:
                return historical[-1], None
            if not self.state.pending and tickets:
                return None, "未找到对应的可处理送物工单，请说明要修改或取消哪种物品。"
            return None, None
        if len(active) > 1:
            return None, "当前有多项送物服务，请先说明要修改或取消哪一种物品。"
        if active:
            return active[0], None
        pending_delivery = self.state.pending and self.state.pending.get("intent") == "送物"
        return (completed[-1] if completed and not pending_delivery else None), None

    def _delivery_change(self, text: str, trace: list[dict[str, str]]) -> tuple[str | None, str | None]:
        command = parse_delivery_command(text)
        state = self.state
        if command.action not in {"replace", "cancel"}:
            return None, None
        if command.unresolved_target or command.ambiguous or command.action == "replace" and command.quantity == 0:
            trace.append({"agent": "Supervisor", "event": "delivery_target_required", "detail": "需要确认更换的物品和有效数量"})
            return "请确认要修改或取消的具体送物物品及有效数量，暂未修改任何工单。", None
        pending_delivery = state.pending and state.pending.get("intent") == "送物"
        named_item = bool(command.references)
        if not command.item and not named_item and not pending_delivery and state.intent != "送物":
            return None, None
        ticket, target_error = self.delivery_target(text, command)
        if target_error:
            trace.append({"agent": "Supervisor", "event": "delivery_target_required", "detail": target_error})
            return target_error, None
        if ticket:
            reply = self.housekeeping.revise(ticket, command)
            state.pending = None
            state.intent, state.active_agent = "送物", self.housekeeping.name
            self.sync_borrowed_ticket(ticket)
            trace.append({"agent": self.housekeeping.name, "event": "delivery_change", "detail": reply})
            return reply, None
        if command.action == "cancel":
            state.pending = None
            trace.append({"agent": "Supervisor", "event": "task_cancelled", "detail": "送物尚未派单，取消待办"})
            return "已取消送物请求，尚未派单。", None
        if command.ambiguous or not command.item:
            return "请说明需要更换成哪种物品，暂未派送。", None
        prior_detail = state.pending.get("detail", "") if pending_delivery else ""
        quantity = command.quantity or _requested_quantity(prior_detail)
        return None, f"送 {quantity} 件{command.item}"

    def process(
        self, text: str, *, record_user: bool = True, cancellation_check: Callable[[], bool] | None = None,
    ) -> str:
        text = text.strip()
        if not text:
            return "请告诉我您需要的服务。"
        state = self.state
        if cancellation_check and cancellation_check():
            raise TaskInterrupted()
        analysis, state.model_mode = self.reception.understand(text)
        if cancellation_check and cancellation_check():
            raise TaskInterrupted()
        room = analysis["room_no"]
        is_correction = any(w in text for w in (
            "搞错", "更正", "换成", "改为", "重新", "不是",
            "切换", "我是", "我住", "登记", "换到", "其实是", "不是这"
        ))

        # Always record user turn immediately so conversation history is never dropped
        if record_user:
            state.messages.append({"role": "user", "content": text, "room_no": state.room_no or room})

        # Dual-stream FAQ assembly
        faq_part = None
        faq_entries = analysis.get("faq_entries", [])
        urgent_intent = analysis.get("intent") in {"工程报修", "客诉紧急"}
        explicit_bathroom_question = any(phrase in text for phrase in (
            "能不能洗澡", "可以洗澡吗", "能洗澡吗", "淋浴设施如何",
            "淋浴设施怎么样", "有淋浴吗", "有没有淋浴",
        ))
        if urgent_intent and not explicit_bathroom_question:
            faq_entries = [entry for entry in faq_entries if entry["id"] != "bathroom"]
        if faq_entries:
            faq_part = " ".join(e["answer"] for e in faq_entries)
            trace_faq_detail = f"hotel_knowledge.json / {len(faq_entries)}条命中 ({', '.join(e['id'] for e in faq_entries)})"
        elif analysis.get("faq_entry") and (not urgent_intent or explicit_bathroom_question or analysis["faq_entry"]["id"] != "bathroom"):
            faq_part = analysis["faq_entry"]["answer"]
            trace_faq_detail = f"hotel_knowledge.json / {analysis['faq_entry']['id']}"
        else:
            trace_faq_detail = None

        # Security guard: Intercept unauthorized cross-room spoofing
        # Trigger only when an active service/write operation is targeted at a DIFFERENT room,
        # and the user is NOT switching/correcting their session room.
        has_service_target = (analysis["intent"] in SERVICE_INTENTS) or (state.pending is not None)
        if state.room_no and room and room != state.room_no and not is_correction and has_service_target:
            trace = [{"agent": "ReceptionAgent", "event": "跨房安全拦截", "detail": f"当前绑定 {state.room_no}，阻止操作 {room}"}]
            guard_msg = f"您当前会话已关联 {state.room_no} 房。为保障住客安全与隐私，系统已拦截跨房为 {room} 房办理业务的操作；如需为该房间办理，请发送'切换到{room}'或点击侧边栏清空会话。"
            reply = f"{faq_part} 另外，{guard_msg}" if faq_part else guard_msg
            return self._finish(reply, trace)

        if room:
            self._bind_room(room)
        state.sentiment, state.active_agent = analysis["sentiment"], "ReceptionAgent"
        trace = [{"agent": "ReceptionAgent", "event": "关联档案", "detail": f"房间 {state.room_no or '待补全'}；{'已关联 CRM' if state.guest_profile else '档案待核实'}"}]
        change_reply, delivery_detail = self._delivery_change(text, trace)
        if change_reply is not None:
            return self._finish(f"{faq_part} 另外，{change_reply}" if faq_part else change_reply, trace)
        if delivery_detail:
            analysis["intent"] = "送物"
        if cancellation_check and cancellation_check():
            raise TaskInterrupted()

        pacing_keywords = (
            "还没好", "还没修好", "怎么回事", "还要多久", "多久能到",
            "催一下", "有人来吗", "进度如何", "还没送到",
        )
        if any(keyword in text for keyword in pacing_keywords) and state.room_no:
            active_tickets = [
                ticket for ticket in state.tickets
                if ticket.get("room_no") == state.room_no and ticket.get("status") == "处理中"
            ]
            if active_tickets:
                ticket = active_tickets[-1]
                state.pending = None
                state.intent = ticket["category"]
                state.active_agent = ticket.get("agent", "EngineeringAgent")
                trace.append({
                    "agent": ticket.get("agent", "EngineeringAgent"),
                    "event": "催单加急响应",
                    "detail": f"住客追问进度，已加急督办工单 {ticket['ticket_id']}",
                })
                reply = (
                    f"您的{ticket['category']}（{ticket['item_detail']}）正在加急排查处理中，"
                    "已为您再次催促工作人员尽快上门，请您稍候！"
                )
                return self._finish(reply, trace)

        # Service completion is a close operation, not another intent to route.
        # Resolve it before pending-slot handling so the same message can never
        # create a duplicate ticket.
        if analysis.get("is_resolution") or analysis.get("intent") == "服务完结":
            state.pending = None
            open_tickets = [
                ticket for ticket in state.tickets
                if ticket.get("room_no") == state.room_no and ticket.get("status") == "处理中"
            ]
            repair_terms = ("修", "工程", "漏水", "故障", "维修", "断电", "门锁")
            delivery_terms = ("送", "水", "毛巾", "收", "拿")
            preferred_category = None
            if any(term in text for term in repair_terms):
                preferred_category = "工程报修"
            elif any(term in text for term in delivery_terms):
                preferred_category = "送物"
            ticket = None
            if preferred_category:
                ticket = next((item for item in reversed(open_tickets) if item.get("category") == preferred_category), None)
            if ticket is None and open_tickets:
                ticket = open_tickets[-1]
            state.intent = "服务完结"
            if ticket:
                if (ticket.get("inventory_item") in self.housekeeping.inventory
                        and ticket.get("reserved_quantity", 0) != ticket.get("quantity", 1)):
                    trace.append({"agent": "Supervisor", "event": "delivery_stock_unconfirmed", "detail": "尚无完整库存预留，需核实实际派送"})
                    return self._finish("收到您的反馈，这张服务单仍需保洁核验实际派送和库存记录，暂未标记完成。", trace)
                ticket["status"] = "已结单"
                if ticket["category"] == "送物":
                    ticket["delivery_status"] = "已送达"
                ticket["completed_at"] = datetime.now().isoformat(timespec="seconds")
                state.active_agent = ticket.get("agent", "EngineeringAgent")
                trace.append({
                    "agent": ticket.get("agent", "EngineeringAgent"),
                    "event": "服务完结闭环",
                    "detail": f"住客确认完工，工单 {ticket['ticket_id']} ({ticket['category']}) 状态流转为已结单",
                })
                reply = f"收到您的反馈，很高兴问题已为您妥善解决！对应服务单（{ticket['category']}）已为您办结。祝您入住愉快，如还有其他需要请随时告诉我们。"
            else:
                state.active_agent = "ReceptionAgent"
                trace.append({"agent": "ReceptionAgent", "event": "服务完结反馈", "detail": "未找到当前房间处理中工单"})
                reply = "收到您的反馈，很高兴服务让您满意！祝您入住愉快，随时为您效劳。"
            return self._finish(reply, trace)

        has_service = analysis["intent"] in SERVICE_INTENTS

        # Cancellation, negation, or denial of pending action
        is_negation = any(term in text for term in (
            "取消申请", "不用了", "取消请求", "不要退房", "没说退房", "不是退房", "不用退房",
            "不退了", "不退房", "不需要", "换个话题", "重来", "我不退", "不打算退"
        ))
        if is_negation:
            state.pending = None
            if not has_service and not faq_part:
                state.intent = "其他"
                reply = "好的，已为您取消待办事项。请问还有什么其他服务可以帮您？"
                return self._finish(reply, trace)

        if faq_part and trace_faq_detail:
            trace.append({"agent": "ReceptionAgent", "event": "知识检索", "detail": trace_faq_detail})

        if has_service:
            state.pending = {
                "intent": analysis["intent"],
                "detail": delivery_detail or text,
                "sentiment": analysis["sentiment"],
                "requested_time": analysis["requested_time"],
                "nights": analysis.get("nights", 1),
            }
        elif state.pending and (room or analysis["requested_time"]):
            if analysis["requested_time"]:
                state.pending["requested_time"] = analysis["requested_time"]
        elif not has_service and faq_part:
            # When user shifts to an independent FAQ topic without providing pending slots,
            # clear the stale pending action so the state machine never gets stuck.
            state.pending = None

        pending = state.pending
        if not pending:
            if faq_part:
                state.intent = "酒店咨询"
                reply = faq_part
            else:
                state.intent = analysis.get("intent", "其他")
                if state.intent in {"闲聊", "其他"}:
                    state.active_agent = "ReceptionAgent · 软引流"
                    trace.append({"agent": "ReceptionAgent", "event": "闲聊软引流", "detail": "范围外话题已短答并转回酒店服务"})
                    reply = self.reception.generate_chitchat_reply(text, state.guest_profile.get("guest_name") if state.guest_profile else None)
                else:
                    reply = "您好，已关联您的房间。有什么可以帮您？" if room and state.guest_profile else "我可以帮您办理酒店服务或解答设施政策，请告诉我具体需求。"
            return self._finish(reply, trace)

        state.intent, state.sentiment = pending["intent"], pending["sentiment"]
        if state.intent == "送物":
            delivery = parse_delivery_command(pending["detail"])
            if delivery.ambiguous or delivery.quantity == 0:
                state.pending = None
                trace.append({"agent": "Supervisor", "event": "delivery_target_required", "detail": "单次送物需明确一种物品及有效数量"})
                notice = "请分别说明要送的物品和数量，每次先处理一种物品，数量需要大于零；本次尚未派单。"
                return self._finish(f"{faq_part} 另外，{notice}" if faq_part else notice, trace)
        service_reply = ""
        if not state.room_no:
            state.active_agent = "ReceptionAgent · 补全房间号"
            prompt_slot = "延迟退房" if state.intent == "延迟退房" else "续住" if state.intent == "续住申请" else "服务"
            service_reply = f"办理{prompt_slot}需核验您的在住档案与权益，请问您的房间号是多少呢？" if state.intent in {"延迟退房", "续住申请"} else "请问您的房间号是多少呢？"
            trace.append({"agent": "ReceptionAgent", "event": "等待补槽", "detail": "需要房间号，未执行写操作"})
        elif state.intent == "续住申请":
            nights = pending.get("nights", 1)
            if not state.guest_profile or state.guest_profile.get("room_status") != "在住":
                service_reply = f"暂未查到 {state.room_no} 房间的有效在住档案，办理续住请核对房号或前往前台办理。"
                trace.append({"agent": "ReceptionAgent", "event": "档案校验", "detail": f"房间 {state.room_no} 未找到在住档案"})
                state.pending = None
            else:
                inventory = state.guest_profile.get("inventory_status", "充裕")
                try:
                    ticket = self._verified_department_ticket("续住申请", pending["detail"])
                except AgentConflict as error:
                    return self._finish(self._record_agent_conflict(error, trace), trace)
                state.tickets.append(ticket)
                state.pending, state.active_agent = None, self.concierge.name
                trace.append({"agent": self.concierge.name, "event": "stay_extension_review", "detail": f"{ticket['ticket_id']} → 前台接待 / 房态{inventory}"})
                if inventory == "充裕":
                    service_reply = f"已为您提交 {state.room_no} 房续住 {nights} 晚申请。当前全店房态充裕，前台接待已收到工单，稍后将通过前台联系您确认续费并同步房卡权限。"
                else:
                    service_reply = f"已为您发起 {state.room_no} 房续住 {nights} 晚申请。由于当前房态紧张，前台正在为您协调客房排期，工作人员稍后会联系您处理。"
        elif state.intent == "延迟退房":
            if not state.guest_profile or state.guest_profile["room_status"] != "在住":
                service_reply = f"暂未查到 {state.room_no} 房间的有效在住档案，延迟退房请核对房号或联系前台核实。"
                trace.append({"agent": "ReceptionAgent", "event": "档案校验", "detail": f"房间 {state.room_no} 未找到在住档案"})
            else:
                target = pending["requested_time"]
                if not target:
                    service_reply = "您希望延迟到几点退房？例如下午两点。"
                else:
                    try:
                        result = apply_late_checkout(state.room_no, target, state.guest_profile)
                    except ValueError as exc:
                        service_reply = str(exc) + "，请重新提供时间。"
                    else:
                        stay_id = state.stay_id or (state.guest_profile or {}).get("stay_id")
                        prior = next((r for r in reversed(state.checkout_requests) if r["room_no"] == state.room_no and r["requested_time"] == target
                                      and r.get("stay_id") == stay_id
                                      and (not state.session_id or r.get("owner_session_id") == state.session_id)), None)
                        if prior is None:
                            result.update(request_id="LATE-" + uuid.uuid4().hex, stay_id=stay_id,
                                          owner_session_id=state.session_id)
                            state.checkout_requests.append(result)
                        else:
                            prior.update(result)
                            result = prior
                        state.late_checkout, state.pending = result, None
                        state.active_agent = "ReceptionAgent · PMS 审批"
                        trace.append({"agent": "ReceptionAgent", "event": "apply_late_checkout", "detail": f"{target} → {result['status']}；{result['reason']}"})
                        prefix = f"已更正关联 {state.room_no} 房间。" if is_correction else ""
                        approval_text = f"延迟退房已批准，退房时间更新为 {result['checkout_time']}，无需额外费用。" if result["updated_pms"] else f"延迟至 {target} 的申请已交前台人工审核，可能需要补房费；确认前仍按 {result['checkout_time'] or '原定时间'} 退房。"
                        service_reply = prefix + approval_text
        elif state.intent == "票券服务":
            voucher = self.concierge.issue_breakfast_vouchers(
                state.room_no,
                _requested_quantity(pending["detail"]),
                state.guest_profile,
                state,
            )
            state.pending, state.active_agent = None, self.concierge.name
            trace.append({"agent": self.concierge.name, "event": "issue_breakfast_vouchers", "detail": f"{voucher['quantity']} 张 / {voucher['status']}"})
            service_reply = (
                f"已为您发放 {voucher['quantity']} 张早餐券，早餐券配额已登记。"
                if voucher["status"] == "已发放"
                else "早餐券申请需要前台核验您的在住档案或配额，请稍候联系前台。"
            )
        else:
            existing = next((t for t in state.tickets if t["room_no"] == state.room_no and t["item_detail"] == pending["detail"] and t["status"] == "处理中"), None)
            if existing:
                ticket, dispatch_mode = existing, "重复申请复用"
            else:
                if cancellation_check and cancellation_check():
                    raise TaskInterrupted()
                try:
                    ticket = self._verified_department_ticket(state.intent, pending["detail"])
                except AgentConflict as error:
                    return self._finish(self._record_agent_conflict(error, trace), trace)
                dispatch_mode = ticket.get("agent", "DispatchAgent")
                trace.append({"agent": "Supervisor", "event": "proposal_validated", "detail": "用户需求、部门、房号与风险规则校验通过"})
            if not existing:
                state.tickets.append(ticket)
            state.pending, state.active_agent = None, ticket.get("agent", dispatch_mode)
            state.model_mode += " / " + dispatch_mode
            trace.append({"agent": state.active_agent, "event": "vertical_agent_dispatch", "detail": f"{ticket['ticket_id']} → {ticket['assigned_dept']} / {ticket['priority']}"})
            if ticket["category"] == "客诉紧急":
                service_reply = f"{ticket.get('empathy_response', '很抱歉给您带来不好的体验，大堂经理已收到您的反馈。')} {ticket.get('compensation_suggestion', '')}"
            elif ticket["category"] == "送物":
                service_reply = (
                    "该物品库存不足，已记录协调申请，尚未确认派送，请联系前台。"
                    if ticket.get("inventory_status", "").startswith("库存不足")
                    else f"已为您创建送物服务单，预计 {ticket.get('eta_minutes', 5)} 分钟送达。"
                )
            elif ticket["category"] == "工程报修":
                service_reply = f"{ticket.get('safety_guidance', '')} 已通知工程人员检查，工作人员将尽快联系您。"
            else:
                service_reply = "已为您提交服务申请，工作人员将尽快联系您。"

        if faq_part and service_reply:
            reply = f"{service_reply} 另外，{faq_part}" if state.intent == "客诉紧急" else f"{faq_part} 另外，{service_reply}"
        else:
            reply = service_reply or faq_part or ""

        return self._finish(reply, trace)

    def _finish(self, reply: str, trace: list[dict[str, str]]) -> str:
        guest_name = self.state.guest_profile.get("guest_name") if self.state.guest_profile else None
        reply = _format_greeting(guest_name, reply)
        self.state.request_detail = self.state.pending["detail"] if self.state.pending else None
        self.state.messages.append({"role": "assistant", "content": reply, "room_no": self.state.room_no})
        self.state.last_trace = trace
        self.compact_messages()
        return reply

    def compact_messages(self) -> None:
        if len(self.state.messages) > 12:
            archived = self.state.messages[:-6]
            self.state.messages = self.state.messages[-6:]
            snippets = [
                f"{m.get('role', 'user')}:{m.get('content', '')[:25]}"
                for m in archived[-4:]
            ]
            self.state.rolling_summary = "；".join(snippets)

    def night_audit(self) -> dict[str, Any]:
        """Perform system night audit, advancing date and resetting daily quotas."""
        new_date, actions = run_pms_night_audit(self.state.pms_rooms, getattr(self.state, "current_date", "2026-10-01"))
        self.state.current_date = new_date
        log_entry = {
            "date": new_date,
            "actions_count": len(actions),
            "actions": actions,
            "audited_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.audit_logs.append(log_entry)
        return log_entry

    def guest_progress(self) -> list[str]:
        items = []
        for result in self.state.checkout_requests:
            if result["room_no"] == self.state.room_no:
                items.append(f"延迟退房至 {result['requested_time']}：{result['status']}")
        for voucher in self.state.voucher_ledger:
            if voucher.get("room_no") == self.state.room_no:
                items.append(f"早餐券：{voucher.get('status')} · {voucher.get('quantity', 0)} 张")
        for ticket in self.state.tickets:
            if ticket["room_no"] != self.state.room_no:
                continue
            label = "服务反馈" if ticket["category"] == "客诉紧急" else ticket["category"]
            pending_change = ticket.get("change_request", {}).get("status") == "待部门确认"
            stock_shortage = ticket.get("inventory_status", "").startswith("库存不足")
            eta = f" · 预计 {ticket.get('eta_minutes', 5)} 分钟送达" if ticket["category"] == "送物" and ticket["status"] == "处理中" and not (pending_change or stock_shortage) else ""
            status = "已完成" if ticket["status"] == "已结单" else ticket["status"]
            if pending_change:
                status = "修改待保洁确认"
            elif stock_shortage and ticket["status"] == "处理中":
                status = "待库存协调"
            items.append(f"{label}：{status}{eta}")
        return items or ["暂无进行中的服务请求"]

    def guest_progress_items(self) -> list[dict[str, str]]:
        items = []
        for result in self.state.checkout_requests:
            if result["room_no"] == self.state.room_no:
                st_type = "success" if result["status"] == "已批准" else "error" if result["status"] == "未批准" else "warning"
                items.append({
                    "label": f"延迟退房至 {result['requested_time']}：{result['status']}",
                    "status": result["status"],
                    "type": st_type,
                })
        for voucher in self.state.voucher_ledger:
            if voucher.get("room_no") == self.state.room_no:
                status = voucher.get("status", "需前台核验")
                items.append({
                    "label": f"早餐券：{status} · {voucher.get('quantity', 0)} 张",
                    "status": status,
                    "type": "success" if status == "已发放" else "warning",
                })
        for ticket in self.state.tickets:
            if ticket["room_no"] != self.state.room_no:
                continue
            label = "服务反馈" if ticket["category"] == "客诉紧急" else ticket["category"]
            pending_change = ticket.get("change_request", {}).get("status") == "待部门确认"
            stock_shortage = ticket.get("inventory_status", "").startswith("库存不足")
            eta = f" · 预计 {ticket.get('eta_minutes', 5)} 分钟送达" if ticket["category"] == "送物" and ticket["status"] == "处理中" and not (pending_change or stock_shortage) else ""
            status = "已完成" if ticket["status"] == "已结单" else ticket["status"]
            if pending_change:
                status = "修改待保洁确认"
            elif stock_shortage and ticket["status"] == "处理中":
                status = "待库存协调"
            st_type = "warning" if pending_change else "success" if ticket["status"] == "已结单" else "info" if ticket["status"] == "已取消" else "warning"
            items.append({
                "label": f"{label}：{status}{eta}",
                "status": status,
                "type": st_type,
            })
        return items

    def sync_borrowed_ticket(self, ticket: dict[str, Any]) -> None:
        records = [item for item in self.state.borrowed_items if item["ticket_id"] == ticket["ticket_id"]]
        is_borrowed = ticket.get("is_borrowable") and ticket.get("reserved_quantity", 0) > 0 and ticket["status"] != "已取消"
        if not is_borrowed:
            for item in records:
                item["status"] = "已取消借用"
        elif records:
            records[0].update(item=ticket["inventory_item"], quantity=ticket["quantity"], status="借用在房")
        else:
            self.state.borrowed_items.append({
                "ticket_id": ticket["ticket_id"], "room_no": ticket["room_no"],
                "item": ticket["inventory_item"], "quantity": ticket["quantity"], "status": "借用在房",
                "borrowed_at": datetime.now().isoformat(timespec="seconds"),
            })

    def complete_ticket(self, ticket_id: str) -> None:
        ticket = next(t for t in self.state.tickets if t["ticket_id"] == ticket_id)
        if ticket["status"] == "已取消":
            raise ValueError("已取消工单不能标记为完成")
        if ticket.get("inventory_item") in self.housekeeping.inventory and ticket.get("reserved_quantity", 0) != ticket.get("quantity", 1):
            raise ValueError("库存尚未预留，不能确认送达")
        ticket.update(status="已结单", completed_at=datetime.now().isoformat(timespec="seconds"))
        if ticket["category"] == "送物":
            ticket["delivery_status"] = "已送达"
        self.state.last_trace = [{"agent": "PMS", "event": "人工结单", "detail": ticket_id}]

    def resolve_agent_conflict(self, conflict_id: str) -> None:
        conflict = next(c for c in self.state.agent_conflicts if c["conflict_id"] == conflict_id)
        conflict.update(status="已核验", reviewed_at=datetime.now().isoformat(timespec="seconds"))

    def review_checkout(self, room_no: str, requested_time: str, approved: bool | None, request_id: str | None = None) -> None:
        result = next((r for r in reversed(self.state.checkout_requests) if r["room_no"] == room_no and r["requested_time"] == requested_time
                       and (not request_id or r.get("request_id") == request_id)
                       and (not self.state.stay_id or r.get("stay_id") == self.state.stay_id)), None)
        if not result:
            return
        room = self.state.pms_rooms.get(room_no)
        if approved is True:
            if room:
                room["checkout_time"] = max(room.get("checkout_time", "12:00"), requested_time)
            result.update(status="已批准", updated_pms=True, checkout_time=room["checkout_time"] if room else requested_time, reason="前台人工审核完成")
            self.state.last_trace = [{"agent": "PMS", "event": "人工审批", "detail": f"房间 {room_no} / {requested_time} / 已批准"}]
            fee = "费用按接待订单及前台确认办理。" if os.getenv("AEGIS_SITE_PROFILE") == "academic" else "无需额外费用。"
            msg = f"【前台通知】您的延迟退房申请已获批准！{room_no} 房退房时间已更新为 {requested_time}，{fee}"
            self.state.messages.append({"role": "assistant", "content": msg, "room_no": room_no})
        elif approved is False:
            result.update(status="未批准", updated_pms=False, checkout_time=room["checkout_time"] if room else "12:00", reason="前台房态受限，未予批准")
            self.state.last_trace = [{"agent": "PMS", "event": "人工审批", "detail": f"房间 {room_no} / {requested_time} / 未批准"}]
            msg = f"【前台通知】很抱歉，经前台核查房态，未能批准 {room_no} 房延迟至 {requested_time} 的退房申请，仍按原定时间办理退房。如有特殊需求请联系前台。"
            self.state.messages.append({"role": "assistant", "content": msg, "room_no": room_no})
        elif approved is None:
            prev = result.get("status")
            if prev == "已批准" and room:
                room["checkout_time"] = "12:00"
            result.update(status="需前台审核", updated_pms=False, checkout_time=room["checkout_time"] if room else "12:00", reason="前台撤回原审批，等待重新核查")
            self.state.last_trace = [{"agent": "PMS", "event": "人工撤回", "detail": f"房间 {room_no} / {requested_time} / 撤回重审"}]
            msg = f"【前台通知】关于 {room_no} 房延迟至 {requested_time} 的退房审批已由前台撤回，目前正在重新核查房态中。"
            self.state.messages.append({"role": "assistant", "content": msg, "room_no": room_no})

    def agent_monitoring(self) -> list[dict[str, Any]]:
        """Return live department load derived from the Blackboard tickets."""
        definitions = [
            ("HousekeepingAgent", "🧹", "客房保洁智能体", {"送物"}),
            ("EngineeringAgent", "🔧", "工程安防智能体", {"工程报修"}),
            ("ConciergeAgent", "🛎️", "礼宾接待智能体", {"续住申请", "票券服务"}),
            ("ManagerEscalationAgent", "👔", "大堂经理危机响应", {"客诉紧急"}),
        ]
        results = []
        for name, icon, label, categories in definitions:
            active_tasks = sum(
                ticket.get("status") == "处理中"
                and (ticket.get("agent") == name or ticket.get("category") in categories)
                for ticket in self.state.tickets
            )
            if name == "ConciergeAgent":
                active_tasks += sum(v.get("status") == "需前台核验" for v in self.state.voucher_ledger)
            results.append({"name": name, "label": label, "icon": icon, "status": "处理中" if active_tasks else "待命", "active_tasks": active_tasks})
        return results

    def sorted_tickets(self) -> list[dict[str, Any]]:
        return sorted(reversed(self.state.tickets), key=lambda t: (t["status"] != "处理中", t.get("risk_level") != "红色", {"高": 0, "中": 1, "低": 2}[t["priority"]]))
