"""Regressions from actual guest transcripts; all inputs and records are synthetic."""
from __future__ import annotations

import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from agent_protocol import AgentConflict, parse_delivery_command
from hotel_core import HotelOrchestrator


class ConversationFollowupTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic",
                                     "AEGIS_DB_PATH": "", "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": ""})
        env.start()
        self.addCleanup(env.stop)

    def flow(self):
        flow = HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        return flow

    def test_measured_unknown_compounds_never_partially_order_water(self):
        # Historical method ID retained. CR-01 v2 permits explicitly separated
        # known requests, while unknown clauses get their own rejection.
        cases = (("我要两瓶水，两桶泡面，一桶泡面里装水", "矿泉水", 2),
                 ("我要两瓶水，两桶泡面", "矿泉水", 2), ("送1瓶矿泉水，2盒饼干", "矿泉水", 1),
                 ("送1条毛巾，1袋薯片", "毛巾", 1), ("送水两瓶、两杯奶茶", None, 0),
                 ("送1条浴巾，再加1箱红牛", "浴巾", 1), ("送两瓶水，1听啤酒", "矿泉水", 2),
                 ("送一瓶水，两支口红", "矿泉水", 1))
        for text, item, quantity in cases:
            with self.subTest(text=text):
                flow = self.flow()
                stock = deepcopy(flow.housekeeping.inventory)
                self.assertTrue(parse_delivery_command(text).unknown_items)
                reply = flow.process(text)
                self.assertEqual(len(flow.state.tickets), int(item is not None))
                if item:
                    self.assertEqual((flow.state.tickets[0]["inventory_item"], flow.state.tickets[0]["quantity"]), (item, quantity))
                    stock[item] -= quantity
                self.assertEqual(flow.housekeeping.inventory, stock)
                self.assertIn("尚未创建", reply)

    def test_known_sku_in_same_clause_does_not_hide_an_unknown_measured_noun(self):
        for text in ("送1瓶矿泉水2桶泡面", "送1条毛巾1盒饼干", "给我两瓶矿泉水再拿一桶泡面"):
            with self.subTest(text=text):
                flow = self.flow()
                before = deepcopy(flow.housekeeping.inventory)
                flow.process(text)
                self.assertEqual(flow.state.tickets, [])
                self.assertEqual(flow.housekeeping.inventory, before)

    def test_all_unknown_nouns_are_reported_instead_of_only_last_one(self):
        command = parse_delivery_command("我要两瓶水，两桶泡面，一桶泡面里装水，再拿七个避孕套")
        self.assertTrue(any("泡面" in item for item in command.unknown_items))
        self.assertTrue(any("避孕套" in item for item in command.unknown_items))

    def test_direct_department_cannot_drop_unknown_measured_addition(self):
        flow = self.flow()
        stock = deepcopy(flow.housekeeping.inventory)
        with self.assertRaises(AgentConflict):
            flow.housekeeping.dispatch("1128", "送2瓶矿泉水，两桶泡面", flow.state)
        self.assertEqual(flow.housekeeping.inventory, stock)

    def test_valid_delivery_quantities_and_facility_questions_are_not_unknown_nouns(self):
        for text in ("送2瓶矿泉水", "送2包纸巾", "送1副耳塞", "送一条毛巾，麻烦放门口",
                     "水，两瓶，给我再来一下，不用催", "送2条毛巾，健身房几点关闭"):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual(command.unknown_items, ())

    def test_member_query_uses_bound_profile_or_academic_policy_without_cloud(self):
        for profile in ("academic", "standard"):
            with self.subTest(profile=profile), patch.dict(os.environ, {"AEGIS_SITE_PROFILE": profile}):
                flow = self.flow()
                with patch.object(flow.reception, "understand", side_effect=AssertionError("Profile facts are local")):
                    reply = flow.process("什么级别的会员")
                    flow.process("我是会员吗")
                self.assertIn("前台核验" if profile == "academic" else "携程黑金会员", reply)
                self.assertNotRegex(reply, r"请.*(?:卡号|联系方式|身份证|电话)")
                self.assertEqual(flow.state.tickets, [])

    def test_unbound_profile_does_not_invent_identity_or_collect_membership_details(self):
        flow = HotelOrchestrator(force_rules=True)
        reply = flow.process("我是什么级别的会员")
        self.assertIn("核验", reply)
        self.assertNotIn("张先生", reply)
        self.assertNotRegex(reply, r"请.*(?:卡号|联系方式|身份证|电话)")

    def test_disputed_name_is_not_asserted_as_identity(self):
        flow = self.flow()
        flow.process("我不是张先生，我是刘先生")
        with patch.object(flow.reception, "understand", side_effect=AssertionError("Bound facts are local")):
            reply = flow.process("我是谁")
        self.assertIn("1128", reply)
        self.assertIn("核验", reply)
        self.assertNotIn("张先生", reply)
        self.assertNotIn("您是刘先生", reply)
        self.assertEqual(flow.state.guest_profile["guest_name"], "张先生")

    def test_human_help_refusal_and_bare_number_are_clarified_without_orders(self):
        flow = self.flow()
        flow.process("送1条毛巾")
        before = deepcopy(flow.state.tickets), deepcopy(flow.housekeeping.inventory)
        with patch.object(flow.reception, "understand", side_effect=AssertionError("Clarifiers must not invent intent")):
            reply = flow.process("我需要你之外的帮助")
            self.assertIn("前台", reply)
            reply = flow.process("但我又不想让你帮助")
            self.assertIn("暂不", reply)
            reply = flow.process("1")
            self.assertIn("数字", reply)
        self.assertEqual((flow.state.tickets, flow.housekeeping.inventory), before)

    def test_footbath_and_gym_are_both_answered_without_fabricated_academic_hours(self):
        flow = self.flow()
        reply = flow.process("在哪可以洗脚，健身房几点关门")
        self.assertRegex(reply, r"足浴|洗脚")
        self.assertIn("健身房", reply)
        self.assertIn("核验", reply)
        self.assertNotRegex(reply, r"三楼|06:00|23:00")
        self.assertEqual(flow.state.tickets, [])
        self.assertEqual(flow.state.service_feedback[0]["topic"], "足浴服务")

    def test_multiple_catalog_services_all_receive_a_response(self):
        flow = self.flow()
        reply = flow.process("有没有按摩、泡澡和洗脚")
        for name in ("按摩", "泡澡", "足浴"):
            self.assertIn(name, reply)
        self.assertEqual(len(flow.state.service_feedback), 3)
        self.assertEqual(flow.state.tickets, [])

    def test_chitchat_model_cannot_solicit_identity_or_contact_details(self):
        flow = self.flow()
        flow.reception.model.disabled = False
        flow.reception.model.client = object()
        for output in ("请提供会员卡号或联系方式，以便为您查询。", "请提供您的手机号，以便查询会员等级。"):
            flow.reception.model.chat = lambda *_args, **_kwargs: output
            reply = flow.reception.generate_chitchat_reply("随便聊聊")
            self.assertNotRegex(reply, r"卡号|联系方式|手机号")

    def test_english_chitchat_does_not_match_spa_inside_space(self):
        flow = self.flow()
        flow.process("space is beautiful")
        self.assertEqual(flow.state.service_feedback, [])
        self.assertEqual(flow.state.tickets, [])

    def test_request_for_staff_does_not_prevent_explicit_cancellation(self):
        flow = self.flow()
        flow.process("送2条毛巾")
        flow.process("全部取消，转人工")
        self.assertEqual(flow.state.tickets[0]["status"], "已取消")
        self.assertEqual(flow.housekeeping.inventory["毛巾"], 24)

    def test_staff_and_member_questions_do_not_swallow_real_service(self):
        for text, category in (("请转人工，房间插座冒烟了", "工程报修"),
                               ("查一下我是什么等级的会员，再拿一条毛巾", "送物"),
                               ("来两瓶水，能洗脚吗", "送物")):
            with self.subTest(text=text):
                flow = self.flow()
                reply = flow.process(text)
                self.assertEqual(flow.state.tickets[0]["category"], category)
                self.assertIn("前台", reply)
                if category == "工程报修":
                    self.assertEqual(flow.state.tickets[0]["priority"], "高")
                    self.assertIn("远离", reply)

    def test_negated_unknown_and_pure_price_faq_do_not_block_a_valid_towel(self):
        for text, quantity in (("送两条毛巾，不要一盒饼干", 2), ("送2条毛巾，早餐一份多少钱", 2),
                               ("送毛巾，早餐两份多少钱", 1)):
            with self.subTest(text=text):
                flow = self.flow()
                reply = flow.process(text)
                self.assertEqual(flow.state.tickets[0]["inventory_item"], "毛巾")
                self.assertEqual(flow.state.tickets[0]["quantity"], quantity)
                self.assertEqual(flow.housekeeping.inventory["毛巾"], 24 - quantity)
                self.assertIn("已为您创建", reply)


if __name__ == "__main__":
    unittest.main()
