"""Generate 240-room realistic PMS simulation dataset for AegisStay."""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SURNAMES = ["张", "李", "王", "赵", "陈", "刘", "杨", "黄", "吴", "周", "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗"]
GENDERS = ["先生", "女士"]
MEMBER_TIERS = ["普通会员", "携程黑金会员", "携程白金会员", "散客非会员"]
MEMBER_WEIGHTS = [0.45, 0.10, 0.20, 0.25]
ROOM_STATUSES = ["在住", "空净房", "待清洁", "维修中"]
STATUS_WEIGHTS = [0.65, 0.20, 0.10, 0.05]


def generate_hotel_rooms() -> dict[str, dict[str, str]]:
    random.seed(42)  # Deterministic seed for reproducible testing
    rooms: dict[str, dict[str, str]] = {}

    for floor in range(1, 11):
        for seq in range(1, 25):
            room_no = f"{floor}{seq:02d}"
            # Reserve slots for dedicated VIP benchmark rooms 1128 and 1106
            # so the persisted simulation contains exactly 240 rooms.
            if room_no in ("1001", "1002"):
                continue
            status = random.choices(ROOM_STATUSES, weights=STATUS_WEIGHTS)[0]
            if seq <= 8:
                room_type = "标准双床房"
            elif seq <= 18:
                room_type = "高级大床房"
            elif seq <= 22:
                room_type = "行政大床房"
            else:
                room_type = "豪华景观套房"

            if status == "在住":
                guest_name = f"{random.choice(SURNAMES)}{random.choice(GENDERS)}"
                member_level = random.choices(MEMBER_TIERS, weights=MEMBER_WEIGHTS)[0]
                checkout_time = "12:00"
                inventory = "充裕" if random.random() > 0.3 else "紧张"
            else:
                guest_name = "—"
                member_level = "—"
                checkout_time = "—"
                inventory = "充裕"

            rooms[room_no] = {
                "room_no": room_no,
                "floor": str(floor),
                "room_type": room_type,
                "guest_name": guest_name,
                "member_level": member_level,
                "checkout_time": checkout_time,
                "room_status": status,
                "inventory_status": inventory,
            }

    # Strict baseline guarantees
    rooms["1128"] = {
        "room_no": "1128", "floor": "11", "room_type": "行政套房",
        "guest_name": "张先生", "member_level": "携程黑金会员",
        "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕",
    }
    rooms["1106"] = {
        "room_no": "1106", "floor": "11", "room_type": "行政大床房",
        "guest_name": "赵女士", "member_level": "携程白金会员",
        "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕",
    }
    rooms["808"] = {
        "room_no": "808", "floor": "8", "room_type": "高级大床房",
        "guest_name": "李女士", "member_level": "普通会员",
        "checkout_time": "12:00", "room_status": "在住", "inventory_status": "紧张",
    }
    rooms["902"] = {
        "room_no": "902", "floor": "9", "room_type": "标准双床房",
        "guest_name": "王先生", "member_level": "普通会员",
        "checkout_time": "12:00", "room_status": "在住", "inventory_status": "充裕",
    }

    return rooms


def main() -> None:
    rooms = generate_hotel_rooms()
    out_file = ROOT / "pms_rooms_240.json"
    out_file.write_text(json.dumps(rooms, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(rooms)} rooms in {out_file}")


if __name__ == "__main__":
    main()
