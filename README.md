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

**It works with no internet connection.** Every script, font and the animation library the pages use ships in
`static/` and is served by the app itself; nothing is fetched from a CDN or from Google Fonts
(see [`static/vendor/README.md`](static/vendor/README.md) for the list, versions and licenses).

Choose a role on the first screen. The **coordinator** starts on the **Dashboard**, a live ticker above a table of
every student with a status filter, **Select all** and CSV **Import**. The **guard** starts on the **Assistant**, a
full-page chat: type a student ID such as `P202400230`, or a name, and the assistant answers with that student.
The **student** has nothing but that chat: lookups, the **Request help** button and the follow-up of a request all
happen inside it. Apart from the guard's check-in and check-out commands, the incident pop-up with its Resolve
buttons, and a student's own Request help form, chat messages never change anything; staff save changes on the
Dashboard.

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
| **Hosted** (Supabase, Neon, Railway and similar) | Copy the connection string the host shows you and use it as `DATABASE_URL`. Keep `sslmode=require` if it has it. If the host gives you a pooled address as well as a direct one, the direct one is the one this app was tested with. The app turns prepared statements off so a pooler should also work, but that was not tried. Never commit the URL: it contains the password. For Vercel see [Put it online](#put-it-online-vercel). |

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

## Put it online (Vercel)

`app.py` is the entry point for hosts that look for a WSGI object called `app`; on your own computer keep using
`python Chatbot.py`. A Vercel project connected to this repository installs `requirements.txt` on Python 3.12
(`.python-version`) and needs these settings from you:

| Setting | Value |
|---|---|
| `DATABASE_URL` (required) | A hosted PostgreSQL connection string. Vercel does not host the database, and the app writes nothing to disk. |
| `COORDINATOR_PASSWORD` (required on Vercel) | The coordinator's password. Open `/coordinator` and sign in with the username `Coordinator` (or `COORDINATOR_USERNAME`) and this password. Without it nobody can sign in on Vercel. Locally, leave it unset to keep the app open, or set it to try the sign-in. `SECRET_KEY` (optional) signs the sign-in cookie. Choose a password longer than `12345` before the defense. |
| `DATABASE_POOL_SIZE` (recommended) | `3`. Every running copy of the app opens its own connections, so keep each copy small. |
| `PUBLIC_BASE_URL` (for the QR stickers) | The site's address, for example `https://your-project.vercel.app` (see [QR printing](#qr-printing-and-phone-scanning)). |

Add them under Project → Settings → Environment Variables (tick Preview as well as Production when you deploy a
branch), then redeploy: a variable only reaches deployments made after it was saved. Until `DATABASE_URL` works, the
site answers every request with a 503 page that says so, and it starts by itself once the database can be reached.
The exact reason is in the deployment's runtime logs.

- **Which connection string.** Use the provider's pooled string on Vercel (Supabase: Transaction pooler, port 6543;
  Neon: the string with `-pooler` in the host name). Supabase's *direct* string is IPv6-only on the free plan and
  Vercel cannot reach it. The app is written for poolers (prepared statements off, transaction-level locks), but only
  a direct connection was tested here. If a pooled string gives errors in the runtime logs, use Supabase's Session
  pooler string (port 5432), or Neon's direct one with `DATABASE_POOL_SIZE=2`.
- **Retrain before you commit.** Vercel installs exactly the scikit-learn version in `requirements.txt` and cannot
  train, so `data/intent_model.joblib` in Git must come from that version. After changing either CSV or the pin, run
  `python train_model.py` and commit the new `data/intent_model.joblib` (`tests/test_vercel_entry.py` fails when
  the two disagree).
- **Time zone.** The hosted copy runs on Philippine time (`SCHOOL_TZ` changes it), so "today", "overdue" and every
  timestamp match the school and not the host's UTC clock.
- **There is no login.** The role is picked in the browser, so anyone with the address can use any role. Put only
  demo data on a public site, and keep the real records on the copy that runs at school.
