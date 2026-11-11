# A bounced address goes on the no-go list (#224)

Ruling (Devanshu, 2026-11-11): an address that bounces is never emailed again, and a bounced email does not use up one
of the 100 sends a day.

## What changes

1. `outreach/sending/inbound.py` `_apply_bounce`: after the touch is marked bounced, the person gets a `no_go` row
   (`reason other`, `source rules`, `source_ref` the notice's Gmail id, note `Email bounced: <address>.`) and a
   `no_go added` event. Every bounce class counts except Gmail's own sending-limit notice (it never reaches
   `_apply_bounce`) and delay notices (not bounces). A person with any earlier `no_go` row, lifted or not, gets none.
2. The address stays on the person row. `add_people` and `intake` merge a pasted address into the person already on
   record and refuse a person on the no-go list, so the same address cannot come back as a new person.
3. `outreach/sending/signals.py` `counted_sends_on`: `sends_on` less each bounced touch's last sent message. The
   queue's daily limit (`queue.prepare`) and the status count use it. The bounce rate still uses `sends_on`.
4. `0011_bounced_no_go.sql`: puts everyone whose touch already bounced (and whose address is still on record) on the
   list. Runs once, when `python -m outreach.install` applies it in the live checkout.

## Not here

- Deleting the notices in Gmail. He does it by hand; it needs a wider Gmail scope and a new sign-in.
- Person-level no-go also stops a LinkedIn connect to that person (intake refuses a no-go person). He lifts it in the
  app when that is wrong.

## Tests

`tests/test_sending_inbound.py` `BounceNoGoTest` (classes hard, policy, blocked with no code in the snippet,
unthreaded; limit and delay add nothing; lifted rows; read twice; the day's count) and
`tests/test_bounced_no_go_migration.py`.
