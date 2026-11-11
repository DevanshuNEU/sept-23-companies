-- 0011: every person whose email already bounced goes on the no-go list (#224). Data only, nothing is rebuilt.
-- From now on the sender does this itself when it reads a bounce (outreach.sending.inbound). This puts the ones that
-- bounced before it did. A person whose address was cleared as dead (`mark_email_bounced`) has no address on record
-- and stays out: they are reachable on LinkedIn. A person who already has a no-go row, lifted or not, is left alone.
INSERT INTO no_go(person_id, reason, source, source_ref, note, created_at)
SELECT p.person_id, 'other', 'rules', 'touch:' || t.touch_id, 'Email bounced: ' || p.email || '.',
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM touch t JOIN person p ON p.person_id = t.person_id
WHERE t.state = 'bounced' AND p.email IS NOT NULL AND p.email <> ''
  AND NOT EXISTS (SELECT 1 FROM no_go g WHERE g.person_id = p.person_id);
