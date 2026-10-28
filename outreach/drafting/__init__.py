"""The drafting harness (#135): deterministic code that builds the model's input and checks its output.

Nothing here calls a model or the network. `voice` reads his DevVault voice files, `brief` binds them to a person and
to me.md with a brief id, and `voice_checks` verifies a finished draft. The MCP tools in `outreach.mcp.tools.drafts`
are thin wrappers around these.
"""
