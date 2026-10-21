# School ID property management chatbot

A local prototype for school ID records: who has an ID, where it is (Pending → Processing → For release →
Released), release appointments, lost IDs, the gate log and small QR stickers. Everyone works through one
**chat assistant**: type a student ID or a name and it shows that student. The proposal was not included
with the request; features follow the described workflow.

## Run

The app keeps its records in **PostgreSQL**, so it needs a database and its address in the
`DATABASE_URL` variable (details in [Database](#database-postgresql); Docker is the quickest way).

Python 3.9 or later:

```bash
docker compose up -d        # PostgreSQL 16 on localhost:5432, first time only (needs Docker)
export DATABASE_URL=postgresql://school_id:school_id_dev@localhost:5432/school_id
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python train_model.py
.venv/bin/python Chatbot.py
```

Windows (PowerShell):

```powershell
docker compose up -d
$env:DATABASE_URL = "postgresql://school_id:school_id_dev@localhost:5432/school_id"
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python train_model.py
.venv\Scripts\python Chatbot.py
```

`DATABASE_URL` only lasts for that terminal window. If you run the app from a new window, set it again.

If the app stops with "ANN model version differs from this installation", the saved model was
trained with another scikit-learn version than the one installed. Run `train_model.py` once.

Open http://localhost:5000. Six fictional demo students are created when the app starts on an
empty database. Records and appointment changes persist in PostgreSQL.
Choose a role on the first screen. The **coordinator** starts on the **Dashboard**, a table of every student
with a status filter, **Select all** and CSV **Import**. The **guard** and the **student** start on the
**Assistant**, a full-page chat: type a student ID such as `P202400230`, or a name, and the assistant
answers with that student. Apart from the guard's check-in and check-out commands, chat messages never
change anything; staff save changes on the Dashboard.

### QR printing and phone scanning

Set `PUBLIC_BASE_URL` to the reachable URL of this application before printing:

```bash
PUBLIC_BASE_URL=https://your-school-app.example .venv/bin/python Chatbot.py
```

Without it, QR codes use the URL used to request the image. A localhost QR cannot be
opened from another phone. A QR contains an opaque URL token; scanning displays only
name, course, section, and year level. Anyone with that URL can view those details.
**Print sticker** creates a 30 mm QR area with a student ID caption; print at 100%
and check legibility with a phone. A caption or label can require extra space beyond 30 mm.
Long URLs increase density; test with the final hostname and physical sticker.

**Print stickers** in the inventory toolbar opens one sheet with a 30 × 30 mm QR code for
every selected student (up to 60 per sheet; print at 100%).

**Scan QR** (guard role, Verify ID) reads a sticker with the camera and looks the student up.
Browsers only allow camera access on `https://` pages and on `localhost`, so the page opened
from a phone as `http://192.168.x.x:5000` cannot scan. Use an HTTPS address (your deployed
host, or a tunnel such as ngrok or Cloudflare Tunnel) or type the student ID instead; the page
explains this when it happens. The scanner only accepts this application's own
`/student/<token>` links. The decoder, jsQR 1.4.0 (Apache-2.0, see `static/vendor/README.md`),
runs inside the browser; camera frames are never uploaded.

## Database (PostgreSQL)

The app reads the database address from `DATABASE_URL`, in the form
`postgresql://USER:PASSWORD@HOST:PORT/DATABASE`. Characters such as `@` or `/` inside the password
must be percent-encoded (`@` becomes `%40`). The tables are created when the app starts, so there is no
separate setup step. If the database cannot be reached at start, the app says so and stops.

| Where the database is | What to do |
|---|---|
| **Docker** (easiest on a laptop) | `docker compose up -d`, then use the URL from `docker-compose.yml` (shown in Run above). Data lives in a Docker volume: `docker compose down` keeps it, `docker compose down -v` deletes it. That password is for your own laptop only. |
| **PostgreSQL installed on your computer** | Create a login and a database, for example in `psql`: `CREATE ROLE school_id LOGIN PASSWORD 'choose-one';` and `CREATE DATABASE school_id OWNER school_id;`. Then `DATABASE_URL=postgresql://school_id:choose-one@localhost:5432/school_id`. |
| **Hosted** (Supabase, Neon, Railway and similar) | Copy the connection string the host shows you and use it as `DATABASE_URL`. Keep `sslmode=require` if it has it. If the host gives you a pooled address as well as a direct one, the direct one is the one this app was tested with. The app turns prepared statements off so a pooler should also work, but that was not tried. Never commit the URL: it contains the password. |

`DATABASE_POOL_SIZE` (default 10) is the most connections one app process opens. If the database goes
away while the app runs (restart, network), requests answer "not available right now" (HTTP 503) and
work again by themselves once it is back. Use a password of your own, never the one in
`docker-compose.yml`, for anything other people can reach, and do not expose PostgreSQL to the internet.

**Back up** before real data goes in, and regularly after:

```bash
pg_dump "$DATABASE_URL" -Fc -f school_id.dump                              # back up
pg_restore --clean --if-exists -d "$DATABASE_URL" school_id.dump            # restore
```

A dump holds student records. Keep it out of Git (`.gitignore` already skips `*.dump`).

### Moving data from the old SQLite version (once)

Before this version, records lived in `data/inventory.sqlite3`. The file that comes with the repository
only holds the six demo students, so there is nothing to move unless you added records of your own.

1. Stop every copy of the app, old and new, so nothing is written during the copy.
2. Point `DATABASE_URL` at the new database. An empty one is best. If you already started the new app
   once, the database only holds the six demo students, and the copy replaces them. Anything else in
   there is never overwritten: the script stops and says so.
3. Check first, then copy:

```bash
.venv/bin/python migrate_sqlite_to_postgres.py --dry-run
.venv/bin/python migrate_sqlite_to_postgres.py                  # or: ... path/to/other.sqlite3
```

The script opens the SQLite file read-only (it stays as your backup), copies every table with its original
ids inside one transaction (all of it lands, or none of it), reads every row back and compares it before
saving, and lets new records continue after the highest copied id. It also fixes what PostgreSQL cannot hold:
NUL characters are removed (it tells you how many), and student IDs that differ only in letter case
(`abc-1` and `ABC-1`) are reported before anything is written. Delete the SQLite file only after you have
checked the records in the app. If it is tracked by Git, `git rm --cached data/inventory.sqlite3` stops tracking it.

### What differs from the SQLite version

- Student IDs that differ only in letter case are the same student, and the database now enforces it.
- A deleted record's id is never given to a new record (SQLite handed out the highest id again).
- Appointment dates and times are always saved as `2026-01-05` and `09:30`, so they sort correctly.
- Verify lists exact ID or QR matches first, then name matches.
- Two coordinators saving at the same moment no longer overwrite each other, and a request cannot be accepted twice.

## Pages by role

The role is chosen on the first screen. It is a convenience for the demo, not a security
control (see [Privacy and access](#privacy-and-access)).

| Role | Pages | What it does |
|---|---|---|
| Coordinator | Dashboard, Assistant, Appointments, Requests, Incidents, Feedback, Activity | **Dashboard**: every student with Student ID, Full name, Year & Section, Status, Address and Contact number. Filter by status, **Select all**, change the status of many students at once, print sticker sheets, **Import students** (CSV), **Add student**, export CSV. Tiles and shortcuts (Not yet made, Overdue, Today, For release without a date, Pending without a reason) sit above the table; the old Overview page is now part of it. **Requests** is an inbox: Submitted → In review → Accepted → Closed. **Incidents** shows what the guard filed and lets the coordinator mark or withdraw a false statement. |
| Guard | Assistant, Verify ID, Incidents | **Assistant**: type a student ID or name to see whether the ID is released, then check the student in or out. The gate log and the list of IDs not yet made are typed there too. **Verify ID** finds a student by ID, name or QR scan. **Incidents** are filed and resolved here. |
| Student | Assistant, Request help | **Assistant**: type a student ID or name to see that student's ID status (never the address, contact number, staff notes or QR token). **Request help** sends a Lost ID report or a question to the office, and follows it. |

### Students on the dashboard

| Column | Rule |
|---|---|
| Student ID | The letter `P` and 9 digits, for example `P202400230`. The pattern is one line, `STUDENT_ID_RE` in `student_data.py`: change it there if your school numbers IDs differently. IDs already in the database in another format (such as `DEMO-001`) keep working. |
| Full name | Required, up to 100 characters. |
| Year & Section | A year from 1 to 6, then the section: `3E`, also typed `3-E` or `3 e`. |
| Status | **Pending** (ID not yet made), **Processing** (being made), **For release** (made, ready to claim) or **Released** (claimed). A CSV may also say `For released`, `Ready for release` or `Claimed`; it is stored under the four names. |
| Address | Optional, up to 200 characters. |
| Contact number | Optional. `09171234567`, `+63 917 123 4567` and `9171234567` (a spreadsheet drops the zero) are all stored as `09171234567`; other numbers of 7 to 13 digits are kept as typed. |

**Select all** selects every student that matches the current filter, including the rows below
"Show more". A bulk status change tells you how many students it changed.

**Import students** takes a CSV file (up to 1 MB and 2,000 students; the Dashboard offers a template to
download). The first row names the columns: `student_id, full_name, year_section` are required and `status,
address, contact_number` are optional (common spellings such as `Name`, `Year and Section` and `Phone`
are understood; `,` `;` and tab separators and both of Excel's CSV types work). A student already on the Dashboard is
**skipped, never overwritten**. A row with a mistake is reported with its line number and the reason, and the
good rows are still added. Nothing is imported twice if you upload the file again.

### Release hours

Release appointments are Monday to Friday, **8:00 AM to 12:00 PM and 1:00 PM to 5:00 PM** (lunch break
12:00 to 1:00 PM). The Dashboard form and the API refuse a date or time outside these hours, with the reason.
The check runs when an appointment is set or moved, not when another field of an old record is edited.
The hours are `OFFICE_WINDOWS` in `student_data.py`.

### If the ID is lost

When the coordinator **accepts a Lost ID request** (Requests page), the student's record starts over: the status
goes back to **Pending** (as a Lost ID request) and, for a Released card, the release appointment is cleared,
because a new card has to be made. The same happens from **⚠️ Lost ID (back to Pending)** in a Released student's
⋯ menu on the Dashboard (`POST /api/inventory/<id>/lost`), which works only for a Released card and refuses
anything else with the reason. A student's own Lost ID request, or a No ID or Lost ID incident, changes nothing in
the records by itself: the coordinator decides.

A student who lost the ID is sent to the **Cashier first** (replacement fee), then to the Property Management
Office with the Cashier's receipt, an affidavit of loss and a 1×1 photo. The app does not know the fee or where the
Cashier is, so it names neither. The wording is `CASHIER_STEP`, `OFFICE_STEP` and `STUDENT_LOST_STEPS` in
`student_data.py` and `OFFICE['requirements']['Lost ID']` in `Chatbot.py`; add the amount and the place there once
the office confirms them.

### The Assistant is a real chat

It is the same page for the three roles; only what it may show differs.

- Type a **student ID** (`P202400230`) or a **name** (`Reynald Malabanan`, `malabanan`, `Reynald Jake`) and the
  answer is *that student*: Student ID, name, Year & Section, status and what it means, the appointment and
  why a pending ID is pending. The coordinator also gets the address and contact number; guards and
  students are told those are shown to the coordinator only.
- Lookups come straight from the records. A name or ID that is not on the Dashboard is reported as not found.
  The ANN only recognises ordinary questions ("Where is the office?", "I lost my ID").
- The student found stays selected above the message box, so "what is the status?" needs no ID. **✕ Clear**
  and **New chat** forget them. A name that fits several students lists up to five as buttons.
- The buttons under a student's card (Check in, Check out, File incident, Preview ID card, Request help …)
  act on that student.

### Gate log, in the chat (guard)

There is no Gate page. The guard types, or taps the button under a student's card:

| Type | Result |
|---|---|
| `check in P202400230`, `in Reynald Malabanan`, `time in …`, `pasok …` | logs the student in |
| `check out P202400230`, `out …`, `time out …`, `labas …` | logs the student out |
| `check in visitor Juan Dela Cruz`, `pasok bisita Juan Dela Cruz` | logs a visitor by name |
| `gate log` (guard and coordinator) | today's entries, newest first, with the in and out counts |
| `records`, `hindi pa nagagawang ID` (guard and coordinator) | the IDs that are not made yet (Pending and Processing) |

The same student logged the same way again within 20 seconds counts as one tap, not two. A message with a `?`
is treated as a question, not a command. Students cannot log the gate or see the gate log or the records list.
The old `/api/gate` endpoints and the `gate_logs` table are unchanged, so earlier entries are kept.

**IDs not yet made** ("hindi pa nagagawang ID") are the Pending and Processing students. The Dashboard has a
**Not yet made** filter and tile, a student's card says what the status means, and the guard's check-in answer
says the student cannot have the card yet.

### Incidents, Cashier and the penalty

At the gate there are two incidents, and only these two: **No ID** (the student has no ID with them) and
**Lost ID**. Older incidents with other categories are kept as they were. When a student ID is typed, the form
compares the claim with the records:

| Incident | Records "agree" | Records "do not agree" / cannot say |
|---|---|---|
| No ID | the ID is Pending, Processing or For release | Released: the student may have left it at home, so the form says to ask where the card is |
| Lost ID | the ID was Released | it was never released: a card that was never handed over cannot be lost |

A **Lost ID** incident sends the student to the Cashier first, then to the Property Management Office (see above).

**Giving false information twice costs ₱1,500.** The guard ticks *False statement* when filing, or the coordinator
marks or withdraws it on the Incidents page. The count is per student ID, in the order the incidents were filed:
the **second** false statement carries the ₱1,500 penalty (once per student); the first has none. A third and
later one is recorded without an automatic amount, because the rule you gave covers only "twice"; the coordinator
decides. Withdrawing a statement recalculates the others. The amount and the number are `PENALTY_AMOUNT` and
`PENALTY_AT` in `student_data.py`. Students are only told the rule (on Request help), never their count.
The app assumes the penalty is paid at the Cashier and does not record payment.

### Privacy and access

- There is **no sign-in**. The role is a button the browser remembers, and the chat sends it to the server as
  `workspace_role`. Anyone who can reach the server can call `/api/inventory` (which includes the address and
  contact number), the CSV export, `/api/verify` (which returns a QR token) and the other endpoints directly. Hiding
  the address and contact number from the guard and the student in the chat is a courtesy of the interface, not a
  protection.
- Typing someone's name in the student's chat shows that student's name, ID, Year & Section and status. That is how
  the chat was asked to work, and it is why a sign-in matters before real records are used.

## Chatbot answers and feedback

The Assistant page says it is an automated assistant that can be wrong. Answers about school
IDs show how closely your words matched a topic (Strong / Possible / Weak; the number is in the
tooltip). Greetings and thanks do not.

- **Not sure what you meant?** When the best match is weak, the chatbot offers up to three
  "did you mean" questions you can tap. Staff-only topics are never offered to guards or students.
- **Sure?** The answer is followed by a few likely next questions.
- **👍 / 👎 under each answer.** A thumb is saved immediately and can be taken back by tapping
  it again. 👎 opens an optional form: what went wrong (Wrong answer, Didn't understand me,
  Missing information, Other), a short comment (200 characters), and a box to also share the
  question that was asked.
- **What is stored.** The rating, reason, comment, the topic the chatbot picked, its match
  score, and the role. The question text is stored **only if the box is ticked**. Known student
  IDs, registered names (and name parts of four letters or more), e-mail addresses, phone numbers
  and long numbers are removed from the question and the comment first, and the text is capped
  at 500 characters. Redaction cannot catch everything, which is why sharing is opt-in.
- **Only the chat session that received an answer can rate it.**
- **Coordinator → Feedback.** Totals, the topics with the most 👎, the reasons, and a list you
  can filter (New / Reviewed). For each shared question the coordinator can choose the topic
  it should have matched, mark it Reviewed, and export everything to CSV (spreadsheet formulas
  are neutralised).

### From feedback to a test set

Shared questions that have a corrected topic are real examples of how students ask. To evaluate
the classifier on them without mixing them into the training data:

```bash
# 1. Feedback page → Export CSV (saves feedback.csv)
.venv/bin/python feedback_to_dataset.py feedback.csv data/feedback_questions.csv
.venv/bin/python train_model.py --test-dataset data/feedback_questions.csv
```

`feedback_to_dataset.py` keeps rows that have a shared question and a topic from
`property_management_intent.csv`, keeps a repeated question once, drops a question that was
given two different topics, never overwrites a file unless you add `--force`, and tells you when
there are too few questions (under 30) to quote as an accuracy figure. Report counts and
examples, and say that the questions come from this app's own users.

## Dataset and intents

- `property_management_dataset.csv`: 240 synthetic utterances, 15 per intent, with
  `id,text,intent,language,author` columns. Generic conversational examples retained
  from the supplied CSV; school ID examples replace general campus support requests.
- `property_management_intent.csv`: 16 intent definitions and staff actions.
- `property_management_dataset.tab`: Orange version with `intent` as the class,
  `text` as a string meta, and identifiers/language/author as metadata.

## ANN training in Python

The guide allows a locally trained Artificial Neural Network. `train_model.py` builds
TF-IDF word/bigram features and a scikit-learn `MLPClassifier`: one 32-neuron ReLU
hidden layer and 16 output intents. It uses L-BFGS, L2 regularization (`alpha=0.1`),
seed 42, and at most 600 iterations. No external AI service or pretrained model is used.

```bash
.venv/bin/python train_model.py
.venv/bin/python Chatbot.py
```

The script evaluates a stratified 80/20 split (192 training, 48 test examples), fitting
vocabulary and IDF only on the training partition. It then refits on all 240 source
examples and saves `data/intent_model.joblib`. `Chatbot.py` loads that model. Train before
starting the app; retrain and restart after changing either CSV or scikit-learn version.
Only load model files produced by your own training script: joblib uses Python pickle.

Reports are written to `data/training/`: `metrics.json` (parameters, data hashes,
split IDs, per-class results and warnings), `predictions.csv`, `confusion_matrix.csv`,
and `evaluation.md`. Use `--report-dir` to choose another destination. Test scores
refer to the evaluation model, not the final model refit on the full source dataset.

The CSVs contain synthetic seed examples. Exact normalized duplicates are rejected,
but semantic paraphrases may still occur in both partitions. Do not claim these scores
as real student performance. Gather independently written student questions, review
paraphrase groups, and freeze your model choices before final evaluation. You can
supply a separate labeled CSV with `id,text,intent` columns:

```bash
.venv/bin/python train_model.py --test-dataset independent_student_questions.csv
```

This mode trains on all source examples and tests on the separate file. It rejects
normalized text overlap; report coverage per intent, even if some classes are absent.
`--hidden-units`, `--max-iter`, `--seed`, and `--threshold` configure the baseline.
The fixed default fallback threshold is 0.35. Inputs with no known vocabulary or a
maximum softmax score below that threshold use `out_of_scope`; these scores are not
calibrated probabilities. The threshold is a starting point, not an assurance that
unrelated questions will always be rejected.

`intent_model.py` shares model loading and fallback logic with the application. Actual
ID status, dates/times, and student details are retrieved from PostgreSQL after intent
classification. QR generation remains separate. The model cannot book appointments
or determine school policy. Group members should review and understand this code,
curate the dataset, and document their own contributions for the guide's defense.

## Orange workflow

Install Orange3 with its Text add-on on your desktop, then open `chatbot.ows`.

1. In **File**, choose `property_management_dataset.tab`. There are no saved absolute
   Windows file paths or unrelated SQL connections in this workflow.
2. In **Corpus**, select only **text** as the text feature. Check that `intent` is the
   target/class. If you import CSV instead, set text to Text/String and intent to
   categorical; in **Select Columns**, move **intent** to **Target**, keep text in
   **Metas**, and keep id/language/author out of Features.
3. In **Preprocess Text**, lowercase and tokenize. Start without stopword removal:
   preserve Filipino words and distinctions such as pending/release/lost.
4. In **Bag of Words**, select TF-IDF. The Neural Network learner is connected
   to **Test and Score** with the vectorized corpus. Set one hidden layer of 32 neurons,
   then select stratified 5-fold cross
   validation and inspect macro F1, per-class recall, and **Confusion Matrix**.
5. Before reporting accuracy, group near-duplicate/paraphrased utterances so they stay
   in one split, and evaluate on a separate set of real, anonymized student questions.
   Synthetic cross validation alone can overestimate performance. Fit vocabulary/IDF
   inside training folds for the final evaluation pipeline to avoid preprocessing leakage.

The workflow XML was checked locally; Orange desktop execution is not verified in this
sandbox. The Python chatbot does not automatically load an Orange trained model.

## Verification

The database tests need a PostgreSQL database in which they may create temporary schemas. The one from
`docker-compose.yml` is fine, even when it holds your real records: every test works in its own schema
named `test_...` and drops it afterwards, so your tables are never read or changed.

```bash
export TEST_DATABASE_URL=postgresql://school_id:school_id_dev@localhost:5432/school_id
.venv/bin/python -m unittest discover -s tests -v          # Windows: .venv\Scripts\python -m unittest discover -s tests -v
```

PowerShell: `$env:TEST_DATABASE_URL = "postgresql://school_id:school_id_dev@localhost:5432/school_id"`.
Without `TEST_DATABASE_URL` the database tests are skipped and the summary says how many (`skipped=...`):
a run that skipped them has not tested the app.

`tests/test_student_data.py` covers the ID, name, section, contact and status formats, the CSV reader, the release
hours and the name matching. `tests/test_records.py` covers the Dashboard API: add, edit, import, bulk change, the
Lost ID rule and the release hours. `tests/test_chat_lookup.py` covers the chat: lookups by ID and name, what each role
may see, the gate commands, visitors, the records list and the typed-command edge cases. `tests/test_incidents.py`
covers the two incident types, the claim check and the ₱1,500 rule. `tests/test_assistant_tools.py` covers feedback,
suggestions, requests and the staff tools, `tests/test_postgres.py` covers start-up, simultaneous edits and locks,
text PostgreSQL cannot store, and a database that disappears, `tests/test_migration.py` covers the SQLite copy
script, and `tests/test_feedback_dataset.py` covers the feedback-to-test-set script. They do not drive a
browser or a camera, so check these by hand on the final machine:

- [ ] Ask "pending", tap 👎, pick a reason, tick or leave the share box, **Send**; the coordinator's
      Feedback page shows it (question only if shared) and the nav badge counts it.
- [ ] Type nonsense: the chatbot says it is automated and offers tappable guesses or a way to
      reach the office.
- [ ] Coordinator: **Import students** with a file that has a good row, a repeated ID and a bad ID; the result
      lists the line numbers; filter by status; **Select all** → set a status → **Apply**.
- [ ] Coordinator: set a release appointment on a Saturday and at 12:30; both are refused with the reason.
- [ ] Coordinator: release a student, then accept that student's Lost ID request; the status is Pending again.
- [ ] Guard: type an ID, tap **Check in**, then type `gate log`; also `check in visitor Juan Dela Cruz`.
- [ ] Guard: file **No ID** twice for the same student with *False statement* ticked; the second shows ₱1,500.
- [ ] Student on a phone: type an ID and a name; the message box is on screen without scrolling; **Request help** →
      Lost ID shows the Cashier step first.
- [ ] Guard on a phone over **https**: Verify ID → **Scan QR** reads a printed 30 mm sticker in
      your real lighting.
- [ ] Print one sticker sheet at 100% and scan every sticker with a phone.

## Recommended next steps

1. Confirm the proposal, official status stages, required documents, fees, office hours,
   contact details, and who may set or change release appointments.
2. Have staff test the filter → status update → appointment → chat lookup → printed QR
   flow with fictional records, including pending requirements and lost IDs.
3. Add staff sign-in, student identity verification, access controls, an audit trail,
   QR access/revocation policy, and backups before using actual student records. This
   prototype has no authentication; record endpoints are available to anyone who can
   reach it. The QR profile deliberately displays the requested student details. Moving to
   PostgreSQL did not change this: the database password protects the database, not the app's endpoints.
4. Collect varied English/Filipino/Taglish questions, improve confusing intents, then
   train and evaluate the final classifier with a held-out set.
5. Add approved appointment availability and reminders once the office workflow is agreed.
6. Use the Feedback page regularly: set the correct topic on shared questions, export, run
   `feedback_to_dataset.py`, evaluate, then add the useful paraphrases to the dataset and retrain.
7. Decide who may read feedback and how long it is kept. The Feedback, request and gate
   endpoints are as open as the rest of the API, and there is no delete button for staff yet.
8. Before changing a table in a database that holds real data, add a migration tool such as Alembic.
   The app only creates tables that are missing; it never alters existing ones.
9. Ask the Cashier for the replacement fee and where to pay, and confirm that the ₱1,500 penalty is paid there. Decide
   what happens at a third false statement, and whether the app should record payment.
10. If your school's ID numbers are not `P` and 9 digits, change `STUDENT_ID_RE` before importing real students.

School dates/times are stored as local wall time, and the server's own clock decides what
"today" and "overdue" mean. Run it on a machine set to Philippine time (on Linux/macOS you
can start it with `TZ=Asia/Manila`; Windows uses its time-zone setting), otherwise those
labels shift by the difference. Office capacity, conflicts, holidays,
notifications, fee payment and penalty payment are not implemented.
