# Lunaschal — Module Structure

High-level structure of the application. See `CLAUDE.md` for per-module details.

```mermaid
flowchart LR
    subgraph SHELL["Desktop / launch"]
        MAIN["main.py<br/>PyWebView window"]
        SCRIPTS["start.sh / start-server.sh<br/>start-node.sh"]
    end

    subgraph FE["Frontend — src/ (React 19 + Vite + Tailwind)"]
        APP["App.tsx<br/>view router + Login"]
        SIDEBAR["Sidebar"]
        SHORTCUTS_FE["src/shortcuts/<br/>keymap + ShortcutProvider"]
        subgraph VIEWS["Views (src/components/) — sidebar order"]
            V_LEARN["Learning/"]
            V_CHAT["Chat / ChatNav"]
            V_TASKS["Tasks"]
            V_JOURNAL["Journal"]
            V_NOTE["Notebook"]
            V_MEET["Meetings"]
            V_WRITE["Writing/"]
            V_IDEAS["Ideas/"]
            V_CAL["Calendar"]
            V_FOOD["Food/ (log + recipes)"]
            V_LIFE["Lifestyle/"]
            V_FIC["Fanfic/ (Library + Reader)"]
            V_NEWS["Newspapers"]
            V_JOBS["Jobs/ (pipeline · profile · answer kit)"]
            V_PAPER["Paper/"]
            V_FILES["Editor/ (Files + SttPanel)"]
            V_SET["Settings/"]
        end
        API_TS["src/hooks/api.ts<br/>typed REST client"]
        LIB["src/lib/<br/>pure logic (node-env tests)"]
    end

    subgraph BE["Backend — backend/ (Flask, :5000 prod / :5001 dev)"]
        APPFACTORY["app.py — create_app()<br/>+ auth middleware"]
        AUTH["auth.py<br/>JWT cookie (network mode)"]
        subgraph ROUTES["Blueprints — backend/routes/"]
            R_CORE["auth · settings · files · shortcuts"]
            R_CHAT["chat (SSE)"]
            R_JOURNAL["journal · calendar<br/>curated_tags · transcriptions"]
            R_LEARN["learning"]
            R_WRITE["writing"]
            R_IDEAS["ideas (SSE)"]
            R_TASKS["tasks"]
            R_FOOD["food · cookbook"]
            R_LIFE["lifestyle"]
            R_FIC["fanfic"]
            R_NEWS["newspapers"]
            R_MEET["meetings"]
            R_PAPER["paper"]
            R_NOTE["notebook"]
            R_STT["stt (transcribe / tts)"]
        end
        subgraph AI["AI layer — backend/ai/"]
            PROVIDER["provider.py + llm.py<br/>OpenAI SDK → llama-server"]
            AI_CHAT["chat · classifier<br/>chat_title · briefing"]
            AI_EMBED["embeddings.py"]
            AI_LEARN["learning_generation · learning_grading<br/>learning_verification · learning_chat"]
            AI_MISC["journal · writing · meetings<br/>recipes · food · workouts · images"]
            AI_IDEAS["repo_context · idea_assessment<br/>idea_research"]
            MCP["mcp_client.py"]
            BG["background.py<br/>run_bg — 1 shared worker"]
            PRIORITY["priority.py<br/>interactive-first gate"]
        end
        subgraph PKGS["Feature packages"]
            P_LEARN["learning/<br/>FSRS scheduler + dedup<br/>deferred attempt grading"]
            P_FIC["fanfic/<br/>xenforo parser · download<br/>epub/docx · sanitize"]
            P_MEET["meetings/<br/>recorder · pipeline · merge"]
            P_NEWS["newspapers/<br/>scraper · sync"]
            P_LIFE["lifestyle/<br/>activity · exercises · storage"]
            P_FOOD["food/<br/>exif · storage"]
            P_PAPER["paper/ · journal/<br/>storage"]
            P_RESEARCH["research/<br/>repo_facts · web (SSRF-guarded)<br/>wiki · agent · worker · assess"]
            P_JOBS["jobs/<br/>linkage · keywords · retention (pure)<br/>tailor (bounded schema) · answers · render"]
        end
        subgraph SCHED["Daemon loops (no cron — threads from create_app)"]
            S_TITLE["chat_title_scheduler<br/>02:00–03:00"]
            S_REPO["research/repo_scheduler<br/>03:00–05:00"]
            S_BRIEF["briefing_scheduler<br/>05:00–07:00"]
            S_RESEARCH["research/research_scheduler<br/>no window — yields via priority"]
            S_JOBS["jobs/scheduler<br/>linkage every tick (no model)<br/>purge 07:00–08:00"]
        end
        DBLAYER["db/ — schema.sql + connection.py<br/>WAL SQLite · FTS5 ×4<br/>_ensure_* migrations"]
    end

    subgraph STORE["./data/"]
        DB[("lunaschal.db")]
        FILES_STORE["fanfic/ · meetings/ · newspapers/<br/>journal/ · lifestyle/ · food/ · paper/<br/>jobs/ (purged on retention)<br/>shortcuts.json"]
    end

    subgraph VOICE["OS-level voice — stt/"]
        LISTENER["listener.py<br/>hotkeys · voice chat ·<br/>commands · task nudges"]
        CHECKIN["morning_checkin.py"]
    end

    subgraph BROWSER["Browser extension — extension/ (MV3, unpacked)"]
        EXT_BG["background.js<br/>service worker — the only caller<br/>(content scripts are CORS-blocked)"]
        EXT_CS["content.js<br/>injected on gesture (activeTab)<br/>read · fill · attach"]
        EXT_UI["popup · options<br/>pick application · dictate"]
    end

    subgraph EXT["External"]
        ATS["ATS forms<br/>Greenhouse · Lever · Ashby · Workday<br/>(the user's own logged-in session)"]
        LLMS["llama-server (router :8080)<br/>qwen36 · gemma4-12b-omni · embed"]
        WHISPER["Whisper + Kokoro TTS<br/>(local or OpenAI API)"]
        PYANNOTE["pyannote (HF token)"]
        FORUMS["XenForo forums<br/>(SB / SV / QQ)"]
        FRONTPAGES["frontpages.com"]
        MCPSRV["MCP evidence servers"]
        WEB["Web — search provider<br/>(Brave · SearXNG)<br/>+ arbitrary pages"]
        AUDIO["ffmpeg + PipeWire"]
    end

    MAIN --> APPFACTORY
    MAIN -.->|webview| APP
    SCRIPTS --> APPFACTORY

    APP --> SIDEBAR & VIEWS & SHORTCUTS_FE
    VIEWS --> API_TS
    VIEWS -.-> LIB
    API_TS -->|"REST /api + SSE"| APPFACTORY
    APPFACTORY --> AUTH & ROUTES

    EXT_CS -.->|"chrome.runtime.sendMessage"| EXT_BG
    EXT_UI -.-> EXT_BG
    EXT_BG -->|"for-url · answers · recorded-answers<br/>resume download · transcribe"| APPFACTORY
    EXT_CS -->|"fill · attach — never submits"| ATS

    R_CHAT --> AI_CHAT
    R_JOURNAL --> AI_MISC
    R_LEARN --> AI_LEARN & P_LEARN
    R_WRITE --> AI_MISC
    R_IDEAS --> AI_IDEAS & P_RESEARCH
    R_TASKS --> AI_CHAT
    R_FOOD --> AI_MISC & P_FOOD
    R_LIFE --> AI_MISC & P_LIFE
    R_FIC --> P_FIC
    R_NEWS --> P_NEWS
    R_MEET --> P_MEET
    R_PAPER --> P_PAPER
    P_MEET --> AI_MISC
    AI_LEARN --> MCP --> MCPSRV
    P_LEARN --> AI_EMBED
    P_RESEARCH --> AI_IDEAS
    P_RESEARCH -->|"web_search · web_fetch"| WEB

    S_TITLE & S_BRIEF --> AI_CHAT
    S_REPO --> P_RESEARCH
    S_RESEARCH -->|"one task per tick"| P_RESEARCH

    AI_MISC & AI_LEARN -.->|"deferred work"| BG
    BG & PRIORITY -.->|"background parks<br/>while the user waits"| PROVIDER

    AI_CHAT & AI_EMBED & AI_LEARN & AI_MISC & AI_IDEAS --> PROVIDER --> LLMS

    ROUTES --> DBLAYER --> DB
    P_FIC --> FORUMS
    P_FIC & P_MEET & P_NEWS & P_LIFE & P_FOOD & P_PAPER --> FILES_STORE
    P_NEWS --> FRONTPAGES
    P_MEET --> AUDIO
    P_MEET -.-> PYANNOTE
    R_STT --> WHISPER

    LISTENER -->|"transcribe · chat/stream<br/>tts"| APPFACTORY
    CHECKIN --> APPFACTORY
```

## Voice pipeline (global hotkeys)

```mermaid
flowchart LR
    KEY["evdev hotkey<br/>(paste / voice / journal)"] --> REC["record mic"]
    REC --> TR["POST /api/transcribe<br/>(Whisper: local or OpenAI)"]
    TR --> PASTE["paste via wtype"]
    TR --> CHAT["POST /api/chat/stream → TTS reply"]
    TR --> JRNL["save journal entry"]
    TR --> LOG[("transcriptions table<br/>→ Journal feed")]
```
