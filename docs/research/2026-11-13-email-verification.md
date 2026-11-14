> **Evidence only.** Decisions live in issue #9; where this file disagrees, #9 wins. Nothing here is built. The
> Recommendation section is a proposal for Devanshu to accept or change.

# Checking that a mailbox exists without sending (research, 2026-11-13)

Read 2026-11-13. **SECONDARY** = vendor blog, aggregator or forum, **UNVERIFIED** = I could not read the primary page,
*inference* = my reasoning. Prices are from the vendors' own pages on 2026-11-13 unless marked.

## 0. Short answer

- Every open-source tool that checks a mailbox does it the same way: connect to the recipient's mail server on port
  25 and ask `RCPT TO` without sending (sections 1 and 3).
- This Mac cannot reach port 25. The one test connection timed out (section 2). So no open-source mailbox check can
  run here without renting a server or a port 25 proxy.
- Free checks that need no port 25 still help: syntax, MX lookup, null MX, the never-send list, disposable and role
  lists, and a cache of domains already known to be catch-all.
- For the rest, a hosted API (Bouncer) costs about $15 to $24 a month at 3,000 checks (section 6).
- No tool, open or hosted, can tell whether an address at a catch-all domain exists. Expect a real share of
  "risky" or "unknown" answers that go to Devanshu.

## 1. Open-source verifiers

Dates are from the GitHub API (`gh api repos/<owner>/<repo>`, `/releases`, `/commits`), read 2026-11-13.

