"""Exercise app control only against temporary apps written by this project."""

import json
from pathlib import Path
import plistlib
import select
import shutil
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "build/KakaoAppControl"


def fixture(root, name):
    app = root / (name + ".app")
    (app / "Contents/MacOS").mkdir(parents=True)
    shutil.copyfile(ROOT / "build/app-control-fixture", app / "Contents/MacOS/KakaoTalk")
    (app / "Contents/MacOS/KakaoTalk").chmod(0o755)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "org.local.KakaoAppControlFixture." + uuid.uuid4().hex,
        "CFBundleExecutable": "KakaoTalk", "CFBundleName": name, "CFBundlePackageType": "APPL",
        "LSUIElement": True,
    }))
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return app


def start(app, refuse=False):
    process = subprocess.Popen([str(app / "Contents/MacOS/KakaoTalk")] + (["--refuse"] if refuse else []),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ready, _, _ = select.select([process.stdout], [], [], 12)
    if not ready or process.stdout.readline().strip() != "READY":
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("Owned test app did not launch")
    return process


def control(action, app):
    return subprocess.run([str(HELPER), action, str(app)], capture_output=True, text=True, timeout=22)


def main():
    with tempfile.TemporaryDirectory(prefix="kakao-app-control-test-") as temporary:
        root = Path(temporary).resolve()
        target = fixture(root, "Target")
        other = fixture(root, "Other")
        processes = []
        try:
            a, b = start(target), start(other)
            processes += [a, b]
            result = control("stop", target)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["was_running"] is True, (result.stdout, result.stderr, a.poll(), b.poll())
            assert a.wait(timeout=5) == 0
            assert b.poll() is None, "Another app with the same executable name was stopped"
            result = control("stop", target)
            assert result.returncode == 0 and json.loads(result.stdout)["was_running"] is False

            result = control("open", target)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["pid"] > 0
            result = control("stop", target)
            assert result.returncode == 0 and json.loads(result.stdout)["was_running"] is True

            refused = start(target, refuse=True)
            processes.append(refused)
            result = control("stop", target)
            assert result.returncode != 0
            assert refused.poll() is None, "Quit refusal must never cause a force kill"
            print("PASS: exact-target normal quit, unrelated app preservation, already stopped, relaunch, refused quit")
        finally:
            for process in processes:
                if process.poll() is None:
                    # Only PIDs returned by our own fixture launches are cleaned up.
                    process.terminate()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            control("stop", target)


if __name__ == "__main__":
    main()
