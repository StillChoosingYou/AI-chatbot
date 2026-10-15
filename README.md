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
Sign in as a coordinator, add a record, and use **Update / schedule** for processing and appointments. Verified students use **ID status** in their chatbot for their own record. Public guidance is at **/help**, also linked from sign-in. Students confirm a replacement
pickup booking by clicking an available date in the chat. Text questions open a confirmation form; they do not
submit reports or change appointments. Coordinators manage records and publish availability.

### Assistant experience tools

- **Private student progress:** shows the signed-in student's recorded processing stage, current appointment and report tickets. It refreshes every 15 seconds while visible, and after a confirmed report or booking. Found/missing-stock cases do not display an invented processing progression.
- **Appointment calendar:** after booking, download an `.ics` file with the actual appointment time converted from school time (UTC+8), plus a one-hour calendar reminder. The file omits name, student number and QR. Import it into your calendar; this is a snapshot, so download again after rescheduling. Downloading does not book an appointment or configure school email/SMS reminders.
- **QR camera and photo tools for guards:** camera frames and PNG/JPEG/WebP photos (up to 10 MB) are decoded on the device with bundled jsQR 1.4.0. Only the decoded code is sent to the application; photos are not uploaded. This also works when the browser has no native BarcodeDetector. Camera access requires HTTPS or localhost and permission. Photo/pasted-code alternatives remain available. Guards cannot get status from names, student numbers or numeric record URLs.
- **Optional voice typing:** supported browsers offer English or Filipino dictation through the browser's speech service. The recognized text stays in the input until the user taps Send. Microphone permission, browser support and speech-service connectivity are required; typing always remains available.
- **Guided chat:** quick questions, an expandable guide, request progress, useful connection errors and a retry button. Duplicate sends are blocked without clearing the next draft. Start a new conversation clears the visible chat, without changing school records.
- **Accessibility/mobile:** keyboard focus, skip-to-chat, labeled controls, live request status and responsive progress/scanner panels.

The calendar endpoint `/api/my-appointment.ics` requires a verified student session and always exports that student's saved appointment. PMO continues using the inventory workspace. The ANN classifier and existing dataset are unchanged by these UI tools. No external AI API or additional Python packages are needed. The pinned QR decoder, integrity and Apache-2.0 license are documented in `static/vendor/README.md`.

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
one assigned role; student accounts are linked to one inventory record. Student sign-in
requires the assigned username/password **and the matching student number**; staff leave
the student-number field blank. Existing student sessions must sign in again to verify.

**Verification boundary:** these are local, PMO-provisioned prototype accounts.
This is not school Google/Microsoft SSO or email/SMS OTP. Connecting the official school
account provider or OTP delivery requires the school's chosen provider/configuration;
no fake OTP or neural-network prediction is accepted as verification.

| Role | Access |
| --- | --- |
| Student | View their own record and ID images; verify/confirm a lost report; receive notices; choose application/pickup dates; ask about representative pickup (PMO approval required) |
| Property management coordinator | Manage inventory/images, policy and appointment availability; process/issue replacements; print new stickers; log custody/claims/stock audits; explicitly notify found-ID owners |
| Guard | Search records; scan QR and view ID images/status; confirm physical receipt of found IDs and verified claiming; cannot edit inventory or issue replacements |

Permissions are checked on the server. Passwords are hashed; sessions expire after
8 hours and form/API writes require a CSRF token. `data/.session-key` is generated
locally and must stay private; preserve it across restarts, or remove it to invalidate
existing sessions. There is no password reset, account administration screen, or
login throttling yet; deployment for real students needs these operational controls
and HTTPS. QR details and images require guard/staff sign-in; students see their own images through their authenticated workspace.

### Intent permissions and school entry

| Intent | Access |
| --- | --- |
| `bot_help`, `id_requirements`, `office_hours_location`, `qr_student_details` | Public guidance; no inventory lookup |
| `id_release_schedule`, `id_processing_status`, `pending_id`, `pending_id_schedule` | Verified student: own record; guard: scan guidance only; status requires the ID QR code |
| `lost_id_report`, `lost_id_schedule`, `authorized_pickup` | Verified student: own record only; representative pickup needs PMO approval |
| `id_inventory`, `qr_sticker_request` | Coordinator inventory functions; chatbot directs guards/students to PMO |
| `found_id_report` | Public hand-in guidance; guard records custody through chat/scanner; PMO notifies owner |

### Separate workspaces

- **Property management coordinator:** signs in to inventory, filters records, updates processing, uploads ID images, prints stickers, publishes appointments, manages policy and sends found-ID notifications. There is no chatbot on this workspace; `/api/chat` denies coordinator requests.
- **Guard:** signs in to the chatbot, scans the QR code on the student's ID to view ID status and processing status and logs physically received IDs. Name, student number and numeric record IDs cannot reveal status or generate a QR for guards. Guards cannot load the inventory feed, list all tickets, edit records, print stickers or book student appointments.
- **Student:** signs in with their assigned account and student number to the chatbot. They can check their own ID, submit a confirmed lost report, receive PMO notices and choose available replacement appointments. They cannot browse inventory or other students' records.

