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

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python train_model.py
.venv\Scripts\python Chatbot.py
```

If the app stops with "ANN model version differs from this installation", the saved model was
trained with another scikit-learn version than the one installed. Run `train_model.py` once.

Open http://localhost:5000. Six fictional demo records are created on the first run.
Records and appointment changes persist in `data/inventory.sqlite3` (ignored by Git).
The interface supports search, course, section, year, request type, and status filters.
QR codes appear on the left; status, appointments, and updates appear on the right.
Add a record, choose **Update / schedule**, set a status and appointment, then choose
**Ask about ID** to ask the chatbot about that record. Chat requests do not submit reports
or book appointments; staff use the inventory form to save changes.

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

## Pages by role

The role is chosen on the first screen. It is a convenience for the demo, not a security
control (see next steps).

| Role | Pages | What it does |
|---|---|---|
| Coordinator | Overview, ID inventory, Queue, Requests, Feedback, Activity, Assistant | Overview tiles show today's and overdue appointments, open requests and 👎 waiting for review. The inventory has **Needs attention** shortcuts (Overdue, Today, Ready but not scheduled, Pending without a reason), bulk status updates, sticker sheets and CSV export. Requests are an inbox with a fixed flow: Submitted → In review → Accepted → Closed. Accepting a Lost ID request updates the record once; a closed request stays closed. |
| Guard | Verify ID, Gate log, Incidents, Assistant | Find a student by ID, name or QR scan, then one-tap **Check in** / **Check out**. The gate log shows today's counts. Incidents can be filed and resolved. |
| Student | My ID, Request help, Assistant | Look up your own ID (name, course, status, appointment; staff notes and the QR token are never shown), send a request, and follow its status. |

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
.venv/bin/python -m unittest discover -s tests -v          # Windows: .venv\Scripts\python -m unittest discover -s tests -v
```

`tests/test_assistant_tools.py` covers feedback, suggestions, requests and the staff tools,
`tests/test_feedback_dataset.py` covers the feedback-to-test-set script. They do not drive a
browser or a camera, so check these by hand on the final machine:

- [ ] Ask "pending", tap 👎, pick a reason, tick or leave the share box, **Send**; the coordinator's
      Feedback page shows it (question only if shared) and the nav badge counts it.
- [ ] Type nonsense: the chatbot says it is automated and offers tappable guesses or a way to
      reach the office.
- [ ] Coordinator: Overview tiles match the queue; a Lost ID request moves Submitted → Accepted
      and the record becomes Pending / Lost ID.
- [ ] Guard on a phone over **https**: Verify ID → **Scan QR** reads a printed 30 mm sticker in
      your real lighting; then **Check in** writes one row in the gate log.
- [ ] Print one sticker sheet at 100% and scan every sticker with a phone.

## Recommended next steps

1. Confirm the proposal, official status stages, required documents, fees, office hours,
   contact details, and who may set or change release appointments.
2. Have staff test the filter → status update → appointment → chat lookup → printed QR
   flow with fictional records, including pending requirements and lost IDs.
3. Add staff sign-in, student identity verification, access controls, an audit trail,
   QR access/revocation policy, and backups before using actual student records. This
   prototype has no authentication; record endpoints are available to anyone who can
   reach it. The QR profile deliberately displays the requested student details.
4. Collect varied English/Filipino/Taglish questions, improve confusing intents, then
   train and evaluate the final classifier with a held-out set.
5. Add approved appointment availability and reminders once the office workflow is agreed.
6. Use the Feedback page regularly: set the correct topic on shared questions, export, run
   `feedback_to_dataset.py`, evaluate, then add the useful paraphrases to the dataset and retrain.
7. Decide who may read feedback and how long it is kept. The Feedback, request and gate
   endpoints are as open as the rest of the API, and there is no delete button for staff yet.

School dates/times are stored as local wall time, and the server's own clock decides what
"today" and "overdue" mean. Run it on a machine set to Philippine time (on Linux/macOS you
can start it with `TZ=Asia/Manila`; Windows uses its time-zone setting), otherwise those
labels shift by the difference. Office capacity, conflicts, holidays,
notifications, fee payment, and lost ID report submission are not implemented.
