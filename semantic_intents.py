"""Validate source-grounded model actions before existing business execution."""
from __future__ import annotations

import re

from agent_protocol import DELIVERY_NUMBER, DELIVERY_UNIT, parse_delivery_command
from guest_requests import FAULT, RequestPart, is_device_control
from hotel_catalog import ITEM_CATALOG


_MEASURE = re.compile(DELIVERY_NUMBER + r"\s*" + DELIVERY_UNIT)
_REQUEST = re.compile(r"送|拿|借|给我|我要|需要|还要|再来|再加|补|来")
_QUESTION = re.compile(r"多少钱|几点|在哪|哪里|什么时候|何时|如何|怎么|政策|押金|发票|能不能|是否|有没有|有哪些|哪些|还有.*吗")
_REMINDER = re.compile(r"还没送到|还要多久|多久能到|催|把.+送(?:来|过来)|快点|赶时间")
_MUTATION = re.compile(r"取消|不要|不用|不需要|无需|别|不送|不想|没说要|没让|改(?:成|为|送|要)|换(?:成|为)|不不不|不对|记错|口误|更正")


def _source_kind(source: str, *, delivery_context: bool) -> str | None:
    command = parse_delivery_command(source)
    if command.action != "create":
        return None
    delivery = bool(command.item or command.unknown_items)
    if is_device_control(source):
        return None if delivery else "device"
    if re.search(FAULT, source):
        # A merged repair and delivery must be split before either can execute.
        return None if delivery and _REQUEST.search(source) else "service"
    if (command.ambiguous or command.item and command.unknown_items
            or len(command.unknown_items) > 1 or len(_MEASURE.findall(source)) > 1):
        return None
    if command.item and command.item not in ITEM_CATALOG:
        return None
    if command.quantity is not None and command.quantity <= 0:
        return None
    if command.item and command.quantity is None and _REMINDER.search(source):
        return "reminder"
    if _QUESTION.search(source) or re.search(r"已经?送到|送到了|收到了|拿到了", source):
        return None
    if delivery and (_REQUEST.search(source) or delivery_context and command.quantity is not None):
        return "delivery"
    return None


def validated_model_parts(text: str, cloud: dict) -> tuple[RequestPart, ...]:
    """Return verified source clauses, or () to retain the existing rules path.

    Only kind/source are accepted on each action. SKU, quantity and capability
    come from the existing parser and catalog; model room/role fields are unused.
    No business state is read or changed here.
    """
    if not isinstance(text, str) or not isinstance(cloud, dict):
        return ()
    actions = cloud.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        return ()
    command = parse_delivery_command(text)
    if (command.action != "create" or _MUTATION.search(text)
            or re.search(r"(?:都|各)(?:只)?(?:要)?\s*" + DELIVERY_NUMBER + DELIVERY_UNIT, text)):
        return ()

    parts, spans, seen = [], [], set()
    cursor = 0
    delivery_context = bool(_REQUEST.search(text))
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"kind", "source"}:
            return ()
        kind, source = action["kind"], action["source"]
        if (not isinstance(kind, str) or kind not in {"delivery", "device", "service", "reminder"}
                or not isinstance(source, str) or not source.strip() or source in seen):
            return ()
        start = text.find(source, cursor)
        if start < 0:
            return ()
        actual_kind = _source_kind(source, delivery_context=delivery_context)
        if actual_kind is None:
            return ()
        cursor = start + len(source)
        spans.append((start, cursor, actual_kind))
        parts.append(RequestPart(actual_kind, source))
        seen.add(source)

    def covered(start: int, end: int, *, delivery_only: bool = False) -> bool:
        return any(left <= start and end <= right and (not delivery_only or kind == "delivery")
                   for left, right, kind in spans)

    # A model may split clauses, but cannot trim off a requested quantity or
    # omit another catalog/unknown item. Check original offsets, before aliases
    # are normalized, so a source cannot turn 水果 into the approved water SKU.
    for match in _MEASURE.finditer(text):
        if not covered(*match.span(), delivery_only=True):
            return ()
    nouns = set(command.references) | set(command.unknown_items)
    if "矿泉水" in command.references:
        nouns.update(("瓶装水", "饮用水", "纯净水", "水"))
    for noun in nouns:
        for match in re.finditer(re.escape(noun), text):
            if not covered(*match.span()):
                return ()
    cursor = 0
    for start, end, _ in spans:
        gap = text[cursor:start]
        if is_device_control(gap) or re.search(FAULT, gap):
            return ()
        cursor = end
    if is_device_control(text[cursor:]) or re.search(FAULT, text[cursor:]):
        return ()
    return tuple(parts)
