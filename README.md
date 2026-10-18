# Game Bot + MAFIA MYSTIC (Chinese Zodiac fortune)

A Telegram bot (python-telegram-bot 21+, SQLite) with coin games (Blackjack, Slot, Mine, Groupmine,
Sic Bo, Bingo, Mafia, Lottery) and the MAFIA MYSTIC fortune feature. **No AI / LLM API is used anywhere.**
All fortune readings are prewritten and stored in `mystic_readings.json.gz`; Python only selects one.

## Files
| File | Purpose |
|---|---|
| `main.py` | starts the bot, loads the fortune content, registers handlers |
| `db.py`, `common.py`, `economy.py`, `solo.py`, `group.py`, `mafia.py`, `welcome.py` | coins and games |
| `zodiac.py` | Chinese zodiac from a birth date using Chinese New Year dates (1900-2035) |
| `fortune_core.py` | SQLite schema, content import, no-repeat selection, daily/monthly/yearly policy, saved steps |
| `fortune.py` | Telegram menus, questions, birth-date input, readings, compatibility, history |
| `mystic_readings.json.gz` | the 28,800 + 1,560 readings (compiled from the 14 original JSON files) |
| `tools/build_dataset.py` | validates the original JSON files and compiles the `.gz` (refuses to write if invalid) |
| `tools/verify_cny.py` | independent astronomical check of the Chinese New Year table |
| `tests/` | automated tests (`python -m unittest discover -s tests -t .`) |

## Setup (Railway from GitHub)
1. Create the bot with @BotFather, copy the token. Disable privacy mode (`/setprivacy`) if it will run in groups.
2. Upload all runtime files (the `.py` files, `mystic_readings.json.gz`, `requirements.txt`, `Procfile`, `welcome.jpg`) to the root of a GitHub repo.
3. Railway: New Project -> Deploy from GitHub repo. Add the variables from `.env.example`.
4. **Add a Volume** mounted at `/data` and set `DB_PATH=/data/bot.db`. Without it the database (coins, reading history) is lost on every deploy.
5. The first start imports the readings into SQLite (about 1 second). Later starts skip the import.

Local run: `pip install -r requirements.txt`, set the variables, `python main.py`.

## How the fortune flow works
`/start` -> **🔮 ဗေဒင်မေးရန်** -> category (12) -> question (10 per category) -> birth date `DD-MM-YYYY`
-> **Start** -> reading. Buttons: read again, back, cancel. Also **💞 Compatibility** and **📜 History**.
The fortune menu works in private chat only (a group button sends the user to the bot).

* **Zodiac**: the birth date is compared with the Chinese New Year date of that year, so a January/early
  February birth belongs to the previous animal. Dates 1900-01-01 .. 2035-12-31 are accepted; anything else is refused,
  never guessed. Burmese digits are accepted.
* **Selection**: a reading comes only from the pool with the same category + animal + question (20 readings).
  `fx_history` (SQLite) stores what each user has seen. The same user never gets a repeat until all 20 are used; then a
  new cycle starts with the least recently shown reading. Selection runs in one transaction, so simultaneous
  taps and restarts cannot produce a duplicate. Double-tapped "read again" buttons are ignored.
* **Daily / Monthly / Yearly**: same user + same question on the same day (month / year, in `BOT_TZ`) returns the same
  reading however often the button is pressed; the next day/month/year gives a new one.
* **Privacy**: reading ids, pool sizes and database details are never shown. "Clear history" deletes a user's history,
  saved birth date and pending step.

## Dataset checks and updating content
`python tools/build_dataset.py PATH/TO/data --out mystic_readings.json.gz` validates the 13 JSON files (counts per
pool, unique ids, duplicates inside a pool, placeholders, missing fields) and writes `content_report.json`.
To improve the content: edit the original JSON, rebuild, commit the new `.gz`. The bot re-imports automatically when the
file changes (its SHA-256 is stored in `fx_meta`). See `CONTENT_REPORT.md` for the current content quality.

## Backup
Copy the SQLite file (`/data/bot.db`) while the bot is running (WAL mode: copy `bot.db`, `bot.db-wal`, `bot.db-shm`
together, or use `sqlite3 bot.db ".backup backup.db"`). The database is about 65 MB because it contains the readings
(they can always be re-imported from the `.gz`).

## Tests
`python -m unittest discover -s tests -t .` (needs `python-telegram-bot` installed for the handler tests).
