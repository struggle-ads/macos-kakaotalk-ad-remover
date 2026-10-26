import argparse
import json
import os
from pathlib import Path
import sys

from . import VERSION
from .app import Patcher, PatchError, RISK_NOTICE
from .macho import MachOError
from .install import Installer


def main(argv=None):
    parser = argparse.ArgumentParser(description="비공식 카카오톡 광고 영역 패처 — 이용 제한 위험이 있습니다.")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="action", required=True)
    install = commands.add_parser("install", help="한 번에 빌드·정상 종료·패치·재실행")
    install.add_argument("--app", default="/Applications/KakaoTalk.app", help="대상 .app 폴더")
    install.add_argument("--accept-risk", action="store_true", help="위험 고지에 동의하고 확인 질문 생략")
    install.add_argument("--restore", action="store_true", help="같은 절차로 원본 복구")
    install.add_argument("--no-relaunch", action="store_true", help="완료 후 앱을 다시 실행하지 않음")
    for action, help_text in (("status", "설치본/백업 상태 확인"), ("apply", "위험 동의 후 로컬 패치·재서명"), ("restore", "백업한 원본 복구")):
        sub = commands.add_parser(action, help=help_text)
        sub.add_argument("--app", default="/Applications/KakaoTalk.app", help="대상 .app 폴더")
        if action == "apply":
            sub.add_argument("--accept-risk", action="store_true", help="계정 이용 제한·오작동·재서명 위험에 명시적으로 동의")
            sub.add_argument("--payload", default=str(Path(__file__).resolve().parents[1] / "build/KakaoAdBlock.dylib"), help="빌드한 네이티브 페이로드")
    args = parser.parse_args(argv)
    patcher = Patcher()
    try:
        if args.action == "install":
            if os.geteuid() == 0:
                raise PatchError("install은 sudo 없이 실행하세요. 필요한 파일 작업에서만 관리자 권한을 요청합니다.")
            from .app import app_path
            result = Installer().run(app_path(args.app), accept_risk=args.accept_risk,
                                     restore=args.restore, relaunch=not args.no_relaunch)
        elif args.action == "apply":
            if args.accept_risk:
                print(RISK_NOTICE, file=sys.stderr)
            result = patcher.apply(args.app, args.payload, accept_risk=args.accept_risk)
        elif args.action == "restore":
            result = patcher.restore(args.app)
        else:
            result = patcher.status(args.app)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PatchError, MachOError, OSError, ValueError) as error:
        print("오류: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단했습니다. status로 상태를 확인하고 필요하면 restore를 실행하세요.", file=sys.stderr)
        return 130
