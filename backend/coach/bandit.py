"""Which reminder to send, and when: Thompson sampling. Pure; the caller
passes the random source, so every choice is reproducible in a test.

What is learned, per behaviour (weight, calories, each habit, goal-setting):

- **the framing** — plain, goal-linked, identity, streak, or the tiny
  version — against **not sending at all**, and
- **the time slot**, among the few the policy offers for that behaviour.

Each is a Beta posterior over "did the thing get done within its window",
counted from `coach_decisions` with older evidence **discounted** (×0.98 a
day), so what worked in spring doesn't outvote what works now — reminder
effects fade as they become familiar (HeartSteps saw it within about three
weeks).

Two rules keep the learning honest:

- **A share of decisions sends nothing, at random** (`no_send_rate`, 25% by
  default). Without that control there is no way to tell what a reminder
  caused from what would have happened anyway — the micro-randomized-trial
  design reminders like this are evaluated with.
- **Sending has a cost.** The policy sends only when the best framing's
  sampled success beats not sending by more than `SEND_COST`, so a behaviour
  that happens anyway stops being nagged about.
"""
from dataclasses import dataclass

ARMS = ('plain', 'goal', 'identity', 'streak', 'tiny')
NO_SEND = 'none'
GAMMA = 0.98
SEND_COST = 0.05


@dataclass
class Beta:
    alpha: float = 1.0
    beta: float = 1.0
    n: float = 0.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def width(self) -> float:
        """Roughly the 95% interval's width — how unsure this still is."""
        a, b = self.alpha, self.beta
        var = a * b / ((a + b) ** 2 * (a + b + 1))
        return 4 * var ** 0.5


def _age(day: str, today: str) -> int:
    from datetime import date
    return max(0, (date.fromisoformat(today) - date.fromisoformat(day)).days)


def _usable(row: dict) -> bool:
    return (row.get('reward') is not None and not row.get('excused')
            and row.get('mode') != 'snooze' and row.get('status') in ('sent', 'held'))


def posteriors(rows: list[dict], today: str, gamma: float = GAMMA) -> dict:
    """{behavior: {'arms': {arm: Beta}, 'slots': {slot: Beta}}} from resolved
    decisions. Arms include NO_SEND, learned from the held rows. Slots learn
    only from sent reminders: a slot is good if a reminder there works."""
    out: dict[str, dict] = {}
    for row in rows:
        if not _usable(row):
            continue
        weight = gamma ** _age(row['day'], today)
        reward = float(row['reward'])
        entry = out.setdefault(row['behavior'], {'arms': {}, 'slots': {}})
        arm = row['arm'] if row['status'] == 'sent' else NO_SEND
        for table, key in ((entry['arms'], arm), (entry['slots'], row['slot'] if row['status'] == 'sent' else None)):
            if key is None:
                continue
            b = table.setdefault(key, Beta())
            b.alpha += weight * reward
            b.beta += weight * (1 - reward)
            b.n += weight
    return out


def choose_arm(post: dict | None, rng, no_send_rate: float) -> tuple[str, str]:
    """(arm, mode). mode is 'control' for the randomized hold-back, 'policy'
    otherwise — and a policy may also decide not to send."""
    if rng.random() < no_send_rate:
        return NO_SEND, 'control'
    arms = (post or {}).get('arms', {})
    sampled = {arm: rng.betavariate(arms.get(arm, Beta()).alpha, arms.get(arm, Beta()).beta) for arm in ARMS}
    best = max(ARMS, key=lambda a: sampled[a])
    # Until a held decision has resolved there is nothing to compare against,
    # and the policy sends; the control keeps holding some back regardless.
    none = arms.get(NO_SEND)
    baseline = rng.betavariate(none.alpha, none.beta) if none else 0.0
    if sampled[best] - SEND_COST < baseline:
        return NO_SEND, 'policy'
    return best, 'policy'


def choose_slot(post: dict | None, slots: list[str], rng) -> str:
    known = (post or {}).get('slots', {})
    return max(slots, key=lambda s: rng.betavariate(known.get(s, Beta()).alpha, known.get(s, Beta()).beta))
