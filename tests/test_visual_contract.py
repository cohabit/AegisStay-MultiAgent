"""Mechanical checks for the current Streamlit visual contract."""
from __future__ import annotations

import unittest
import re
from pathlib import Path
from unittest.mock import patch

from app import THEME_PALETTES, _style


APP_SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def _relative_luminance(color: str) -> float:
    rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _computed_styles(css, palette, nodes):
    """Evaluate the descendant-selector cascade for a native control DOM.

    This deliberately checks specificity, source order, inheritance and the
    text-fill override; actual browser rendering is a separate release check.
    Selectors outside this small contract are ignored rather than guessed.
    """
    def matches(token, node):
        attributes = re.findall(r'\[([\w-]+)(?:="([^"]*)")?\]', token)
        classes = re.findall(r'\.([\w-]+)', token)
        tag = re.match(r'^[a-zA-Z][\w-]*', token)
        remainder = re.sub(r'\[[^\]]+\]|\.[\w-]+|:disabled', '', token)
        if remainder not in ("", "*") and (not tag or remainder != tag[0]):
            return False
        return ((not tag or tag[0] == node["tag"])
                and all(node.get(name) == value if value else name in node for name, value in attributes)
                and all(name in node.get("class", "").split() for name in classes)
                and (":disabled" not in token or node.get("disabled", False)))

    def selected(selector, target):
        parts = selector.split()
        if not parts or not matches(parts[-1], nodes[target]):
            return False
        ancestor = target - 1
        for token in reversed(parts[:-1]):
            while ancestor >= 0 and not matches(token, nodes[ancestor]):
                ancestor -= 1
            if ancestor < 0:
                return False
            ancestor -= 1
        return True

    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    rules = []
    for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css.replace("<style>", "").replace("</style>", "")):
        for selector in selectors.split(","):
            selector = selector.strip()
            specificity = (len(re.findall(r'\.[\w-]+|\[[^\]]+\]|:disabled', selector)),
                           len(re.findall(r'(?:^|\s)[a-zA-Z][\w-]*', selector)))
            for declaration in declarations.split(";"):
                if ":" not in declaration:
                    continue
                name, value = (part.strip() for part in declaration.split(":", 1))
                if name not in {"color", "-webkit-text-fill-color", "background", "background-color"}:
                    continue
                important = "!important" in value
                value = value.replace("!important", "").strip()
                rules.append((selector, specificity, important, "background" if name == "background-color" else name, value))

    inherited = {"color": "#000000", "-webkit-text-fill-color": "currentColor"}
    results = []
    variables = {"--as-" + name.replace("_", "-"): value for name, value in palette.items()}
    for target in range(len(nodes)):
        computed = {**inherited, "background": "transparent"}
        winners = {}
        for order, (selector, specificity, important, name, value) in enumerate(rules):
            rank = (important, specificity, order)
            if selected(selector, target) and rank > winners.get(name, (False, (-1, -1), -1)):
                winners[name] = rank
                variable = re.fullmatch(r'var\((--[\w-]+)\)', value)
                computed[name] = variables[variable[1]] if variable else value
        inherited = {name: computed[name] for name in ("color", "-webkit-text-fill-color")}
        results.append(computed)
    return results


def _button_styles(css, palette, *, sidebar, submit, disabled):
    nodes = [{"tag": "div", "class": "stApp"}]
    if sidebar:
        nodes.append({"tag": "section", "data-testid": "stSidebar"})
    nodes.extend([
        {"tag": "div", "data-testid": "stExpander"}, {"tag": "details"},
        {"tag": "div", "class": "stFormSubmitButton" if submit else "stButton",
         "data-testid": "stFormSubmitButton" if submit else "stButton"},
        {"tag": "button", "kind": "primary", "disabled": disabled},
        {"tag": "div", "data-testid": "stMarkdownContainer"},
        {"tag": "p"}, {"tag": "span"},
    ])
    return _computed_styles(css, palette, nodes)[-4:]


