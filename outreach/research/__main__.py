"""Company research on the command line (#205).

    python -m outreach.research company <id> [<id>...] [--force] [--dry-run]

Asks GPT (Codex CLI, live web search) what each company is building, then keeps only the facts whose quote code finds
on the page. A real run calls a model and the network and writes the live store, so it only starts in the live
checkout. `--dry-run` prints the prompt and calls and writes nothing; it runs anywhere.

Exit 0 when every company is fresh, stored or had nothing checkable; 1 when any run failed or a company is unknown.
"""
import argparse
import sys

import live_checkout
from outreach.net import UrllibClient
from outreach.research.gpt import CodexRunner, build_prompt, company_info, research_company
from outreach.store.db import connect
from outreach.store.timefmt import utcnow

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.research",
                                     description="Research companies with GPT; keep only checked facts.")
    sub = parser.add_subparsers(dest="command", required=True)
    co = sub.add_parser("company", help="research one or more companies (live checkout only, unless --dry-run)")
    co.add_argument("ids", nargs="+", type=int, help="company ids")
    co.add_argument("--force", action="store_true", help="research even when facts are fresh")
    co.add_argument("--dry-run", action="store_true", help="print the prompt; call and write nothing")
    return parser


def main(argv: list[str] | None = None, *, stdout=None, runner=None, client=None, now: str | None = None) -> int:
    args = _parser().parse_args(argv)
    stdout = stdout if stdout is not None else sys.stdout
    if not args.dry_run and not live_checkout.is_live():
        raise SystemExit(f"research only runs from the live checkout ({live_checkout.LIVE_ROOT}); "
                         f"this is {live_checkout.REPO_ROOT}.")
    conn = connect()
    code = 0
    for company_id in args.ids:
        try:
            if args.dry_run:
                company, postings = company_info(conn, company_id)
                print(f"--- company {company_id}: {company['name']}\n{build_prompt(company, postings)}", file=stdout)
                continue
            res = research_company(conn, company_id, runner=runner or CodexRunner(), client=client or UrllibClient(),
                                   now=now or utcnow(), force=args.force)
        except Exception as err:  # one company failing never skips the rest
            print(f"company {company_id}: failed ({err})" if not isinstance(err, LookupError)
                  else f"company {company_id}: {err}", file=stdout)
            code = 1
            continue
        extra = f" ({res['error']})" if res.get("error") else ""
        print(f"company {company_id}: {res['status']}, kept {res['kept']}, rejected {len(res['rejected'])}{extra}",
              file=stdout)
        if res["status"] == "failed":
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
