# One-command installation

**Goal:** `./install.sh` handles risk acknowledgement, cached build, graceful quit,
backup/patch/sign, and restoring the previous running state. `--restore` follows
the same flow. The existing low-level CLI remains available.

**Approach:** Python orchestration plus a small authored AppKit helper. Observe
application termination through KVO; never force-kill. Reuse packaged binaries
when their source/binary hashes match. Request `sudo` only for the existing
low-level filesystem operation if necessary; keep build and app control as the
logged-in user. Missing Python/Command Line Tools produce installation guidance,
not silent system software installation.

**Validation:** Test cancellation before side effects, operation order, no-op
repeated install, unsupported/updated app rejection, quit/sign/relaunch failures,
restore, source/binary cache invalidation, launcher from a different directory,
and exact-target normal termination using an owned test app. Run the existing
Mach-O, transaction and native UI tests. Do not patch the installed KakaoTalk.

**Outcome:** Risk confirmation, cached build, normal stop, apply/restore and
conditional relaunch implemented. Python-selection review finding reproduced
with a broken PATH shim and fixed by propagating the selected interpreter.
Native integration caught a JSON number/boolean mismatch; the helper now emits
actual booleans. The full Python suite and owned-app integration checks pass.
