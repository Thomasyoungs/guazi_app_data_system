from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


class RunQualityChecksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        script_path = cls.repo_root / "scripts" / "run_quality_checks.py"
        spec = importlib.util.spec_from_file_location("run_quality_checks", script_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.module = module

    def test_required_files_are_present(self) -> None:
        self.assertTrue(self.module.check_required_files(self.repo_root))

    def test_python_file_iterator_covers_governance_script_and_tests(self) -> None:
        files = {path.relative_to(self.repo_root).as_posix() for path in self.module.iter_python_files(self.repo_root)}
        self.assertIn("scripts/run_quality_checks.py", files)
        self.assertIn("guazi_app_data_system/tests/test_run_quality_checks.py", files)

    def test_test_environment_uses_safe_test_mode_and_current_python(self) -> None:
        env = self.module.test_environment(self.repo_root)
        self.assertEqual(env["GUAZI_TEST_MODE"], "1")
        self.assertIn("guazi_app_data_system", env["PYTHONPATH"])
        self.assertTrue(env["GUAZI_PYTHON"])


if __name__ == "__main__":
    unittest.main()
