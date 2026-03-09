CREATE TABLE `calendar_journal_links` (
	`id` text PRIMARY KEY NOT NULL,
	`calendar_event_id` text NOT NULL,
	`journal_entry_id` text NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`calendar_event_id`) REFERENCES `calendar_events`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`journal_entry_id`) REFERENCES `journal_entries`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
ALTER TABLE `calendar_events` ADD `end_time` text;--> statement-breakpoint
ALTER TABLE `calendar_events` ADD `journal_id` text REFERENCES journal_entries(id);