- **Show the local copy at the defense.** It needs no internet (see [Run](#run)). The hosted copy is for sharing a link.

## Pages by role

The role is chosen on the first screen. It is a convenience for the demo, not a security
control (see [Privacy and access](#privacy-and-access)).

| Role | Pages | What it does |
|---|---|---|
| Coordinator | Dashboard, Assistant, Appointments, Requests, Incidents, Feedback, Activity | **Dashboard**: every student with Student ID, Full name, Year & Section, Status, Address and Contact number. Filter by status, **Select all**, change the status of many students at once, print sticker sheets, **Import students** (CSV), **Add student**, export CSV. A **live ticker** (status counts, what is overdue or due today, open requests and incidents, the latest activity) scrolls above the table and refreshes by itself; see [The Dashboard ticker](#the-dashboard-ticker). Tiles and shortcuts (Not yet made, Overdue, Today, For release without a date, Pending without a reason) sit under it; the old Overview page is now part of it. **Requests** is an inbox: Submitted → In review → Accepted → Closed. **Incidents** is the review list of what the guard filed: Resolve or Reopen, and mark or withdraw a false statement. A coordinator files an incident the same way the guard does, in the Assistant (its **File incident** button switches there and opens the pop-up). |
| Guard | Assistant, Verify ID | **Assistant**: type a student ID or name to see whether the ID is released, then check the student in or out. The gate log and the list of IDs not yet made are typed there too, and so are the **incidents**: a small **⚠️ File incident** button opens the report as a pop-up (like Request help), and `incidents` lists them with a Resolve button each. **Verify ID** finds a student by ID, name or QR scan. There is no Incidents page in the guard's sidebar. |
| Student | Assistant (the only page) | **Everything is in the chat.** Type a student ID or name to see that student's ID status (never the address, contact number, staff notes or QR token). When the answer is about a lost, missing or unreleased ID, a small **📝 Request help** button appears under it and opens the request form as a pop-up; type `my requests` to follow what was sent. There is no sidebar page for any of this and no Search ID form. |

### Students on the dashboard

| Column | Rule |
|---|---|
| Student ID | The letter `P` and 9 digits, for example `P202400230`. The pattern is one line, `STUDENT_ID_RE` in `student_data.py`: change it there if your school numbers IDs differently. IDs already in the database in another format (such as `DEMO-001`) keep working. |
| Full name | Required, up to 100 characters. |
| Year & Section | A year from 1 to 4, then the section: `3E`, also typed `3-E` or `3 e`. Years 5 and 6 are refused with the reason. The limit is `MAX_YEAR` in `student_data.py`; students already stored with another year keep working and can be edited without touching the section. |
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
- No selecting is needed first: the ID or name typed in the message box is the lookup, in every role.

### Request help, inside the student's chat

The student role is chat-only, so the help request lives where the question is asked:

1. The student types something like `I lost my ID`, an ID, or a name. The answer carries a small **📝 Request help**
   button (and `request help` typed in full opens it too).
2. The button opens the **Request help** form as a pop-up, already set to the right type (Lost ID, Status inquiry,
   Appointment help …) and, when a student was found, filled with that student's ID and name. A Lost ID shows the
   Cashier step and the ₱1,500 rule before it is sent. Cancel, ✕ or Escape closes it without sending anything.
3. After **Send request** the chat shows the request number, what happens next and `my requests`. The text the
   student typed in the form goes to the coordinator's Requests inbox only: it is not copied into the chat history.
4. `my requests` lists the requests made with the selected student ID or the name typed (`my requests P202400104`),
   with their status: Submitted → In review → Accepted → Closed.

The same chat answers the staff roles as before; the button and `my requests` are for students.

### The Dashboard ticker

The strip under the Dashboard heading is a live news ticker for the coordinator. It reads the same records and
counters as the tiles, so its numbers always agree with the table.

| Shows | From |
|---|---|
| Pending, Processing, For release and Released counts | the student records |
| Overdue appointments and appointments today | the records (overdue is shown in red) |
| Open requests, open incidents (shown in red), feedback to review | `GET /api/stats` |
| The latest six actions (who did what) | `recent_activity` in `GET /api/stats` |
| "Nothing needs attention right now" | when all of the above are quiet |

It scrolls on its own and **holds still while the pointer is on the text or the keyboard focus is in it**. **⏸ / ▶**
pauses it for good (remembered in the browser). It refreshes every 20 seconds and when the tab is opened again;
if the counts changed, the table below reloads too. Screen readers get one plain sentence instead of the moving
text. With "reduce motion" switched on in the operating system, the ticker does not move: it shows one copy of the
items that can be scrolled by hand.

### Animations

Every role's features move a little: dialogs spring up, chat messages fly in, pages rise in, numbers count up, rows
that are new slide in and rows that changed glow. The animations are played by **Motion** (<https://motion.dev>, MIT),
the animation engine that **Framer Motion** is built on, in its plain-JavaScript build. Framer Motion itself is a React
library and this app is plain Flask and JavaScript, so Motion is the same engine without React. It is vendored in
`static/vendor/motion.js` (so it also works offline) and **all** the motion code is in one file, `static/fx.js`; the
rest of the app only says what happened ("a message arrived", "a row changed").

| Where | What moves |
|---|---|
| Every role | the role chooser and every dialog (spring up, fields follow), the toast, the sidebar and the phone drawer, the theme button, hover and press on every button, chip and card, the page you open |
| Coordinator | Dashboard tiles slide in and count up, filter chips pop, only the **new** rows slide in and a **changed** row glows, deleted rows slide out, the ⋯ menu grows out of its button, the bulk bar rises, the import result, the ticker, Appointments / Requests / Incidents / Activity cards (a changed card glows, a card that leaves its filter slides out), Feedback tiles and bars grow |
| Guard | the incident pop-up, its claim check (a disagreeing claim nudges), the confirmation card (it glows once), Verify ID results, the QR scan line |
| Student | the Request help pop-up and its Lost ID notice, the confirmation card, student cards, the three typing dots |

How it behaves:

- Only what is **new or changed** moves. Typing in the search box or refreshing the numbers does not replay the page.
- Nothing waits for an animation: buttons work while they are still moving, and each one clears the styles it added
  when it ends.
- It is switched off by the operating system's **reduce motion** setting. To switch it off for this browser only, run
  `localStorage.setItem('motion', 'off')` in the browser console and reload (`localStorage.removeItem('motion')`
  switches it on again). With it off, or if `motion.js` or `fx.js` did not load, the page works the same, just still.

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

**Filing and following up happen in the chat**, the same way a student's Request help does:

1. The guard (or coordinator) taps **⚠️ File incident**: under a student's card, in the quick buttons above the message
   box, on a **Verify ID** result, or after typing `file incident`, `no id`, `walang id` or `lost id incident`. The
   button opens the report as a pop-up over the chat, already set to the student the chat is on and, when the answer
   shows it, to the category. As soon as a student ID is in it, the pop-up checks the claim against the records.
2. **File incident** in the pop-up saves it and the chat shows the confirmation: the incident number, what the records
   say, the false-statement count and penalty, and what to do next. The text typed in *What happened?* is kept for
   the coordinator's Incidents page and is not copied into the chat history. Cancel, ✕ or Escape files nothing.
3. Follow-up is typed or tapped, never on a page of its own:

| Type | Result |
|---|---|
| `incidents`, `show incidents`, `incident log` | the latest incidents, newest first, with a **✔ Resolve #N** button for each open one |
| `resolve incident 12`, `reopen incident 12` | changes that incident's status and says so |

Only the guard and the coordinator can file or resolve; the server answers a student's attempt with 403. The
coordinator's **Incidents** page stays as the review list (Resolve and Reopen, mark or withdraw a false statement).

**Giving false information twice costs ₱1,500.** The guard ticks *False statement* in the pop-up, or the coordinator
marks or withdraws it on the Incidents page. The count is per student ID, in the order the incidents were filed:
the **second** false statement carries the ₱1,500 penalty (once per student); the first has none. A third and
later one is recorded without an automatic amount, because the rule you gave covers only "twice"; the coordinator
decides. Withdrawing a statement recalculates the others. The amount and the number are `PENALTY_AMOUNT` and
`PENALTY_AT` in `student_data.py`. Students are only told the rule (in the Request help form), never their count.
The app assumes the penalty is paid at the Cashier and does not record payment.

### Privacy and access

- There is **no sign-in**. The role is a button the browser remembers, and the chat sends it to the server as
  `workspace_role`. Anyone who can reach the server can call `/api/inventory` (which includes the address and
  contact number), the CSV export, `/api/verify` (which returns a QR token), `/api/stats` (whose latest-activity list
  names student IDs) and the other endpoints directly. Hiding the address and contact number from the guard and the
  student in the chat is a courtesy of the interface, not a protection.
- Typing someone's name in the student's chat shows that student's name, ID, Year & Section and status. That is how
  the chat was asked to work, and it is why a sign-in matters before real records are used. For the same reason
  `my requests` lists the requests of whichever student ID or name is typed, and the Request help form can be filed
  for any student ID: nothing proves who is typing. Both need the sign-in too.
- Filing and resolving incidents from the chat trusts the same client-supplied role: `/api/chat/incident` and the
  `incidents` / `resolve incident N` commands refuse `student`, but anyone who can reach the server can send
  `guard` or `coordinator`. The coordinator-only controls (marking a false statement) use the `/api/incidents` API and
  are just as open. Until there is a sign-in, treat incident records as unauthenticated.

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

## Dashboard news (PGPC announcements)

The strip under the Dashboard heading scrolls the announcements in `data/pgpc_news.json`: a list of
`{"icon": "📣", "text": "..."}` (text up to 200 characters, 20 items). Edit the file, commit and redeploy to change it.
The three items in it now are placeholders: replace them with PGPC's real news. Nothing is fetched from the internet,
because the chatbot has to work offline. The PGPC seal is `static/pgpc-logo.png` (brand) and `static/favicon.png` (tab icon).

## Dataset and intents

- `property_management_dataset.csv`: 165 synthetic utterances, 15 for each of the 11 school ID
  intents, with `id,text,intent,language,author` columns. Only school ID questions are in it
  (ids start at `S076`; the removed chit-chat rows `S001`-`S075` are not renumbered).
- `property_management_intent.csv`: the intent catalog and staff actions: the 11 ID intents,
  the `out_of_scope` fallback (no training rows), and nothing else. Hello, thanks, "what can you
  do" and "talk to a person" are answered by the rules in `small_talk.py`, not by the ANN.
- `property_management_dataset.tab`: Orange version with `intent` as the class,
  `text` as a string meta, and identifiers/language/author as metadata.

## ANN training in Python

The guide allows a locally trained Artificial Neural Network. `train_model.py` builds
TF-IDF word/bigram features and a scikit-learn `MLPClassifier`: one 32-neuron ReLU
hidden layer and 11 output intents (the school ID topics). It uses L-BFGS, L2 regularization (`alpha=0.1`),
seed 42, and at most 600 iterations. No external AI service or pretrained model is used.

```bash
.venv/bin/python train_model.py
.venv/bin/python Chatbot.py
```

The script evaluates a stratified 80/20 split (132 training, 33 test examples), fitting
vocabulary and IDF only on the training partition. It then refits on all 165 source
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
The fixed default fallback threshold is 0.35. `out_of_scope` has no training rows: the ANN
answers it when the input has no known vocabulary, when the maximum softmax score is below the
threshold, or when no word in it is a known ID word (the *topic-word gate*: words such as
"where", "the", "please" do not count; the list is `FUNCTION_WORDS` in `intent_model.py`).
Scores are not calibrated probabilities. On our own probe questions this rejects about 75-80%
of unseen off-topic questions; near-domain ones ("registrar's office", "what time is it")
can still leak through, so it is not an assurance.

`small_talk.py` recognises hello, thanks/bye, "what can you do" and "talk to a person" by rules
(whole-message patterns, so "Hi, why is my ID pending?" still goes to the ANN). It runs after the
commands and student lookup and before the ANN; these replies carry confidence 0.

Evaluation note: with the chit-chat classes gone, the seed-42 held-out score dropped slightly
(raw accuracy 69.7%, 63.6% with the fallback). The old figure was padded by chit-chat classes
that scored a perfect F1. The average over other splits is about 58%, so quote the report
files, not a single number.

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
may see, the gate commands, visitors, the records list and the typed-command edge cases. `tests/test_student_chat.py`
covers the student's chat-only flow: which answers carry the Request help button, sending the form from the chat,
`my requests` (and its 10-word limit, so a long story is never mistaken for the command), Years 1 to 4 and the data behind the ticker. `tests/test_incidents.py`
covers the two incident types, the claim check and the ₱1,500 rule, and `tests/test_incident_chat.py` covers filing and
resolving them from the chat (the pop-up's endpoint, the `incidents` and `resolve incident N` commands, and that a
student is refused). `tests/test_motion.py` guards the animation layer (the vendored file is the recorded one, nothing
is loaded from the internet, every `fx` call in the page exists) and `tests/test_offline_assets.py` checks that the
pages and the stylesheet ask for nothing outside the app and that the fonts ship. `tests/test_vercel_entry.py` guards the hosted entry point (`app.py`: what Vercel looks for, the 503 page and the
restart by itself, the clock) and that the committed model matches the pinned scikit-learn. `tests/test_assistant_tools.py` covers feedback,
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
- [ ] Guard: tap **⚠️ File incident** (under a card, or the button above the message box): a pop-up opens over the chat
      and there is no Incidents page in the sidebar. File **No ID** twice for the same student with *False statement*
      ticked; the confirmation appears in the chat and the second shows ₱1,500. Type `incidents`, tap **✔ Resolve**.
- [ ] Coordinator: the Incidents page lists what the guard filed; **Mark as false statement** and **Withdraw** work, and
      **⚠️ File incident** there opens the same pop-up over the Assistant.
- [ ] Animations (all three roles): dialogs spring up, chat messages fly in, the Dashboard numbers count up once when it
      opens, a status change makes its row glow, and nothing keeps moving afterwards. With the system's **reduce
      motion** on, or after `localStorage.setItem('motion', 'off')`, nothing moves and everything still works.
- [ ] Offline: turn the Wi-Fi off and reload. The page looks the same (the fonts are local) and the chat answers.
- [ ] Student on a phone: type an ID and a name; the message box is on screen without scrolling. Type `I lost my ID`:
      a small **Request help** button appears; it opens the pop-up with the Cashier step first; **Send request**
      puts the confirmation in the chat; `my requests` lists it. There is no tab bar and no Search ID form.
- [ ] Coordinator: the ticker scrolls, stops under the pointer, **⏸** keeps it stopped after a reload, and the
      numbers match the tiles. Add a request as a student in another window: within 20 seconds the ticker shows it.
- [ ] Coordinator: **Add student** with Year & Section `5A` is refused; `4A` is accepted.
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
