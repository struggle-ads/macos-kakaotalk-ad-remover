import hashlib
import importlib
import os
from pathlib import Path
import plistlib
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_macho import thin, DYLIB


def module():
    try:
        return importlib.import_module("kakao_patch.app")
    except ModuleNotFoundError:
        raise AssertionError("App transaction has not been implemented") from None


class FilesystemTools:
    """Only macOS process/signature services are substituted; files are real."""
    def __init__(self):
        self.running = False
        self.signing_fails = False

    def assert_stopped(self, app):
        if self.running:
            raise module().PatchError("app is running")

    def verify_original(self, app):
        pass

    def verify_patched(self, app):
        pass

    def verify_payload(self, payload):
        pass

    def copy(self, source, destination):
        shutil.copytree(source, destination, symlinks=True)

    def sign(self, app, entitlements):
        if self.signing_fails:
            raise module().PatchError("signing failed")


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / "KakaoTalk.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/Resources").mkdir()
        (self.app / "Contents/Frameworks").mkdir()
        self.info = {"CFBundleIdentifier": "com.kakao.KakaoTalkMac", "CFBundleExecutable": "KakaoTalk",
                     "CFBundleShortVersionString": "26.9.0", "CFBundleVersion": "2006"}
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))
        self.original = thin(with_entitlements=True)
        self.executable = self.app / "Contents/MacOS/KakaoTalk"
        self.executable.write_bytes(self.original)
        data = bytearray(thin())
        struct.pack_into("<I", data, 12, 6)
        self.payload = self.root / "KakaoAdBlock.dylib"
        self.payload.write_bytes(data)
        self.tools = FilesystemTools()
        self.supported = {("26.9.0", "2006"): {"sha256": hashlib.sha256(self.original).hexdigest(), "nibs": {}}}

    def tearDown(self):
        self.temp.cleanup()

    def patcher(self):
        return module().Patcher(tools=self.tools, supported=self.supported)

    def apply(self):
        return self.patcher().apply(self.app, self.payload, accept_risk=True)

    def test_missing_risk_acceptance_has_no_filesystem_side_effect(self):
        m = module()
        before = sorted(self.root.iterdir())
        with self.assertRaises(m.PatchError):
            self.patcher().apply(self.app, self.payload, accept_risk=False)
        self.assertEqual(sorted(self.root.iterdir()), before)
        self.assertEqual(self.executable.read_bytes(), self.original)

    def test_apply_preserves_original_and_restore_recovers_exact_bytes(self):
        result = self.apply()
        self.assertIn(DYLIB.encode(), self.executable.read_bytes())
        self.assertEqual((Path(result["backup"]) / "Contents/MacOS/KakaoTalk").read_bytes(), self.original)
        self.assertEqual(self.patcher().status(self.app)["state"], "patched")
        self.patcher().restore(self.app)
        self.assertEqual(self.executable.read_bytes(), self.original)
        self.assertFalse((self.app / "Contents/Frameworks/KakaoAdBlock.dylib").exists())
        self.assertEqual(self.patcher().status(self.app)["state"], "unpatched")

    def test_unsupported_binary_is_not_modified(self):
        self.executable.write_bytes(self.original + b"modified")
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual(self.executable.read_bytes(), self.original + b"modified")

    def test_running_app_is_not_modified(self):
        self.tools.running = True
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual(self.executable.read_bytes(), self.original)

    def test_signing_failure_keeps_original_at_its_path(self):
        self.tools.signing_fails = True
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual(self.executable.read_bytes(), self.original)
        self.assertEqual(self.patcher().status(self.app)["state"], "unpatched")

    def test_failed_activation_rolls_original_back(self):
        replace = os.replace
        def fail_stage(source, destination):
            if Path(source).name == "staging.app" and Path(destination) == self.app:
                raise OSError("simulated activation failure")
            return replace(source, destination)
        with patch("os.replace", side_effect=fail_stage):
            with self.assertRaises(module().PatchError):
                self.apply()
        self.assertEqual(self.executable.read_bytes(), self.original)

    def test_restore_recovers_interrupted_activation(self):
        result = self.apply()
        state = Path(result["backup"]).parent
        os.replace(self.app, state / "staging.app")
        self.assertEqual(self.patcher().status(self.app)["state"], "interrupted")
        self.patcher().restore(self.app)
        self.assertEqual(self.executable.read_bytes(), self.original)

    def test_restore_does_not_overwrite_an_app_store_update(self):
        result = self.apply()
        self.executable.write_bytes(b"new official version")
        with self.assertRaises(module().PatchError):
            self.patcher().restore(self.app)
        self.assertEqual(self.executable.read_bytes(), b"new official version")
        self.assertTrue(Path(result["backup"]).exists())

    def test_symlinked_state_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".KakaoTalk.app.kakao-ad-remover").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_framework_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        frameworks = self.app / "Contents/Frameworks"
        frameworks.rmdir()
        frameworks.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    def test_repeated_apply_refuses_to_replace_original_backup(self):
        result = self.apply()
        with self.assertRaises(module().PatchError):
            self.apply()
        self.assertEqual((Path(result["backup"]) / "Contents/MacOS/KakaoTalk").read_bytes(), self.original)

    def test_restore_after_completed_swap_cleans_only_owned_artifacts(self):
        result = self.apply()
        state = Path(result["backup"]).parent
        os.replace(self.app, state / "discard.app")
        os.replace(state / "original.app", self.app)
        self.patcher().restore(self.app)
        self.assertEqual(self.executable.read_bytes(), self.original)
        self.assertFalse((state / "discard.app").exists())


if __name__ == "__main__":
    unittest.main()
