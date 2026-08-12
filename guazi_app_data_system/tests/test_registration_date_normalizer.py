import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from registration_date_normalizer import normalize_registration_date  # noqa: E402


class RegistrationDateNormalizerTest(unittest.TestCase):
    def assert_normalizes_to_2022_08(self, value):
        result = normalize_registration_date(value)

        self.assertIsNotNone(result)
        self.assertEqual(result.normalized_date, "2022.08")
        self.assertEqual(result.year, 2022)
        self.assertEqual(result.month, 8)

    def test_supported_registration_date_formats(self):
        for value in [
            "22.8",
            "22.08",
            "2022.8",
            "2022.08",
            "2022-08",
            "2022/08",
            "2022\u5e748\u6708",
            "2022\u5e7408\u6708",
        ]:
            with self.subTest(value=value):
                self.assert_normalizes_to_2022_08(value)

    def test_invalid_registration_date_is_rejected(self):
        for value in ["", "abc", "2022.13", "202.08"]:
            with self.subTest(value=value):
                self.assertIsNone(normalize_registration_date(value))


if __name__ == "__main__":
    unittest.main()
