import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


def target(config_model: str) -> dict:
    return {
        "brand": "欧拉",
        "series": "黑猫",
        "model_year": "2019款",
        "trim": config_model,
    }


def node(label: str, bounds: list[int]) -> dict:
    return {"labels": [label], "bounds": bounds, "enabled": True, "clickable": True}


def card_nodes(title: str, metadata: str, price: str, y: int) -> list[dict]:
    return [
        node(title, [40, y, 980, y + 56]),
        node(metadata, [40, y + 72, 820, y + 116]),
        node(price, [720, y + 132, 820, y + 184]),
        node("万", [816, y + 132, 868, y + 184]),
    ]


class S10SecondStageHandoffTitleMatchTest(unittest.TestCase):
    def test_target_config_with_brand_series_prefix_matches_live_card_title(self):
        audit = runtime.match_reference_title_by_normalized_alias(
            "欧拉黑猫 2019款 351km 亲子版",
            target("欧拉黑猫 351km 亲子版"),
        )

        self.assertTrue(audit["title_normalized_match"], audit)
        self.assertEqual(audit["matched_config_model_candidate"], "351km亲子版")

    def test_target_config_with_year_brand_series_prefix_matches_live_card_title(self):
        audit = runtime.match_reference_title_by_normalized_alias(
            "欧拉黑猫 2019款 351km 亲子版",
            target("2019款 欧拉黑猫 351km 亲子版"),
        )

        self.assertTrue(audit["title_normalized_match"], audit)
        self.assertEqual(audit["matched_config_model_candidate"], "351km亲子版")

    def test_target_config_without_prefix_matches_live_card_title(self):
        audit = runtime.match_reference_title_by_normalized_alias(
            "欧拉黑猫 2019款 351km 亲子版",
            target("351km 亲子版"),
        )

        self.assertTrue(audit["title_normalized_match"], audit)

    def test_target_config_keeps_strict_trim_boundary(self):
        false_titles = [
            "欧拉黑猫 2019款 351km 女神版",
            "欧拉黑猫 2019款 351km 灵智版",
            "欧拉黑猫 2019款 351km 灵睿版",
            "欧拉黑猫 2019款 351km 灵趣版",
            "欧拉黑猫 2019款 301km 标准版",
            "欧拉黑猫 2019款 310km 灵动版",
            "欧拉黑猫 2019款 405km 公务版",
        ]

        for title in false_titles:
            with self.subTest(title=title):
                audit = runtime.match_reference_title_by_normalized_alias(title, target("351km 亲子版"))
                self.assertFalse(audit["title_normalized_match"], audit)

    def test_mock_s10_page_extracts_three_ora_black_cat_trisame_cards(self):
        nodes = [node("价格从低到高", [20, 20, 280, 80])]
        nodes.extend(card_nodes("欧拉黑猫 2019款 351km 亲子版", "2020年 | 7.27万公里 | 长沙", "3.14", 120))
        nodes.extend(card_nodes("欧拉黑猫 2019款 351km 亲子版", "2020年 | 4.67万公里 | 肇庆", "3.33", 420))
        nodes.extend(card_nodes("欧拉黑猫 2019款 351km 亲子版", "2020年 | 5.43万公里 | 唐山", "3.48", 720))
        nodes.extend(card_nodes("欧拉黑猫 2019款 310km 灵趣版", "2020年 | 6.00万公里 | 上海", "3.80", 1020))
        nodes.append(node("找不到想要的车？", [40, 980, 980, 1030]))
        nodes.append(node("viewport", [0, 0, 1080, 1800]))
        snapshot = {
            "nodes": nodes,
            "target_brand": "欧拉",
            "target_car": target("欧拉黑猫 351km 亲子版"),
        }

        cards = runtime._extract_s10_reference_cards(snapshot)
        audit = snapshot["s10_reference_order_audit"]

        self.assertGreater(len(cards), 0)
        self.assertEqual(audit["trisame_cards_count"], 3)
        self.assertGreaterEqual(audit["raw_visible_cards_count"], 3)
        self.assertNotIn(
            "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            [card.get("stop_code") for card in cards],
        )


if __name__ == "__main__":
    unittest.main()
