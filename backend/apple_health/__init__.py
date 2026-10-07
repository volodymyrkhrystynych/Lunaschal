"""Apple Health, mirrored from the iPhone app.

HealthKit lives only on the phone (the Watch's data reaches it on its own), so
the phone reads it and posts batches to `/api/apple-health/sync`. Nothing here talks
to Apple. `ingest.py` validates and upserts a batch, `nights.py` turns sleep
samples into the night a day's wake/sleep come from, and `queries.py` reads the
tables back for the Lifestyle card and for analysis.
"""
