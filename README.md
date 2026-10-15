# School ID property management chatbot

A local prototype for student ID inventory, processing status, release appointments,
pending ID follow-ups, lost ID replacement appointments, and small QR stickers.
The proposal was not included with the request; features follow the described workflow.

## Run

Python 3.9 or later:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python train_model.py
.venv/bin/python Chatbot.py
```

Open http://localhost:5000. Six fictional demo records are created on the first run.
Records, accounts, and appointments persist in `data/inventory.sqlite3`. Back up your local database before updating. Avoid committing runtime database changes.
The interface supports search, course, section, year, request type, and status filters.
QR codes appear on the left; status, appointments, and updates appear on the right.
Sign in as a coordinator, add a record, choose **Update / schedule**, set a status and appointment, then choose
**Ask about ID** to ask the chatbot about that record. Students confirm a replacement
pickup booking by clicking an available date in the chat. Text questions alone do not
submit reports or change appointments. Coordinators manage records and publish availability.

### Roles and sign-in

There is no public role picker or shared default password. Create accounts on the
computer that runs the app. Run these commands from the project folder before opening
the workspace; passwords are prompted twice and must be at least 10 characters:

```bash
.venv/bin/python -m flask --app Chatbot:create_app create-account --username coordinator --role coordinator
.venv/bin/python -m flask --app Chatbot:create_app create-account --username guard --role guard
.venv/bin/python -m flask --app Chatbot:create_app create-account --username sam --role student --student-id DEMO-003
```

On **Windows PowerShell**, use:

```powershell
.\venv\Scripts\python.exe -m flask --app Chatbot:create_app create-account --username coordinator --role coordinator
.\venv\Scripts\python.exe -m flask --app Chatbot:create_app create-account --username guard --role guard
.\venv\Scripts\python.exe -m flask --app Chatbot:create_app create-account --username sam --role student --student-id DEMO-003
.\venv\Scripts\python.exe Chatbot.py
```

Replace the usernames with assigned account names. A student's `--student-id` must
already exist in inventory. The coordinator first adds new students through the
workspace, then provisions their accounts with the command above. Accounts have
one assigned role; student accounts are linked to one inventory record.

| Role | Access |
| --- | --- |
| Student | View their own record; receive available pickup dates and book/reschedule their own ready replacement ID; ask about status or QR; print their sticker |
| Property management coordinator | Search all records; add records; update ID status, processing, pending reasons, and appointments; manage ID images |
| Guard | Search all records; ask about a student's ID; view status and share/print its QR; no record changes |

Permissions are checked on the server. Passwords are hashed; sessions expire after
8 hours and form/API writes require a CSRF token. `data/.session-key` is generated
locally and must stay private; preserve it across restarts, or remove it to invalidate
existing sessions. There is no password reset, account administration screen, or
login throttling yet; deployment for real students needs these operational controls
and HTTPS. QR links remain shareable links to student details and images.

### Guard lookup and school entry policy

As a guard, ask **“What's the processing status of Sam Reyes's ID?”**. You can also
use a student ID such as **DEMO-003**, or select **Ask about ID** on a record. A named
student takes precedence over a previously selected card. Full names ignore case
and curly/straight possessive apostrophes; partial names such as **Sam** work in
**“What is the status of Sam's ID?”**. Multiple matches require choosing a record;
missing names never silently return another student's status.

The reply gives **ID status**, **processing status**, and a QR link. Scan it to see
current statuses, the appointment if recorded, and front/back ID images. The same
QR reflects later coordinator updates; it is not a snapshot of the original status.
Students' authenticated inventory/chat access is limited to their own record.

- ID status: **Not issued**, **Active**, or **Lost**.
- Processing status: **Pending**, **Processing**, **Ready for release**, or **Released**.

Existing databases migrate automatically: Lost ID requests initially become Lost;
released IDs become Active; other IDs become Not issued. The coordinator should
verify these migrated statuses. Changing processing status does not automatically
activate an ID; set ID status independently after confirming issuance/replacement.
The chatbot repeats **“No ID, No Entry.”** It reports inventory facts and does not
approve entry or treat a status QR as a substitute for wearing an ID.

### Student chooses an available replacement pickup date

1. The coordinator opens **Available replacement pickup dates**, publishes each date
   and time, and sets **Students per slot** (1–500). Use school time, **UTC+8** (PGPC,
   Philippines). Only future dates can be published. There are no invented default dates.
2. Track the student's lost ID as **Lost**. Once the replacement is physically
   available, set processing status to **Ready for release**.
3. The student's chatbot automatically shows the published dates/times that have space.
   It checks every **15 seconds** while the workspace is visible and also on sign-in
   or return to the tab. If the student is away, the offer appears next time they open
   the workspace. This is an in-app message; email/SMS delivery is not configured.
4. The student clicks **Book · date at time** in the chat. The appointment is saved
   immediately and confirmed with a QR link. They can ask **“Can I reschedule my lost
   ID pickup?”** or choose **Choose / change pickup date** to select another slot.
5. Booking a new slot replaces the previous appointment and frees its space. The same
   QR page shows the latest date/time. If a slot fills before the click, the previous
   appointment stays intact and the chatbot refreshes available choices.
6. After collection, the coordinator marks the ID **Active** and **Released**.

Only authenticated students can book their own ready replacement; guards cannot book
or change availability. The coordinator can also book a published slot for a student.
Capacity is checked in one SQLite transaction, including competing booking attempts.
Legacy lost ID appointments at the same date/time count toward capacity. Closed,
full, and past slots are not offered for new bookings. Closing a slot stops new
bookings and preserves confirmed appointments; reopen it if it is still in the future.
The coordinator's general form still supports replacement processing appointments;
assignments to a published slot also respect capacity.

### QR printing and phone scanning

Set `PUBLIC_BASE_URL` to the reachable URL of this application before printing:

```bash
PUBLIC_BASE_URL=https://your-school-app.example .venv/bin/python Chatbot.py
```

Without it, QR codes use the URL used to request the image. A localhost QR cannot be
opened from another phone. A QR contains an opaque URL token; scanning displays the front and back ID images plus
name, course, section, and year level. Anyone with that URL can view those details and the uploaded ID images.
**Print sticker** creates a 30 mm QR area with a student ID caption; print at 100%
and check legibility with a phone. A caption or label can require extra space beyond 30 mm.
Long URLs increase density; test with the final hostname and physical sticker.

### Front and back ID images

Choose **ID images** on an inventory record, select front and/or back JPEG/PNG files,
then **Save ID images**. Scan its existing QR code to view both sides; no new sticker
is needed. You can replace one side without changing the other, or remove a side to
restore its sample placeholder. Click an image in the scan page to open it at full size.

Until you upload an image, each side shows the PGPC reference layout marked **SAMPLE**, with a neutral photo silhouette and placeholders for contact and
signature. The school header and a person-free campus crop are included as sanitized assets;
no face, personal name, signature, address, phone number or original QR is copied.
The back follows the emergency-contact/reminder/QR/signatory/year layout. The back QR
is generated from the same student's application link. Source artwork is blurred:
a clean blank PGPC template is needed for pixel-exact campus artwork and typography. Upload only approved student ID images.

Images persist in the `id_images` table of the existing SQLite inventory database.
Existing inventories upgrade automatically without changing records or QR tokens.
Each image must be a single JPEG/PNG, at most 5 MB and 20 megapixels. Uploads are
normalized to PNG, resized proportionally to fit 1600 × 2400 pixels, and stripped of
embedded metadata. Both sides are validated before either is saved. The QR destination
still requires a phone-reachable `PUBLIC_BASE_URL`; actual ID images are visible to
anyone who has that link. Shareable QR access still needs the school’s approved policy before using real student data.

### Windows PowerShell

From the extracted project folder, use your existing `venv` directly:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe train_model.py
.\venv\Scripts\python.exe Chatbot.py
```

