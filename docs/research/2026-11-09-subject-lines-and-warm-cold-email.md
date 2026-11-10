Source: GPT (Codex CLI with live web search), run 2026-11-09 for the drafting design. Lines starting with > are quotes kept as the source shows them. Used by `docs/superpowers/specs/2026-11-09-handwritten-drafts-design.md`; the lab results that tested it are in `2026-11-09-drafting-lab.md`.

# Part 1. Subject lines that get a stranger to open, and answer

**The evidence supports relevance, clarity, and a small request more consistently than any particular subject-line formula.** Length and personalization findings conflict across datasets. None of the studies below directly establishes the best subject for a US new graduate asking an engineer for a referral. Treat sales findings as hypotheses, not guarantees. [Yesware](https://www.yesware.com/blog/email-subject-line-analysis/), [Backlinko/Pitchbox](https://backlinko.com/email-outreach-study), [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf)

Research conducted with live web search; every cited page was opened. Examples are published wording, with their evidentiary limitations stated. Short excerpts are used rather than reproducing complete copyrighted emails.

## What the measured evidence actually says

| Source and population | Sample and method | Outcome and finding | Applicability |
|---|---|---|---|
| **Sahni, Wheeler, and Chintagunta, Marketing Science, 2018** | Randomized field experiments with three companies, sending advertising emails to **millions of individuals**. The accessible abstract does not give the exact combined sample. | Adding the recipient’s name to the subject increased opens from **9.05% to 10.80%**: about **20% relative**, not 20 percentage points. Sales leads increased from **0.39% to 0.51%**. | Strong causal evidence **for mass marketing personalization**; not a test of personal networking or referrals. [Paper](https://pubsonline.informs.org/doi/10.1287/mksc.2017.1066) |
| **Boomerang, 2016** | Observational analysis of **over 40 million emails** for which customers requested response reminders. | **Replies:** subjects of **3 to 4 words** performed best; longer subjects declined gradually. Emails without subjects received replies **14%** of the time. | Everyday email, including existing relationships; not an isolated cold-email sample. [Analysis](https://blog.boomerangapp.com/2016/02/7-tips-for-getting-more-responses-to-your-emails-with-data/) |
| **Backlinko with Pitchbox, 2019** | Observational analysis of **12 million outreach emails**, principally content promotion, links, guest posts, and related outreach. | **Replies:** overall response rate **8.5%**; **36 to 50 characters** performed best. Personalized subjects correlated with **30.5% more replies**; personalized bodies with **32.7% more**. | Relevant to unsolicited outreach, but not hiring. The article gives inconsistent longer-versus-shorter summaries, **24.6%** in its overview and **32.7%** later, so that particular lift is unreliable as presented. [Analysis](https://backlinko.com/email-outreach-study) |
| **Yesware, 2014** | **Over 500,000 sales emails**; observational. | **Opens and replies:** subjects containing “hey” underperformed other common keywords. Subject length had **no significant effect** in the reported analysis. | Sales emails, not necessarily all first contacts. [Analysis](https://www.yesware.com/blog/email-subject-line-analysis/) |
| **Yesware, 2019** | **262,518 templates**, described as **4.5 million data points**; observational. | **Opens and replies:** subjects of **1 to 5 words** performed best. | Template-based business email; the sample differs from Yesware’s earlier analysis. [Analysis](https://www.yesware.com/blog/response-insights/) |
| **Gong with 30MPC, later research** | **85 million-plus cold sales emails**; observational vendor analysis. | **Replies:** short subjects outperformed longer ones. **Opens:** lowercase showed **+11%**, title case **+6%**, relative to sentence case in its chart. Empty subjects increased opens **30%** but reduced replies **12%**. | Large, explicitly cold dataset; commercial prospecting differs substantially from personal job outreach. [Report](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf) |
| **Lavender, 2023** | Vendor reports findings from its data but **does not disclose the sample size or experimental design on this page**. | Reports questions reducing opens **56%**, numbers **46.33%**, and lack of title case **30%**. | Lower-confidence quantitative evidence because methods and denominators are missing; its title-case advice conflicts with Gong. [Article](https://lavender.ai/blog/cold-email-subject-line-tips) |
| **Mailchimp, current guidance** | Research on customer campaigns; **sample size and effect sizes unspecified** on this page. | Recommends descriptive subjects, personalization, and no more than **9 words/60 characters**; evaluates **opens**. | **Mass marketing guidance**, not evidence that a personal note should resemble a campaign. [Guidance](https://mailchimp.com/help/best-practices-for-email-subject-lines/) |

These are mostly correlations: sender quality, targeting, body text, and relationship may differ alongside the subject. Even a measured reply can be a rejection. Boomerang explicitly says its subjectivity analysis cannot distinguish positive responses from hostile ones. [Boomerang](https://blog.boomerangapp.com/2016/02/7-tips-for-getting-more-responses-to-your-emails-with-data/)

## The specific subject-line questions

| Feature | Supported conclusion |
|---|---|
| **Length** | Short is a reasonable starting point, but there is no universal optimum. Boomerang favors 3 to 4 words, newer Yesware 1 to 5, and Pitchbox 36 to 50 characters; earlier Yesware found no significant effect. Preserve useful context rather than obeying a magic count. [Boomerang](https://blog.boomerangapp.com/2016/02/7-tips-for-getting-more-responses-to-your-emails-with-data/), [Yesware](https://www.yesware.com/blog/email-subject-line-analysis/), [Pitchbox](https://backlinko.com/email-outreach-study) |
| **Lowercase versus title case** | Unsettled. Gong favors lowercase; Lavender favors title case. Neither establishes what engineers opening personal job requests prefer. [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf), [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips) |
| **Questions** | A question mark is not a proven advantage. Lavender reports worse opens for subject questions. That does not contradict asking one specific question **inside** the email. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips), [Sriram Krishnan](https://sriramk.com/coldemail) |
| **Recipient’s name** | The randomized marketing study supports it in advertising; personal outreach remains uncertain. A name can attract attention without demonstrating substantive relevance. [Sahni et al.](https://pubsonline.informs.org/doi/10.1287/mksc.2017.1066), [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips) |
| **Company name** | Useful when it clarifies the actual purpose. I found firsthand hiring examples, but no controlled study isolating its effect in new-grad referral requests. [Nick Singh’s account](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf) |
| **Mutual/shared detail** | Firsthand accounts support relevant common ground: a known colleague, shared institution, or actual work. They do not establish that any arbitrary shared hobby improves replies. [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf), [Gleb Bahmutov](https://glebbahmutov.com/blog/rating-recruiter-pitches/) |
| **Numbers** | No universal benefit. Lavender and Gong report adverse sales-email associations. A truthful credential or result can still provide useful context in personal outreach. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips), [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf), [Philip Kiely](https://www.softwaresessions.com/episodes/writing-for-software-developers/) |
| **Curiosity** | Genuine relevance can create curiosity; obscuring the purpose is unnecessary. Empty subjects illustrate the danger: more opens, fewer replies. [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf) |
| **“Quick Question” and vagueness** | Lavender identifies it as a cliché; Pitchbox argues that it gives recipients insufficient context. Neither proves it always fails. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips), [Pitchbox](https://backlinko.com/email-outreach-study) |
| **Sales/spam language** | Recipient accounts object to vague promotional claims and empty praise. This is evidence about **human recognition**, not proof of a universal Gmail blacklist of individual words. [Bahmutov](https://glebbahmutov.com/blog/rating-recruiter-pitches/), [Lavender](https://lavender.ai/blog/a-new-method-for-cold-email-subject-lines) |

## Gmail: subject and opening as one unit

Gmail can display a body snippet alongside the subject; on mobile, preview text appears beneath the subject. For an ordinary plain-text note, the opening words therefore do double duty as the beginning of the message and part of its inbox presentation. Litmus says clients typically extract preview text from the first lines, with behavior depending on client and settings. [Litmus](https://www.litmus.com/blog/the-ultimate-guide-to-preview-text-support), [AWeber](https://blog.aweber.com/email-marketing/how-long-should-my-email-subject-line-be.htm)

**There is no fixed Gmail character allowance that applies to every screen.** Published observations differ:

| Source | Gmail phone subject | Gmail desktop subject |
|---|---:|---:|
| AWeber, 2024 | **38 characters** | **98** on its 15-inch laptop example |
| BuzzStream, 2026, combining cited device tests with its own | Approximately **37 to 40** | Approximately **60 to 70**, varying with window width |

These are observed display counts, not Gmail’s maximum subject length. Their disagreement is a reason to front-load meaning and inspect the actual inbox view. [AWeber](https://blog.aweber.com/email-marketing/how-long-should-my-email-subject-line-be.htm), [BuzzStream](https://www.buzzstream.com/blog/email-subject-line-length/)

For preview text, Litmus recommends keeping the important content early and generally under **90 characters**, while acknowledging settings can show much more, or nothing. I did not verify a universal desktop/phone Gmail snippet count. [Litmus](https://www.litmus.com/blog/the-ultimate-guide-to-preview-text-support)

**Practical inference:** let the subject identify the topic; let the opening supply the personal reason for writing. Spending that opening entirely on pleasantries sacrifices useful preview context. This combines rendering evidence with firsthand job-seeker advice, rather than a randomized test of that exact combination. [Litmus](https://www.litmus.com/blog/11-reasons-to-test-every-email-before-you-send), [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf)

## Real subjects with documented recipient attention

1. **Software job search, published in a guide hosted by February 2020**

   > Ex-Google & Microsoft Intern Interested in Working FT at Periscope Data

   Nick Singh presents this in a recruiter sequence he says worked well. He reports cold outreach producing software interviews at Airbnb, Snapchat, and Cloudflare. **Limitation:** he does not publish a Periscope reply establishing that this individual subject caused a response. [Original guide, p. 10](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf)

2. **Founder-to-founder outreach, April 2013**

   > How I lost your Sperry’s.. and apt. And why you should meet with me.

   Dave Daily sent this to Noah Kagan, referring to a real auction involving Kagan’s apartment and shoes. Kagan published the email and says they met. It is unusually personal because the underlying event was unusual; copying its theatricality would miss the point. [Kagan’s account](https://noahkagan.com/cold_email/)

3. **Acquisition outreach**

   > Acquire Indie Hackers.

   Courtland Allen’s firsthand interview describes receiving this from Patrick Collison before Allen had contacted Stripe. **Limitation:** the sender was already a prominent founder; this is not comparable evidence for an unknown graduate. The page’s original publication date was not established. [Allen’s account](https://kit.com/resources/creator-stories/courtland-allen)

4. **Internship application, published January 2025**

   > A Resume Doesn’t Sell, But I Do.

   Stripo’s head of recruitment, Valeriia Samoilova, recalls this sales-intern subject making her inspect the résumé. **Outcome is attention, not a documented reply or hire**, and the wordplay suits sales more directly than engineering. [Recruiter interview](https://stripo.email/blog/subject-line-for-cold-email-internship-how-to-craft-the-perfect-line-to-get-noticed/)

## What busy recipients say

- Paul Graham: **“Two-liners I often reply to immediately.”** His explanation is that long emails get deferred and remain unanswered. [Personal contact guidance](https://www.paulgraham.com/info.html)
- Sriram Krishnan: **“Anything with friction gets sorted into a ‘later’ bucket.”** He recommends a specific request requiring little work. [2020 account](https://sriramk.com/coldemail)
- Dropbox founder Drew Houston: **“there is no commandment from God that you have to respond to every email you receive.”** He describes ignoring cold requests and tiring of questions answerable with basic homework. [2018 interview](https://tim.blog/2018/08/29/the-tim-ferriss-show-transcripts-drew-houston/)

# Part 2. Warm messages that sound like real people

**The strongest accessible examples do not all provide preserved originals.** Below, “published email” and “recalled wording” are deliberately distinguished. I did not verify a large collection of complete, under-120-word thank-you emails from new graduates with published replies.

## 1. Appreciation without a hidden assignment

> “That was brilliant. I loved it. This was amazing. Count me as a fan.”

Derek Sivers gives this wording while describing how he contacts creators whose work he loves. He recalls doing this about **20 times**, with only one or two nonresponses; some exchanges became friendships.

**Why it feels warm:** uncomplicated enthusiasm and appreciation, with no demand attached. **Limitation:** this is his retrospective account of typical wording, not an archived individual email. His existing prominence also limits transferability. [2020 interview](https://sive.rs/2020-04-email)

## 2. An intern admitting why something matters

> “I’m an unpaid intern; this would mean so much if you would let me put this ad into the world.”

Chris Beresford-Hill recalls emailing Mark Cuban for permission to use Cuban’s name in an advertisement. He reports a reply about an hour later:

> “Go for it. -M.”

**Why it feels personal:** a plainly stated position, a specific piece of work, and an honest feeling about what permission would mean. **Limitation:** these are remembered words in a **2024** interview about an earlier event; the original email and subject are not reproduced. [Firsthand account](https://tim.blog/2024/01/13/chris-beresford-hill-transcript/)

## 3. A recruiter actually reading an engineer’s work

> “The README documentation is really well thought out and organized which says a lot about you as an engineer.”

Engineer Gleb Bahmutov publishes this recruiter email and explicitly identifies a subsequent email as arriving after he replied.

**Why this passage feels attentive:** it notices an often overlooked part of engineering work, documentation, and recognizes care in it. The full message also names repositories and posts. **Limitation:** the complete email contains substantial praise and is longer and more polished than the desired note. Its useful feature is demonstrated attention. [Published correspondence, 2015](https://glebbahmutov.com/blog/rating-recruiter-pitches/)

## 4. A new graduate speaking directly

> “Hey, I just graduated from college and made $20,000 selling a book.”

Philip Kiely recalls opening his pitch to *Software Sessions* this way. The recipient discusses receiving it, and the resulting interview is the source itself.

**Why it sounds human:** ordinary words, a concrete event in his life, and no inflated professional introduction. **Limitation:** this is more candid and interesting than tender; the recipient had already seen the book on Hacker News. It does not establish success with a completely unfamiliar sender. [Sender to recipient conversation, June 2020](https://www.softwaresessions.com/episodes/writing-for-software-developers/)

## Where warmth becomes flattery, neediness, or obligation

**Moderate warmth has some quantitative support.** Boomerang found slightly or moderately emotional emails received **10 to 15% more responses** than neutral ones; excessive positivity lost that advantage. This is observational evidence across ordinary email, not proof that adding emotional language helps a referral request. [Boomerang](https://blog.boomerangapp.com/2016/02/7-tips-for-getting-more-responses-to-your-emails-with-data/)

**False familiarity can repel.** Hannah Herman describes a recruiter calling themselves her biggest fan:

> “Reader, I had no idea who this person was”

She found the familiarity uncomfortable and recommends work-related, unexaggerated compliments. [Firsthand account at Zapier](https://zapier.com/blog/how-to-write-good-recruiting-emails/)

**Interest is different from requesting ongoing responsibility.** Sivers welcomes introductions and appreciation, but discourages broad mentorship requests and bundles of questions. A sincere note can still impose too much work. [Sivers](https://sive.rs/2020-04-email)

**Recipients differ sharply.** Sivers enjoys hearing from strangers; Houston describes a much stricter boundary. Warmth cannot establish an obligation to answer. [Sivers](https://sive.rs/2020-04-email), [Houston](https://tim.blog/2018/08/29/the-tim-ferriss-show-transcripts-drew-houston/)

# Part 3. Originality without gimmicks

## Originality comes from the underlying observation

Bahmutov’s answered recruiter email recognizes specific repositories, writing, and documentation. Daily’s subject refers to an actual auction. Both contain facts that would become wrong if sent indiscriminately to other people. **My inference:** the most defensible route to originality is a real reason this particular sender is writing to this particular recipient. [Bahmutov](https://glebbahmutov.com/blog/rating-recruiter-pitches/), [Kagan](https://noahkagan.com/cold_email/)

A shared detail also needs a reason to matter. Krishnan recommends a thoughtful response to something the recipient wrote or said, and showing actual work when institutional credentials are unavailable. That supplies substance beyond a compliment. [Krishnan](https://sriramk.com/coldemail)

## The writer’s life can supply the unexpected detail

Kiely’s graduation and book launch are specific developments, expressed plainly. In another interview, he describes sending nearly **100** interview requests to obtain **11** interviews for his book, adapting requests to each person’s work and accommodating their schedules. That account supports specificity and effort; it does not isolate which sentence produced a yes. [Software Sessions](https://www.softwaresessions.com/episodes/writing-for-software-developers/), [Entrepreneurial Coder](https://www.ecpodcast.io/episodes/30-philip-kiely-nailing-a-book-launch-with-only-13-twitter-followers)

## Readers notice the seam between a template and personalization

Bahmutov explicitly penalizes a style mismatch between boilerplate and inserted custom text. A researched opening can still feel artificial if the rest sounds like a different writer. [His scoring system](https://glebbahmutov.com/blog/rating-recruiter-pitches/)

Recruiter Meridith Dennes describes repeatedly receiving student emails using the same phrases:

> “cut their teeth”

> “probably don’t get many of these emails.”

Her **April 2026** account illustrates the problem with trying to announce uniqueness: recipients can recognize wording the sender believes is distinctive. This is a firsthand account from finance, not a measured engineering-hiring result. [Original post](https://prospectrockpartners.com/15-students-just-sent-me-the-exact-same-cutting-your-teeth-in-finance-cold-email-lets-talk-about-ai-and-networking/)

## A relationship is not necessarily a transaction

Alexey Guzey describes strangers contacting him about his writing and exchanges developing into calls and friendships. He also explicitly acknowledges that more than half of his invitations produced a refusal or silence. This is useful counterweight to collections that publish only successful messages. [2019 to 2021 account](https://guzey.com/how-to-make-friends-over-the-internet/)

# Part 4. Source-based takeaways

These are **practical rules inferred from the sources**, not ten experimentally established laws for referral emails.

## Ten rules for subjects

1. **Name the actual topic.** Descriptive wording sets an honest expectation. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips)
2. **Start short, then retain words that add clarity.** The length studies disagree; brevity should serve comprehension. [Yesware](https://www.yesware.com/blog/email-subject-line-analysis/), [Pitchbox](https://backlinko.com/email-outreach-study)
3. **Put the distinguishing information early.** Phone subjects truncate sooner than desktop subjects. [AWeber](https://blog.aweber.com/email-marketing/how-long-should-my-email-subject-line-be.htm)
4. **Consider the opening alongside the subject.** Both can appear before the email is opened. [Litmus](https://www.litmus.com/blog/11-reasons-to-test-every-email-before-you-send)
5. **Use a shared connection when it is real and relevant.** Shared background supplies context; it is not a magic word. [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf)
6. **Treat first-name insertion as optional.** Marketing evidence does not establish a referral-email advantage. [Sahni et al.](https://pubsonline.informs.org/doi/10.1287/mksc.2017.1066)
7. **Do not assume numbers improve attention.** Sales findings provide counterevidence. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips)
8. **Choose natural capitalization; avoid claiming a universal winner.** Published vendor findings conflict. [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf), [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips)
9. **Avoid fake conversation cues.** Google says subjects must represent content accurately; use reply/forward prefixes only for actual replies/forwards. [Google](https://support.google.com/mail/answer/81126?hl=en)
10. **Prefer a useful response over an attention trick.** Blank subjects improved opens while hurting replies. [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf)

## Ten rules for a warm, original body

1. **Give a concrete reason for choosing this person.** Actual engagement with their work is stronger than interchangeable admiration. [Bahmutov](https://glebbahmutov.com/blog/rating-recruiter-pitches/)
2. **Introduce yourself clearly.** Let the recipient understand who is speaking. [Krishnan](https://sriramk.com/coldemail)
3. **Include one relevant piece of evidence about yourself.** A project or accomplishment gives interest substance. [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf)
4. **Use plain words.** The successful graduate pitch is straightforward and concrete. [Kiely](https://www.softwaresessions.com/episodes/writing-for-software-developers/)
5. **Express appreciation proportionately.** Warmth need not become superlative praise. [Boomerang](https://blog.boomerangapp.com/2016/02/7-tips-for-getting-more-responses-to-your-emails-with-data/)
6. **Ask one specific thing.** A bounded request is easier to answer than a broad appeal. [Sivers](https://sive.rs/2020-04-email)
7. **Keep the effort required small.** Make the next action clear. [Krishnan](https://sriramk.com/coldemail)
8. **Be direct about wanting a job.** A recruiter can recognize a concealed application request. [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf)
9. **Keep one consistent voice throughout.** Inserted personalization should not sound detached from the remainder. [Bahmutov](https://glebbahmutov.com/blog/rating-recruiter-pitches/)
10. **Respect silence and boundaries.** Warmth does not entitle the sender to a conversation. [Houston](https://tim.blog/2018/08/29/the-tim-ferriss-show-transcripts-drew-houston/)

## Patterns to avoid, and what the sources actually establish

| Published pattern | Reason to avoid it |
|---|---|
| **“Quick Question”**, **“Thoughts?”**, **“15 minutes?”** | Identified as clichés by Lavender; limited topic information. Not proven universally ineffective. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips) |
| **“hoping to connect”** | Lavender explicitly calls it unoriginal and nondescriptional. [Lavender](https://lavender.ai/blog/cold-email-subject-line-tips) |
| **“Hey”** as a borrowed magic subject | Yesware’s sales results contradict transferring its famous campaign success indiscriminately. [Yesware](https://www.yesware.com/blog/email-subject-line-analysis/) |
| Blank subject | Evidence of an opens to replies tradeoff. [Gong](https://www.gong.io/files/gong-guide-how-to-master-cold-email-get-the-data-backed-guide-based-on-85-million-emails.pdf) |
| Fake **“Re:”** or **“Fwd:”** | Misrepresents the relationship or thread. [Google](https://support.google.com/mail/answer/81126?hl=en) |
| **“I hope this email finds you well.”** | Singh regards it as expendable or insincere in short job outreach. This is practitioner judgment, not an experiment. [Singh](https://fromtheguts.com/wp-content/uploads/2020/02/Cold_Email_Tips_To_Land_Your_Dream_Job.pdf) |
| **“my biggest fan”** claims from a stranger | A documented recipient found this false familiarity uncomfortable. [Herman](https://zapier.com/blog/how-to-write-good-recruiting-emails/) |
| **“pick your brain”**, **“lifelong passion”**, **“unique perspective”** | Dennes reports these as repeated student-outreach language. Avoid substituting them for an observation and a precise request. [Dennes](https://prospectrockpartners.com/15-students-just-sent-me-the-exact-same-cutting-your-teeth-in-finance-cold-email-lets-talk-about-ai-and-networking/) |
| Announcing that the recipient probably receives few such emails | Dennes documents receiving essentially the same purportedly distinctive approach repeatedly. [Dennes](https://prospectrockpartners.com/15-students-just-sent-me-the-exact-same-cutting-your-teeth-in-finance-cold-email-lets-talk-about-ai-and-networking/) |
| A broad mentorship request as the first ask | Creates an undefined ongoing obligation. [Sivers](https://sive.rs/2020-04-email) |