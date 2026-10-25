"""Task cancellation, delivery commands and supervisor checks, without UI dependencies."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from hotel_catalog import ITEM_CATALOG


class TaskInterrupted(Exception):
    """A superseded run must not publish its staged changes."""


class AgentConflict(ValueError):
    """A department proposal contradicts the task contract or a safety rule."""


DELIVERY_ITEMS = (*ITEM_CATALOG, "房卡")
DELIVERY_NUMBER = r"(?:\d+|[一两二三四五六七八九十])"
DELIVERY_UNIT = r"(?:条|瓶|罐|个|把|双|份|张|块|床|件|包|卷|支|副|桶|盒|袋|杯|箱|听)"


@dataclass(frozen=True)
class DeliveryCommand:
    action: str
    item: str | None
    quantity: int | None
    ambiguous: bool = False
    references: tuple[str, ...] = ()
    unresolved_target: bool = False
    cancel_all: bool = False
    unknown_items: tuple[str, ...] = ()


def normalize_delivery_text(text: str) -> str:
    """Normalize supported item aliases without rewriting hot water or plumbing."""
    text = re.sub(r"瓶装水|饮用水|纯净水", "矿泉水", text)
    # A standalone service noun is an alias. Do not replace every 水 character:
    # 热水、漏水、洗发水 and 水管 must retain their original meaning.
    lead = r"(?P<lead>(?:^|[，,。；;!?！？]\s*|(?<!\d)\d{3,4}(?!\d)\s*)(?:(?:我(?:的)?|那(?:个)?|这(?:个)?|刚才(?:要的|的)?)\s*)?|(?:送|拿|来|给我|我要|要|取消|不需要|不用|不要|别送|改成|换成|和|还有)\s*)"
    quantity = r"(?P<quantity>(?:(?:\d+|一|两|二|三|四|五|六|七|八|九|十)?\s*瓶\s*)?)"
    suffix = r"(?=\s*(?:(?:就|先)?不(?:用|要|需要)(?:再送|送)?(?:了)?|(?:都|各)(?:只)?要|取消|别送|了|和|跟|以及|[，,。；;!?！？]|$))"
    return re.sub(lead + quantity + r"水" + suffix,
                  lambda match: match["lead"] + match["quantity"] + "矿泉水", text)


def _ignored_quantity_context(text: str, start: int, end: int) -> bool:
    """Negated alternatives and read-only FAQ quantities are not stock requests."""
    prefix = re.split(r"[，,。；;!?！？、]|以及|和|跟", text[:start])[-1]
    clause = prefix + text[start:end] + re.split(r"[，,。；;!?！？、]", text[end:], maxsplit=1)[0]
    return bool(re.search(r"(?:不要|不需要|不用|别送|不送|无需|取消)(?:再|给我|送|拿|\s)*$", prefix)
                or re.search(r"多少钱|几点|哪里|何时|怎么|政策", clause)
                and not re.search(r"送|拿|借|给我|我要|只要|需要|再加|补", clause))


def _unknown_requested_items(text: str) -> tuple[str, ...]:
    """Find explicit uncatalogued nouns without treating polite/FAQ clauses as SKUs."""
    known = any(item in text for item in DELIVERY_ITEMS)
    separator = r"以及|还有|和|跟|(?<!以)及(?!时)|、|[，,；;]|(?=再(?:加|拿|送|借|要|来))"
    coordinated = bool(re.search(r"以及|还有|和|跟|(?<!以)及(?!时)|、", text))
    unknown = []
    # Inspect every quantified noun before skipping clauses containing a known
    # SKU. Otherwise "1瓶矿泉水2桶泡面" silently loses the second request.
    measured = DELIVERY_NUMBER + r"\s*" + DELIVERY_UNIT
    noun_end = r"(?=" + measured + r"|[，,。；;!?！？、]|再(?:加|拿|送|借|要|来)|以及|和|跟|$)"
    for match in re.finditer(measured + r"\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9-]*?)" + noun_end, text):
        if _ignored_quantity_context(text, match.start(), match.end()):
            continue
        noun = re.split(r"里(?:面)?(?:装|放|加)|(?:都|各)(?:只)?要", match[1])[0].strip(" 了吧啦")
        if noun and not any(noun.startswith(item) for item in DELIVERY_ITEMS) and noun not in unknown:
            unknown.append(noun)
    for part in re.split(separator, text):
        if any(item in part for item in DELIVERY_ITEMS):
            continue
        if re.match(r"\s*(?:我)?(?:不需要|不用|不要|无需|先别)", part):
            continue
        if re.fullmatch(r"\s*(?:请|麻烦)?(?:给我|帮我)?(?:再)?(?:来|送|拿)(?:一下|过来|到房间|就好|就行)[。！？!?\s]*", part):
            continue
        explicit = bool(re.search(r"(?:送|拿|来|借|给我|我要|只要|需要|再加|补)", part))
        measured_item = bool(re.search(measured, part))
        if not explicit and not (known and (coordinated or measured_item)):
            continue
        if re.search(r"几点|哪里|何时|怎么|政策|维修|报修|故障|坏了|漏水|退房|续住|会议|早餐|谢谢|辛苦", part):
            continue
        candidate = re.sub(r"(?<!\d)\d{3,4}(?!\d)", "", part).strip()
        candidate = re.sub(r"(?:可以吗|好吗|好么|行吗|吗|么)[。！？!?\s]*$", "", candidate)
        candidate = re.sub(r"^(?:(?:请|麻烦|帮我|给我|我|再|顺便|只|需要|想|要|送|拿|来|借|补|加|到房间)\s*)+", "", candidate)
        candidate = re.sub(r"^" + DELIVERY_NUMBER + r"\s*(?:" + DELIVERY_UNIT + r")?", "", candidate)
        candidate = re.split(r"(?:都|各|只要|只需|需要|数量|\d+|" + measured + r"|里(?:面)?(?:装|放|加))", candidate)[0]
        candidate = candidate.strip(" 。！？!?了吧啦的 ")
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9-]{0,19}", candidate) and candidate not in unknown:
            unknown.append(candidate)
    return tuple(unknown)


def parse_delivery_command(text: str) -> DeliveryCommand:
    """Extract the requested item, excluding explicitly negated alternatives."""
    text = normalize_delivery_text(text)
    occurrences: list[tuple[int, str]] = []
    references: set[str] = set()
    negated = False
    for item in DELIVERY_ITEMS:
        for match in re.finditer(re.escape(item), text):
            references.add(item)
            prefix = re.split(r"[，,。；;！!？?]", text[:match.start()])[-1]
            suffix = text[match.end():]
            is_negative = bool(
                re.search(r"(?:不要|不需要|不用|别送|不送|取消|不是)(?:再|给我|送|的|了|\s)*$", prefix)
                or re.match(r"\s*(?:就|先)?(?:不(?:要|用|需要)(?:再送|送)?|取消|别(?:再)?送)(?:了|啦|吧)?(?=\s*(?:[，,。；;!?！？]|$))", suffix)
            )
            if is_negative:
                negated = True
            else:
                occurrences.append((match.start(), item))
    if not occurrences and re.search(r"送水|瓶水|瓶矿泉水", text) and not negated:
        occurrences.append((text.find("水"), "矿泉水"))
        references.add("矿泉水")
    occurrences.sort()
    replacing = bool(re.search(
        r"改(?:成|为|送|要)|换(?:成|为)|(?:不要了|不用了|不需要了|不送了).{0,16}(?:我要|想要|改|换)", text,
    )) or (negated and bool(occurrences))
    cancelling = bool(re.search(r"取消|不用了|不需要了|不要了|别送|不用送|不要送|不送了", text))
    cancel_all = cancelling and bool(re.search(r"全部|全都|所有|这些|都不(?:用|要|需要)", text))
    deny_cancel = bool(re.search(r"(?:不要|别|不用|无需|先别|不是要|没说要|没有要|没让你|不想|没有让你).{0,12}取消", text))
    if replacing and occurrences:
        action = "replace"
    elif (cancelling or negated) and not occurrences:
        action = "cancel"
    else:
        action = "create"
    non_delivery_cancel = bool(re.search(r"退房|续住|报修|维修|投诉", text)) and not references and "送物" not in text
    compound_batch = cancel_all and (replacing or bool(re.search(
        r"(?:取消|不要了|不用了|不需要了).{0,12}(?:再送|再来|只要|我要|给我|改成|换成)", text,
    )))
    if cancel_all and not replacing and not deny_cancel and not non_delivery_cancel:
        action = "cancel"
    if non_delivery_cancel:
        cancel_all = False
    quantities = [match[1] for match in re.finditer("(" + DELIVERY_NUMBER + r")\s*" + DELIVERY_UNIT, text)
                  if not _ignored_quantity_context(text, match.start(), match.end())]
    digits = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw_quantity = quantities[-1] if quantities else None
    quantity = int(raw_quantity) if raw_quantity and raw_quantity.isdigit() else digits.get(raw_quantity)
    unique_items = {item for _, item in occurrences}
    unresolved_text = text
    for item in references:
        unresolved_text = unresolved_text.replace(item, "")
    unresolved_target = action in {"replace", "cancel"} and "水" in unresolved_text
    unknown = _unknown_requested_items(text) if action != "cancel" else ()
    return DeliveryCommand(action, occurrences[-1][1] if occurrences else None, quantity,
                           (len(unique_items) > 1 or action == "cancel" and len(references) > 1) and not cancel_all or deny_cancel or non_delivery_cancel or compound_batch,
                           tuple(item for item in DELIVERY_ITEMS if item in references), unresolved_target,
                           cancel_all and action == "cancel" and not (deny_cancel or compound_batch), unknown)


def validate_department_ticket(
    proposal: Any, *, room_no: str, intent: str, department: str, minimum_priority: str = "低",
    delivery: DeliveryCommand | None = None,
) -> None:
    """The supervisor owns commits; user intent and policy constrain its authority."""
    if not isinstance(proposal, dict):
        raise AgentConflict("部门结果必须是结构化工单")
    expected = {"room_no": room_no, "category": intent, "assigned_dept": department}
    for field, value in expected.items():
        if proposal.get(field) != value:
            raise AgentConflict(f"{field} 与用户任务不一致：预期 {value}，实际 {proposal.get(field)}")
    if not isinstance(proposal.get("ticket_id"), str) or not proposal["ticket_id"] or proposal.get("status") != "处理中":
        raise AgentConflict("新工单缺少标识或包含未经确认的完成状态")
    priorities = {"低": 0, "中": 1, "高": 2}
    if proposal.get("priority") not in priorities or priorities[proposal["priority"]] < priorities[minimum_priority]:
        raise AgentConflict("部门结果降低了业务规则要求的优先级")
    if minimum_priority == "高" and proposal.get("risk_level") != "红色":
        raise AgentConflict("高风险任务不能取消红色预警")
    if delivery and delivery.item:
        if proposal.get("inventory_item") != delivery.item:
            raise AgentConflict("子 Agent 的物品判断与用户最新有效需求不一致")
        if type(proposal.get("quantity")) is not int or proposal["quantity"] <= 0 or proposal["quantity"] != (delivery.quantity or 1):
            raise AgentConflict("子 Agent 的数量判断与用户需求不一致")
