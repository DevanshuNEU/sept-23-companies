# Cold email deliverability for a personal Gmail sender (research, 2026-10-18)

Read 2026-10-18. **SECONDARY** = vendor or third-party page, **UNVERIFIED** = unreadable or unsourced, *inference* = my reasoning.

## 1. Hard limits

- Consumer Gmail stops sending after "more than 500 recipients in a single email and or more than 500 emails sent in a day". Sending resumes "within 1 to 24 hours" ([Gmail help 22839](https://support.google.com/mail/answer/22839)).
- A second lock fires if "a large number of emails you send" are invalid or bounce: "You may be able to send emails again after 24 hours." No number is published (same page). Guessed addresses threaten this lock.
- Gmail API: `messages.send` costs 100 units against 6,000 units per user per minute ([quota](https://developers.google.com/workspace/gmail/api/reference/quota)). The daily limit is shared with all his clients; exceeding it returns 429 "User-rate limit exceeded (Mail sending)", and "You can't assume that a 200 response means the email was successfully sent" ([errors](https://developers.google.com/workspace/gmail/api/guides/handle-errors)).
- Workspace: 2,000 messages and 2,000 unique external recipients a day ([limits](https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace)).
- Bulk rules apply to senders of "close to 5,000 messages or more to personal Gmail accounts within a 24-hour period", counted per primary domain. Bulk status is permanent. One-click unsubscribe is "required only for marketing and promotional messages" ([FAQ](https://support.google.com/a/answer/14229414)). None of this applies at 100 a day. Outlook.com's rule also starts at 5,000 ([Microsoft](https://techcommunity.microsoft.com/blog/outlook/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730)).
- All senders: SPF or DKIM, TLS, spam rate below 0.3% ([guidelines](https://support.google.com/a/answer/81126)), ideally 0.1% ([top 10](https://support.google.com/mail/answer/15256272)). Google authenticates gmail.com mail itself (*inference*).

## 2. How receivers decide

**Gmail (and Workspace inboxes).** User reports drive filtering: "When Gmail users mark emails as spam, it increases the likelihood that future messages you send will also be classified as spam" ([Program Policies](https://support.google.com/mail/answer/16734397)). Similar emails get caught "more efficiently" ([help 1366858](https://support.google.com/mail/answer/1366858)). Mail "from an address in the recipient's contacts" is "less likely to be marked as spam" ([guidelines](https://support.google.com/a/answer/81126)). Rejections include 4.7.28 "unusual rate of unsolicited email" and 5.7.1 "likely unsolicited email" ([SMTP codes](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)).

**Microsoft 365.** Filtering "learns from ... user feedback from our consumer platform, Outlook.com" plus admin and user reports ([anti-spam](https://learn.microsoft.com/en-us/defender-office-365/anti-spam-protection-about)). Source reputation (Google's IPs, for him) is checked first ([spam vs bulk](https://learn.microsoft.com/en-us/defender-office-365/anti-spam-spam-vs-bulk-about)). Bulk complaint level (BCL) uses "internal and external sources"; the default threshold is 7 (Strict 5), sending to Junk or quarantine ([BCL](https://learn.microsoft.com/en-us/defender-office-365/anti-spam-bulk-complaint-level-bcl-about)). Defender's phishing campaign view groups mail by "content, style, and tone" and "recipient domains" ([campaigns](https://learn.microsoft.com/en-us/defender-office-365/campaigns)).

**Gateways.** Proofpoint help pages need a login. Search snippets describe bulk as mail "sent to a large list of recipients" and a filter that "searches for impersonation and mass spam attacks" (**UNVERIFIED**, [Proofpoint](https://help.proofpoint.com/Proofpoint_Essentials/Email_Security/User_Topics/040_spamsettings/Options_for_handling_Bulk_Mail)). Mimecast only offers a product blog citing "Behavioral and engagement signals" and "Organization-wide patterns" (**SECONDARY**, [Mimecast](https://www.mimecast.com/blog/what-is-graymail-and-why-ai-detection-matters/)). **No vendor publishes a per-domain message count.**

**Industry stance.** M3AAWG (Nov 2025) describes cold email that is personalized to "appear as one-to-one communications" and "may be sent at random intervals" or from "multiple sending accounts, with the intention of avoiding spam filter detection". It lists "high unknown user hard bounces, and high complaint rates" as warning signs ([M3AAWG cold email](https://www.m3aawg.org/M3AAWGPositionOnColdEmail)). Spamhaus accepts "legitimate, one-to-one messages" between strangers; "The problem begins when 'cold emailing' becomes automated, scaled, and sent in bulk" ([Spamhaus](https://submit.spamhaus.org/resources/spamhaus-take-on-cold-emailing-aka-spam/)). His design matches the surface pattern; real per-person writing is what differs (*inference*).

## 3. Bounces

- "The volume of permanent failures is one indicator used by receivers to build a reputation about a sender." "B2B mail may have a higher rate of asynchronous bounces" ([M3AAWG BCP v4](https://www.m3aawg.org/sites/default/files/doc_files/m3aawg-sender-best-common-practices-aug-27-2026.pdf)). Outlook asks senders to "Remove invalid addresses regularly" ([Microsoft](https://techcommunity.microsoft.com/blog/outlook/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730)).
- Google and Microsoft publish no bounce threshold. Amazon SES reviews accounts at 5% hard bounces and may pause them at 10%, because providers read high bounces as "a sign ... that the sender might be sending unsolicited email" ([SES](https://docs.aws.amazon.com/ses/latest/dg/reputationdashboardmessages.html)).
- Without port 25: DNS MX lookup. A null MX means the domain "does not accept email" ([RFC 7505](https://www.rfc-editor.org/rfc/rfc7505)). Servers "MAY" disable VRFY, and 252 means "cannot verify" ([RFC 5321](https://www.rfc-editor.org/rfc/rfc5321#section-3.5.3)). Microsoft 365 edge blocking is on by default for cloud mailboxes and rejects bad guesses with "550 5.4.1" ([DBEB](https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/use-directory-based-edge-blocking)). Gmail returns 550 5.1.1 ([SMTP codes](https://knowledge.workspace.google.com/admin/support/troubleshooting/gmail-smtp-errors-and-codes)). Catch-all domains accept everything, so silence proves nothing. SMTP verifiers need port 25 open ([Reacher](https://github.com/reacherhq/check-if-email-exists)).

## 4. Warm-up and ramp

- Google: "Increasing your sending volume too quickly can result in delivery issues". After deferrals, pause 15 minutes, send one message, hold below the deferral volume for 24 hours, then increase "with a common daily increase of 25% to 100%" ([top 10](https://support.google.com/mail/answer/15256272)). Also: "Avoid sending email in bursts" ([guidelines](https://support.google.com/a/answer/81126)).
- M3AAWG suggests "6 weeks of warm-up as an average" for a new domain. It flags volume jumps above 40% as abnormal ([BCP v4](https://www.m3aawg.org/sites/default/files/doc_files/m3aawg-sender-best-common-practices-aug-27-2026.pdf)).
- Verdict: his steps (+100% down to +25%) fit Google's band, but that is recovery advice for established senders, and his baseline is personal mail (*inference*). Follow-ups count: with three per person, 100 sends a day is about 30 new people (*inference*).

## 5. Content and subject lines

- Links "should be visible and easy to understand". Don't "use HTML and CSS to hide content" ([guidelines](https://support.google.com/a/answer/81126)).
- Microsoft's optional Advanced Spam Filter (off by default) can mark mail as spam for remote image links, numeric-IP URLs, .biz or .info links and "Web bugs" (tracking pixels). Empty messages and "Sensitive words" (a Microsoft-maintained list) count as high-confidence spam ([ASF](https://learn.microsoft.com/en-us/defender-office-365/anti-spam-policies-asf-settings-about)). Safe Links rewrites and scans URLs ([Safe Links](https://learn.microsoft.com/en-us/defender-office-365/safe-links-about)). Attachment detonation "typically ... completes within 15 minutes" ([Safe Attachments](https://learn.microsoft.com/en-us/defender-office-365/safe-attachments-about)).
- Plain text vs HTML: no primary source says plain text scores better, but it sidesteps every ASF HTML rule (*inference*).
- URL shorteners: no Google or Microsoft statement found. Claims that Gmail penalizes them are **SECONDARY/UNVERIFIED** ([SMTP2GO](https://www.smtp2go.com/blog/rethink-url-shorteners-in-emails/)).
- Subjects: "Don't include Re: or Fwd: in the subject header for messages that aren't actual replies or forwards" ([top 10](https://support.google.com/mail/answer/15256272)). Headers must be "accurate, and not misleading" ([guidelines](https://support.google.com/mail/answer/81126)). Spam-word lists beyond Microsoft's optional list have no primary support (**UNVERIFIED**).

## 6. Follow-ups in the thread

RFC 5322 sets In-Reply-To to the parent's Message-ID. The subject "MAY start with the string 'Re: '", once only ([RFC 5322](https://www.rfc-editor.org/rfc/rfc5322#section-3.6.5)). The Gmail API threads a reply when "The Subject headers match" and References/In-Reply-To follow RFC 2822 ([sending guide](https://developers.google.com/workspace/gmail/api/guides/sending)). A follow-up to his own message with those headers is a real reply, so "Re:" is honest there. On a new thread it is the fake Google warns about (*inference*). No primary source covers cadence.

## 7. Multiple senders, terms and CAN-SPAM

- Gmail Program Policies: "Don't use Gmail to distribute spam or unsolicited commercial mail". They prohibit "Creating or using multiple accounts or other methods intended to engage in a behavior that was previously prohibited" and actions "intended to bypass our policies, subvert restrictions placed on your account" ([Program Policies](https://support.google.com/mail/answer/16734397)). Google's Terms allow suspension for "spamming, misleading others" ([Terms](https://policies.google.com/terms)).
- Workspace AUP forbids "unsolicited mass email" and use "to evade filtering capabilities" ([AUP](https://workspace.google.com/terms/use_policy/)).
- M3AAWG names "multiple sending accounts" used to avoid filters as deceptive, and calls "attempts to bypass mail volume limits" "particularly egregious" ([cold email](https://www.m3aawg.org/M3AAWGPositionOnColdEmail)).
- A new domain's reputation starts "unknown", "similar to bad" ([BCP v4](https://www.m3aawg.org/sites/default/files/doc_files/m3aawg-sender-best-common-practices-aug-27-2026.pdf)).
- CAN-SPAM covers mail whose primary purpose is "commercial advertisement or promotion of a commercial product or service". It has no B2B exception, and penalties reach $53,088 per email ([FTC](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)). A one-to-one job inquiry is likely outside it (*inference*, not legal advice).

## 8. Monitoring without Postmaster Tools

Postmaster Tools needs DNS proof of the DKIM or SPF domain and may show nothing at low volume ([setup](https://support.google.com/a/answer/9981691)), so gmail.com is out. Signals readable through the API:

- Bounce messages in the inbox: 5.1.1 (Gmail), 5.4.1 and 5.1.10 (Microsoft).
- Policy rejections: Gmail 4.7.28 and 5.7.1; Microsoft 4.7.500 to 699 ("Suspicious activity was detected") and 5.7.511 ("banned sender") ([Microsoft NDRs](https://learn.microsoft.com/en-us/troubleshoot/exchange/email-delivery/ndr/non-delivery-reports-in-exchange-online)).
- API 429 errors and Gmail's two limit messages.
- Replies: overall rate and negative replies.

Google: "If messages are bounced, deferred, or not authorized, reduce your sending volume until the SMTP error rates drop" ([top 10](https://support.google.com/mail/answer/15256272)). Spam-folder placement stays invisible (*inference*).

## Recommendation

My judgment on top of the sources; no provider publishes these numbers.

1. **Ramp (all sends, follow-ups included).** Business days only, the same volume all day. Week 1: 20, 20, 25, 30, 30. Week 2: 35, 40, 45, 50, 60. After that, add at most 20% per week, and only after five clean days. That reaches 100 around week 5. Two weeks to 100 is too fast for cold outreach.
2. **Per company.** At most 3 a day per domain, 60+ minutes apart. At most 8 people per company in total, best-fit first. Any reply from a company pauses the rest of it for 3 business days. A complaint or "stop" ends the domain.
3. **Spacing.** Keep 4 to 9 minute gaps as pacing, not as disguise.
4. **Follow-ups.** At most 2, at 5 and 12 business days. Send them in-thread with In-Reply-To, References and "Re: <original subject>", no new links. None after a reply or bounce. They count toward both caps.
5. **Stop conditions.**
   - API 429 or either Gmail limit message: halt 24 hours, then resume at half volume.
   - Any 4.7.28, 5.7.1 "unsolicited", Microsoft 4.7.5xx or 5.7.511, or gateway policy rejection: drop that domain and halve global volume for 3 days.
   - Hard bounces above 3% of the last 100 sends: verified addresses only. Two hard bounces at one domain: stop guessing there.
   - Any "spam" or "stop" reply: suppress that person forever. Two in 7 days: halt everything.
   - Reply rate under 2% after 200 first touches: stop scaling.
6. **Address checks.** Syntax check, then MX (drop null MX and domains with no MX or A record). Send one guessed pattern per company and wait 24 hours (late bounces) before reusing it. One variant per person. Treat catch-alls as unknown. No SMTP probing from home.
7. **Content.** Plain text, no pixel, no link tracking. At most one full, visible URL, never shortened. No attachment on first touch (offer the resume). Signature: name and one link. At least two person-specific facts. Never the same body to two people at one company.
8. **Subjects.** Unique per person and accurate, naming the role or team. No "Re:" or "Fwd:" on first touch, no urgency or all caps.
9. **Second sender: no.** Splitting volume across accounts conflicts with the Program Policies and matches M3AAWG's "multiple sending accounts". A new Workspace domain needs about 6 weeks of warm-up. Revisit only after a clean month at 80 to 100 a day, as a primary identity.
