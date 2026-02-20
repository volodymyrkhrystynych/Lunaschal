import { sqliteTable, text, integer, real, blob } from 'drizzle-orm/sqlite-core';

// Journal entries
export const journalEntries = sqliteTable('journal_entries', {
  id: text('id').primaryKey(),
  content: text('content').notNull(),
  title: text('title'),
  tags: text('tags'), // JSON array
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'timestamp' }).notNull(),
});

// Calendar events (activity log)
export const calendarEvents = sqliteTable('calendar_events', {
  id: text('id').primaryKey(),
  title: text('title').notNull(),
  description: text('description'),
  date: text('date').notNull(), // ISO date YYYY-MM-DD
  time: text('time'), // Optional HH:MM
  tags: text('tags'), // JSON array
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
});

// Flashcards with SM-2 spaced repetition
export const flashcards = sqliteTable('flashcards', {
  id: text('id').primaryKey(),
  front: text('front').notNull(),
  back: text('back').notNull(),
  sourceId: text('source_id').references(() => journalEntries.id),
  easiness: real('easiness').default(2.5),
  interval: integer('interval').default(0),
  repetitions: integer('repetitions').default(0),
  nextReview: integer('next_review', { mode: 'timestamp' }).notNull(),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
});

// Chat conversations
export const conversations = sqliteTable('conversations', {
  id: text('id').primaryKey(),
  title: text('title'),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'timestamp' }).notNull(),
});

// Chat messages
export const messages = sqliteTable('messages', {
  id: text('id').primaryKey(),
  conversationId: text('conversation_id')
    .notNull()
    .references(() => conversations.id, { onDelete: 'cascade' }),
  role: text('role', { enum: ['user', 'assistant', 'system'] }).notNull(),
  content: text('content').notNull(),
  metadata: text('metadata'), // JSON
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
});

// Vector embeddings for RAG
export const embeddings = sqliteTable('embeddings', {
  id: text('id').primaryKey(),
  sourceType: text('source_type', { enum: ['journal', 'flashcard', 'message'] }).notNull(),
  sourceId: text('source_id').notNull(),
  chunk: text('chunk').notNull(),
  embedding: blob('embedding').notNull(),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
});

// User settings (single row)
export const settings = sqliteTable('settings', {
  id: integer('id').primaryKey().default(1),
  passwordHash: text('password_hash'),
  aiProvider: text('ai_provider').default('openai'),
  aiModel: text('ai_model'),
  openaiApiKey: text('openai_api_key'),
  googleApiKey: text('google_api_key'),
  ollamaUrl: text('ollama_url').default('http://localhost:11434'),
  ollamaModel: text('ollama_model'),
  createdAt: integer('created_at', { mode: 'timestamp' }).notNull(),
  updatedAt: integer('updated_at', { mode: 'timestamp' }).notNull(),
});

// Type exports
export type JournalEntry = typeof journalEntries.$inferSelect;
export type NewJournalEntry = typeof journalEntries.$inferInsert;
export type CalendarEvent = typeof calendarEvents.$inferSelect;
export type NewCalendarEvent = typeof calendarEvents.$inferInsert;
export type Flashcard = typeof flashcards.$inferSelect;
export type NewFlashcard = typeof flashcards.$inferInsert;
export type Conversation = typeof conversations.$inferSelect;
export type NewConversation = typeof conversations.$inferInsert;
export type Message = typeof messages.$inferSelect;
export type NewMessage = typeof messages.$inferInsert;
export type Settings = typeof settings.$inferSelect;
