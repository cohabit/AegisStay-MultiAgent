"""Mechanical checks for the current Streamlit visual contract."""
from __future__ import annotations

import unittest
from pathlib import Path

from app import THEME_PALETTES


APP_SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def _relative_luminance(color: str) -> float:
    rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class VisualContractTests(unittest.TestCase):
    def test_all_themes_meet_text_and_button_contrast(self):
        self.assertEqual(set(THEME_PALETTES), {"日间", "护眼", "夜间"})
        for theme, palette in THEME_PALETTES.items():
            with self.subTest(theme=theme, field="text"):
                self.assertGreaterEqual(_contrast(palette["text"], palette["bg"]), 4.5)
            with self.subTest(theme=theme, field="muted"):
                self.assertGreaterEqual(_contrast(palette["muted"], palette["bg"]), 4.5)
            with self.subTest(theme=theme, field="accent_text"):
                self.assertGreaterEqual(_contrast(palette["accent_text"], palette["accent"]), 4.5)

    def test_room_grid_and_placeholder_contract(self):
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", APP_SOURCE)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", APP_SOURCE)
        self.assertIn("room-tile-empty", APP_SOURCE)
        self.assertRegex(APP_SOURCE, r"remainder\s*=\s*len\(tiles\)\s*%\s*6")

    def test_workspace_routing_keeps_operations_out_of_guest_client(self):
        self.assertIn('"住客端"', APP_SOURCE)
        self.assertIn('"运营中枢"', APP_SOURCE)
        self.assertIn('workspace-page', APP_SOURCE)
        self.assertIn('if workspace == "住客端"', APP_SOURCE)
        self.assertIn('_render_staff(orchestrator)', APP_SOURCE)
        self.assertNotIn('client_chat, client_board = st.tabs', APP_SOURCE)
        self.assertNotIn('"🧠 会话黑板"', APP_SOURCE)
        self.assertEqual(APP_SOURCE.count('st.form("entry-conversation-form"'), 1)
        self.assertEqual(APP_SOURCE.count('key="guest-prompt"'), 1)
        self.assertIn('[data-testid="stChatInput"] { display: none !important; }', APP_SOURCE)
        self.assertIn('placeholder="描述送物、报修、退房或咨询需求"', APP_SOURCE)
        self.assertNotIn('例如：1128 送两条毛巾', APP_SOURCE)
        self.assertNotIn('水不用了\'来取消矿泉水', APP_SOURCE)

    def test_client_chat_has_empty_and_active_layout_contract(self):
        self.assertIn('client-chat-stage', APP_SOURCE)
        self.assertIn('client-chat-history', APP_SOURCE)
        self.assertIn('client-composer', APP_SOURCE)
        self.assertIn('client-chat-stage-active', APP_SOURCE)
        self.assertIn('入住凭证', APP_SOURCE)

    def test_hotel_simulation_contains_shared_state_facilities(self):
        for marker in ("hotel-sim-shell", "hotel-building-overview", "building-floor-row", "sim-facility-elevator", "安全楼梯", "布草服务", "pms", "tickets"):
            with self.subTest(marker=marker):
                self.assertIn(marker, APP_SOURCE)
        self.assertIn("酒店空间仿真 · 全楼概览", APP_SOURCE)

    def test_primary_and_disabled_button_css_are_theme_aware(self):
        self.assertIn("color: var(--as-accent-text)", APP_SOURCE)
        self.assertIn(".stApp button:disabled", APP_SOURCE)
        self.assertNotIn("button[kind=\"primary\"] { color: #fff", APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
