"""`python -m outreach.deploy plan|install|retire-old|status|rollback`: the switch's launchd steps (#17).

`plan` and `status` read only and run anywhere. `install`, `retire-old` and `rollback` run only from the live
checkout, and check that before they touch any file or call launchctl.

Nothing is ever deleted. An old job's plist is renamed to `<plist>.disabled`; a new job's plist is renamed the same
way on rollback; a plist install would replace is kept as `<plist>.before-switch`. launchd loads only `*.plist`
files, so a set-aside file stays on disk and does nothing.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import live_checkout
from outreach.deploy.jobs import NEW_JOBS, OLD_JOBS, DeployError, Job, render
from outreach.deploy.launchctl import Runner, SystemRunner

DEFAULT_AGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()
DEFAULT_LOG_DIR = Path("~/Library/Logs/outreach").expanduser()
DISABLED = ".disabled"
BEFORE_SWITCH = ".before-switch"
VENV_PYTHON = ".venv/bin/python"
WEB_INDEX = "web/dist/index.html"
OLD_SENDER = "com.devanshu.outreach-mail-sender"
NEW_SENDER = "com.devanshu.outreach-sender"
OLD_CONFIG = "mail/config.json"
PAUSE_FIRST = "Pause sending in the old dashboard (back on :3001), then run rollback again."
ROLLBACK_REMINDER = ("The old sender is paused in the old dashboard. Run python -m outreach.importer rollback-check "
                     "before you unpause it.")


class Context:
    def __init__(self, runner: Runner, agents_dir: Path, root: str, live_root: str, log_dir: Path):
        self.runner = runner
        self.agents_dir = agents_dir
        self.root = root
        self.live_root = live_root
        self.log_dir = log_dir

    def plist(self, job: Job) -> Path:
        return self.agents_dir / job.plist

    def disabled(self, job: Job) -> Path:
        return self.agents_dir / f"{job.plist}{DISABLED}"

    def template(self, job: Job) -> Path:
        return Path(self.root) / job.template

    def log_path(self, job: Job) -> Path:
        return self.log_dir / f"{job.label.removeprefix('com.devanshu.outreach-')}.log"


def set_aside(path: Path, suffix: str) -> Path:
    """Rename `path` to `<path><suffix>`, or `<suffix>-2`, `-3` ... when taken. Never overwrites, never deletes."""
    target = path.with_name(path.name + suffix)
    n = 2
    while target.exists():
        target = path.with_name(f"{path.name}{suffix}-{n}")
        n += 1
    path.rename(target)
    return target


def old_sender_paused(live_root: str) -> tuple[bool, str]:
    """Whether the old mailer's live config says `paused: true`, and what it says otherwise. Read only."""
    path = Path(live_root) / OLD_CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"{OLD_CONFIG} is missing"
    except (OSError, ValueError) as err:
        return False, f"{OLD_CONFIG} could not be read ({err})"
    paused = config.get("paused") if isinstance(config, dict) else None
    return paused is True, f"{OLD_CONFIG} says paused is {json.dumps(paused)}"


def plist_state(ctx: Context, job: Job) -> str:
    present, disabled = ctx.plist(job).exists(), ctx.disabled(job).exists()
    if present and disabled:
        return "present (a .disabled copy too)"
    return "present" if present else ".disabled" if disabled else "missing"


def install_problems(ctx: Context, skip: set[str]) -> list[str]:
    """Why install would refuse, in plain words. Empty when it may go on."""
    problems = []
    labels = {job.label: job for job in NEW_JOBS}
    for label in sorted(skip):
        if label not in labels:
            problems.append(f"--skip {label}: not a new job (known: {', '.join(labels)})")
        elif labels[label].required:
            problems.append(f"--skip {label}: a required job cannot be skipped")
    for job in NEW_JOBS:
        if job.required and not ctx.template(job).exists():
            problems.append(f"{job.label}: required template {job.template} is missing ({job.owner} adds it)")
    if not (Path(ctx.live_root) / VENV_PYTHON).exists():
        problems.append(f"{VENV_PYTHON} is missing in the live checkout: run uv sync there first")
    if not (Path(ctx.live_root) / WEB_INDEX).exists():
        problems.append(f"{WEB_INDEX} is missing: build the web app first (switch step S10)")
    return problems


