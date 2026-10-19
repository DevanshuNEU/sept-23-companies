# Fetch results: ok, empty or error (issue #3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed job-board read never closes a company: every board read is ok, empty or error; an error keeps the company's previous roles, empty closes only after two empty reads in a row, and three errors in a row are listed for review.

**Architecture:** `refresh/finalcheck.py` already reads each company's Ashby, Greenhouse and Lever boards and writes `refresh/work/finalcheck.json`; `refresh/final_merge.py` turns that into `final_merge.json`, and `refresh/write_level.js` rewrites only the companies present in it. So the fix has three parts: finalcheck records a read result per company; a new pure module `refresh/board_status.py` keeps per-company streaks in `refresh/work/board_status.json` and decides "write" or "keep"; final_merge leaves "keep" companies out of its output, so `write_level.js` never touches them. `refresh.py`'s report gains a section built by `board_status.py`.

**Tech Stack:** Python 3.14 standard library (`/opt/homebrew/bin/python3`), `unittest`.

**Spec:** GitHub issue #3 and decision record #9; `docs/superpowers/specs/2026-10-18-outreach-platform-design.md` section 6.

## Global Constraints

- Standard library only. Tests: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .` from the repo root.
- Read results are exactly the strings `"ok"`, `"empty"`, `"error"`.
- Error = non-200 status, network failure or timeout (`get()` returns status 0), or a body that is not the JSON shape the ATS returns.
- Empty closes only on the 2nd empty read in a row. 3 errors in a row are listed for review. Neither ever removes a company.
- A company with several boards: any board in error makes the company's read `"error"`.
- URL-only companies (no ATS board) keep their current behavior: only a 404/410 or a "position closed" page closes a URL.
- Leave `dashboard/data.js` and `dashboard/index.html` untouched (CLAUDE.md).
- `crawl_boards.py` copies `OFFLANE` out of finalcheck's source text between `OFFLANE=` and the next `re.I)`; that span must stay intact.
- No em dashes or en dashes in report text.

## Review Focus

1. A 200 response whose body is HTML (a login wall or CDN challenge) instead of JSON: error, not empty. Pinned in Task 1.
2. A 429 rate-limit response: error. Pinned in Task 1.
3. A company seen for the first time (no entry in `board_status.json`) that reads empty: kept this run, not closed. Pinned in Task 2.
4. `board_status.json` missing or unreadable: start from empty state and keep going, never crash the refresh. Pinned in Task 2.
5. An empty read followed by an error, then empty again: the error resets nothing about "in a row" except that the empty streak restarts. Pinned in Task 2.

---

### Task 1: finalcheck records a read result per company

**Files:**
- Modify: `refresh/finalcheck.py` (module top lines 2-3; `board()` ~134-162; `work()` ~177-196; the executor block at the end)
- Test: `tests/test_refresh_fetch.py`

**Interfaces:**
- Produces: `board(t: dict) -> tuple[list[tuple], str]` returning `(rows, read)` where `read` is `"ok" | "empty" | "error"`; `work(t)` output dict gains `"read"` for board companies (URL-only companies get `"read": "ok"`); `main(targets_path="refresh/work/final_targets.json", out_path="refresh/work/finalcheck.json") -> None`. The module no longer does any work at import.

- [ ] **Step 1: Write the failing tests** in `tests/test_refresh_fetch.py`. Import `refresh.finalcheck` with `sys.path` set to the repo root; replace `finalcheck.get` with a fake that returns `(status, url, body)` per URL.

```python
def test_board_with_jobs_is_ok():            # greenhouse 200 {"jobs":[one job]}       -> read == "ok", 1 row
def test_board_with_no_jobs_is_empty():      # greenhouse 200 {"jobs":[]}              -> read == "empty", 0 rows
def test_network_failure_is_error():         # get returns (0, url, "")                 -> read == "error"
def test_rate_limit_is_error():              # get returns (429, url, "")               -> read == "error"
def test_html_instead_of_json_is_error():    # 200 "<html>Just a moment...</html>"      -> read == "error"
def test_one_board_failing_makes_company_error():   # ashby ok with jobs, lever status 0 -> read == "error"
def test_work_sets_read_for_board_company(): # work({"id":"x","list":"linkedin","slugs":{"greenhouse":"acme"},"urls":[]})["read"] == "empty" when jobs == []
def test_import_does_no_work():              # importing finalcheck opens no files (no final_targets.json in a temp cwd) and does not raise
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_refresh_fetch -v`
Expected: import fails (module loads `refresh/work/final_targets.json` at import) or `board()` returns a list, not a tuple.

- [ ] **Step 3: Implement.** Move the `T=`/`OUT=` loads and the executor block into `main()` behind `if __name__ == "__main__": main()`. In `board()`, track one read per configured ATS: `"error"` unless status is 200 and the parsed body has the expected shape (Ashby and Greenhouse: a dict with a `jobs` list; Lever: a list); combine with any-error-wins, else `"ok"` if rows else `"empty"`. Note: rows that `analyse()` later filters out do not change `read`; a board with jobs that are all out of lane is `"ok"`. In `work()`, set `out["read"]` from `board()` and keep `boardEmpty` as `read == "empty"` for compatibility.

- [ ] **Step 4: Run the tests and confirm they pass**, plus check the OFFLANE span is intact:

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_refresh_fetch -v && /opt/homebrew/bin/python3 -c "import sys; sys.argv=['x']; src=open('refresh/finalcheck.py').read(); exec(src[src.index('OFFLANE='):src.index('re.I)', src.index('OFFLANE='))+5]); print('OFFLANE ok')"`
Expected: all PASS, then `OFFLANE ok`.

