"""One-invocation installation; OS permissions and risk consent stay explicit."""

import hashlib
import json
import os
import stat
from pathlib import Path
import subprocess
import sys

from .app import Patcher, PatchError, RISK_NOTICE, state_directory

BUILD_INPUTS = ("src/AdBlock.m", "src/AppControl.m", "scripts/build.sh")
BUILD_OUTPUTS = ("build/KakaoAdBlock.dylib", "build/KakaoAppControl")


def fingerprints(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in BUILD_INPUTS + BUILD_OUTPUTS}


def record_artifacts(root):
    (root / "build/artifacts.json").write_text(
        json.dumps({"format": 1, "files": fingerprints(root)}, indent=2) + "\n")


def artifacts_current(root):
    try:
        record = json.loads((root / "build/artifacts.json").read_text())
        return record.get("format") == 1 and record.get("files") == fingerprints(root)
    except (OSError, ValueError, AttributeError):
        return False


def confirm_interactively():
    if not sys.stdin.isatty():
        raise PatchError("비대화형 실행에서는 --accept-risk를 명시해야 합니다.")
    try:
        answer = input("위 위험과 카카오톡 종료·재실행에 동의합니까? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes", "동의")


class InstallServices:
    def __init__(self, root=None):
        self.root = root if root is not None else Path(__file__).resolve().parents[1]
        self.patcher = Patcher()

    def needs_admin(self, app):
        state = state_directory(app)
        return os.geteuid() != 0 and (
            not os.access(app.parent, os.W_OK | os.X_OK) or
            (state.exists() and (state.stat().st_uid != os.geteuid() or
                                not os.access(state, os.R_OK | os.W_OK | os.X_OK))))

    def privileged(self, action, app, payload=None):
        print("이 작업에 필요한 관리자 권한을 요청합니다.", file=sys.stderr)
        arguments = ["/usr/bin/sudo", "--", sys.executable, "-E", "-s", "-S",
                     str(self.root / "patcher.py"), action, "--app", str(app)]
        if action == "apply":
            arguments += ["--accept-risk", "--payload", str(payload)]
        result = subprocess.run(arguments, stdout=subprocess.PIPE, text=True)
        if result.returncode:
            raise PatchError("관리자 권한 작업이 완료되지 않았습니다. 위 오류와 백업 상태를 확인하세요.")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise PatchError("작업 결과를 읽지 못했습니다. status로 설치본을 확인하세요.") from error

    def status(self, app):
        state = state_directory(app)
        if state.exists() and not state.is_symlink() and not os.access(state, os.R_OK | os.X_OK):
            return self.privileged("status", app)
        return self.patcher.status(app)

    def prepare(self):
        if not artifacts_current(self.root):
            print("필요한 실행 파일을 빌드합니다.", file=sys.stderr)
            environment = dict(os.environ, KAKAO_PATCHER_PYTHON=sys.executable)
            result = subprocess.run(["/bin/bash", str(self.root / "scripts/build.sh")],
                                    cwd=self.root, stdout=sys.stderr, stderr=sys.stderr, env=environment)
            if result.returncode:
                raise PatchError("빌드 실패. Apple Command Line Tools가 없다면 xcode-select --install로 설치하세요.")
            if not artifacts_current(self.root):
                raise PatchError("빌드 산출물 검증에 실패했습니다.")
        helper = self.root / "build/KakaoAppControl"
        if not os.access(helper, os.X_OK):
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
        return self.root / "build/KakaoAdBlock.dylib"

    def control(self, action, app):
        helper = self.root / "build/KakaoAppControl"
        try:
            result = subprocess.run([str(helper), action, str(app)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PatchError("카카오톡 종료/실행 상태를 확인할 수 없습니다.") from error
        if result.returncode:
            raise PatchError(result.stderr.strip() or "카카오톡 정상 종료/실행에 실패했습니다.")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise PatchError("앱 제어 도구가 올바른 결과를 반환하지 않았습니다.") from error

    def stop(self, app):
        return self.control("stop", app)["was_running"]

    def apply(self, app, payload):
        if self.needs_admin(app):
            return self.privileged("apply", app, payload)
        return self.patcher.apply(app, payload, accept_risk=True)

    def restore(self, app):
        if self.needs_admin(app):
            return self.privileged("restore", app)
        return self.patcher.restore(app)

    def relaunch(self, app):
        self.control("open", app)


class Installer:
    def __init__(self, services=None, confirm=None, output=None):
        self.services = services if services is not None else InstallServices()
        self.confirm = confirm if confirm is not None else confirm_interactively
        self.output = output if output is not None else lambda text: print(text, file=sys.stderr)

    def run(self, app, accept_risk=False, restore=False, relaunch=True):
        if not restore:
            self.output(RISK_NOTICE)
            self.output("동의하면 빌드·정상 종료·백업·패치를 진행하며, 원래 실행 중이었다면 완료 후 다시 엽니다.")
            if not accept_risk and not self.confirm():
                self.output("취소했습니다. 변경하지 않았습니다.")
                return {"state": "cancelled"}
        else:
            self.output("실행 중인 카카오톡을 정상 종료한 뒤 원본을 복구합니다.")

        status = self.services.status(app)
        if not restore and status["state"] == "patched":
            self.output("이미 패치되어 있습니다. 변경하지 않았습니다.")
            return status
        if restore and status["state"] == "unpatched":
            self.output("이미 원본 상태입니다.")
            return status
        allowed = ("patched", "interrupted", "restore-cleanup") if restore else ("unpatched",)
        if status["state"] not in allowed:
            raise PatchError(status.get("reason") or "설치본/백업 상태가 변경되었습니다. status를 확인하세요.")

        payload = self.services.prepare()
        self.output("카카오톡에 정상 종료를 요청합니다.")
        was_running = self.services.stop(app)
        self.output("원본을 복구합니다." if restore else "원본을 백업하고 패치를 적용합니다.")
        result = self.services.restore(app) if restore else self.services.apply(app, payload)
        if was_running and relaunch:
            self.output("카카오톡을 다시 실행합니다.")
            try:
                self.services.relaunch(app)
            except PatchError as error:
                result["launch_warning"] = str(error)
                self.output("파일 작업은 완료됐지만 자동 실행에 실패했습니다. 카카오톡을 직접 열어주세요.")
        self.output("완료했습니다.")
        return result
