# Food photo context and transcript polishing

Food photos are described through `backend/ai/images.describe_image`, with a
food-specific prompt that quotes legible dish, ingredient, brand and restaurant
names and marks uncertainty. Uploads queue `food.describe_media` transactionally
with the media row when vision is configured. Description text/status/errors are
stored on `food_media`; the durable queue resumes pending work after restart.
The Food card offers Describe/Describe again for existing photos and failures.

`structure_food_entry` reads the latest accumulated `raw_content`, regardless of
the older text in a queued payload, and supplies completed photo descriptions
alongside memory to `parse_food_entry`. Descriptions only help correct plausible
mishearings; they must not add photo-only facts to the user's spoken note.
Description completion queues structuring again, so upload order does not matter.

`generated_notes` stores the last AI version. Background work can replace notes
only when empty or still equal to that version; PATCH clears the marker for a
manual edit. Existing notes have no marker and are preserved. Explicit Polish
can replace them from the original transcript and current descriptions. Both
paths check for edits made during inference. `raw_content` and each clip's
transcript are preserved. Metadata still only fills empty fields.

The food list polls while descriptions, clip transcription, or queued structuring
are active. Tests: `test_food_descriptions.py`, `test_food_recordings.py`,
`test_food_ai.py`, and `FoodDescriptions.test.tsx`.
