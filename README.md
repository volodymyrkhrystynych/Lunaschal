# Lunaschal

A privacy-first, self-hosted personal AI knowledge assistant with journaling, calendar, flashcards, and conversational AI.

## Features

- **Conversational AI** - Chat with an AI that understands your personal context
- **Smart Journal** - Write entries naturally, with full-text and semantic search
- **Activity Calendar** - Track events and link them to journal entries
- **Flashcards** - AI-generated flashcards with spaced repetition (SM-2 algorithm)
- **Knowledge Base** - RAG-powered semantic search across all your content
- **Privacy-First** - All data stored locally in SQLite, self-hosted

## Requirements

- Node.js 20+
- npm

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/lunaschal.git
cd lunaschal

# Install dependencies
npm install

# Run database migrations
npm run db:generate
npm run db:migrate

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173` (frontend) and `http://localhost:3000` (API).

## Initial Setup

1. Open the app in your browser
2. Create a password for your account
3. Go to **Settings** and configure an AI provider:
   - **OpenAI**: Enter your API key (recommended for best experience)
   - **Google Gemini**: Enter your Google AI API key
   - **Ollama**: Configure local URL and model (for fully offline use)

## Usage Guide

### Chat

The chat interface is your primary way to interact with Lunaschal. You can:

- **Have conversations** - Ask questions, discuss ideas, get help
- **Write journal entries** - Just describe your day naturally
  - Example: "Today I learned about React hooks and built a custom useAuth hook"
  - The AI will detect this and offer to save it as a journal entry
- **Log activities** - Mention events and appointments
  - Example: "I have a dentist appointment next Tuesday at 2pm"
  - The AI will offer to save it to your calendar
- **Quiz yourself** - Request flashcards on any topic
  - Example: "Quiz me on JavaScript promises"
  - The AI will generate flashcards and start an in-chat review session

When the AI detects a journal entry, calendar event, or quiz request, a confirmation bar appears at the bottom. Click **Save** to confirm or **Dismiss** to skip.

### Journal

The Journal section stores your thoughts, learnings, and reflections.

**Creating entries:**
- Click **+ New Entry** to write directly
- Or describe your day in chat and save from there

**Searching:**
- Use the search bar for keyword search (FTS5 full-text search)
- Semantic search is automatic when chatting - the AI finds relevant entries

**Generating flashcards:**
- Click **Flashcards** on any entry to generate study cards from that content

### Calendar

The Calendar tracks your activities and events.

**Views:**
- **Month view** - See the full month with event dots
- **Week view** - Detailed daily view with times

**Creating events:**
- Click any date to add an event
- Or mention events in chat (e.g., "meeting with John tomorrow at 3pm")

**Linking journals:**
- Open an event to see or link related journal entries
- Great for connecting reflections to specific activities

### Flashcards

Flashcards use the SM-2 spaced repetition algorithm for efficient learning.

**Modes:**
- **Browse** - View all your cards with status indicators
- **Review** - Study cards that are due
- **Create** - Add cards manually

**Card status:**
- **Due** (orange) - Ready for review
- **Learning** (blue) - Still being learned
- **Mastered** (green) - Interval of 21+ days

**Generating cards:**
- From journal entries: Click **Flashcards** on any entry
- From chat: Say "Quiz me on [topic]"
- The AI creates 3-8 cards based on content

**Reviewing:**
- Click **Show Answer** to reveal the back
- Rate your recall: Again, Hard, Good, or Easy
- The algorithm adjusts the next review date accordingly

### Settings

**AI Provider:**
- Select OpenAI, Google Gemini, or Ollama
- Enter API keys as needed
- Ollama runs locally for complete privacy

**Knowledge Base:**
- Shows total journal entries and indexing status
- Click **Rebuild Knowledge Base** after changing AI providers
- New entries are automatically indexed

**Password:**
- Change your account password

## Knowledge Base (RAG)

Lunaschal uses RAG (Retrieval-Augmented Generation) to give the AI context from your personal knowledge base.

**How it works:**
1. Journal entries are split into chunks and embedded
2. When you chat, relevant chunks are retrieved
3. The AI sees this context and gives personalized responses

**Example:**
- You wrote about learning React hooks last week
- You ask "How did I implement the useAuth hook?"
- The AI retrieves that journal entry and answers with your specific implementation

**Indicator:**
- When context is used, you'll see "Using X sources from your knowledge base"
- This appears above the AI's response while streaming

## Data Storage

All data is stored locally:
- **Database**: `./data/lunaschal.db` (SQLite)
- **Embeddings**: Stored in the same database using sqlite-vec

No data is sent to external servers except:
- AI API calls (to your configured provider)
- Embedding generation (same provider)

## Scripts

```bash
npm run dev        # Start development servers
npm run build      # Build for production
npm run preview    # Preview production build
npm run db:generate # Generate migrations from schema
npm run db:migrate  # Run migrations
```

## Tech Stack

- **Frontend**: React 19, Vite, Tailwind CSS v4
- **Backend**: Hono, tRPC v11, Node.js
- **Database**: SQLite with Drizzle ORM, FTS5, sqlite-vec
- **AI**: Vercel AI SDK (OpenAI, Google, Ollama)
- **Auth**: bcrypt + JWT

## License

MIT
