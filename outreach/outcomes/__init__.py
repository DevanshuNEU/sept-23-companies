"""The outcomes domain: periods, wins, the 7 day prompt, the funnel, findings and corrections.

Standard library only. Pure unless a function takes a `conn`; time is always a parameter, no function reads the
clock. The API (outreach/api) wraps these; the sender writes email touch context through `context.build_context`.
"""
