# Recommendation Module README

## 1. What The Script Does

`generate_recommendation_from_curves.py` compares one patient/session motion curve against a saved local standard curve and generates AI-assisted, rule-based advice.

`evaluate_recommendation_accuracy.py` evaluates severity accuracy when labeled sessions are available.

It supports:

- `walking`
- `squat`
- `upstairs`

The script writes:

- a structured JSON result;
- a readable TXT report for doctors or the web team.

It can also write optional analysis outputs:

- patient-vs-standard HTML visualization;
- normalized patient average curve CSV;
- detected segment/repetition summary CSV;
- key metrics CSV.

It does not regenerate standard curves. It loads existing standard CSV files produced by `Walk.py`, `Squat.py`, or `Upstairs.py`.

For walking and squat, the script can also segment a complete patient session before comparison:

- walking is segmented into detected gait cycles;
- squat is segmented into detected repetitions;
- upstairs currently remains a full-action comparison because the available standard curve represents the whole stair-climbing action, not one isolated step.

This gives fairer results for complete API sessions because each detected cycle/repetition is normalized to 0-100% before being compared with the standard curve.

Current segmentation does not reject walking or squat segments solely because the absolute peak knee angle is low. Low flexion can be a patient finding, so duration, amplitude, and point count are used to filter obvious invalid segments.

When several walking cycles or squat repetitions are detected, the patient reference curve is aggregated with a pointwise median rather than a simple mean. This makes the comparison less sensitive to one noisy or incomplete segment.

The JSON also separates the global result from component-level engineering status:

- `shape`: curve shape after vertical offset correction;
- `rangeOfMotion`: movement amplitude compared with the standard;
- `verticalOffset`: whether the whole patient curve is shifted above or below the standard;
- `standardBand`: percentage of points outside the healthy standard-deviation band.

## 2. Why Raw/Processed Data Input Is Used

Lee from the web team confirmed that image input is not needed for the first version. Raw or processed curve data is preferred because it is easier to compare numerically, easier to send through the backend, and avoids image parsing errors.

This version therefore does not analyze chart images. It compares numeric curve data directly.

## 3. Required Inputs

Required:

- `--action`: `walking`, `squat`, or `upstairs`
- `--standard-csv`: local standard curve CSV
- `--out-json`: JSON output path
- `--out-txt`: TXT output path

Patient input, choose one:

- `--patient-csv`
- `--patient-session-id`

If both are provided, `--patient-csv` is used and the script prints a warning.

Optional segmentation control:

- `--segment-patient auto`: default. Segment walking/squat when a time axis is available.
- `--segment-patient never`: compare the full patient curve directly.
- `--segment-patient always`: require segmentation and fail if no valid segments are detected.

Optional output files:

- `--out-html`: patient-vs-standard visual report.
- `--out-average-csv`: normalized average patient curve, standard curve, and deviation per percent point.
- `--out-segments-csv`: detected cycle/repetition summary and per-segment metrics.
- `--out-metrics-csv`: compact key-value metrics table for backend import or quick review.

The default API base URL is:

```text
http://113.44.220.94:3000/measurements
```

The default angle ID is:

```text
left_knee
```

## 4. Example Commands

Walking:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-csv path/to/patient_curve.csv \
  --standard-csv outputs/walking/normal_knee_curve.csv \
  --out-json outputs/recommendations/walking/recommendation_walking.json \
  --out-txt outputs/recommendations/walking/recommendation_walking.txt \
  --out-html outputs/recommendations/walking/recommendation_walking.html \
  --out-average-csv outputs/recommendations/walking/recommendation_walking_average.csv \
  --out-segments-csv outputs/recommendations/walking/recommendation_walking_segments.csv \
  --out-metrics-csv outputs/recommendations/walking/recommendation_walking_metrics.csv
```

Squat:

```bash
python generate_recommendation_from_curves.py \
  --action squat \
  --patient-csv path/to/patient_curve.csv \
  --standard-csv outputs/squat/standard_squat_curve.csv \
  --out-json outputs/recommendations/squat/recommendation_squat.json \
  --out-txt outputs/recommendations/squat/recommendation_squat.txt
```

Upstairs:

```bash
python generate_recommendation_from_curves.py \
  --action upstairs \
  --patient-csv path/to/patient_curve.csv \
  --standard-csv outputs/upstairs/standard_upstairs_curve.csv \
  --out-json outputs/recommendations/upstairs/recommendation_upstairs.json \
  --out-txt outputs/recommendations/upstairs/recommendation_upstairs.txt
```

API input example:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-session-id 33 \
  --standard-csv outputs/walking/normal_knee_curve.csv \
  --out-json outputs/recommendations/walking/recommendation_walking.json \
  --out-txt outputs/recommendations/walking/recommendation_walking.txt
```

S1 / AuCloud patient API examples:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-session-id 209 \
  --standard-csv outputs/walking/normal_knee_curve.csv \
  --out-json outputs/recommendations/walking/recommendation_walking_209.json \
  --out-txt outputs/recommendations/walking/recommendation_walking_209.txt