def to_install(ctx: Context, skip: set[str]) -> list[Job]:
    return [job for job in NEW_JOBS if job.label not in skip and ctx.template(job).exists()]


def cmd_plan(ctx: Context, _args) -> int:
    print(f"New jobs, rendered into {ctx.agents_dir}:")
    for job in NEW_JOBS:
        kind = "required" if job.required else "optional"
        found = "template present" if ctx.template(job).exists() else "template missing"
        target = ctx.plist(job)
        print(f"  {job.label} [{kind}, {job.owner}]: {job.template} ({found}) -> {target} "
              f"({'exists' if target.exists() else 'absent'}), log {ctx.log_path(job)}")
    print("Old jobs, retired by renaming each plist to .disabled:")
    for job in OLD_JOBS:
        loaded = "loaded" if ctx.runner.loaded(job.label) else "not loaded"
        print(f"  {job.label}: {ctx.plist(job)} ({plist_state(ctx, job)}), {loaded}")
    problems = install_problems(ctx, set())
    if problems:
        print("Install would refuse:")
        for problem in problems:
            print(f"  {problem}")
    return 0


def cmd_status(ctx: Context, _args) -> int:
    for job in NEW_JOBS + OLD_JOBS:
        loaded = "loaded" if ctx.runner.loaded(job.label) else "not loaded"
        print(f"{job.label}: {loaded}, plist {plist_state(ctx, job)}")
    return 0


def install_one(ctx: Context, job: Job, text: str) -> str:
    runner, target, data = ctx.runner, ctx.plist(job), text.encode("utf-8")
    notes = []
    if not target.exists() and ctx.disabled(job).exists():  # set aside by an earlier rollback: take it back
        ctx.disabled(job).rename(target)
        notes.append(f"took back {target.name}{DISABLED}")
    if target.exists():
        if target.read_bytes() == data:
            runner.lint(target)
            if runner.loaded(job.label):
                return f"{job.label}: unchanged, already loaded"
            runner.bootstrap(target)
            return f"{job.label}: unchanged, loaded"
        if runner.loaded(job.label):
            runner.bootout(job.label)
            notes.append("booted out the running copy")
        notes.append(f"kept the old plist as {set_aside(target, BEFORE_SWITCH).name}")
    partial = target.with_name(target.name + ".installing")  # not *.plist, so launchd never reads it
    partial.write_bytes(data)
    os.replace(partial, target)
    runner.lint(target)
    if runner.loaded(job.label):  # loaded from a plist that is gone: replace the running copy
        runner.bootout(job.label)
    runner.bootstrap(target)
    return f"{job.label}: installed and loaded ({target})" + (f"; {'; '.join(notes)}" if notes else "")


def cmd_install(ctx: Context, args) -> int:
    live_checkout.require_live("installing the new launchd jobs", root=ctx.root, live_root=ctx.live_root)
    skip = set(args.skip or ())
    problems = install_problems(ctx, skip)
    if problems:
        raise DeployError("install refused, nothing changed:\n  " + "\n  ".join(problems))
    # Render every job before writing anything, so a bad template changes nothing.
    rendered = [(job, render(ctx.template(job).read_text(encoding="utf-8"), live_root=ctx.live_root,
                             log_path=str(ctx.log_path(job)), label=job.label)) for job in to_install(ctx, skip)]
    ctx.agents_dir.mkdir(parents=True, exist_ok=True)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    done = {job.label for job, _ in rendered}
    for job in NEW_JOBS:
        if job.label in done:
            continue
        reason = "skipped on request" if job.label in skip else f"skipped, no template yet ({job.owner})"
        print(f"{job.label}: {reason}")
    for job, text in rendered:
        print(install_one(ctx, job, text), flush=True)
    return 0


def retire_problems(ctx: Context) -> list[str]:
    return [f"{job.label}: both {ctx.plist(job)} and {ctx.disabled(job)} exist; move one aside by hand"
            for job in OLD_JOBS if ctx.plist(job).exists() and ctx.disabled(job).exists()]