If the environment is missing, create it first with `py -3.11 -m venv venv`. When
updating an existing installation, keep `venv/` and `data/`; replace the Python files,
`templates/`, `static/`, and `assets/`, install requirements, and restart the app. This role/status update does not require retraining an existing model whose CSVs remain unchanged. Create the role accounts once using the commands above.

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
ID status, dates/times, and student details are retrieved from SQLite after intent
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

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Recommended next steps

1. Confirm the proposal, official status stages, required documents, fees, office hours,
   contact details, and who may set or change release appointments.
2. Have staff test the filter → status update → appointment → chat lookup → printed QR
   flow with fictional records, including pending requirements and lost IDs.
3. Verify account assignments and student identity; add an audit trail, account/password
   administration, login throttling, a QR access/revocation policy, and backups before
   using actual student records. The QR profile deliberately displays the requested
   student details to anyone holding its link.
4. Collect varied English/Filipino/Taglish questions, improve confusing intents, then
   train and evaluate the final classifier with a held-out set.
5. Add approved appointment availability and reminders once the office workflow is agreed.

School dates/times are stored as local wall time. Pickup slots have capacity checks. Automatic holiday calendars, email/SMS
notifications, fee payment, and lost ID report submission are not implemented.

For an isolated local preview/test database, set `SCHOOL_ID_DATABASE` to an alternate SQLite file path. The default remains `data/inventory.sqlite3`.
