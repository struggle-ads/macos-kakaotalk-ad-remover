# Event-driven local patcher

Build a distributable patcher, not a modified KakaoTalk distribution. The user
explicitly selected a local patcher with an account-restriction warning.

The native payload hooks the existing main/lock controller outlet setters and
pins only their advertisement views and height constraints. No timer, polling,
authenticated lifecycle replacement, chat inspection, or network interception.

The Python 3 standard-library CLI checks the exact supported release, prepares a
copy, adds a dylib load command, preserves the original entitlements while
allowing the local library, and re-signs that copy. It verifies before activating
it and keeps the original bundle for restore. Applying requires explicit risk
acceptance and a stopped app. Creating this tool does not install it here.

Initial support: KakaoTalk macOS 26.9.0 (2006), the exact examined universal binary.
Unknown builds fail closed. Payloads build for arm64 and x86_64; runtime tests
on this machine cover arm64. Account safety and authenticated end-to-end behavior
are not established by the earlier isolated layout experiment.

Git and release archives contain only our own code, tests and documentation.
Local analysis, probes, app copies, customer data and compiled bundles are ignored.
