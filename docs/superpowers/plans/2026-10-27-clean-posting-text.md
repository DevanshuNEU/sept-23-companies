# Clean posting text before the rules and the model read it (#112)

Goal: invisible characters and hidden HTML can no longer change a sponsorship or citizenship reading, and never reach
the posting reader or the profile judge. Ported from `tools/posting_guard.py` in ai-job-search-dev/ai-job-search (MIT,
Mads Lorentzen): its character lists and its hidden-style test. Its reporting half (instruction-shaped lines,
mixed-script words) is out of scope.

## Task 1: `strip_invisible` in `outreach/rules/text.py`

- Test (`tests/test_rules_decide.py`): `do n‍ot sponsor` reads no, `do not spon­sor` reads no, `U.S. citi​zens`
  sets citizenship, a U+2028 between two sentences splits them, the quote holds no invisible character.
- `strip_invisible(text)`: drops zero-width (U+200B to U+200D, U+2060 to U+2064), bidi controls (U+200E, U+200F,
  U+202A to U+202E, U+2066 to U+2069), soft hyphen, BOM, tag characters (U+E0000 to U+E007F) and variation selectors
  (U+FE00 to U+FE0F, U+E0100 to U+E01EF); U+2028 and U+2029 become line breaks.
- `decide()`'s `_clean` calls it on all four inputs. `text.py` and `decide.py` are in `RULE_FILES`, so the ruleset
  version changes and the next crawl tick re-decides every open posting.

## Task 2: hidden elements in `html_to_text`

- Test (`tests/test_crawl_text.py`): `hidden`, `display:none`, `visibility:hidden`, `font-size:0`,
  `color:transparent`, off-screen `left:-9999px`, a collapsed box with `overflow:hidden`, an entity-encoded or
  comment-split style, nested same-name tags, a `/>` on a non-void tag, an unclosed hidden `<li>` or `<p>` closed by a
  sibling, a hidden void tag (does not hide the rest), `<template>` and `<svg>` dropped, invisible characters dropped.
  Not hidden: `aria-hidden="true"` (on screen), `opacity:0` (Webflow fades every block in from it), `height:0` or
  `overflow:hidden` alone.
- `visible_only=False` gives master's output exactly (checked on all 110 HTML fixtures); the careers-page watcher
  passes it, so its stored fingerprints keep and a collapsed tab of jobs still counts.
- The extractor keeps a stack of open elements with HTML's implied ends (a `<p>` closes on any block opener; `li`,
  `dd`, `dt`, `tr`, `td`, `th`, `option` close on a sibling) and skips text while an element on the stack is hidden.

## Task 3: `save_body` stores clean text

- Test (`tests/test_crawl_bodies.py`): a body with a tag-character run is stored and hashed without it.
- `save_body` calls `strip_invisible` before hashing, so the reader and the judge read what the rules read.

Deploy: Python changed in the crawler; kickstart `com.devanshu.outreach-crawl` (its next tick runs the rebuild).
