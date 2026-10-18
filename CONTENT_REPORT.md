# MAFIA MYSTIC dataset report (30,360 records)

Checked with `tools/build_dataset.py` on `MAFIA_MYSTIC_30360_Burmese_Readings.zip`.

## Structure - passes
* 12 categories x 12 animals x 10 questions x 20 readings = 28,800 primary readings, all present.
* 78 animal pairings x 20 = 1,560 compatibility readings, all present.
* 0 duplicate ids, 0 missing fields, 0 placeholders, 0 duplicate texts inside any pool.
* No frightening or absolute wording found (death, illness, cheating, divorce, "certain"...); every reading carries a "not a guarantee" sentence.

## Content quality - NOT production quality
* Only **90 distinct sentences** (animal name and question text removed) build all 28,800 readings. Every reading is ~6 sentences taken from a shared bank; **79 of the 90 sentences appear in all 12 categories** (a love answer and a money answer use the same advice).
* The question only appears as a quoted phrase in one sentence; the advice does not depend on what was asked.
* The animal only changes the name and one short trait phrase.
* 12,974 readings are word-for-word identical to a reading in another pool.
* Daily / monthly / yearly readings contain the words "today / this month / this year" but nothing date- or season-specific.
* **Compatibility is not pair-specific**: only **16 distinct sentences** (60 distinct texts) for all 78 pairings. Rat+Dragon and Rat+Horse get the same kind of text with names swapped. In 96 of 240 same-animal readings the text says the two have "different habits".
* The README inside the zip says the same: "first-pass generated ... some sentence patterns recur ... review before public deployment".

## What this means
The bot works correctly with this data, but users who read many answers will notice the repetition. The code does not depend on the content: replacing `mystic_readings.json.gz` with better text needs no code change.