```

```bash
python generate_recommendation_from_curves.py \
  --action upstairs \
  --patient-session-id 219 \
  --standard-csv outputs/upstairs/standard_upstairs_curve.csv \
  --out-json outputs/recommendations/upstairs/recommendation_upstairs_219.json \
  --out-txt outputs/recommendations/upstairs/recommendation_upstairs_219.txt
```

```bash
python generate_recommendation_from_curves.py \
  --action squat \
  --patient-session-id 228 \
  --standard-csv outputs/squat/standard_squat_curve.csv \
  --out-json outputs/recommendations/squat/recommendation_squat_228.json \
  --out-txt outputs/recommendations/squat/recommendation_squat_228.txt
```

## 5. Expected JSON Output

The JSON contains:

- action;
- angle ID;
- comparison version;
- input type;
- patient source;
- standard source;
- comparison mode;
- segmentation summary;
- status;
- component status;
- confidence;
- engineering thresholds;
- numeric metrics;
- quality notes;
- observations;
- recommendation text;
- clinical advice draft;
- doctor review note;
- limitations.

Example status values:

```text
normal
mild_deviation
significant_deviation
unclear
```

Example confidence values:

```text
high
medium
low
```

`comparisonVersion` is included so V2/M2 can track which recommendation algorithm produced the result.

The current comparison version is:

```text
v0.6-clinical-advice-accuracy
```

`clinicalAdviceDraft` is a clinician-review support message. It is not a diagnosis, prescription, or standalone treatment plan.

## 6. How Status Is Calculated

The script uses simple preliminary engineering thresholds:

- RMSE;
- amplitude difference;
- percentage outside the standard deviation band.

These thresholds are constants at the top of `generate_recommendation_from_curves.py` and are easy to change.

They are not clinically validated.

When segmentation is used, the main status is calculated from the pointwise median normalized patient cycle/repetition compared with the standard curve. The JSON also includes per-segment metric summaries so the web team or doctors can inspect variability between cycles/repetitions.

The HTML visualization shows:

- the standard curve;
- the patient average curve;
- the healthy standard-deviation band when available;
- individual patient segments when segmentation was used;
- observations and data-quality notes.

Confidence is an engineering confidence estimate, not a clinical confidence score. It is reduced when important data is missing, segmentation is weak, too many detected segments are rejected, or the patient curve appears strongly offset from the standard curve.

## 7. Accuracy Evaluation

Use this when S1/AuCloud or clinicians provide validated severity labels.

Create a labels CSV with this structure:

```csv
action,session_id,severity_label,injury_location,notes
walking,209,severe,knee,validated label example
upstairs,219,severe,knee,validated label example
squat,229,mild,knee,validated label example
```

Allowed `severity_label` values:

```text
normal
mild
severe
```

Run:

```bash
python evaluate_recommendation_accuracy.py \
  --labels-csv data/labeled_sessions_template.csv \
  --out-dir outputs/accuracy_analysis
```

The evaluator maps recommendation status to severity:

```text
normal -> normal
mild_deviation -> mild
significant_deviation -> severe
unclear -> excluded from accuracy and reported separately
```

It writes:

- `outputs/accuracy_analysis/accuracy_report.json`
- `outputs/accuracy_analysis/accuracy_report.csv`
- `outputs/accuracy_analysis/confusion_matrix.csv`
- `outputs/accuracy_analysis/accuracy_report.md`

The report includes overall severity accuracy, per-action accuracy, confusion matrix, macro precision/recall/F1, failed sessions, and unclear sessions.

Injury location, such as `knee` or `ankle`, is stored as metadata only in this phase. The current module primarily uses `left_knee`, so it should not claim ankle-vs-knee diagnostic accuracy.

Do not commit real labeled patient data to a public repository unless it is anonymized and explicitly approved for sharing.

## 8. Automated Tests

Run the local tests with:

```bash
python -m unittest test_recommendation_module.py test_accuracy_evaluation.py
```

The tests use only standard-library Python and temporary CSV files. They check:

- full-curve comparison;
- walking cycle segmentation on synthetic data;
- JSON/TXT/HTML/CSV output generation;
- `comparisonVersion`;
- `confidence`.
- severity label parsing;
- severity accuracy calculation;
- confusion matrix generation.

## 9. Limitations

- This is not a medical diagnosis.
- The result is based only on motion curve data.
- The thresholds are preliminary engineering values.
- The output should be interpreted by a qualified clinician.
- The standard curve quality depends on the healthy baseline data.
- The first version does not use chart images.
- Walking/squat segmentation uses preliminary engineering thresholds and should be visually validated.
- Upstairs is currently compared as a full action because the current standard curve is full-action based.
- Accuracy evaluation is severity-only in this phase.

## 10. Future Improvements

- Use more clinical data.
- Validate thresholds with doctors.
- Train a supervised model only after enough validated labeled sessions are available.
- Integrate directly with V2/M2.
- Improve the HTML report with interactive charts if M2 needs richer UI.
- Store normalized comparison curves and segment summaries directly in V2.
- Optionally add LLM text generation later.
- Optionally accept chart images later if the web team needs that workflow.
