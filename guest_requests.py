"""Source-grounded request clauses; capability checks do not execute devices."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from agent_protocol import DELIVERY_NUMBER, DELIVERY_UNIT, parse_delivery_command


DEVICE_CONTROL_REPLY = (
    "空调温度：暂不支持通过聊天远程调温，当前未连接客房设备控制。"
    "请使用房内温控设备；如果无法调节，可以告诉我具体故障或联系前台协助。"
)
FAULT = r"冒烟|漏电|着火|火警|被困|坏|故障|报修|维修|漏水|不制冷|不亮|降不下来|没有热水|没热水|停电|断电|水管爆|门锁"


@dataclass(frozen=True)
class RequestPart:
    kind: str
    source: str


def is_device_control(text: str) -> bool:
    return bool("空调" in text and not re.search(FAULT, text)
                and re.search(r"温度|调低|调高|调到|调成|调一下", text)
                and not re.search(r"不用|不要|无需|别调", text))


def request_parts(text: str, *, service_detector: Callable[[str], bool] | None = None) -> tuple[RequestPart, ...]:
    """Only independently separated clauses can authorize separate executions.

    Shared quantities and mixed cancellation/replacement retain the supervisor's
    existing clarification guard. A free-form model label is never a subrequest.
    """
    command = parse_delivery_command(text)
    if command.action != "create":
        return ()
    if re.search(r"不不不|不对|记错|口误|更正", text):
        # Corrections describe one evolving request, not multiple independent
        # room targets or additional quantities. Keep the existing last-value
        # correction and authorization path in charge.
        return ()
    if len(command.references) > 1 and re.search(r"(?:都|各)(?:只)?(?:要)?\s*" + DELIVERY_NUMBER + DELIVERY_UNIT, text):
        return ()
    parts = []
    delivery_context = bool(re.search(r"送|拿|借|给我|我要|需要|还要|再来", text))
    for clause in re.split(r"[，,；;。]|(?:另外|并且|顺便)(?=给我|送|拿|借|要|把|调)", text):
        clause = clause.strip()
        if not clause:
            continue
        if parse_delivery_command(clause).action != "create":
            return (RequestPart("clarify", "检测到新增与取消或修改混合的需求，请分别确认操作；本次尚未执行任何项目。"),)
        if is_device_control(clause):
            parts.append(RequestPart("device", clause))
        elif (service_detector(clause) if service_detector else re.search(FAULT, clause)):
            parts.append(RequestPart("service", clause))
        else:
            item = parse_delivery_command(clause)
            if item.action != "create":
                return ()
            if item.ambiguous:
                if len(item.references) > 1:
                    return (RequestPart("clarify", "您提到了多种物品，请分别说明物品与数量；本次尚未派单。"),)
                continue
            if item.item and item.quantity is None and re.search(r"还没送到|还要多久|多久能到|催|把.+送(?:来|过来)|快点|赶时间", clause):
                parts.append(RequestPart("reminder", clause))
                continue
            if item.item or item.unknown_items:
                # Read-only questions, timing and emotion alone are not delivery
                # actions; a quantity-only continuation may inherit 送 from above.
                explicit = bool(re.search(r"送|拿|借|给我|我要|需要|还要|再来|再加", clause))
                question = bool(re.search(r"多少钱|几点|在哪|哪里|什么时候|何时|多久|如何|怎么|政策|押金|发票|能不能|是否|有没有|有哪些|哪些|还有.*吗", clause))
                if not question and (explicit or delivery_context and item.quantity is not None):
                    parts.append(RequestPart("delivery", clause))
    if len(parts) > 8:
        return (RequestPart("clarify", "本次需求较多，请分成不超过8项提交；尚未执行任何项目。"),)
    if parts and all(part.kind == "service" for part in parts):
        return ()
    if len(parts) > 1 or any(part.kind == "device" for part in parts):
        return tuple(parts)
    if len(parts) == 1 and parts[0].kind == "delivery" and parse_delivery_command(parts[0].source).unknown_items:
        return tuple(parts)
    if len(parts) == 1 and re.search(r"(?:点|些)(?:矿泉水|水)", text):
        return tuple(parts)
    return ()
