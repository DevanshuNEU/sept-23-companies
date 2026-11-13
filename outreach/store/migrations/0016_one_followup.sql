-- 0016: one follow-up (#9 multi-sender ruling, 2026-11-12). 0001 to 0015 are never edited.
-- A follow-up that has not gone out is skipped when it is seq 2 or later, or when its touch was sent from the
-- resting account (account 1: the thread's follow-up moves with the new sender, not from here). planned, drafted
-- and approved to skipped are legal in the 0005 trigger. Each skip gets one message event, keyed by the message.
CREATE TEMP TABLE one_followup_skip AS
  SELECT m.message_id, m.revision AS old_revision, p.company_id AS company_id
  FROM message m
  JOIN touch t ON t.touch_id = m.touch_id
  JOIN person p ON p.person_id = t.person_id
  WHERE m.state IN ('planned','drafted','approved')
    AND (m.seq >= 2 OR (m.seq >= 1 AND t.account_id = 1));

UPDATE message
   SET state = 'skipped',
       -- a skipped row needs a subject and body (0005 CHECK); a planned row has none, so it gets a marker. The row
       -- stays (not deleted) so the (touch, seq) slot is taken and a re-plan cannot bring the follow-up back.
       subject = CASE WHEN length(trim(subject)) = 0 THEN '(skipped: follow-up rule changed)' ELSE subject END,
       body = CASE WHEN length(trim(body)) = 0 THEN '(skipped: follow-up rule changed)' ELSE body END,
       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
       revision = revision + 1
 WHERE message_id IN (SELECT message_id FROM one_followup_skip);

INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail, idempotency_key)
  SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'message', message_id,
         company_id, 'skipped', 'system', '{"reason":"rule_changed","ruling":"multi-sender 2026-11-12"}',
         'skipped_rule_changed:' || message_id
  FROM one_followup_skip;

DROP TABLE one_followup_skip;
