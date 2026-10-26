"""Stage, verify, activate and restore a local KakaoTalk installation."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile

from . import VERSION
from .macho import MachOError, dylib_names, entitlements, inject_dylib, slices

BUNDLE_ID = "com.kakao.KakaoTalkMac"
TEAM_ID = "L75WVXX68A"
LIBRARY = "@executable_path/../Frameworks/KakaoAdBlock.dylib"
MARKER = "Contents/Resources/KakaoTalkAdRemover.json"
SUPPORTED = {
    ("26.9.0", "2006"): {
        "sha256": "456e1a3ed7a87168888bdb8c6e4894764a028e62c0af68562a760d9909d2bb65",
        "nibs": {
            "FSMainViewController.nib": "21842c678fde4ce88583ad7c3b42863e7ac91daba7b5f6d106b06714020257be",
            "FSLockViewController.nib": "afc94ac8d58ed61c724fc94631f7d41b625c267676fe67ed47203b8c349d1003",
        },
    }
}

RISK_NOTICE = (
    "비공식 실험용 패처입니다. 설치된 카카오톡 실행 파일을 수정하고 로컬로 재서명합니다.\n"
    "카카오의 승인을 받지 않았으며 계정 이용 제한, 실행·로그인·업데이트 오류가 발생할 수 있습니다.\n"
    "계정 안전성과 정상 동작을 보장하지 않습니다. 백업 복구로 서버 측 이용 제한을 해제할 수는 없습니다."
)


class PatchError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def app_path(value):
    path = Path(value).expanduser().absolute()
    if path.suffix != ".app" or path.is_symlink():
        raise PatchError("실제 .app 폴더를 지정해야 합니다. 심볼릭 링크는 지원하지 않습니다.")
    path = path.parent.resolve(strict=True) / path.name
    if any(part.endswith(".kakao-ad-remover") for part in path.parts[:-1]):
        raise PatchError("패처 백업 폴더를 적용 대상으로 지정할 수 없습니다.")
    return path


def state_directory(app):
    return app.parent / ("." + app.name + ".kakao-ad-remover")


def safe_member(app, relative):
    current = app
    if current.is_symlink():
        raise PatchError("앱/백업 폴더가 심볼릭 링크입니다.")
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PatchError("패치 대상 경로의 심볼릭 링크를 허용하지 않습니다: " + str(current))
    return current


def executable(app):
    return safe_member(app, "Contents/MacOS/KakaoTalk")


def read_info(app):
    for relative in ("Contents", "Contents/MacOS", "Contents/Resources", "Contents/Frameworks"):
        if not safe_member(app, relative).is_dir():
            raise PatchError("앱 구조가 올바르지 않습니다: " + relative)
    try:
        info = plistlib.loads(safe_member(app, "Contents/Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise PatchError("Info.plist를 읽을 수 없습니다.") from error
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "KakaoTalk":
        raise PatchError("공식 카카오톡 번들 식별자가 아닙니다.")
    if not executable(app).is_file():
        raise PatchError("카카오톡 실행 파일이 없습니다.")
    return info


def command(arguments):
    try:
        result = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PatchError("필수 macOS 도구를 실행할 수 없습니다: " + arguments[0]) from error
    if result.returncode:
        raise PatchError((result.stderr or result.stdout).strip() or "명령 실행 실패: " + arguments[0])
    return result.stdout + result.stderr


class MacTools:
    def assert_stopped(self, app):
        expected = str(executable(app).resolve())
        listing = command(["/bin/ps", "-ww", "-axo", "comm="])
        if any(os.path.realpath(line.strip()) == expected for line in listing.splitlines() if line.strip()):
            raise PatchError("카카오톡을 완전히 종료한 뒤 다시 실행하세요.")

    def verify_original(self, app):
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
        identity = command(["/usr/bin/codesign", "-dvv", str(app)])
        if ("TeamIdentifier=" + TEAM_ID) not in identity.splitlines():
            raise PatchError("카카오의 원본 서명이 아닙니다.")

    def verify_patched(self, app):
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])

    def verify_payload(self, payload):
        command(["/usr/bin/codesign", "--verify", "--strict", str(payload)])

    def copy(self, source, destination):
        # Copy decompressed file bytes without protected provenance xattrs/ACLs.
        command(["/usr/bin/ditto", "--norsrc", "--noextattr", "--noacl", "--noqtn", str(source), str(destination)])

    def sign(self, app, original_entitlements):
        permissions = dict(original_entitlements)
        permissions["com.apple.security.cs.disable-library-validation"] = True
        with tempfile.NamedTemporaryFile(suffix=".plist") as file:
            file.write(plistlib.dumps(permissions))
            file.flush()
            command(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime",
                     "--entitlements", file.name, str(app)])


@contextmanager
def locked_state(app):
    state = state_directory(app)
    if state.is_symlink():
        raise PatchError("백업 경로가 심볼릭 링크입니다.")
    state.mkdir(mode=0o700, exist_ok=True)
    metadata = state.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise PatchError("백업 폴더의 소유자/권한이 안전하지 않습니다.")
    fd = os.open(state / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PatchError("올바르지 않은 잠금 파일입니다.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PatchError("다른 패치/복구 작업이 진행 중입니다.") from error
        yield state
    finally:
        os.close(fd)


def read_record(state, app):
    path = state / "state.json"
    if state.is_symlink() or path.is_symlink():
        raise PatchError("백업 기록의 심볼릭 링크를 허용하지 않습니다.")
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 16384:
            raise ValueError("oversized record")
        record = json.loads(path.read_text())
        if record.get("format") != 1 or record.get("app") != str(app):
            raise ValueError("record identity mismatch")
        for key in ("original_sha256", "patched_sha256"):
            if not re.fullmatch("[0-9a-f]{64}", record.get(key, "")):
                raise ValueError("invalid hash")
        return record
    except (OSError, ValueError, AttributeError, TypeError) as error:
        raise PatchError("백업 기록이 올바르지 않습니다. 원본/백업을 보존했습니다.") from error


def write_record(state, record):
    path = state / "state.json"
    if path.exists() or path.is_symlink():
        raise PatchError("기존 백업 기록을 덮어쓸 수 없습니다.")
    with open(path, "x", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())


def remove_owned(path):
    if path.is_symlink():
        raise PatchError("작업 폴더가 심볼릭 링크로 변경되어 정리를 중단했습니다.")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


class Patcher:
    def __init__(self, tools=None, supported=None):
        self.tools = tools if tools is not None else MacTools()
        self.supported = supported if supported is not None else SUPPORTED

    def supported_source(self, app):
        info = read_info(app)
        data = executable(app).read_bytes()
        build = (str(info.get("CFBundleShortVersionString")), str(info.get("CFBundleVersion")))
        approved = self.supported.get(build)
        if not approved or digest(data) != approved["sha256"]:
            raise PatchError("지원하지 않는 버전/실행 파일입니다. 현재는 확인된 26.9.0 (2006) 원본만 지원합니다.")
        for name, expected in approved["nibs"].items():
            if digest(safe_member(app, "Contents/Resources/" + name).read_bytes()) != expected:
                raise PatchError("화면 리소스가 지원 버전과 다릅니다.")
        return info, data

    def status(self, value):
        app = app_path(value)
        state = state_directory(app)
        record = read_record(state, app)
        if record:
            if not app.exists():
                return {"state": "interrupted", "backup": str(state / "original.app")}
            current = digest(executable(app).read_bytes())
            if current == record["patched_sha256"]:
                return {"state": "patched", "backup": str(state / "original.app")}
            if current == record["original_sha256"] and not (state / "original.app").exists():
                return {"state": "restore-cleanup", "backup": None}
            return {"state": "changed", "backup": str(state / "original.app")}
        try:
            info, _ = self.supported_source(app)
            return {"state": "unpatched", "version": info["CFBundleShortVersionString"], "build": str(info["CFBundleVersion"])}
        except PatchError as error:
            return {"state": "unsupported", "reason": str(error)}

    def apply(self, value, payload, accept_risk=False):
        if not accept_risk:
            raise PatchError(RISK_NOTICE + "\n계속하려면 apply --accept-risk를 명시하세요.")
        app = app_path(value)
        self.tools.assert_stopped(app)
        info, original = self.supported_source(app)
        self.tools.verify_original(app)
        payload = Path(payload).expanduser().resolve(strict=True)
        library_bytes = payload.read_bytes()
        target_arches = {item.cpu for item in slices(original)}
        library_slices = slices(library_bytes)
        if any(item.filetype != 6 for item in library_slices) or not target_arches.issubset({item.cpu for item in library_slices}):
            raise PatchError("페이로드의 형식/아키텍처가 설치본과 맞지 않습니다.")
        self.tools.verify_payload(payload)
        permissions = entitlements(original)
        if permissions.get("com.apple.security.app-sandbox") is not True:
            raise PatchError("원본 샌드박스 권한을 확인할 수 없어 중단했습니다.")
        modified = inject_dylib(original, LIBRARY)
        with locked_state(app) as state:
            backup, stage = state / "original.app", state / "staging.app"
            if read_record(state, app) or backup.exists() or backup.is_symlink():
                raise PatchError("기존 백업이 있습니다. 먼저 status/restore를 실행하세요.")
            if stage.exists() or stage.is_symlink():
                raise PatchError("이전 작업 폴더가 남아 있습니다. 내용을 확인해 보존한 뒤 정리하세요.")
            try:
                self.tools.copy(app, stage)
                read_info(stage)
                executable(stage).write_bytes(modified)
                destination = safe_member(stage, "Contents/Frameworks/KakaoAdBlock.dylib")
                if destination.exists():
                    raise PatchError("페이로드 이름의 파일이 이미 있습니다.")
                destination.write_bytes(library_bytes)
                destination.chmod(0o755)
                marker = {"patcher_version": VERSION, "original_sha256": digest(original),
                          "payload_sha256": digest(library_bytes), "source_version": info["CFBundleShortVersionString"]}
                safe_member(stage, MARKER).write_text(json.dumps(marker, indent=2) + "\n")
                self.tools.sign(stage, permissions)
                self.tools.verify_patched(stage)
                patched = executable(stage).read_bytes()
                if not all(LIBRARY in names for names in dylib_names(patched)):
                    raise PatchError("페이로드 로드 명령 검증 실패")
                self.tools.assert_stopped(app)
                _, current = self.supported_source(app)
                if current != original:
                    raise PatchError("준비 중 설치본이 변경되었습니다.")
                self.tools.verify_original(app)
                write_record(state, {"format": 1, "app": str(app), "patcher_version": VERSION,
                                     "original_sha256": digest(original), "patched_sha256": digest(patched)})
                os.replace(app, backup)
                try:
                    os.replace(stage, app)
                except BaseException:
                    os.replace(backup, app)
                    raise
            except BaseException as error:
                if not backup.exists():
                    remove_owned(stage)
                    remove_owned(state / "state.json")
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise PatchError("패치 실패. 원본 또는 백업을 보존했습니다: " + str(error)) from error
            return {"state": "patched", "backup": str(backup), "app": str(app)}

    def restore(self, value):
        app = app_path(value)
        with locked_state(app) as state:
            record = read_record(state, app)
            if not record:
                raise PatchError("이 패처가 만든 백업 기록이 없습니다.")
            backup, discard, stage = state / "original.app", state / "discard.app", state / "staging.app"
            if backup.is_symlink():
                raise PatchError("백업 경로가 심볼릭 링크입니다.")
            self.tools.assert_stopped(app)
            if not backup.exists():
                _, data = self.supported_source(app)
                if digest(data) != record["original_sha256"]:
                    raise PatchError("복구 가능한 원본 백업이 없습니다.")
                self.tools.verify_original(app)
            else:
                _, data = self.supported_source(backup)
                if digest(data) != record["original_sha256"]:
                    raise PatchError("원본 백업 해시가 일치하지 않습니다.")
                self.tools.verify_original(backup)
                if app.exists():
                    if digest(executable(app).read_bytes()) != record["patched_sha256"] or not safe_member(app, MARKER).is_file():
                        raise PatchError("설치본이 패치 후 변경/업데이트되어 덮어쓰지 않습니다. 백업을 보존했습니다.")
                    if discard.exists() or discard.is_symlink():
                        raise PatchError("이전 복구 작업 파일이 남아 있어 덮어쓰지 않습니다.")
                    os.replace(app, discard)
                try:
                    os.replace(backup, app)
                except BaseException:
                    if discard.exists() and not app.exists():
                        os.replace(discard, app)
                    raise
            remove_owned(discard)
            remove_owned(stage)
            remove_owned(state / "state.json")
            return {"state": "restored", "app": str(app)}
