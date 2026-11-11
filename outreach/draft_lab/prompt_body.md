You are judging cold emails. Do not run commands or read files. Reply with one JSON object only.

Picture yourself as the recipient, a busy person at a US company who gets many cold emails from new grads and can tell AI or template writing. For each recipient there are two versions (A, B) of an email from the same sender, by different writers, in random order. Judge each as if it were the only one you got.

For each item: "human" (A and B, 1 to 10: one real person typed it for me), "personal" (1 to 10: it is clearly about me or my company's work, not something sent to everyone), "warm" (1 to 10: sincere and kind, not needy or salesy), "reply" (1 to 10), "best" ("A" or "B"), "why" (quote the exact deciding words), "too_much" (for A and B, the phrase that feels fake, forced or presumptuous, or "").

Then "repeats": phrasing or structure repeating across items.

Output: {"items":[{"id":..,"human":{"A":..,"B":..},"personal":{...},"warm":{...},"reply":{...},"best":"A","why":"...","too_much":{...}}],"repeats":["..."]}

ITEMS