| Tool | Language | License | Last release | Last commit | Mailbox check over SMTP | Library or server |
|---|---|---|---|---|---|---|
| [reacherhq/check-if-email-exists](https://github.com/reacherhq/check-if-email-exists) (Reacher) | Rust | MIT (since 2026-10-07) | v0.11.11-beta.0, 2026-10-30 | 2026-11-09 | yes, with catch-all probe | Rust crate, CLI, Docker HTTP server |
| [AfterShip/email-verifier](https://github.com/AfterShip/email-verifier) | Go | MIT | v1.5.0, 2026-09-10 | 2026-11-02 | yes, off by default, catch-all probe | Go library, sample API server |
| [truemail-rb/truemail](https://github.com/truemail-rb/truemail) | Ruby | MIT | v4.0.0, 2026-10-16 | 2026-10-16 | yes | Ruby gem; server is [truemail-rack](https://github.com/truemail-rb/truemail-rack) (last release v0.11.0, 2024-04-29) |
| [JoshData/python-email-validator](https://github.com/JoshData/python-email-validator) | Python | Unlicense | 2.3.0, 2025-08-26 ([PyPI](https://pypi.org/project/email-validator/)) | 2026-11-08 | **no**, syntax and DNS only | Python library |
| py3-validate-email ([GitHub, archived](https://github.com/karolyi/py3-validate-email); [new home](https://git.ksol.io/karolyi/py3-validate-email)) | Python | LGPL on [PyPI](https://pypi.org/project/py3-validate-email/); license changed 2025-04-16, new terms not read (**UNVERIFIED**) | 1.0.5.post2 on PyPI, 2024-06-01; tag v1.0.12, 2024-06-02 | 2025-04-16 | yes | Python library |
| [mfbx9da4/deep-email-validator](https://github.com/mfbx9da4/deep-email-validator) | TypeScript | MIT | v0.1.23, 2026-04-21 | 2026-04-21 | yes | Node library |
| [sdwolfe32/trumail](https://github.com/sdwolfe32/trumail) | Go | BSD-3-Clause | n/a | archived, last push 2018-06-15 | yes | dead, skip |
| `validate-email` on PyPI ([page](https://pypi.org/project/validate-email/)) | Python | LGPL | 1.3, 2015-03-23 | n/a | yes | dead, skip |

**What the licenses mean for Knock.** MIT, BSD and the Unlicense allow any use, including commercial, if the notice is
kept. Reacher's docs say: "You are free to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of Reacher ... including as part of proprietary software or commercial services"
([Reacher licensing](https://docs.reacher.email/self-hosting/licensing.md)). Reacher was AGPL with a paid commercial
license before; the switch is commit "chore(license): Switch to MIT (#1640)" on 2026-10-07, then "Remove Commercial
License and AGPL mentions (#1661)" on 2026-10-09 (`gh api repos/reacherhq/check-if-email-exists/commits?path=LICENSE.md`).
Reacher's [pricing page](https://app.reacher.email/en/pricing) still offers a "Commercial License Plan", so the site
lags the repo (*inference*). LGPL (py3-validate-email) is fine for personal use as a library. For a personal,
non-commercial, self-hosted tool, none of these licenses is a blocker (*inference*, not legal advice).

### What each one checks

**Reacher.** Syntax, MX, disposable, SMTP deliverability, disabled mailbox, full inbox, catch-all, role account,
Gravatar and HaveIBeenPwned ([README](https://github.com/reacherhq/check-if-email-exists)). Result `is_reachable` is
`safe`, `risky`, `invalid` or `unknown`. "safe" is "very likely to be delivered (bounce rate below 2%)"; catch-all is
listed under risky; unknown means "the email provider blocks our real-time verification"
([is-reachable](https://docs.reacher.email/getting-started/is-reachable.md)). The catch-all probe sends `RCPT TO` for
a random 15-character address on the same domain (`core/src/smtp/connect.rs`, `smtp_is_catch_all`). It retries "to
avoid greylisting" (same file). `core/src/rules.json` skips the catch-all probe for gmail.com, hotmail and yahoo.

**AfterShip.** Syntax, MX, SMTP with catch-all detection "enabled by default", disposable, free provider, role
account, Gravatar, typo suggestions. SMTP is off by default: "because most of the ISPs block outgoing SMTP requests
through port 25 ... the module will not perform SMTP checking by default"
([README](https://github.com/AfterShip/email-verifier)). `reachable: "unknown"` "means that the server does not allow
real-time verification of an email right now, or the email provider is a catch-all email server" (README FAQ).

**Truemail.** Allow and block lists, regex, MX, MX blocklist, then SMTP as "a final, fourth validation level"
([README](https://github.com/truemail-rb/truemail)). An optional `smtp_safe_check` reads SMTP error text. The README
says nothing about catch-all detection or role accounts. You must set a `verifier_email`.

**email-validator (Python).** Syntax and an MX check (falls back to A or AAAA, honors a reject-all SPF). It does not
talk SMTP: "there is nothing to be gained by trying to actually contact an SMTP server, so that's not done here",
because "servers are good at not giving away whether an address is deliverable or not" and mail "can bounce mail
after a delay" ([README](https://github.com/JoshData/python-email-validator)). This is the only one that runs here
with no port 25.

**py3-validate-email.** Regex, a disposable-domain blocklist (auto-refreshed after 5 days), and an SMTP conversation
"cancelling it before sending anything". Temporary errors give an ambiguous result; "greylisting is a frequent cause
for this" ([git.ksol.io README](https://git.ksol.io/karolyi/py3-validate-email)). No catch-all detection is
mentioned.

**deep-email-validator.** Regex, typos, disposable list, MX, "Validates mailbox exists on SMTP server"
([README](https://github.com/mfbx9da4/deep-email-validator)).

**Lists worth reusing without any tool.** [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains)
is CC0 and was pushed today. AfterShip ships `cmd/build_metadata/role.txt` and Reacher ships `core/src/misc/roles.txt`
(both MIT).

### Gmail, Google Workspace, Microsoft 365, Outlook.com, Yahoo

How the big providers answer a plain `RCPT TO`, from their docs:

- **Gmail and Google Workspace** answer an unknown user with `550 5.1.1` "The email account that you tried to reach
  does not exist" ([Google SMTP errors](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)).
  So Google does reveal existence at `RCPT TO`. A Workspace admin can add an "Unrecognized/Catch-all" route that
  takes mail for unknown users ([Google catch-all](https://support.google.com/a/answer/12943537)); then the domain
  accepts everything.
- **Microsoft 365** with Directory-Based Edge Blocking rejects unknown recipients with `550 5.4.1 Recipient address
  rejected: Access denied`. "If all recipients for your domain are in Exchange Online, DBEB is already in effect."
  Hybrid domains need the MX to point at Microsoft for it to work, and "There might be infrequent instances where
  recipient addresses that don't exist ... are allowed to relay"
  ([DBEB](https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/use-directory-based-edge-blocking)).
  So most cloud-only Microsoft 365 tenants reveal existence too.
- **Domains behind Proofpoint or Mimecast** (common at US companies): Reacher detects them by MX and still uses SMTP
  (`core/src/smtp/verif_method.rs`). Whether these gateways reject unknown users at `RCPT TO` depends on each
  customer's setup. I found no vendor page that says (**UNVERIFIED**).

What each tool does per provider:

| Provider | Reacher | AfterShip | Truemail, py3-validate-email, deep-email-validator |
|---|---|---|---|
| Gmail, Google Workspace | plain SMTP. An old HTTP trick (`mail.google.com/mail/gxlu`) is kept but `#[deprecated]`: "This method doesn't work anymore, as Google has patched the vulnerability" (`core/src/smtp/gmail.rs`) | plain SMTP. The Gmail API verifier was removed in v1.4.1 ([CHANGELOG](https://github.com/AfterShip/email-verifier/blob/main/CHANGELOG.md)) | plain SMTP |
| Microsoft 365 ("Hotmail B2B") | plain SMTP. A OneDrive URL trick (HEAD on `<tenant>-my.sharepoint.com/personal/...`, 403 means the user exists) is in `core/src/smtp/outlook/microsoft365.rs` but marked `#[allow(dead_code)]` and not called from `check_smtp` (`core/src/smtp/mod.rs`). Its own comment says a negative answer "is ambigious". Since PR #1668 (2026-10-13), `451 4.4.4 ... no mail-enabled subscriptions` is `invalid` | plain SMTP | plain SMTP |
| Outlook.com, Hotmail ("B2C") | default is a headless Chrome on "Hotmail's password recovery page" via a WebDriver at `RCH_WEBDRIVER_ADDR` (`verif_method.rs`). PR #1669 says plain SMTP returns `unknown` with "4.5.3 Recipients belong to multiple regions" for real mailboxes | plain SMTP | plain SMTP |
| Yahoo | default is headless on "Yahoo's password recovery page"; a Yahoo API mode also exists (`verif_method.rs`) | Yahoo API verifier removed in v1.5.0 as "non-functional" (CHANGELOG) | plain SMTP |

The headless and API tricks are undocumented by the providers. They depend on page layout and on the provider not
blocking the browser. AfterShip removed both of its tricks because they stopped working (CHANGELOG), and Reacher
retired its Gmail one. Treat all of them as fragile (*inference*). For Knock this matters little: recipients are at
company domains, so the consumer tricks rarely apply (*inference*).

## 2. The port 25 problem

**This Mac.** On 2026-11-13 I ran `nc -vz -w 5 gmail-smtp-in.l.google.com 25` once. Result:
`nc: connectx to gmail-smtp-in.l.google.com port 25 (tcp) failed: Operation timed out`. No SMTP command was sent.
After that first failure `nc` did not exit (it likely moved on to the host's other address; the name resolves to
`172.253.63.26` and `2607:f8b0:4004:c0b::1a`), so I stopped it. DNS resolution worked, so the network was up. A
silent timeout, not a refusal, is what an ISP or router filter on outbound port 25 looks like (*inference*). I did not
run a control test on another port, so a local firewall or VPN is not ruled out.

**Home ISPs.**

- Comcast/Xfinity: "Comcast does not support port 25 for the transmission of email by our residential Internet
  customers", because much of its use comes from "computers that have been infected by malware"
  ([Xfinity help](https://www.xfinity.com/support/articles/email-port-25-no-longer-supported)). Forum posts say
  exceptions ended 2024-08-25 (**SECONDARY**,
  [Xfinity forum](https://forums.xfinity.com/t5/Internet/ANSWERED-Xfinity-Blocked-Internet-Ports-List-and-How-to/ta-p/3232503)).
- Verizon: a Verizon community post says the block "will be applied to FIOS and High Speed Internet services that use
  dynamic IP addresses" (**SECONDARY**, old, [Verizon forum](https://forums.verizon.com/t5/Fios-Internet/Blocked-ports/td-p/6975)).
  No current official page found (**UNVERIFIED**).
- AT&T: forum and blog reports of a default residential block that support can lift on request (**SECONDARY**,
  [ka9q.net](https://www.ka9q.net/Uverse/port25.html)). AT&T's forums now redirect to a "community forums sunset"
  page, so the official post could not be read (**UNVERIFIED**).
- Spectrum: no official page found (**UNVERIFIED**).
- Industry: M3AAWG has urged providers since 2005 to block port 25 from residential and dynamic space
  ([MAAWG paper](https://www.internetsociety.org/wp-content/uploads/2018/01/MAAWG-Managing-Port-25-for-Residential-or-Dynamic-IP-Space-Benefits-of-Adoption-and-Risks-of-Inaction-0511.pdf)).
  RFC 5068 records that "some providers ... block all use of port 25 SMTP for mail that is being sent outbound" and
  "offers no recommendation" itself ([RFC 5068](https://www.rfc-editor.org/rfc/rfc5068)).
- The tools agree: Reacher "Many ISPs and cloud providers block outgoing requests on port 25"
  ([install](https://docs.reacher.email/self-hosting/install.md)); AfterShip "it will instead hang until timeout
  error ... there is no easy workaround" (README); Trumail "requires bidirectional communication on port 25 which most
  residential ISPs restrict" ([README](https://github.com/sdwolfe32/trumail)).

**Clouds are not an easy way out.** EC2 blocks port 25 to public addresses by default; you can ask AWS to lift it
([AWS](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html)). DigitalOcean: "SMTP ports 25,
465, and 587 are blocked on Droplets" ([DigitalOcean](https://docs.digitalocean.com/support/why-is-smtp-blocked/)).
Reacher's answer is a paid SOCKS5 proxy with port 25 open: "if you wish to process more than very small volumes you
will need SMTP proxy servers" (README; [proxies](https://docs.reacher.email/self-hosting/proxies.md)). I did not read
proxy prices (**UNVERIFIED**).

## 3. Risks of probing from a home IP

- **Blocklists.** Spamhaus PBL lists "end-user IP address ranges from which email should never be sent directly to the
  final destination". Such IPs "should not be attempting to directly deliver unauthenticated SMTP email to any Internet
  mail server"; ISPs add these ranges themselves ([Spamhaus PBL](https://www.spamhaus.org/blocklists/policy-blocklist/)).
  A home IP is likely on it already (*inference*; check at check.spamhaus.org).
- **What a listed IP gets back.** Gmail: `550 5.7.1` "The IP you're using to send email is not authorized to send
  email directly to our servers", and `550 5.7.25` when the IP has no PTR record
  ([Google SMTP errors](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)).
  Google requires a PTR "that resolves to a hostname" for sending IPs ([guidelines](https://support.google.com/a/answer/81126)).
  Home IPs rarely have one that matches (*inference*).
- **False results, the worst risk.** Reacher issue [#1684](https://github.com/reacherhq/check-if-email-exists/issues/1684)
  (open, filed 2026-11-09): when the verifier's IP is on Spamhaus, Microsoft 365 answers every `RCPT TO` with
  `550 5.7.1 Service unavailable, Client host [...] blocked using Spamhaus`, and Reacher v0.11.11 reports both real and
  random addresses as `invalid`. The reporter measured Microsoft 365 "invalid" going "from ~3% to ~78%" while listed
  (a user's own numbers, **UNVERIFIED**). AfterShip warns the same code is ambiguous: "Yandex answers an unknown
  recipient with the same `5.7.1` that Apple uses to say our sending IP is blocklisted"
  ([CHANGELOG](https://github.com/AfterShip/email-verifier/blob/main/CHANGELOG.md), PR #222). For Knock this means
  a home-IP probe could put good people on the never-send list.
- **Greylisting.** "Temporarily degraded service to unknown email clients", answered with a 4xx such as 421 or 450
  ([RFC 6647](https://www.rfc-editor.org/rfc/rfc6647)). Probes read it as unknown unless they retry later.
- **Rate limits and filters.** Gmail rate-limits by IP with `421 4.7.0` and `421 4.7.28` (Google SMTP errors).
  Spamhaus: "Sending high volumes of verification probes without an attempt to actually send an email will often
  trigger filters", "thus invalidating the data", and verifiers with poor reputation "may be blocked or temp-failed";
  it calls probing "an attempted breach of the receiver's security policies"
  ([Spamhaus, 2015](https://www.spamhaus.org/resource-hub/deliverability/on-the-dubious-merits-of-email-verification-services)).
- **Late bounces.** RFC 5321 lets a server accept at `RCPT TO` and fail later; those "SHOULD treat a failure ... as a
  'subsequent failure'" ([RFC 5321 3.3](https://www.rfc-editor.org/rfc/rfc5321#section-3.3)). A "valid" probe is not
  a promise.
- **Defaults that impersonate.** Reacher's default `MAIL FROM` is "reacher.email@gmail.com" and its default `EHLO` is
  "gmail.com" (`core/src/smtp/verif_method.rs`). AfterShip defaults to `user@example.org` and `localhost`
  (`constants.go`) and says to set `FromEmail()` and `HelloName()` (README). A real setup needs a domain you own
  (*inference*).

**Does probing touch his Gmail or Microsoft 365 accounts?** No. A probe is a TCP connection from this Mac to the
recipient's MX. Knock sends through the Gmail API, which runs on Google's servers, and the university account sends
through Microsoft's. Neither sees a probe (*inference* from how SMTP works; no provider page says it). Two cautions:
never put his own addresses in `MAIL FROM` (a probe would then claim to come from him over an IP his domain does not
authorize, *inference*), and a hosted API is an HTTPS call that touches no mail account at all.

## 4. Accuracy

- **Tools' own docs.** Reacher claims only that "safe" means bounce rate "below 2%"
  ([is-reachable](https://docs.reacher.email/getting-started/is-reachable.md)). AfterShip, Truemail and the Python
  libraries make no accuracy claim. I found no published benchmark of any open-source verifier (**UNVERIFIED** gap).
- **Catch-all is unsolved everywhere.** Reacher calls catch-all "risky"; AfterShip calls it "unknown"; Bouncer puts
  "Accept All" under risky ([Bouncer docs](https://docs.usebouncer.com/llms-full.txt)). MillionVerifier advertises
  "Advanced catch-all verification at no extra cost" ([MillionVerifier](https://www.millionverifier.com/)) but does
  not say how (**UNVERIFIED**).
- **The best benchmark I found** is vendor-run: Hunter, 3 Nov 2025, 15 tools through Clay, about 2,700 real business
  addresses whose truth came from Hunter's own email activity, plus 300 invalid ones. Unknown counts as wrong. Top
  score 70.00% (Hunter), Bouncer 65.43%, NeverBounce 63.17%, ZeroBounce 60.70%. Hunter says the method may favor
  itself. MillionVerifier and Reacher were not tested
  ([Hunter](https://hunter.io/email-verification-guide/best-email-verifiers)). Read: even hosted tools give no
  clear answer on about a third of real B2B addresses (*inference*). Other "99%" figures come from vendor blogs
  (**SECONDARY**).
- **Microsoft 365.** Plain SMTP works well on cloud tenants because DBEB rejects unknown users (section 1). It fails
  when the probing IP is blocklisted (#1684), and DBEB itself lets "infrequent" unknown addresses through (DBEB page),
  which reads as a false "valid". Hosted verifiers
  probe from IPs with "high IP reputation" (Reacher SaaS wording on its [pricing page](https://app.reacher.email/en/pricing)),
  which is the main thing they sell (*inference*).
- **Apollo's "verified".** Apollo's help center says it keeps addresses at catch-all domains it judges valid: "we
  don't eliminate a valid email just because it belongs to a catch-all domain" (search snippet of
  [How Apollo Verifies Emails](https://knowledge.apollo.io/hc/en-us/articles/10826699994381); the page returned 403,
  **UNVERIFIED**). That fits the wrong "verified" addresses Devanshu sees (*inference*).

## 5. Hosted options (prices read 2026-11-13)

| Service | Smallest buy | Cost of 3,000 checks | Free tier | Unknown charged? | Credits expire? |
|---|---|---|---|---|---|
| [Bouncer](https://www.usebouncer.com/pricing/) | 1,000 for $8; 5,000 $35; 10,000 $60 | $24 (3 x 1,000) or $21 (share of a 5,000 pack) | 100 credits | "never charge ... for 'unknown' results" | "never expire" |
| NeverBounce | $0.008 each, pay as you go (**SECONDARY**, [emelia.io](https://emelia.io/hub/neverbounce-pricing)) | about $24 | 1,000 trial (**SECONDARY**) | not read | 12 months (**SECONDARY**) |
| [ZeroBounce](https://www.zerobounce.net/email-validation-pricing) | 2,000 for $39; ZeroBounce ONE $99 a month (min 10,000) | about $58 (*inference*, no 3,000 tier shown) | 100 a month, refills | "never charge you for 'unknown' results" | never with ONE; otherwise per terms |
| [MillionVerifier](https://www.millionverifier.com/) | "Plans From $39"; slider starts at 10K; 50,000 $89 | about $12 a month if $39 buys 10,000 (*inference*) | 100 credits | catch-all check "at no extra cost" | "Credits never expire" |
| [Reacher SaaS](https://app.reacher.email/en/pricing) | SaaS 10K plan €69 a month; a €699 plan (likely the 100K one) | €69 | no free trial for SaaS; 30-day refund | not read | monthly plan |
| Reacher self-host | free (MIT) plus server and proxy | $0 plus proxy (**UNVERIFIED**) | n/a | n/a | n/a |

NeverBounce's pricing page returned 403 to every fetch, so its row is secondary only.

Bouncer API facts ([docs](https://docs.usebouncer.com/llms-full.txt)): statuses are `deliverable`, `risky`,
`undeliverable`, `unknown`. Reasons: `rejected_email` ("email address does not exist"), `invalid_domain`,
`invalid_email`, `low_deliverability` (mostly "catch_all / accept_all ... or full mailbox"), `low_quality` (mostly
disposable), and for unknown `dns_error`, `unavailable_smtp`, `unsupported`, `timeout`. Flags include
`domain.acceptAll`, `account.role`, `account.fullMailbox`, and a `toxicity` score. The real-time endpoint
`GET /v1.1/email/verify` answers within 10 seconds (max 30), allows 1,000 requests a minute, returns "5% more
'unknown' results" than batch, and adds `retryAfter` after greylisting. Bouncer's own advice: remove `undeliverable`,
toxicity 4 or 5, and risky with full mailbox; keep accept-all and unknown only "if your deliverability is not impacted".

## 6. Recommendation for Knock

**Use both: free local checks first, a paid Bouncer check for what is left. Do not self-host a prober.** *(inference
throughout, from sections 1 to 5)*

Why not open source alone: port 25 is closed here (section 2). A home IP is likely on the PBL, and #1684 shows that a
listed IP turns Microsoft 365 answers into false "invalid", which would put real people on the never-send list. A
VPS with port 25 or a SOCKS5 proxy, a domain for `EHLO` and `MAIL FROM`, a PTR record and reputation upkeep cost more
time than $20 a month saves.

**Step 1, local and free, in Python, no port 25.**

1. Already on the never-send list (`bounced_address`, migration 0017): block, no paid check.
2. Syntax and MX with `email-validator` (Unlicense; needs `dnspython` and `idna` per PyPI). Its deliverability check
   wants an MX record "(Except a Null MX record" (README). No MX or a null MX
   ([RFC 7505](https://www.rfc-editor.org/rfc/rfc7505)): undeliverable.
3. Disposable domain (CC0 list) or role account (MIT role lists): ask Devanshu.
4. Domain cache: if a paid check marked this domain catch-all (`acceptAll = yes`) in the last 30 days, skip the paid
   check and ask Devanshu. A catch-all answer for one address is the answer for all.
5. Same address checked in the last 30 days: reuse the stored result.
6. Record the MX provider (Google, Microsoft, Proofpoint, Mimecast, other) from the MX host, as Reacher does
   (`core/src/mx/mod.rs`). It costs nothing and shows Devanshu why an answer is unknown.

**Step 2, Bouncer real-time API** for every address that passes step 1. Of the four hosted tools he would pick from, Bouncer
scored best in the one benchmark found (65.43%, behind Hunter, Clearout and Kickbox), is the price he already named, does not charge unknown, and its credits do not expire.

**Monthly cost at 3,000 checks.** Worst case every check is paid: $24 a month in 1,000 packs, or $21 a month from
5,000 packs. With the cache and reuse cutting perhaps 15% to 30% (a guess; Knock sends several people per company):
about $15 to $20 a month, using a 5,000 or 10,000 pack. The 100 free credits cover a trial. MillionVerifier is the
cheap fallback at about $12 a month, but it has no independent accuracy numbers.

**Mapping into Knock.**

| Result | Source | Knock does |
|---|---|---|
| deliverable | Bouncer `deliverable` | send: the draft goes to the Outbox as now; his click still approves |
| undeliverable | Bouncer `undeliverable` (`rejected_email`, `invalid_domain`, `invalid_email`); local no MX, null MX, bad syntax | block, and add to the never-send list. `bounced_address.source` allows only `bounce`, `marked`, `backfill` today, so this needs a new value such as `verifier` and a migration |
| risky | Bouncer `risky`: accept-all, full mailbox, disposable (`low_deliverability`, `low_quality`); local disposable or role | ask Devanshu (Needs-you), showing the reason. Bouncer itself says to remove full mailbox, so show that reason first |
| catch-all | `domain.acceptAll = yes`, or the domain cache | ask Devanshu; one answer per domain is enough |
| unknown | Bouncer `unknown` (`timeout`, `unavailable_smtp`, `unsupported`, `dns_error`) | ask Devanshu; a retry after `retryAfter` may resolve greylisting |

Store per address: status, reason, `acceptAll`, provider, raw JSON, and the check time, so a later bounce can be
compared with what the verifier said. A bounce after a `deliverable` answer is the real accuracy number for Knock.

**Open questions for Devanshu.**

- Should a verifier `undeliverable` go on the same never-send list as a real bounce, or a separate list he can clear?
  #1684 shows verifiers can be wrong.
- Is sending each recipient's address to Bouncer (a third party) acceptable?
- Should risky accept-all addresses at companies he cares about go out anyway, given about a third of B2B answers land
  there (section 4)?
