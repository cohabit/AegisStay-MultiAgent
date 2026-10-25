"""Numbered semantic regressions: query scope, approved facts and action preservation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from hotel_core import HotelKnowledge, HotelOrchestrator


# Expectations are hand-derived facts, never produced by the answer formatter.
# Removing scope isolation, fact lookup or action composition must fail these cases.
CASES = [
    ("F01", "closing", "standard", "健身房几点关门", ("健身房23:00关门",), ("06:00", "三楼")),
    ("F02", "closing", "standard", "健身房什么时候关闭？", ("23:00关门",), ("06:00",)),
    ("F03", "closing", "standard", "健身房何时闭馆", ("23:00关门",), ("06:00",)),
    ("F04", "closing", "standard", "健身房几点打烊", ("23:00关门",), ("06:00",)),
    ("F05", "closing", "standard", "gym 最晚几点结束营业", ("23:00关门",), ("06:00",)),
    ("F06", "closing", "standard", "泳池几点关门", ("泳池22:00关门",), ("08:00", "23:00")),
    ("F07", "closing", "standard", "游泳池何时关闭", ("22:00关门",), ("08:00",)),
    ("F08", "closing", "standard", "健身几点停止营业", ("23:00关门",), ("06:00",)),
    ("F09", "opening", "standard", "健身房几点开门", ("健身房06:00开门",), ("23:00", "三楼")),
    ("F10", "opening", "standard", "健身房何时开馆？", ("06:00开门",), ("23:00",)),
    ("F11", "opening", "standard", "健身房什么时候开始营业", ("06:00开门",), ("23:00",)),
    ("F12", "opening", "standard", "gym 几点开门", ("06:00开门",), ("23:00",)),
    ("F13", "opening", "standard", "泳池几点开放", ("08:00开门",), ("22:00",)),
    ("F14", "opening", "standard", "游泳池何时开始营业", ("08:00开门",), ("22:00",)),
    ("F15", "fields", "standard", "健身房在哪里", ("健身房位于三楼",), ("06:00", "23:00")),
    ("F16", "fields", "standard", "在哪可以健身", ("三楼",), ("06:00", "23:00")),
    ("F17", "fields", "standard", "泳池在几楼", ("泳池位于三楼",), ("08:00", "22:00")),
    ("F18", "fields", "standard", "健身房开放时间", ("06:00–23:00",), ("三楼",)),
    ("F19", "fields", "standard", "泳池营业时段", ("08:00–22:00",), ("三楼",)),
    ("F20", "fields", "standard", "健身房多少钱", ("健身房", "费用", "核验"), ("免费", "23:00")),
    ("F21", "fields", "standard", "健身房怎么预约", ("健身房", "预约", "核验"), ("已预约", "23:00")),
    ("F22", "fields", "standard", "健身房在哪几点关门", ("三楼", "23:00关门"), ("06:00",)),
    ("F23", "multi", "standard", "在哪可以洗脚，健身房几点关门", ("足浴", "位置", "核验", "23:00关门"), ("06:00", "健身房、健身：")),
    ("F24", "multi", "standard", "在哪可以洗脚健身房几点关门", ("足浴", "位置", "23:00关门"), ("06:00",)),
    ("F25", "multi", "standard", "健身房几点关门在哪可以洗脚", ("23:00关门", "足浴", "位置"), ("06:00",)),
    ("F26", "multi", "standard", "健身房几点开门泳池几点关门", ("健身房06:00开门", "泳池22:00关门"), ("健身房23:00", "泳池08:00")),
    ("F27", "multi", "standard", "泳池在哪里健身房几点关门", ("泳池位于三楼", "健身房23:00关门"), ("08:00", "06:00")),
    ("F28", "multi", "standard", "健身房和泳池几点关门", ("健身房23:00关门", "泳池22:00关门"), ("06:00", "08:00")),
    ("F29", "multi", "standard", "按摩多少钱健身房几点关门", ("按摩", "费用", "23:00关门"), ("06:00",)),
    ("F30", "multi", "standard", "健身房几点开门、几点关门", ("06:00开门", "23:00关门"), ("三楼",)),
    ("F31", "multi", "standard", "足浴在哪；健身房几点关闭？按摩怎么预约", ("足浴", "位置", "23:00关门", "按摩", "预约"), ("06:00",)),
    ("F32", "multi", "standard", "洗脚按摩和健身房在哪里", ("足浴", "按摩", "位置", "健身房位于三楼"), ("06:00", "23:00")),
    ("F33", "academic", "academic", "健身房几点关门", ("健身房", "关门时间", "核验"), ("23:00", "06:00", "三楼")),
    ("F34", "academic", "academic", "健身房几点开门", ("健身房", "开门时间", "核验"), ("06:00", "23:00")),
    ("F35", "academic", "academic", "健身房在哪", ("健身房", "位置", "核验"), ("三楼",)),
    ("F36", "academic", "academic", "泳池几点关闭", ("泳池", "关门时间", "核验"), ("22:00", "08:00")),
    ("F37", "academic", "academic", "在哪可以洗脚，健身房几点关门", ("足浴", "位置", "健身房", "关门时间", "核验"), ("23:00", "06:00", "三楼")),
    ("F38", "academic", "academic", "健身房在哪里泳池几点开放", ("健身房", "位置", "泳池", "开门时间"), ("三楼", "08:00")),
    ("F39", "academic", "academic", "健身房收费吗", ("健身房", "费用", "核验"), ("免费",)),
    ("F40", "academic", "academic", "健身房开放时段和位置", ("开放时段", "位置", "核验"), ("23:00", "三楼")),
    ("F41", "custom", "custom", "健身房几点关门", ("健身房19:30关门",), ("23:00", "09:15", "六楼")),
    ("F42", "custom", "custom", "健身房几点开门", ("健身房09:15开门",), ("19:30", "06:00")),
    ("F43", "custom", "custom", "健身房在哪", ("健身房位于六楼",), ("三楼", "19:30")),
    ("F44", "custom", "custom", "健身房多少钱", ("健身房费用：每次30元",), ("免费", "19:30")),
    ("F45", "custom", "custom", "健身房如何预约", ("健身房", "预约", "前台提前一天登记"), ("已预约",)),
    ("F46", "custom", "custom", "按摩几点关门", ("按摩服务20:45关门",), ("19:30", "23:00")),
    ("F47", "custom", "custom", "按摩在哪健身房几点关闭", ("按摩服务位于一楼理疗室", "健身房19:30关门"), ("三楼", "20:45")),
    ("F48", "custom", "custom", "健身房几点开门按摩多少钱", ("健身房09:15开门", "按摩服务费用：每次80元"), ("19:30", "20:45")),
    ("F49", "mixed", "standard", "送2条毛巾，健身房几点关门", ("23:00关门", "已为您创建"), ("06:00",)),
    ("F50", "mixed", "standard", "在哪可以洗脚，再拿1条毛巾", ("足浴", "位置", "已为您创建"), ()),
    ("F51", "mixed", "standard", "全部取消，健身房几点关门", ("已取消", "23:00关门"), ("06:00",)),
    ("F52", "mixed", "standard", "毛巾全部取消，在哪可以洗脚", ("已取消", "足浴", "位置"), ()),
    ("F53", "mixed", "standard", "房间插座冒烟，健身房几点关门", ("远离", "23:00关门"), ("06:00",)),
    ("F54", "mixed", "academic", "房间插座冒烟，在哪可以洗脚", ("远离", "足浴", "位置", "核验"), ("23:00",)),
    ("F55", "mixed", "standard", "浴缸漏水请维修，按摩在哪里", ("按摩", "位置", "工程"), ("泡澡设施的可用情况",)),
    ("F56", "mixed", "academic", "送2瓶水，健身房几点关闭", ("已为您创建", "健身房", "关门时间", "核验"), ("23:00",)),
    ("F57", "negative", "standard", "不问健身房开门时间，只问几点关门", ("23:00关门",), ("06:00",)),
    ("F58", "negative", "standard", "健身房不用告诉我位置，几点关门", ("23:00关门",), ("三楼", "06:00")),
    ("F59", "negative", "standard", "不是问健身房几点关门，是问几点开门", ("06:00开门",), ("23:00",)),
    ("F60", "negative", "standard", "不需要预约按摩，只问在哪可以洗脚", ("足浴", "位置", "核验"), ("已预约",)),
]


class FacilityIntentTests(unittest.TestCase):
    def run_case(self, case):
        number, group, profile, text, wanted, forbidden = case
        with tempfile.TemporaryDirectory() as temporary:
            env = {"AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "standard" if profile == "custom" else profile,
                   "AEGIS_DB_PATH": "", "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": ""}
            catalog = {"services": [{"name": "按摩服务", "aliases": ["按摩"], "status": "available",
                                      "source": "synthetic-approved-test", "fields": {
                                          "location": "一楼理疗室", "closes_at": "20:45", "price": "每次80元"}}]}
            with patch.dict(os.environ, env):
                flow = HotelOrchestrator(force_rules=True)
                flow._bind_room("1128")
                if profile == "custom":
                    path = Path(temporary) / "knowledge.json"
                    path.write_text(json.dumps({"fallback": "请核验", "entries": [{"id": "gym", "topic": "健身房",
                        "keywords": ["健身房", "健身", "gym"], "answer": "健身房须依批准配置答复。",
                        "facility_topics": [{"topic": "健身房", "aliases": ["健身房", "健身", "gym"],
                            "fields": {"location": "六楼", "opens_at": "09:15", "closes_at": "19:30",
                                       "price": "每次30元", "reservation": "前台提前一天登记"}}]}]}, ensure_ascii=False), encoding="utf-8")
                    flow.knowledge = HotelKnowledge(path)
                    flow.reception.knowledge = flow.knowledge
                if "取消" in text:
                    flow.process("送1条毛巾")
                tickets_before = deepcopy(flow.state.tickets)
                stock_before = deepcopy(flow.housekeeping.inventory)
                with patch("hotel_core.load_service_catalog", return_value=catalog) if profile == "custom" else _NoPatch():
                    reply = flow.process(text)
                compact = "".join(reply.split())
                for fragment in wanted:
                    self.assertIn(fragment, compact, f"{number}/{group}: {reply}")
                for fragment in forbidden:
                    self.assertNotIn(fragment, compact, f"{number}/{group}: {reply}")
                if group != "mixed":
                    self.assertEqual(flow.state.tickets, tickets_before)
                    self.assertEqual(flow.housekeeping.inventory, stock_before)
                elif "取消" in text:
                    self.assertEqual(flow.state.tickets[0]["status"], "已取消")
                    self.assertEqual(flow.housekeeping.inventory["毛巾"], 24)
                elif "冒烟" in text or "维修" in text:
                    self.assertEqual(flow.state.tickets[0]["category"], "工程报修")
                    if "冒烟" in text:
                        self.assertEqual(flow.state.tickets[0]["priority"], "高")
                        if "健身房" in text:
                            self.assertLess(reply.index("远离"), reply.index("健身房"))
                else:
                    self.assertEqual(len(flow.state.tickets), 1)
                    expected_item, quantity = ("矿泉水", 2) if "2瓶水" in text else ("毛巾", 2 if "2条" in text else 1)
                    self.assertEqual(flow.state.tickets[0]["inventory_item"], expected_item)
                    self.assertEqual(flow.state.tickets[0]["quantity"], quantity)
                    self.assertEqual(flow.housekeeping.inventory[expected_item], stock_before[expected_item] - quantity)


class _NoPatch:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


def _case_method(case):
    def test(self):
        self.run_case(case)
    test.__doc__ = f"{case[0]} [{case[1]}] {case[3]}"
    return test


for _case in CASES:
    setattr(FacilityIntentTests, f"test_{_case[0]}_{_case[1]}", _case_method(_case))


if __name__ == "__main__":
    unittest.main()
