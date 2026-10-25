"""The only place the deploy tool runs `launchctl` and `plutil`. Tests pass their own Runner and never build this one."""
import os
import subprocess
from pathlib import Path
from typing import Protocol

from outreach.deploy.jobs import DeployError

TIMEOUT_S = 60


class Runner(Protocol):
    def bootstrap(self, plist: Path) -> None: ...
    def bootout(self, label: str) -> None: ...
    def loaded(self, label: str) -> bool: ...
    def lint(self, plist: Path) -> None: ...


def bootstrap_command(uid: int, plist: Path) -> list[str]:
    return ["launchctl", "bootstrap", f"gui/{uid}", str(plist)]


def bootout_command(uid: int, label: str) -> list[str]:
    return ["launchctl", "bootout", f"gui/{uid}/{label}"]


def print_command(uid: int, label: str) -> list[str]:
    return ["launchctl", "print", f"gui/{uid}/{label}"]


def lint_command(plist: Path) -> list[str]:
    return ["plutil", "-lint", str(plist)]


class SystemRunner:
    """Runs the real tools for the logged-in user's GUI domain (`gui/<uid>`)."""

    def __init__(self, uid: int | None = None):
        self.uid = os.getuid() if uid is None else uid

    def _run(self, command: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise DeployError(f"{' '.join(command)} could not run: {err}") from None

    def _check(self, command: list[str]) -> None:
        done = self._run(command)
        if done.returncode != 0:
            output = (done.stdout + done.stderr).strip()
            raise DeployError(f"{' '.join(command)} failed ({done.returncode}): {output}")

    def bootstrap(self, plist: Path) -> None:
        self._check(bootstrap_command(self.uid, plist))

    def bootout(self, label: str) -> None:
        self._check(bootout_command(self.uid, label))

    def loaded(self, label: str) -> bool:
        return self._run(print_command(self.uid, label)).returncode == 0

    def lint(self, plist: Path) -> None:
        self._check(lint_command(plist))
