You are testing subject lines. Do not run commands or read files. Reply with one JSON object only.

For each item, you are the person named in "you_are", at work, on your phone between meetings. Your inbox shows these unread emails from people you don't know (each row is a different person): sender name, subject, and the preview text Gmail shows. You will open at most 3 of them today. The rest you'll archive without reading.

For each item give:
- "open": the row numbers you'd open, best first (at most 3).
- "never": the row numbers you'd archive on sight because they look like a template, AI, a mass email or sales.
- "why": for your first pick, one sentence quoting the exact words in the subject or preview that made you open it.

Output: {"items":[{"id":..,"open":[..],"never":[..],"why":"..."}]}

ITEMS
