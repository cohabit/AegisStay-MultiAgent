"""Offline parser contracts for oral modifiers and real product nouns."""
from __future__ import annotations

import unittest

from agent_protocol import normalize_delivery_text, parse_delivery_command


class LanguageModifierTests(unittest.TestCase):
    def test_discourse_marker_is_not_an_unknown_product(self):
        for text in ("那送两罐红牛吧", "那么请给我送两罐红牛吧", "那麻烦送一桶泡面吧"):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual(command.unknown_items, ("红牛" if "红牛" in text else "泡面",))

    def test_soft_water_quantity_and_final_particle_are_not_products(self):
        for text in ("再给我送点水吧", "送些水吧", "请送一点水吧", "麻烦送一些水吧", "送水吧"):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual((command.action, command.item, command.quantity), ("create", "矿泉水", None))
                self.assertEqual(command.unknown_items, ())

    def test_add_and_supplement_water_verbs_preserve_explicit_quantity(self):
        for text, quantity in (("再加一瓶水", 1), ("补一瓶水", 1), ("加两瓶水", 2)):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual((command.action, command.item, command.quantity), ("create", "矿泉水", quantity))
                self.assertEqual(command.unknown_items, ())

    def test_unquantified_unknown_addition_is_not_lost_alongside_known_item(self):
        for text in ("还要泡面", "再给我送点水吧，还要泡面", "送一条毛巾，还要泡面", "送一条毛巾还要泡面"):
            with self.subTest(text=text):
                self.assertEqual(parse_delivery_command(text).unknown_items, ("泡面",))

    def test_hairdryer_reminder_does_not_invent_product_or_quantity(self):
        command = parse_delivery_command("吹风机快点，我有点着急赶时间")
        self.assertEqual((command.item, command.quantity), ("吹风机", None))
        self.assertEqual(command.unknown_items, ())

    def test_soft_water_alias_does_not_rewrite_other_water_nouns(self):
        for text in ("送点热水吧", "送些洗发水吧", "热水不用了", "洗澡的水不用了", "水管漏水了"):
            with self.subTest(text=text):
                self.assertEqual(normalize_delivery_text(text), text)
                self.assertNotIn("矿泉水", parse_delivery_command(text).references)

    def test_water_quantity_and_order_controls_keep_their_meaning(self):
        for text, action, item, quantity in (
            ("送两瓶水吧", "create", "矿泉水", 2),
            ("送0瓶水吧", "create", "矿泉水", 0),
            ("不要送点水吧", "cancel", None, None),
            ("水不用了", "cancel", None, None),
            ("不要水了，改成浴巾", "replace", "浴巾", None),
        ):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual((command.action, command.item, command.quantity), (action, item, quantity))
                self.assertFalse(command.unresolved_target)
                self.assertEqual(command.unknown_items, ())


if __name__ == "__main__":
    unittest.main()