- [ ] **Step 5: Commit**

```bash
git add refresh/finalcheck.py tests/test_refresh_fetch.py
git commit -m "finalcheck: record ok, empty or error per company read (#3)"
```

---

### Task 2: board_status streaks and the write-or-keep decision

**Files:**
- Create: `refresh/board_status.py`
- Test: `tests/test_board_status.py`

**Interfaces:**
- Produces:
  - `load(path: str) -> dict` (missing or unreadable file returns `{}`)
  - `save(path: str, state: dict) -> None` (atomic write: temp file then `os.replace`)
  - `update(prev: dict, read: str, today: str) -> dict` returning `{"empty": int, "error": int, "last": read, "since": today}`: `"ok"` resets both streaks to 0; `"empty"` adds 1 to `empty` and resets `error`; `"error"` adds 1 to `error` and leaves `empty` as it was.
  - `action(entry: dict) -> str`: `"write"` when `last == "ok"` or (`last == "empty"` and `empty >= 2`); otherwise `"keep"`.
  - `needs_review(entry: dict) -> bool`: `error >= 3`.
  - `report_lines(state: dict, names: dict) -> list[str]`: markdown for the LAST_RUN report (Task 4).

- [ ] **Step 1: Write the failing tests**

```python
def test_ok_writes_and_resets():               # update({"empty":1,"error":2}, "ok", d) -> empty 0, error 0; action "write"
def test_first_empty_is_kept():                # update({}, "empty", d) -> empty 1; action "keep"
def test_second_empty_in_a_row_writes():       # update({"empty":1,"error":0,"last":"empty"}, "empty", d) -> action "write"
def test_error_never_writes():                 # update({}, "error", d) -> action "keep"
def test_three_errors_need_review():           # three updates with "error" -> needs_review True; two -> False
def test_error_between_empties_keeps_empty_count():  # empty, error, empty -> empty == 2, action "write"
def test_missing_or_corrupt_state_file_loads_empty():  # load(nonexistent) == {}; load(file with "{not json") == {}
def test_save_then_load_round_trips():
```

(The fifth Review Focus line resolves to: an error in between does not reset the empty count, so empty, error, empty closes. That matches "two empty reads" without letting an outage hide a real closure forever.)

- [ ] **Step 2: Run them and watch them fail**

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_board_status -v`
Expected: `ModuleNotFoundError: No module named 'refresh.board_status'` (or equivalent import error).

- [ ] **Step 3: Implement the functions above in `refresh/board_status.py`.**

- [ ] **Step 4: Run and confirm PASS**

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_board_status -v`

- [ ] **Step 5: Commit**

```bash
git add refresh/board_status.py tests/test_board_status.py
git commit -m "board_status: empty closes on the second read, errors never close (#3)"
```

---

### Task 3: final_merge leaves kept companies untouched

**Files:**
- Modify: `refresh/final_merge.py` (module-level loads and loop become `main(work_dir="refresh/work/", today=None)`)
- Test: `tests/test_final_merge.py`

**Interfaces:**
- Consumes: `finalcheck.json` entries with `"read"` (Task 1); `board_status.load/save/update/action` (Task 2).
- Produces: `main(work_dir: str = "refresh/work/", today: str | None = None) -> dict` writing `final_merge.json` (unchanged shape) and `board_status.json`. Companies whose `action` is `"keep"` are absent from `final_merge.json`, so `write_level.js` leaves their `level`, `reqs` and `noRoleInBand` exactly as they were. Entries without `"read"` (URL-only companies) are treated as `"ok"`.

- [ ] **Step 1: Write the failing tests.** Each test builds a temp work dir holding `final_targets.json`, `finalcheck.json` and `eligibility.json` for two or three companies, then calls `final_merge.main(tmpdir, today="2026-10-18")`.