class VisualContractTests(unittest.TestCase):
    def test_native_help_portal_and_descendants_have_theme_contrast(self):
        # Streamlit 1.66 Tooltip creates a body portal, outside .stApp. Start
        # from a light framework theme, including explicit Markdown/code colors,
        # so an app-only selector or an inherited color cannot hide this bug.
        framework_css = '''
            [role="tooltip"] { color: #31333f; }
            [data-testid="stTooltipContent"],
            [data-testid="stTooltipErrorContent"] { background: #ffffff; }
            [role="tooltip"] p, [role="tooltip"] span,
            [role="tooltip"] code, [role="tooltip"] a,
            [role="tooltip"] strong, [role="tooltip"] em {
                color: #31333f; -webkit-text-fill-color: #31333f;
            }
            [role="tooltip"] code { background: #f0f2f6; }
        '''
        for theme, palette in THEME_PALETTES.items():
            with patch("app.st.markdown") as markdown:
                _style(theme)
            css = framework_css + markdown.call_args.args[0]
            for content_id in ("stTooltipContent", "stTooltipErrorContent"):
                for leaf in ("span", "strong", "em", "code", "a"):
                    with self.subTest(theme=theme, content=content_id, leaf=leaf):
                        nodes = [
                            {"tag": "body"}, {"tag": "div", "data-overlay-container": "true"},
                            {"tag": "div", "role": "tooltip"},
                            {"tag": "div", "data-testid": content_id},
                            {"tag": "div", "data-testid": "stMarkdownContainer"},
                            {"tag": "p"}, {"tag": leaf},
                        ]
                        styles = _computed_styles(css, palette, nodes)[3:]
                        self.assertEqual(styles[0]["background"], palette["surface"])
                        painted_background = styles[0]["background"]
                        for computed in styles:
                            if computed["background"] != "transparent":
                                painted_background = computed["background"]
                            self.assertEqual(computed["color"], palette["text"])
                            self.assertEqual(computed["-webkit-text-fill-color"], palette["text"])
                            self.assertGreaterEqual(_contrast(computed["color"], painted_background), 4.5)
                            self.assertGreaterEqual(_contrast(computed["-webkit-text-fill-color"], painted_background), 4.5)

    def test_all_themes_meet_text_and_button_contrast(self):
        self.assertEqual(set(THEME_PALETTES), {"日间", "护眼", "夜间"})
        for theme, palette in THEME_PALETTES.items():
            with self.subTest(theme=theme, field="text"):
                self.assertGreaterEqual(_contrast(palette["text"], palette["bg"]), 4.5)
            with self.subTest(theme=theme, field="muted"):
                self.assertGreaterEqual(_contrast(palette["muted"], palette["bg"]), 4.5)
            with self.subTest(theme=theme, field="accent_text"):
                self.assertGreaterEqual(_contrast(palette["accent_text"], palette["accent"]), 4.5)
            with self.subTest(theme=theme, field="surface_text"):
                self.assertGreaterEqual(_contrast(palette["text"], palette["surface"]), 4.5)
            for status in ("occ", "clean", "dirty", "repair"):
                with self.subTest(theme=theme, field=status):
                    self.assertGreaterEqual(_contrast(palette[f"{status}_text"], palette[f"{status}_bg"]), 4.5)

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
        self.assertIn('client-history-panel', APP_SOURCE)
        self.assertIn('height=300', APP_SOURCE)
        self.assertIn('guest-workspace-marker', APP_SOURCE)
        self.assertIn('guest-backdrop', APP_SOURCE)
        self.assertIn('guest-campus-banner', APP_SOURCE)
        self.assertIn('height:96px', APP_SOURCE)
        self.assertIn('padding-top:4.5rem', APP_SOURCE)
        self.assertIn('@media (max-height: 760px)', APP_SOURCE)
        self.assertIn('flex:0 0 var(--as-chat-history-height)', APP_SOURCE)
        self.assertIn(':has(.guest-workspace-marker)', APP_SOURCE)
        self.assertIn('.stBottom { display: none !important; }', APP_SOURCE)
        self.assertIn('[data-testid="stExpander"] details', APP_SOURCE)
        self.assertIn('[data-testid="stRadio"]', APP_SOURCE)
        self.assertIn('client-composer', APP_SOURCE)
        self.assertIn('client-chat-stage-active', APP_SOURCE)
        self.assertIn('入住凭证', APP_SOURCE)
        self.assertIn('@st.fragment(run_every=1.0)\ndef _render_entry_conversation', APP_SOURCE)

    def test_staff_workspace_is_compact_and_image_free(self):
        staff_source = APP_SOURCE.split('def _render_staff', 1)[1].split('def _render_', 1)[0]
        self.assertIn('workspace-header', APP_SOURCE)
        self.assertNotIn('_render_photo_strip()', staff_source)

    def test_hotel_simulation_contains_shared_state_facilities(self):
        for marker in ("hotel-sim-shell", "hotel-building-overview", "building-floor-row", "sim-facility-elevator", "安全楼梯", "布草服务", "pms", "tickets"):
            with self.subTest(marker=marker):
                self.assertIn(marker, APP_SOURCE)
        self.assertIn("酒店空间仿真 · 全楼概览", APP_SOURCE)

    def test_primary_and_disabled_button_css_are_theme_aware(self):
        for theme, palette in THEME_PALETTES.items():
            with patch("app.st.markdown") as markdown:
                _style(theme)
            css = markdown.call_args.args[0]
            for sidebar in (False, True):
                for submit in (False, True):
                    for disabled in (False, True):
                        with self.subTest(theme=theme, sidebar=sidebar, submit=submit, disabled=disabled):
                            foreground = palette["muted" if disabled else "accent_text"]
                            background = palette["surface_soft" if disabled else "accent"]
                            for computed in _button_styles(css, palette, sidebar=sidebar, submit=submit, disabled=disabled):
                                self.assertEqual(computed["color"], foreground)
                                self.assertEqual(computed["-webkit-text-fill-color"], foreground)
                                self.assertEqual(computed["background"], background)
                                self.assertGreaterEqual(_contrast(computed["-webkit-text-fill-color"], computed["background"]), 4.5)


if __name__ == "__main__":
    unittest.main()
