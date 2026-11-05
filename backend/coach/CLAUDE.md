# Coach (`backend/coach/`)

The agent's side of the habits, the goals and the daily logging: a review of each day, the excuses that explain it, an evening check-in, and (from `backend/goals/rollover.py`) the close of each goal period. The research behind each rule is cited where the rule lives; the short version is self-monitoring with feedback works, self-compassion beats self-criticism, and a single missed day doesn't matter but the second one does.

## The review of a day

`day_facts.gather` lists what one 4am day was supposed to hold — every habit that existed, the weigh-in, a calorie log (a meal in `food_entries` counts), each daily-rule goal's verdict, and the day's chat to-do bar — and whether each happened. `coach_behaviors` switches the first three off; weight and calories also count only once the user has ever logged one, so a review never marks down something they never took up.

**The rating is arithmetic, never the model's** (`review_score.py`): done ÷ (counted − excused), banded onto 1–5. The model writes only the two or three sentences under it. A model asked to rate a day it is also being kind about drifts toward kindness, and a rating that can be talked up is not feedback. The agent may move a rating **one star**, with a stored reason (`adjust_day_rating`); the override is dropped the moment the arithmetic itself changes, since it described records that no longer stand.

`draft` re-scores any time. `finalize` is called once — by the morning briefing for yesterday, or by the coach loop after the briefing window if the briefing didn't run — and writes the summary. **A model that can't answer leaves a template summary and `degraded`**: the rating never depended on it, so the review is still complete with the GPU paused. The briefing prepends `review.render()` to its stored content (as it appends the oddities), so the line is there whatever the prose came out as, and puts `review.card()` on `metadata.review` for the web's `CoachCards` and the phone's `ReviewCard`.

## Excuses are context, not failures

`day_excuses` holds why something didn't happen, as the user said it in chat (`record_excuse`) or from the card's Explain. **External and one-off reasons excuse the target**: it leaves the day's score, and a habit's streak freezes rather than resets (`backend/habit_streaks.py` — an excused day neither counts nor breaks). **An internal reason** ("didn't feel like it") is recorded on the item but excuses nothing — it is an obstacle. When the same obstacle comes back on three days in two weeks (`review_score.recurring`), the review asks the model for an if-then plan ("When the kettle goes on, I will step on the scale") and the briefing offers it as a `habit_plan` card; accepting it sets that habit's `cue`. That is WOOP's obstacle → plan, and it is why a recurring excuse is never just excused again.

`record_excuse` takes the day as `today`/`yesterday` and resolves the 4am day key itself, so the model never does date arithmetic across the boundary, and it rewrites any review card already sent for that day so the chat doesn't keep a stale rating.

## The loop

`scheduler.tick` runs every minute: close finished goal periods, finalize yesterday once the briefing window has passed, and post the evening check-in at `coach_checkin_hour` when `coach_checkin_enabled`. **Off by default, and every setting is re-read every tick.** The spoken task nudge this replaces (removed in 0aa6e67) nagged every 45 minutes and read its switch once at startup — both are what this is built not to do. The check-in is a template, not a model call, so it arrives with inference paused.

## Reminders that learn (`engine.py`, `bandit.py`, `policy.py`, `rewards.py`, `compose.py`)

Off until `coach_enabled`. Every day after 4am each behaviour the user tracks — the weigh-in, calories, each habit, and setting goals for a period that has none — gets **one decision** in `coach_decisions`, at a slot the bandit samples. When the slot comes, `policy.blocked` may skip it, checked *then* and not at planning: already done, quiet hours (a window that may wrap midnight), the day's cap, or a habit done five days running with no reminder (it has stuck; one in seven still goes out, to notice if it unsticks). Otherwise the bandit picks a framing — plain, goal-linked, identity, streak, tiny — or holds it back.

Two rules make the learning honest, and both come from how reminder studies are run (HeartSteps' micro-randomized trial):

- **A quarter of decisions send nothing, at random** (`coach_no_send_rate`, floored at 0.1). Without that control there is no telling what a reminder caused from what would have happened anyway. Held decisions are scored exactly like sent ones.
- **Sending has a cost.** The policy sends only when the best framing's sampled success beats not sending by `SEND_COST`, so something that happens anyway stops being nagged about.

`reward` is whether the thing got done inside its window (weight 2 h, calories 3 h, a habit or goals by the end of the 4am day), from when the reminder was due. An excused day and a snooze are left out of learning; a dismissal is recorded as a response, never as a zero. **Posteriors are recomputed from the rows every time** (Beta, discounted ×0.98 a day so habituation shows) — there is no model state to drift. The context of each decision (hour, weekend, place, recent load, last week's rate) is logged for a richer model later; today only the behaviour and slot are learned from, because one person's few decisions a day would take months to fit more.

**The model writes the words, never the choice.** `compose.template` has a message for every behaviour × framing, filled from the overarching goal, the habit's cue and the streak; the model may rewrite it *within the same framing*, and when it can't (paused, preempted, off) the template goes out — timing matters more than wording. Once a framing is clearly ahead (`context_text`: enough evidence and a narrow interval) it is said in every chat prompt and the briefing, so the agent can say what works rather than guess.

## Delivery (`deliver.py`, `backend/push/`)

Everything the coach says — check-in, goal rollover, each reminder — is **first a message in today's chat, then a push**. The message is committed before Apple is called, so the chat is the record and the fallback. Push is APNs token auth over HTTP/2 (`backend/push/apns.py`); the key lives only in the server's environment (`APNS_KEY_PATH`, `APNS_KEY_ID`) and no route returns it. The send loop is `backend/push/notify.py`'s, shared with the Code tab, and a push goes only to rows with `push_devices.coach` set — the phone's reminders switch, since the Code page registers the same phone without asking about reminders. A token Apple calls dead disables its device row; the phone registering again re-enables it. A device token belongs to one APNs environment — Debug builds sandbox, TestFlight production — and the phone says which. Lock-screen buttons (Done, Log weight, Log calories, Reply, Later) are Core's `CoachNotification.swift`; a typed reply goes to `POST /api/coach/reply`, becomes a chat message, and the agent answers it with the app closed (`backend/chat/autoreply.py`) — which is how "busy day, couldn't" from the lock screen reaches `record_excuse`. Setup steps are in `apple/SIGNING.md`.

