"""Job-application automation: profile, tailoring, tracking, email linkage.

The module is deliberately layered so the parts that can be wrong are the parts
that can be tested. `linkage`, `keywords` and `retention` are pure — no DB, no
network, no model — and hold every judgement call the feature makes. The DB and
HTTP layers above them only move rows around.
"""
