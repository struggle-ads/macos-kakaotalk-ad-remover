import importlib
import os
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kakao_patch.app import PatchError


def module():
    try:
        return importlib.import_module("kakao_patch.install")
    except ModuleNotFoundError:
        raise AssertionError("One-command installer has not been implemented") from None


class FakeServices:
    def __init__(self, state="unpatched", running=True):
        self.state = state
        self.running = running
        self.events = []
        self.quit_error = False
        self.apply_error = False
        self.launch_error = False

    def status(self, app):
        self.events.append("status")
        return {"state": self.state, "reason": "unsupported fixture"}

    def prepare(self):
        self.events.append("prepare")
        return Path("/fixture/KakaoAdBlock.dylib")

    def stop(self, app):
        self.events.append("stop")
        if self.quit_error:
            raise PatchError("normal quit refused")
        return self.running

    def apply(self, app, payload):
        self.events.append("apply")
        if self.apply_error:
            raise PatchError("signing failed")
        return {"state": "patched"}

    def restore(self, app):
        self.events.append("restore")
        return {"state": "restored"}

    def relaunch(self, app):
        self.events.append("relaunch")
        if self.launch_error:
            raise PatchError("launch denied")


class InstallTests(unittest.TestCase):
    def test_end_of_input_cancels_the_confirmation(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=EOFError):
            self.assertFalse(module().confirm_interactively())

    def installer(self, services, consent=True):
        return module().Installer(services=services, confirm=lambda: consent, output=lambda message: None)

    def test_declining_consent_does_not_build_or_touch_an_app(self):
        services = FakeServices()
        result = self.installer(services, False).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(services.events, [])

    def test_one_invocation_prepares_quits_patches_and_reopens(self):
        services = FakeServices()
        result = self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(result["state"], "patched")
        self.assertEqual(services.events, ["status", "prepare", "stop", "apply", "relaunch"])

    def test_an_app_that_was_closed_is_not_opened(self):
        services = FakeServices(running=False)
        self.installer(services).run(Path("/fixture/KakaoTalk.app"), accept_risk=True)
        self.assertEqual(services.events, ["status", "prepare", "stop", "apply"])

    def test_explicit_acceptance_does_not_prompt_again(self):
        services = FakeServices()
        def no_prompt():
            self.fail("explicit acceptance must not prompt")
        installer = module().Installer(services=services, confirm=no_prompt, output=lambda message: None)
        self.assertEqual(installer.run(Path("/fixture/KakaoTalk.app"), accept_risk=True)["state"], "patched")

    def test_unsupported_app_is_not_built_or_quit(self):
        services = FakeServices(state="unsupported")
        with self.assertRaises(PatchError):
            self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(services.events, ["status"])

    def test_already_patched_app_is_left_running(self):
        services = FakeServices(state="patched")
        result = self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(result["state"], "patched")
        self.assertEqual(services.events, ["status"])

    def test_refused_quit_never_applies_or_force_kills(self):
        services = FakeServices()
        services.quit_error = True
        with self.assertRaises(PatchError):
            self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(services.events, ["status", "prepare", "stop"])

    def test_failed_patch_is_not_relaunched(self):
        services = FakeServices()
        services.apply_error = True
        with self.assertRaises(PatchError):
            self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertNotIn("relaunch", services.events)

    def test_failed_relaunch_preserves_the_successful_patch_result(self):
        services = FakeServices()
        services.launch_error = True
        result = self.installer(services).run(Path("/fixture/KakaoTalk.app"))
        self.assertEqual(result["state"], "patched")
        self.assertIn("launch_warning", result)

    def test_restore_uses_the_same_single_command_flow(self):
        services = FakeServices(state="patched")
        result = self.installer(services, False).run(Path("/fixture/KakaoTalk.app"), restore=True)
        self.assertEqual(result["state"], "restored")
        self.assertEqual(services.events, ["status", "prepare", "stop", "restore", "relaunch"])

    def test_changed_app_is_not_quit_for_restore(self):
        services = FakeServices(state="changed")
        with self.assertRaises(PatchError):
            self.installer(services).run(Path("/fixture/KakaoTalk.app"), restore=True)
        self.assertEqual(services.events, ["status"])

    def test_no_relaunch_option_keeps_the_app_closed(self):
        services = FakeServices()
        self.installer(services).run(Path("/fixture/KakaoTalk.app"), relaunch=False)
        self.assertNotIn("relaunch", services.events)


class ArtifactCacheTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "uses the real Apple build tools")
    def test_build_uses_the_selected_python_when_path_python_is_broken(self):
        m = module()
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("src", "scripts", "bad-bin"):
                (root / folder).mkdir()
            for name in m.BUILD_INPUTS:
                shutil.copyfile(project / name, root / name)
            shutil.copytree(project / "kakao_patch", root / "kakao_patch", ignore=shutil.ignore_patterns("__pycache__"))
            broken = root / "bad-bin/python3"
            broken.write_text("#!/bin/sh\nexit 73\n")
            broken.chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(root / "bad-bin") + ":/usr/bin:/bin", "KAKAO_PATCHER_PYTHON": ""}):
                payload = m.InstallServices(root).prepare()
            self.assertTrue(payload.is_file())
            self.assertTrue(m.artifacts_current(root))

    def test_packaged_helper_regains_execute_permission_without_rebuilding(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in m.BUILD_INPUTS + m.BUILD_OUTPUTS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            helper = root / "build/KakaoAppControl"
            helper.chmod(0o644)
            m.record_artifacts(root)
            payload = m.InstallServices(root).prepare()
            self.assertEqual(payload, root / "build/KakaoAdBlock.dylib")
            self.assertTrue(os.access(helper, os.X_OK))

    def test_source_or_binary_changes_invalidate_the_build_cache(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in m.BUILD_INPUTS + m.BUILD_OUTPUTS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            self.assertFalse(m.artifacts_current(root))
            m.record_artifacts(root)
            self.assertTrue(m.artifacts_current(root))
            (root / "src/AdBlock.m").write_text("new implementation")
            self.assertFalse(m.artifacts_current(root))
            m.record_artifacts(root)
            self.assertTrue(m.artifacts_current(root))
            (root / "build/KakaoAppControl").write_bytes(b"changed executable")
            self.assertFalse(m.artifacts_current(root))

    def test_broken_manifest_requests_rebuild(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            (root / "build/artifacts.json").write_text("not json")
            self.assertFalse(m.artifacts_current(root))


if __name__ == "__main__":
    unittest.main()
