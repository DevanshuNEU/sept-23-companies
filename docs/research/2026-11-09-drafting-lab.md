# Drafting lab, 2026-11-09

Three rounds on the same 12 approved first emails from the Outbox: 4 engineers, 5 managers, 2 recruiters and one staff
engineer, at 9 companies. Recipients are left out here because this repo is public; the full side-by-side files are in
the harrisburg workspace's `.context/` (`drafting-lab.md`, `research2/`).

Every judge was a fresh GPT session (Codex CLI, `codex exec`, read-only, no tools) that did not know who wrote what.
Versions were shuffled per recipient. One model judging 12 emails is a strong hint, not proof; the point is to compare
versions, not to score any one email.

## Round 1: original vs GPT rewrite vs Claude rewrite

| Version | Feels human | Would reply | Picked best |
|---|---|---|---|
| Original (Knock today) | 5.3 | 4.4 | 0 of 12 |
| GPT rewrite, after two rounds of critique | 4.7 | 4.8 | 1 of 12 |
| Claude rewrite (v1) | 7.7 | 7.2 | 11 of 12 |

GPT's rewrites turned formal and uniform ("I'm contacting you as a...", "I don't know whether..." in nearly every one)
and once invented a detail about a link. So GPT judges; it does not draft.

## Round 2: warmth added (no visa line in any version)

| Version | Human | Warm | Reply | Best |
|---|---|---|---|---|
| Original | 4.9 | 4.7 | 4.5 | 0 |
| v1 (plain, specific, direct) | 7.3 | 6.4 | 7.4 | 7 |
| heart (v1 plus a true story where one fits, plus made-up feeling lines) | 7.2 | 7.0 | 7.1 | 2 |
| heart_true (heart without the made-up feelings) | 7.2 | 6.7 | 7.2 | 3 |

What the judge flagged as "too much" every time: lines that talk about the email itself or the reader's inbox. "You
must read a lot of these", "you don't owe me anything, so thank you for opening this", "Short one, and an honest one",
"I hope it's okay that I'm asking", "your inbox is probably full of people like me". Made-up feelings ("still one of my
favorite days at work") were caught as written for effect, and they are not facts anyway.

A true story won when it was about the work and fit the reader: a reranker that first made code search worse, sent to
an engineer at an AI coding company; teaching a cloud course, sent to a university's VP of engineering; an honest
"two years against your three". The same story lost when it did not fit the reader.

## The inbox test

Each recipient saw an inbox: our candidate subjects with their Gmail preview text (the start of the body), mixed with
6 decoys of the usual kind ("Application for Software Engineer Role - New Grad 2026", "Quick question", "Referral
Request | Software Engineer I", "Would love to connect!", a sales email). Sender names were made up for every row. The
judge, as that person on a phone between meetings, picked at most 3 to open.

Round 2: decoys were opened 0 of 72 times. First picks were concrete and true: "my reranker made search worse first",
"<a Northeastern student group they were in>, then <their company>", "800ms to 280ms", "two years against your three",
"which team owns engineer I?". Warm but vague subjects lost or were archived on sight: "one engineer to another", "a
small favor, if you have a minute", "from a ta who loved the job", "I couldn't see my own waste". Short generic subjects
("data platform question") were opened but rarely first. The judge quoted the preview line in most of its reasons.

## Round 3: the combined recipe (v3)

v3 keeps v1's directness, uses a true story only where it fits the reader, takes the subject from the email's most
concrete true detail, makes the first line pay the subject off, and drops every line about the email itself.

| Version | Human | Warm | Reply | Best | Opened first in the inbox |
|---|---|---|---|---|---|
| v1 | 6.8 | 6.0 | 6.1 | 1 | 6 |
| heart_true | 6.8 | 6.4 | 6.3 | 2 | 1 |
| v3 | 7.8 | 6.9 | 7.2 | 9 | 8 |

Still repeated across the v3 set, per the judge: the same compact credential paragraph ("one of two engineers", "two
years", "every two weeks to every three days"), a job title turned into fake personalization ("You manage engineers
there"), the same project scaffolding ("The project I'd point to", "The project I'd show them"), the same closing shape
(is this yours, else who), and the same precise-metric rhythm. These come from a thin evidence pool: six projects and
two jobs, sent to many people. More true material, and code that counts what has been used, are the fix.

## Sources it agrees with

`2026-11-09-subject-lines-and-warm-cold-email.md` (GPT with live web search) and `2026-11-09-human-cold-outreach.md`
(ChatGPT Deep Research). Also: Boomerang's 40 million email analysis (3 to 4 word subjects got the most replies;
slightly warm emails about 15% more replies than neutral, very positive ones lost it), Gong's 85 million cold emails
(short subjects; buzzwords hurt), Sahni, Wheeler and Chintagunta 2018 (a name in the subject lifted opens in marketing
email) against a 2023 Marketing Letters replication that found no lift, and Kumar and Epley 2018 and Liu et al. 2022
(people underestimate how much a sincere note or a reach-out is appreciated).