def cmd_retire_old(ctx: Context, _args) -> int:
    live_checkout.require_live("retiring the old launchd jobs", root=ctx.root, live_root=ctx.live_root)
    problems = retire_problems(ctx)
    if problems:
        raise DeployError("retire-old refused, nothing changed:\n  " + "\n  ".join(problems))
    for job in OLD_JOBS:
        was_loaded = ctx.runner.loaded(job.label)
        if was_loaded:
            ctx.runner.bootout(job.label)
        state = "booted out" if was_loaded else "not loaded"
        if ctx.plist(job).exists():
            ctx.plist(job).rename(ctx.disabled(job))
            print(f"{job.label}: {state}, plist renamed to {ctx.disabled(job).name}", flush=True)
        elif ctx.disabled(job).exists():
            print(f"{job.label}: {state}, already retired ({ctx.disabled(job).name})", flush=True)
        else:
            print(f"{job.label}: {state}, no plist found", flush=True)
    return 0


def cmd_rollback(ctx: Context, _args) -> int:
    live_checkout.require_live("rolling back to the old launchd jobs", root=ctx.root, live_root=ctx.live_root)
    failed = []
    for job in NEW_JOBS:  # the new jobs stop first, so the new and the old sender never run together
        try:
            was_loaded = ctx.runner.loaded(job.label)
            if was_loaded:
                ctx.runner.bootout(job.label)
            state = "booted out" if was_loaded else "not loaded"
            if ctx.plist(job).exists():
                print(f"{job.label}: {state}, plist renamed to {set_aside(ctx.plist(job), DISABLED).name}",
                      flush=True)
            elif was_loaded:
                print(f"{job.label}: {state}, no plist", flush=True)
        except (DeployError, OSError) as err:
            failed.append(f"{job.label}: {err}")
    for job in OLD_JOBS:
        try:
            if job.label == OLD_SENDER and not ctx.runner.loaded(job.label):
                if ctx.runner.loaded(NEW_SENDER):  # two senders must never run at once
                    print(f"{job.label}: left off, {NEW_SENDER} is still loaded", flush=True)
                    failed.append(f"{job.label}: left off because {NEW_SENDER} is still loaded; boot it out "
                                  f"(launchctl bootout gui/$(id -u)/{NEW_SENDER}), then run rollback again.")
                    continue
                paused, said = old_sender_paused(ctx.live_root)
                if not paused:  # an unpaused old sender would start sending the moment it loads
                    print(f"{job.label}: left off ({said})", flush=True)
                    failed.append(f"{job.label}: {said}. {PAUSE_FIRST}")
                    continue
            if not ctx.plist(job).exists() and ctx.disabled(job).exists():
                ctx.disabled(job).rename(ctx.plist(job))
            if not ctx.plist(job).exists():
                failed.append(f"{job.label}: no {ctx.plist(job).name} and no {ctx.disabled(job).name} to restore")
                continue
            if ctx.runner.loaded(job.label):
                print(f"{job.label}: plist present, already loaded", flush=True)
            else:
                ctx.runner.bootstrap(ctx.plist(job))
                print(f"{job.label}: plist restored and loaded", flush=True)
        except (DeployError, OSError) as err:
            failed.append(f"{job.label}: {err}")
    for problem in failed:
        print(f"error: {problem}", file=sys.stderr)
    print(ROLLBACK_REMINDER)
    return 1 if failed else 0


COMMANDS = {"plan": cmd_plan, "install": cmd_install, "retire-old": cmd_retire_old, "status": cmd_status,
            "rollback": cmd_rollback}


def main(argv: list[str] | None = None, *, runner: Runner | None = None, agents_dir: Path | None = None,
         root: str | None = None, live_root: str | None = None, log_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m outreach.deploy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="show what install and retire-old would do; writes nothing")
    install = sub.add_parser("install", help="render, lint and load the new jobs (live checkout only)")
    install.add_argument("--skip", action="append", metavar="LABEL", help="leave out an optional job")
    sub.add_parser("retire-old", help="boot out the old jobs and rename their plists to .disabled (live checkout only)")
    sub.add_parser("status", help="one line per job: loaded or not, and its plist")
    sub.add_parser("rollback", help="stop the new jobs and bring the old ones back (live checkout only)")
    args = parser.parse_args(argv)
    ctx = Context(
        runner=runner if runner is not None else SystemRunner(),
        agents_dir=Path(agents_dir) if agents_dir is not None else DEFAULT_AGENTS_DIR,
        root=root if root is not None else live_checkout.REPO_ROOT,
        live_root=live_root if live_root is not None else live_checkout.LIVE_ROOT,
        log_dir=Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR,
    )
    try:
        return COMMANDS[args.command](ctx, args)
    except DeployError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
