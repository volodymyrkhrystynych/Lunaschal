import { router } from './trpc.js';
import { chatRouter } from './chat.js';
import { journalRouter } from './journal.js';
import { calendarRouter } from './calendar.js';
import { flashcardRouter } from './flashcard.js';
import { settingsRouter } from './settings.js';

export type { TRPCContext } from './trpc.js';

export const appRouter = router({
  chat: chatRouter,
  journal: journalRouter,
  calendar: calendarRouter,
  flashcard: flashcardRouter,
  settings: settingsRouter,
});

export type AppRouter = typeof appRouter;
