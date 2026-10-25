#!/usr/bin/env python3
"""PreToolUse hook for Bash: Claude never approves or sends, so it never writes to a loopback API.

Reads the hook JSON on stdin. Exit 2 with a plain reason on stderr refuses the command; exit 0 allows it. It refuses a
command that

  1. names a loopback host (127.x, localhost, [::1], 0.0.0.0, in any case, or a numeric spelling of 127.0.0.1) and an
     /api/ path with a write word in it (approve, unapprove, skip, save, pause, resume, release, send), or
  2. names a loopback host, a network client (curl, wget, httpie, python, node, bun, deno, nc ...) and a write
     method or body flag (-X POST/PUT/PATCH/DELETE, -d, -F, -T, --data*, --post-data, --json, --form, .post( ...), or
  3. builds the host from a shell variable and names an /api/ write path (curl "$BASE/api/outbox/1/approve"), or
  4. builds the host of any /api/ path from a shell variable and has a network client and a write method or body flag
     (curl -X POST "$API/api/wins/seen").

urllib's `Request(url, data)` counts as a body flag: its second positional argument is the request body.

This is a backstop to the deny patterns in .claude/settings.json, which cannot see inside `sh -c`, variables or other
tools. It reads the command text only: a command that assembles the host or the path out of pieces gets past it, so
the rule in CLAUDE.md still stands: never write to the API by any means.
"""
import json
import re
import sys

WRITE_WORDS = frozenset({"approve", "unapprove", "skip", "save", "pause", "resume", "release", "send"})

LOOPBACK = re.compile(
    r"(?<![\d.])127(?:\.\d+){1,3}"  # 127.0.0.1, 127.1, 127.0.1
    r"|localhost|ip6-localhost|ip6-loopback"
    r"|\[::1\]|(?<![\w:])::1(?![\w:])|\[::\]|\[0*:0*:0*:0*:0*:0*:0*:0*1\]"
    r"|(?<![\d.])0\.0\.0\.0"
    r"|0x7f|(?<![\d.])0177\.|(?<![\d])2130706433(?!\d)"
    r"|localtest\.me|lvh\.me|nip\.io|sslip\.io",
    re.IGNORECASE,
)

# Flags are case-sensitive (curl -D dumps headers and -F posts a form), so these run on the unlowered text.
WRITE_FLAGS = [
    re.compile(r"(?<!\S)-[A-Za-z]*X\s*=?\s*(?:post|put|delete|patch)\b", re.IGNORECASE),
    re.compile(r"(?<!\S)--(?:request|method)\s*=?\s*(?:post|put|delete|patch)\b", re.IGNORECASE),
    re.compile(r"(?<!\S)-[A-Za-z]{0,5}[dFT](?![A-Za-z])"),
    re.compile(
        r"(?<!\S)--(?:data|data-raw|data-binary|data-urlencode|data-ascii|post-data|post-file|body-data|body-file|"
        r"json|form|form-string|upload-file)\b"
    ),
    re.compile(r"\.(?:post|put|delete|patch)\s*\(", re.IGNORECASE),  # requests.post(, httpx.put(, axios.delete(
    re.compile(r"\bmethod\s*[:=]\s*(?:post|put|delete|patch)\b", re.IGNORECASE),  # fetch(..., {method: 'POST'})
    re.compile(r"\brequest\s*\(\s*(?:post|put|delete|patch)\b", re.IGNORECASE),
    re.compile(r"\b(?:data|body|json)="),  # urlopen(req, data=...), httpx.request(..., json=...)
    re.compile(r"\b(?:data|body)\s*:\s"),  # fetch(..., {body: ...})
    re.compile(r"(?<!\S)(?:POST|PUT|DELETE|PATCH)\s+/"),  # a raw HTTP request line sent with nc or /dev/tcp
    # urllib.request.Request(url, data, ...): a second positional argument (not name=) is the body. Quotes are stripped.
    re.compile(r"\bRequest\s*\(\s*[^,()\s]+\s*,\s*(?![A-Za-z_]\w*\s*=)[^\s)]"),
]
# `http POST host/path`, `xh put ...`, and httpie's body items (`name=value`, `name:=json`), which make it a POST.
HTTPIE = re.compile(r"(?<![\w./:-])(?:http|https|xh|xhs|curlie|hurl)(?=\s)")
HTTPIE_WRITE = [
    re.compile(r"(?<!\S)(?:post|put|delete|patch)(?!\S)", re.IGNORECASE),
    re.compile(r"(?<!\S)[\w.-]+(?::=|=@|=)[^=\s]"),
]
CLIENT = re.compile(
    r"(?<![\w.-])(?:curl|wget|httpie|nc|ncat|netcat|socat|telnet|python[\d.]*|node|nodejs|bun|deno|ruby|perl|php|npx|tsx)"
    r"(?![\w-])|\burlopen\s*\("
)
VARIABLE_HOST = re.compile(r"[$`]")


def _strip_quoting(command: str) -> str:
    """Remove quotes and backslashes so `appr"ove"` and `127.0.0.\\1` read as one word."""
    return re.sub(r"""\$'|['"\\]""", "", command)


def _api_tokens(text: str) -> list[str]:
    """Whitespace-separated tokens that hold an /api/ path."""
    return [token for token in re.split(r"\s+", text) if "/api/" in token]


def _write_path_tokens(text: str) -> list[str]:
    """Whitespace-separated tokens that hold an /api/ path with a write word in it."""
    hits = []
    for token in _api_tokens(text):
        path = token.partition("/api/")[2]
        words = re.split(r"[^a-z]+", re.split(r"[?#]", path, maxsplit=1)[0].lower())
        if WRITE_WORDS.intersection(words):
            hits.append(token)
    return hits


def _has_write_flag(text: str) -> bool:
    if any(pattern.search(text) for pattern in WRITE_FLAGS):
        return True
    return bool(HTTPIE.search(text)) and any(pattern.search(text) for pattern in HTTPIE_WRITE)


def decide(command: str) -> str | None:
    """A plain reason to refuse `command`, or None to allow it."""
    text = _strip_quoting(command)
    loopback = bool(LOOPBACK.search(text))
    write_tokens = _write_path_tokens(text)
    if loopback and write_tokens:
        return f"it names a loopback host and the API write path {write_tokens[0]!r}"
    for token in write_tokens:
        if VARIABLE_HOST.search(token.partition("/api/")[0]):
            return f"it builds the host of the API write path {token!r} from a variable"
    client = bool(CLIENT.search(text) or HTTPIE.search(text))
    if loopback and client and _has_write_flag(text):
        return "it sends a write (POST, PUT, PATCH, DELETE or a request body) to a loopback host"
    if client and _has_write_flag(text):
        for token in _api_tokens(text):
            if VARIABLE_HOST.search(token.partition("/api/")[0]):
                return f"it sends a write to the API path {token!r} on a host built from a variable"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0  # not our input; never break every Bash call
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 0
    reason = decide(command)
    if reason:
        print(
            f"Refused: {reason}. Claude never approves or sends and never writes to the outreach API or the mail API "
            "(CLAUDE.md). Ask Devanshu to do it in the app.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
