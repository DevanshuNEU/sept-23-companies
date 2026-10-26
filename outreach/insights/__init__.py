"""Insights (#81): which companies gave Devanshu interviews and what they have in common.

Standard library only. Every read is set based (each table once for all companies, never a query per company), and
nothing here writes to the store except `duplicates --raise`, which raises merge items he settles in Needs you. The
export writes one file, outside the repo. Merged companies count once: every read follows `company.merged_into` to
its end. Below the minimum sample there are counts and never a rate.
"""