The server enforces these roles on direct API requests as well as the pages. `/api/inventory` is coordinator-only; `/api/my-id` returns only the signed-in student's record without its raw QR token. Individual profile and appointment endpoints retain ownership checks. Greetings, help and other public guidance remain available at `/help`. The neural network classifies intent; login and server rules enforce access. The catalog's `access_role` describes each function, and coordinator functions run through inventory controls.

Public/other-student QR scans display limited guidance without name, course, student
number, processing details or ID images. A revoked reference displays only
**REVOKED — Reported lost, contact PMO.** Unknown/other references give general PMO
instructions. Staff can view the authorized scan profile and front/back images;
students view their own details through their authenticated workspace.

**No ID, No Entry.** The chatbot and QR never approve entry.

### Confirmed lost/found ID workflow

1. The student says **“Nawala ang ID ko”** (`lost_id_report`) and enters their student
   number, course and section. These must match the signed-in student's inventory
   record. The student must tick the loss confirmation before a ticket is created.
2. Status becomes **Reported Lost / Pending**. The original QR is immediately
   **REVOKED**. Scanning it says **“Reported lost, contact PMO.”**
   No scan activity, inactivity, or gate attendance can create a lost report.
3. The bot returns the configured replacement requirements/fee. The coordinator enters
   the approved values under **Approved ID policy**. Blank values explicitly say
   **Hindi pa configured—confirm sa ID office**; the app invents no fees or requirements.
4. The coordinator publishes future **Application** slots (date/time/capacity, UTC+8).
   The student's chat automatically offers available slots. Clicking a date books it;
   choosing another date frees the old slot. There are no invented dates.
5. Use **Update / schedule** to move the confirmed request to **Replacement Processing**,
   then **Ready for release** when the replacement is physically ready. The application
   appointment is cleared when processing starts. Publish **Pickup** slots for collection.
   The student chooses an available pickup date in the chat. Both booking stages enforce
   capacity inside a SQLite write transaction.
6. On actual collection, the coordinator sets **Released**. This issues a new opaque QR,
   makes the replacement **Active**, permanently retires the old QR, and archives the
   original's uploaded images. Print the **new sticker** and upload the replacement's
   images after issuance. The old QR can never reactivate, including after a found report.

For a found ID, public users receive instructions to turn it over to PMO; no owner
identity is revealed. The guard/staff physically receives the ID and confirms custody
under **Log found ID** (`found_id_report`). A scan or chatbot question alone changes nothing.

- If the QR has **never been revoked**, record **Found - For Claiming**. Staff verifies
  the claimant with another valid ID before **Confirm claiming** returns it to Active.
- A **revoked or retired** original remains REVOKED even when recovered. Record custody
  without reactivating it or cancelling the replacement/appointment. PMO handles its
  return; a replacement receives a new QR at issuance.
- A found report does **not** automatically send a notice. PMO/coordinator reviews the
  report ticket and clicks **PMO: notify owner**, confirming physical custody. This
  queues an in-app notice for the owner (claim location or revoked-original instructions).
  Guards/students cannot use that action. Repeat clicks do not duplicate the notice.

Notifications persist until delivered to the owner's signed-in chat. Available slots
and notifications refresh every **15 seconds** while the tab is visible, on tab return,
and on sign-in. These are in-app notices; email/SMS is not configured. The student sees
only their own tickets; only the coordinator can inspect all report tickets.

### Office stock audit and Unclaimed

- **Report missing stock** requires a coordinator to confirm a physical office audit
  of a ready/unclaimed ID. It records **Missing from Stock**, creates a ticket, flags
  its QR, clears appointments and notifies the student. Use found custody/claim actions
  after physical recovery; a general edit cannot clear that flag.
- An ID still **Ready for release** after **more than 30 days** becomes **Unclaimed**
  on the next inventory/profile/offer check. This uses the stored ready date, never the
  last scan. Existing ready inventory starts its clock on migration because its prior
  age is unknown. Pickup remains available for ready/unclaimed replacements.
- Optional gate-pattern prompts are outside this implementation.

ID states: **Not issued**, **Active**, legacy **Lost**, **Reported Lost**,
**Found - For Claiming**, **Missing from Stock**. Processing states include **Pending**,
**Processing**, **Replacement Processing**, **Ready for release**, **For claiming**,
**Unclaimed**, **Released**. Legacy Lost requests remain supported; verified new reports
use the student confirmation flow. Existing inventory/accounts/images are migrated in place.
Back up your local SQLite database before updating.

### QR printing and scanning

The QR contains **only an opaque code**, such as `PGPC-…`; it contains no name,
student number or other personal details. **Print sticker** makes a 30 mm QR area with
its opaque code caption. Print at 100% and physically test the final sticker.

A normal phone camera shows the raw code. Public users can paste it into **Check a QR code** at `/help` to get limited PMO guidance without owner details. Guards/staff open the deployed application,
sign in, then use **Scan with camera** or paste the code into **Scan / enter QR code**.
The camera scanner needs HTTPS (or localhost), camera permission, and a browser with
QR `BarcodeDetector` support. Manual entry is always available. Legacy stickers with
`/student/<code>` URLs also resolve through the staff scanner. Public views remain limited; staff sign-in is required for the full profile.
Use the school's reachable application URL on phones. `PUBLIC_BASE_URL` can set the
origin for shared staff links; it does not add a URL or personal data to new QR codes.

