"""Read-only delivery execution details shared by staff UI and write guards."""
from __future__ import annotations

from typing import Any

from agent_protocol import parse_delivery_command
from hotel_catalog import ITEM_CATALOG


def delivery_fulfillment(ticket: dict, inventory: dict) -> dict[str, Any] | None:
    """Project trusted line fields; never infer missing quantities or reserve stock."""
    if ticket.get("category") != "送物":
        return None
    result = {"state": "needs_review", "label": "待人工核验", "items": [],
              "needs_restock": False, "needs_verification": True,
              "can_dispatch": False, "can_complete": False,
              "message": "原需求或库存明细未通过核验，请先由前台核对，暂不能派送或结单。"}
    item, quantity, reserved = (ticket.get(key) for key in ("inventory_item", "quantity", "reserved_quantity"))
    if not isinstance(item, str):
        return result
    available = inventory.get(item)
    numeric = (type(quantity) is int and quantity > 0 and type(reserved) is int
               and 0 <= reserved <= quantity and type(available) is int and available >= 0)
    if item not in ITEM_CATALOG or not numeric:
        return result
    detail = ticket.get("item_detail")
    command = parse_delivery_command(detail) if isinstance(detail, str) else None
    verified = bool(command and not command.unknown_items and not command.ambiguous
                    and command.action == "create" and command.item == item
                    and (command.quantity is None or command.quantity == quantity))
    shortage = max(quantity - reserved - available, 0)
    active = ticket.get("status") == "处理中"
    needs_restock = active and verified and shortage > 0
    result["items"] = [{"name": item, "unit": ITEM_CATALOG[item]["unit"],
                        "requested_quantity": quantity, "reserved_quantity": reserved,
                        "available_quantity": available, "shortage_quantity": shortage,
                        "needs_restock": needs_restock}]
    if not verified:
        result["message"] = "原话含未核验物品、数量冲突或不完整需求；以下仅为已有记录，须人工核验完整清单，暂不能派送或结单。"
        return result
    result["needs_verification"] = False
    result["needs_restock"] = needs_restock
    status, progress = ticket.get("status"), ticket.get("delivery_status")
    if status == "已取消":
        result.update(state="cancelled", label="已取消", message="原需求已取消，不再执行派送或发起补货。")
    elif ticket.get("change_request", {}).get("status") == "待部门确认":
        result.update(state="change_pending", label="变更待部门确认", message="请先核实撤回或更换申请，不能继续派送或直接结单。")
    elif status == "已结单" and progress == "已送达":
        result.update(state="completed", label="已送达", message="已结单，保留原执行明细供追溯。")
    elif not active:
        result.update(needs_verification=True)
    elif reserved < quantity:
        if shortage:
            result.update(state="awaiting_stock", label="待库存协调", message="库存不足，仅记录协调申请，未确认派送；请核实补货或与住客协商数量。")
        else:
            result.update(state="awaiting_reservation", label="待库存预留", message="当前库存可覆盖需求，但尚未足额预留；请核实并调整申请，不会自动预留或派送。")
    elif progress == "配送中":
        result.update(state="in_transit", label="配送中", can_complete=True, message="物品已足额预留，正在配送。")
    elif progress == "待派送":
        result.update(state="ready", label="待派送", can_dispatch=True, can_complete=True,
                      message="已足额预留，可确认开始派送；当前可用库存不含本单预留。")
    else:
        result.update(needs_verification=True, message="派送状态与库存记录不一致，请人工核验，暂不能派送或结单。")
    return result
