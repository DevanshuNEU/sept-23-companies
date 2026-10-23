"""The local MCP server (#13): lets a Claude Code session (or any MCP client) read the outreach store and do only
what Claude may do. Claude never approves or sends an email; approval is Devanshu's click and sending is the
sender's job. Every connection a tool gets is opened by `outreach.mcp.guard`, which refuses every write the tool
did not declare.

Run it with `python -m outreach.mcp` (stdio). `--check` opens the store and counts the tools;
`--print-registration` prints the `claude mcp add-json` command for the live checkout.
"""
