# Goals and rewards

Ask Chat to track a daily criterion for a number of consecutive days with a
named prize. For example: “Ten pushups each day for seven days, starting today;
the prize is a theater trip.” Chat saves a durable goal. `check_goal` evaluates
it immediately, including today's records; the seventh day's completion earns
the prize at noon just as it does at night. A separate explicit action records
that the user redeemed the prize. Each goal awards one prize, not a recurring
prize every seven days.

The web Lifestyle view shows Goals & Rewards, Check now, redemption, and daily
evidence with journal navigation. Native Chat uses the same backend tools;
there is no new native Goals screen in this version.

## Rules and evidence

- Days follow the app's existing local 04:00 boundary. An unfinished current day
  does not break yesterday's streak. Future days never qualify.
- Repetition goals use the shared exercise canonicalizer and sum structured
  workout sets for the day. The exercise and minimum reps define the same
  criterion for journal evidence. More complex daily criteria use journal text.
- Journal completion is interpreted by the shared AI layer with a bounded JSON
  schema. A completion must cite an existing entry and a verbatim quotation.
  Plans, fictional activity, other people's activity, ambiguous quantities and
  ambiguous dates must not become completion. Model interpretation can still
  be mistaken; the evidence is visible so the user can correct the source.
- Either source can establish completion. Workout and journal quantities are
  never added together: they may describe the same activity. Separate partial
  amounts split across the two sources therefore need a clear journal account
  or a complete workout log.
- A model failure remains unresolved. An explicit check retries immediately;
  background retries wait ten minutes. A successful interpretation is cached
  until source content changes. Oversized daily text stays unresolved rather
  than being silently truncated.
- Editing/deleting a source or a late upload changes its content fingerprint and
  triggers recalculation. If an earned streak no longer holds, the original
  award receipt stays, marked for review; redemption is blocked. Redeemed
  rewards remain redeemed with a review flag when explicitly rechecked.
- Rules are immutable. Cancel an unwanted goal and create a replacement. The
  first version supports targets of 1–366 consecutive days. Historical starts
  are allowed within the past year; a running goal does not expire automatically.

## Implementation

`backend/goals/service.py` is shared by Chat's four goal tools, `/api/goals`,
and the minute-by-minute background sweep. All progress, source fingerprints,
and reward timestamps persist in `goals` and `goal_days`; restart needs no
in-memory checkpoint recovery. These tables are created by the idempotent
schema execution and seeded by `seed_goals` in the demo seeder.

The sweep uses the AI service's background priority and respects
`LUNASCHAL_NO_SCHEDULERS`. It checks active, earned and review-needed goals;
cancelled and redeemed goals are not polled. UI reads never invoke a model.
Only the Chat toolbox exposes goal tools; Writing and research do not.

Verification: `backend/tests/test_goals.py`, the schema seeder test, chat tool
regressions, and `src/components/Lifestyle/GoalsCard.test.tsx`.
