"""The AppleScript runner: the one place the sender starts `osascript` (Mail.app, Phase B).

`osascript_runner` is a `Runner`: script text, arguments and a timeout in seconds in, stdout out. Tests and the
Mail.app client take a Runner as a parameter, so no test ever starts osascript.

Record format. A script prints one record per line, fields separated by a tab. A field is escaped by the script so
it cannot hold a tab or a line break: backslash becomes `\\\\`, tab becomes `\\t`, line feed becomes `\\n` (and a
carriage return is dropped or written as `\\n`). The escape runs backslash first, so `\\t` read back is never
ambiguous. `parse_records` splits output into records and unescapes each field.
"""
import subprocess
from collections.abc import Callable, Sequence

from outreach.sending.mail import GmailError, scrub

__all__ = ["Runner", "osascript_runner", "parse_records", "unescape_field", "escape_field"]

Runner = Callable[[str, Sequence[str], float], str]

# AppleScript's error number when the target application is not running ("Application isn't running").
_NOT_RUNNING = "(-600)"


def osascript_runner(script: str, args: Sequence[str], timeout: float) -> str:
    """Run `script` with `osascript -` (script on stdin, `args` reach `on run argv`) and return stdout as is. Text
    is UTF-8 both ways whatever the locale: subjects are matched exactly, so their decoding must not vary.

    Raises GmailError: `timeout` when the call outlasts `timeout`, `app_closed` when Mail.app is not running
    (error -600), `server` for any other failure. The detail is scrubbed and at most 300 characters.
    """
    try:
        done = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise GmailError("timeout", detail=f"osascript took longer than {timeout:g} seconds") from e
    except OSError as e:
        raise GmailError("server", detail=f"osascript did not start: {e}") from e
    except UnicodeError as e:
        raise GmailError("server", detail="osascript answered with text that is not UTF-8") from e
    if done.returncode != 0:
        stderr = done.stderr or ""
        if _NOT_RUNNING in stderr:
            raise GmailError("app_closed", detail=stderr)
        raise GmailError("server", detail=stderr or f"osascript exited {done.returncode}")
    return done.stdout


_UNESCAPE = {"t": "\t", "n": "\n", "\\": "\\"}


def escape_field(value: str) -> str:
    """What the scripts' `esc` handler does (`mailapp.COMMON_HANDLERS`): backslash first, then tab, then each line
    break (CR LF, CR, LF) as `\\n`. `unescape_field` undoes it. For fakes that answer as the scripts do."""
    out = value.replace("\\", "\\\\").replace("\t", "\\t")
    return out.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def unescape_field(field: str) -> str:
    """Undo the script's escape: `\\t`, `\\n` and `\\\\`. Any other backslash is kept as it is."""
    out: list[str] = []
    i = 0
    while i < len(field):
        c = field[i]
        if c == "\\" and i + 1 < len(field) and field[i + 1] in _UNESCAPE:
            out.append(_UNESCAPE[field[i + 1]])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_records(output: str) -> list[list[str]]:
    """Tab separated records, one per non-empty line, each field unescaped."""
    return [[unescape_field(f) for f in line.split("\t")] for line in output.split("\n") if line]
