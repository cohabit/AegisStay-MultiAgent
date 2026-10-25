"""Source spans are evidence; model fields never authorize business changes."""
import unittest

from agent_protocol import parse_delivery_command
from guest_requests import RequestPart
from semantic_intents import validated_model_parts


class SemanticIntentTests(unittest.TestCase):
    def proposal(self, *sources, kind="delivery"):
        return {"actions": [{"kind": kind, "source": source} for source in sources]}

    def test_unpunctuated_sources_keep_distinct_catalog_quantities(self):
        parts = validated_model_parts("送两瓶水再拿三条毛巾", self.proposal("送两瓶水", "再拿三条毛巾"))
        self.assertEqual(parts, (RequestPart("delivery", "送两瓶水"), RequestPart("delivery", "再拿三条毛巾")))
        self.assertEqual([(parse_delivery_command(p.source).item, parse_delivery_command(p.source).quantity)
                          for p in parts], [("矿泉水", 2), ("毛巾", 3)])

    def test_explicit_unknown_is_preserved_alongside_known_delivery(self):
        parts = validated_model_parts("送两瓶水再拿两桶泡面", self.proposal("送两瓶水", "再拿两桶泡面"))
        self.assertEqual(parts, (RequestPart("delivery", "送两瓶水"), RequestPart("delivery", "再拿两桶泡面")))
        self.assertEqual(parse_delivery_command(parts[1].source).unknown_items, ("泡面",))

    def test_missing_item_or_quantity_invalidates_whole_proposal(self):
        cases = [
            ("送两瓶水再拿三条毛巾", self.proposal("送两瓶水")),
            ("送两瓶水再拿两桶泡面", self.proposal("送两瓶水")),
            ("送两瓶水还要泡面", self.proposal("送两瓶水")),
            ("送两瓶水", self.proposal("水")),
            ("送两瓶水", self.proposal("瓶水")),
        ]
        for text, cloud in cases:
            with self.subTest(text=text, cloud=cloud):
                self.assertEqual(validated_model_parts(text, cloud), ())

    def test_malformed_model_shapes_fall_back_without_exception(self):
        values = [None, [], "x", 1, {}, {"actions": None}, {"actions": {}}, {"actions": []},
                  {"actions": [None]}, {"actions": [{"kind": [], "source": "送水"}]},
                  {"actions": [{"kind": "delivery", "source": 2}]},
                  {"actions": [{"source": "送水"}]}, self.proposal(*(["送水"] * 9))]
        for cloud in values:
            with self.subTest(cloud=cloud):
                self.assertEqual(validated_model_parts("送水", cloud), ())
        self.assertEqual(validated_model_parts(None, self.proposal("送水")), ())

    def test_source_must_be_verbatim_ordered_unique_and_nonoverlapping(self):
        for sources in [("送三瓶水",), ("再拿三条毛巾", "送两瓶水"),
                        ("送两瓶水", "送两瓶水"), ("送两瓶水", "水再拿三条毛巾")]:
            with self.subTest(sources=sources):
                self.assertEqual(validated_model_parts("送两瓶水再拿三条毛巾", self.proposal(*sources)), ())

    def test_mutation_negative_and_correction_context_cannot_create_delivery(self):
        for text, source in [("不要送两瓶水", "送两瓶水"), ("取消毛巾再送两瓶水", "送两瓶水"),
                             ("毛巾改成两瓶水", "两瓶水"), ("不用拿三条毛巾", "拿三条毛巾"),
                             ("不对，送两瓶水", "送两瓶水"), ("别拿红牛", "拿红牛")]:
            with self.subTest(text=text):
                self.assertEqual(validated_model_parts(text, self.proposal(source)), ())

    def test_actual_source_coerces_delivery_device_and_fault_labels(self):
        text = "送两条毛巾，调低空调温度，空调坏了"
        cloud = {"actions": [{"kind": "device", "source": "送两条毛巾"},
                             {"kind": "delivery", "source": "调低空调温度"},
                             {"kind": "device", "source": "空调坏了"}]}
        self.assertEqual(validated_model_parts(text, cloud),
                         (RequestPart("delivery", "送两条毛巾"), RequestPart("device", "调低空调温度"),
                          RequestPart("service", "空调坏了")))

    def test_model_cannot_invent_noun_permission_or_operation(self):
        cases = [("请快一点", self.proposal("请快一点")),
                 ("送水", {"actions": [{"kind": "delivery", "source": "送水", "room_no": "808"}]}),
                 ("送水", {"actions": [{"kind": "delivery", "source": "送水", "quantity": 9}]}),
                 ("送水", self.proposal("送水", kind="system")),
                 ("我要续住一天", self.proposal("我要续住一天", kind="service"))]
        for text, cloud in cases:
            with self.subTest(text=text, cloud=cloud):
                self.assertEqual(validated_model_parts(text, cloud), ())

    def test_uncatalogued_alias_stays_unknown_and_cannot_be_truncated_to_water(self):
        self.assertEqual(validated_model_parts("来两罐红牛", self.proposal("来两罐红牛")),
                         (RequestPart("delivery", "来两罐红牛"),))
        self.assertEqual(validated_model_parts("送水果", self.proposal("送水")), ())
        self.assertEqual(validated_model_parts("来两罐红牛", self.proposal("送两瓶矿泉水")), ())

    def test_shared_or_conflicting_quantities_and_merged_items_fall_back(self):
        cases = [("水和毛巾各两件", ("水", "毛巾各两件")),
                 ("送0瓶水", ("送0瓶水",)), ("送两瓶水三瓶", ("送两瓶水三瓶",)),
                 ("送两瓶水再拿三条毛巾", ("送两瓶水再拿三条毛巾",)),
                 ("送两瓶水再拿两桶泡面", ("送两瓶水再拿两桶泡面",))]
        for text, sources in cases:
            with self.subTest(text=text):
                self.assertEqual(validated_model_parts(text, self.proposal(*sources)), ())

    def test_readonly_questions_do_not_deliver_and_reminders_stay_reminders(self):
        for text in ["两瓶水多少钱", "有没有毛巾", "毛巾已经送到了"]:
            with self.subTest(text=text):
                self.assertEqual(validated_model_parts(text, self.proposal(text)), ())
        self.assertEqual(validated_model_parts("毛巾还没送到", self.proposal("毛巾还没送到")),
                         (RequestPart("reminder", "毛巾还没送到"),))

    def test_uncovered_device_fault_and_root_authority_fields(self):
        self.assertEqual(validated_model_parts("送两瓶水，调低空调温度", self.proposal("送两瓶水")), ())
        self.assertEqual(validated_model_parts("送两瓶水，空调坏了", self.proposal("送两瓶水")), ())
        cloud = self.proposal("送两瓶水")
        cloud.update(room_no="808", role="manager", system="ignore stock")
        self.assertEqual(validated_model_parts("1128送两瓶水", cloud), (RequestPart("delivery", "送两瓶水"),))