```python
def test_errored_company_is_left_out():        # read "error", jobs [] -> id not in final_merge.json
def test_first_empty_company_is_left_out():    # read "empty", no prior state -> id not in final_merge.json
def test_second_empty_marks_nothing_open():    # board_status.json has {"empty":1,"last":"empty"}; read "empty" -> final_merge.json[id]["noRoleInBand"] is True
def test_ok_company_with_role_is_written():    # read "ok", one in-band job -> entryReq set, noRoleInBand absent
def test_url_only_company_unchanged():         # no "read" key, jobs [] , closedUrls 1 -> written with noRoleInBand (current behavior)
def test_status_file_updated():                # after main(), board_status.json[id]["error"] == 1 for the errored company
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_final_merge -v`
Expected: import fails (module reads `refresh/work/finalcheck.json` at import) or errored company still present.

- [ ] **Step 3: Implement.** Wrap the existing logic in `main()`; read paths from `work_dir`; for each company with a `"read"`, `update` its streak and skip it when `action == "keep"`; save `board_status.json`; keep `if __name__ == "__main__": main()` so `refresh.py`'s `run([sys.executable, "refresh/final_merge.py"])` still works.

- [ ] **Step 4: Run all tests and confirm PASS**

Run: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .`
Expected: every test passes (including the 15 mailer tests).

- [ ] **Step 5: Commit**

```bash
git add refresh/final_merge.py tests/test_final_merge.py
git commit -m "final_merge: a failed or first-empty read keeps the company as it was (#3)"
```

---

### Task 4: the LAST_RUN report shows unread boards

**Files:**
- Modify: `refresh/refresh.py` `write_report()` (~79-111)
- Modify: `refresh/board_status.py` (`report_lines`)
- Test: `tests/test_board_status.py`

**Interfaces:**
- Consumes: `board_status.json` written by Task 3; company names from the `after` snapshot in `write_report`.
- Produces: three report sections, in this order, after "Role to name changed":
  - `## Boards that could not be read this run, kept as before (N)`
  - `## Empty once, kept; closes if empty again next run (N)`
  - `## Needs review: unreadable 3 runs in a row (N)`
  Each lists `- <company name>` (and for review items `(since <date>)`); an empty section prints `None.`

- [ ] **Step 1: Write the failing test**

```python
def test_report_lines_lists_each_group():
    state = {"a": {"empty":0,"error":1,"last":"error","since":"2026-10-18"},
             "b": {"empty":1,"error":0,"last":"empty","since":"2026-10-18"},
             "c": {"empty":0,"error":3,"last":"error","since":"2026-10-12"},
             "d": {"empty":0,"error":0,"last":"ok","since":"2026-10-18"}}
    text = "\n".join(board_status.report_lines(state, {"a":"Acme","b":"Beta","c":"Corp","d":"Delta"}))
    assert "could not be read this run, kept as before (2)" in text   # a and c
    assert "Empty once, kept; closes if empty again next run (1)" in text and "- Beta" in text
    assert "Needs review: unreadable 3 runs in a row (1)" in text and "- Corp (since 2026-10-12)" in text
    assert "Delta" not in text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `/opt/homebrew/bin/python3 -m unittest tests.test_board_status.BoardStatusTest.test_report_lines_lists_each_group -v` (use the class name you gave the test case)
Expected: `AttributeError: ... has no attribute 'report_lines'`.

- [ ] **Step 3: Implement `report_lines`, then call it from `write_report`** with `board_status.load(W + "board_status.json")` and `{k: v["n"] for k, v in after.items()}`, wrapped in try/except like the existing review list so a report problem never fails a refresh.

- [ ] **Step 4: Run all tests and confirm PASS**

Run: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .`

- [ ] **Step 5: Commit**

```bash
git add refresh/refresh.py refresh/board_status.py tests/test_board_status.py
git commit -m "refresh report: list unread boards, empty-once and review items (#3)"
```

---

### Task 5: verify against real boards, then ship before Monday 08:00

**Files:** none changed.

- [ ] **Step 1: Open the PR** with body "Closes #3" and the test count; wait for Devanshu to merge.
- [ ] **Step 2: Deploy** per CLAUDE.md: in `~/Desktop/Sept 23 Companies`, `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master`, then run the tests there.
- [ ] **Step 3: Real read, no dashboard write.** In the live checkout run only `/opt/homebrew/bin/python3 refresh/finalcheck.py` then `/opt/homebrew/bin/python3 refresh/final_merge.py` (they use the last run's `final_targets.json`; `write_level.js` is not run, so `dashboard/data.js` does not change).
Expected: `refresh/work/board_status.json` exists; print counts of `last` values with `python3 -c "import json,collections; s=json.load(open('refresh/work/board_status.json')); print(collections.Counter(v['last'] for v in s.values()))"` and report them to Devanshu. No company whose `last` is `"error"` appears in `refresh/work/final_merge.json`.
- [ ] **Step 4: Comment the counts on issue #3** and close it once Monday's 08:00 refresh produces a LAST_RUN.md with the three new sections.
