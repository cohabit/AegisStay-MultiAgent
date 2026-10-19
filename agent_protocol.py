"""Task cancellation, delivery commands and supervisor checks, without UI dependencies."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class TaskInterrupted(Exception):
    """A superseded run must not publish its staged changes."""


class AgentConflict(ValueError):
    """A department proposal contradicts the task contract or a safety rule."""


DELIVERY_ITEMS = (
    "浴巾", "毛巾", "矿泉水", "吹风机", "枕头", "被子", "牙刷", "早餐券",
    "拖鞋", "洗发水", "沐浴露", "梳子", "剃须刀", "雨伞", "充电器", "房卡",
)


@dataclass(frozen=True)
class DeliveryCommand:
    action: str
    item: str | None
    quantity: int | None
    ambiguous: bool = False
    references: tuple[str, ...] = ()
    unresolved_target: bool = False


def normalize_delivery_text(text: str) -> str:
    """Normalize supported item aliases without rewriting hot water or plumbing."""
    text = re.sub(r"瓶装水|饮用水|纯净水", "矿泉水", text)
    # A standalone service noun is an alias. Do not replace every 水 character:
    # 热水、漏水、洗发水 and 水管 must retain their original meaning.
    lead = r"(?P<lead>(?:^|[，,。；;!?！？]\s*|(?<!\d)\d{3,4}(?!\d)\s*)(?:(?:我(?:的)?|那(?:个)?|这(?:个)?|刚才(?:要的|的)?)\s*)?|(?:送|拿|来|给我|我要|要|取消|不需要|不用|不要|别送|改成|换成|和|还有)\s*)"
    quantity = r"(?P<quantity>(?:(?:\d+|一|两|二|三|四|五|六|七|八|九|十)?\s*瓶\s*)?)"
    suffix = r"(?=\s*(?:(?:就|先)?不(?:用|要|需要)(?:再送|送)?(?:了)?|取消|别送|了|和|跟|以及|[，,。；;!?！？]|$))"
    return re.sub(lead + quantity + r"水" + suffix,
                  lambda match: match["lead"] + match["quantity"] + "矿泉水", text)


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
        r"改(?:成|为|送|要)|换(?:成|为)|(?:不要|不用|不需要|不是).*(?:我要|要|换|改|是)", text,
    ))
    cancelling = bool(re.search(r"取消|不用了|不需要了|不要了|别送|不用送|不要送|不送了", text))
    if replacing and occurrences:
        action = "replace"
    elif (cancelling or negated) and not occurrences:
        action = "cancel"
    else:
        action = "create"
    quantities = re.findall(r"(\d+|一|两|二|三|四|五|六|七|八|九|十)\s*(?:条|瓶|个|把|双|份|张|块|床|件)", text)
    digits = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw_quantity = quantities[-1] if quantities else None
    quantity = int(raw_quantity) if raw_quantity and raw_quantity.isdigit() else digits.get(raw_quantity)
    unique_items = {item for _, item in occurrences}
    unresolved_text = text
    for item in references:
        unresolved_text = unresolved_text.replace(item, "")
    unresolved_target = action in {"replace", "cancel"} and "水" in unresolved_text
    return DeliveryCommand(action, occurrences[-1][1] if occurrences else None, quantity,
                           len(unique_items) > 1 or action == "cancel" and len(references) > 1,
                           tuple(item for item in DELIVERY_ITEMS if item in references), unresolved_target)


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
