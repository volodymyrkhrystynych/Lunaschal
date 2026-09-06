# Chat (`src/components/Chat/`)

Frontend for the Chat tab's main-agent tools, web delegate, proposal cards, chat photos, context boundaries, and memory writes — see `backend/delegate/CLAUDE.md` for the full backend contract (SSE frame shapes, proposal lifecycle, compaction/New Chat behavior, `AgentSteps`/`ReasoningBlock` rationale, and chat-photo paths). That file covers both sides; this one exists so it also loads when you're only touching files under `src/components/Chat/`.
