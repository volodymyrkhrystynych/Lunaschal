# Coach push fixtures

Payloads shaped like the ones `backend/coach/deliver.py` sends, for checking how
the app handles a notification without APNs or a server:

```sh
xcrun simctl push booted com.lunaschal.mobile apple/tools/push-fixtures/coach-habit.apns
```

Turn on Settings → Coach reminders in the app first, so iOS shows them. The
simulator only tests *handling* — categories, buttons, where a tap goes —
never delivery: APNs doesn't reach a simulator, and the server can only push
to a real device after the signing steps in `apple/SIGNING.md`. Ids here are
fixtures; a Done on `coach-habit.apns` queues a tick for a habit that doesn't
exist, which the server refuses and the Todo tab reports.