### Front and back ID images

Choose **ID images** on an inventory record, select front and/or back JPEG/PNG files,
then **Save ID images**. Scan its existing QR code to view both sides; no new sticker
is needed. You can replace one side without changing the other, or remove a side to
restore its sample placeholder. Click an image in the scan page to open it at full size.

Until you upload an image, each side shows the PGPC reference layout marked **SAMPLE**, with a neutral photo silhouette and placeholders for contact and
signature. The school header and a person-free campus crop are included as sanitized assets;
no face, personal name, signature, address, phone number or original QR is copied.
The back follows the emergency-contact/reminder/QR/signatory/year layout. The back QR
contains the same opaque ID code. Source artwork is blurred:
a clean blank PGPC template is needed for pixel-exact campus artwork and typography. Upload only approved student ID images.

Images persist in the `id_images` table of the existing SQLite inventory database.
Existing inventories upgrade automatically without changing records or QR tokens.
Each image must be a single JPEG/PNG, at most 5 MB and 20 megapixels. Uploads are
normalized to PNG, resized proportionally to fit 1600 × 2400 pixels, and stripped of
embedded metadata. Both sides are validated before either is saved. Images require
authentication. Flagged original images are preserved until replacement issuance; upload
the replacement images afterward. Retired QR scans show the archived original images
and a permanent-deactivation warning, with the current inventory status labeled separately.

### Windows PowerShell

From the extracted project folder, use your existing `venv` directly:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe train_model.py
.\venv\Scripts\python.exe Chatbot.py
```

If the environment is missing, create it first with `py -3.11 -m venv venv`. When
updating an existing installation, keep `venv/` and `data/`; replace the Python files,
`templates/`, `static/`, and `assets/`, install requirements, and restart the app. This update adds `authorized_pickup` and access scopes (and retains `found_id_report`): retrain with `python train_model.py` after replacing both CSVs, then restart. A stale model is rejected. Create the role accounts once using the commands above.

## Dataset and intents

- `property_management_dataset.csv`: 270 synthetic utterances, 15 per intent, with
  `id,text,intent,language,author` columns. Generic conversational examples retained
  from the supplied CSV; school ID examples replace general campus support requests.
- `property_management_intent.csv`: 18 intent definitions, actions and `access_role` permissions.
- `property_management_dataset.tab`: Orange version with `intent` as the class,
  `text` as a string meta, and identifiers/language/author as metadata.

## ANN training in Python

The guide allows a locally trained Artificial Neural Network. `train_model.py` builds
TF-IDF word/bigram features and a scikit-learn `MLPClassifier`: one 32-neuron ReLU
hidden layer and 18 output intents. It uses L-BFGS, L2 regularization (`alpha=0.1`),
seed 42, and at most 600 iterations. No external AI service or pretrained model is used.

```bash
.venv/bin/python train_model.py
.venv/bin/python Chatbot.py
```

The script evaluates a stratified 80/20 split (216 training, 54 test examples), fitting
vocabulary and IDF only on the training partition. It then refits on all 270 source
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
classification. QR generation remains separate. The model cannot verify identity, grant roles, authorize representative pickup,
book appointments, revoke/issue QR codes or determine school policy. Authentication,
ownership checks, explicit confirmation and server rules control these actions. Group members should review and understand this code,
curate the dataset, and document their own contributions for the guide's defense.

## Defense explanation

> Ang neural network ay nag-uuri lamang ng intent. Ang login, student-number matching,
> ownership checks at authorization rules ang nagve-verify at naglilimita ng access.
> PMO ang nag-aapruba ng representative pickup at nagpapadala ng found-ID notice.

`tests/test_intent_access.py` forces every intent prediction across anonymous/student/
guard/coordinator sessions and checks the permission matrix independently of ANN
accuracy. The current login is a local prototype; official school SSO/OTP integration
must be described as pending until configured and tested with the actual school provider.

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
2. Have PMO test inventory filters, status updates, appointments and printed QR; have guards/students test their chatbot lookups
   flow with fictional records, including pending requirements and lost IDs.
3. Verify account assignments and student identity; add an audit trail, account/password
   administration, login throttling, a QR access/revocation policy, and backups before
   using actual student records. QR details require staff sign-in; review staff access and account assignments.
4. Collect varied English/Filipino/Taglish questions, improve confusing intents, then
   train and evaluate the final classifier with a held-out set.
5. Enter the approved replacement policy, publish real appointment availability, and test the lost/found/claim flow with staff.

School dates/times are stored as local wall time. Pickup slots have capacity checks. Automatic holiday calendars, email/SMS
notifications and fee payment are not implemented. Lost/found submissions and in-app notifications are implemented.

For an isolated local preview/test database, set `SCHOOL_ID_DATABASE` to an alternate SQLite file path. The default remains `data/inventory.sqlite3`.
