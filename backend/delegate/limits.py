"""The three wall-clock budgets, read in one place.

A chat reply, a delegate web search and a nested deep-research pass each get
their own number, because they are not the same kind of wait: a search sits
between the user pressing Enter and their first token, a deep pass is expected
to take a while by definition, and the chat budget is the outer bound both of
them are clamped against (`agent.deadline_from`).

They live here rather than at the three call sites because the lookup is not
the awkward part — the defaults are. A settings row written before these
columns existed reads NULL for the enable flags, and reading NULL as "off"
would silently remove the timeout from exactly the installs that never chose
to turn it off.

Settings are read per run, matching backend/research/web.py's per-call read of
its provider config: a budget changed in Settings applies to the next run, not
after the next restart.
"""
from backend.ai.provider import get_settings
from backend.research.agent import deadline_from

# 15 minutes: long enough that a slow-but-working reply is never cut off on a
# local model, short enough that a wedged one does not hold the tab all day.
DEFAULT_CHAT_TIMEOUT = 900
# Two minutes. Above this the user is watching a spinner for longer than any
# chat answer is worth — the same reasoning behind the six-turn budget.
DEFAULT_SEARCH_TIMEOUT = 120
# Ten minutes. Deliberately generous: taking a while is the whole point of the
# tool, so this is a ceiling, not a target.
DEFAULT_DEEP_TIMEOUT = 600


def _seconds(enabled_col: str, seconds_col: str, default: int) -> int | None:
    """The configured budget in seconds, or None when timeouts are off."""
    settings = get_settings() or {}
    enabled = settings.get(enabled_col)
    if enabled is None:
        enabled = 1
    if not enabled:
        return None
    return settings.get(seconds_col) or default


def chat_deadline() -> float | None:
    """The budget for a whole assistant reply, or None when disabled."""
    return deadline_from(_seconds('chat_timeout_enabled', 'chat_timeout_seconds',
                                  DEFAULT_CHAT_TIMEOUT))


def search_deadline(outer: float | None = None) -> float | None:
    """The budget for one delegate research run, clamped to `outer`."""
    return deadline_from(
        _seconds('research_timeout_enabled', 'research_search_timeout_seconds',
                 DEFAULT_SEARCH_TIMEOUT),
        outer,
    )


def deep_deadline(outer: float | None = None) -> float | None:
    """The budget for one nested deep-research pass, clamped to `outer`."""
    return deadline_from(
        _seconds('research_timeout_enabled', 'research_deep_timeout_seconds',
                 DEFAULT_DEEP_TIMEOUT),
        outer,
    )
