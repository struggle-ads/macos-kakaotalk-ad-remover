from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_help_explains_risk_acceptance(self):
        result = subprocess.run([sys.executable, str(ROOT / "patcher.py"), "apply", "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--accept-risk", result.stdout)

    def test_apply_requires_explicit_acceptance_before_inspecting_an_app(self):
        result = subprocess.run([sys.executable, str(ROOT / "patcher.py"), "apply", "--app", "/does-not-exist/KakaoTalk.app"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--accept-risk", result.stderr)
        self.assertIn("이용 제한", result.stderr)


if __name__ == "__main__":
    unittest.main()
