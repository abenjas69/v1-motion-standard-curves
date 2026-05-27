# V1 Motion Standard Curves

Team V1 - AI & Motion Recognition  
Phase II rehabilitation standard-curve prototype

This repository contains the current Python scripts and generated outputs for building healthy standard motion curves and comparing patient sessions against those saved standards.

- Walking
- Squat
- Upstairs / stair climbing

The current Phase II goal is practical: build healthy standard curves first, then generate AI-assisted rule-based comparison outputs for patient sessions. This repository does not provide medical diagnosis.

## Repository Structure

```text
scripts/
  Walk.py
  Squat.py
  Upstairs.py

outputs/
  walking/
  squat/
  upstairs/
  recommendations/
    walking/
    upstairs/
    squat/

data/
  labeled_sessions_template.csv

docs/
  Phase II analysis and implementation notes

requirements/
  Requirement-2nd.pdf
  Requirement-2nd.docx

assets/
  walk_output_screenshot.png
```

## Data Used

### Walking

- Healthy standard sessions: `91-115`
- Script: `scripts/Walk.py`
- Main output: `outputs/walking/raw_vs_standard_cycles.html`
- Angle ID: `left_knee`
- Method: repeated walking cycles segmented between local minima

### Squat

- Healthy standard sessions: `116,118`
- Ignored sessions: `117,119`
- Script: `scripts/Squat.py`
- Main output: `outputs/squat/raw_vs_standard_repetitions.html`
- Angle ID: `left_knee`
- Method: squat repetitions segmented between local minima

### Upstairs / Stair Climbing

- Healthy standard sessions: `146,147,148,149,150,151,152,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174`
- Script: `scripts/Upstairs.py`
- Main output: `outputs/upstairs/raw_vs_standard_sessions.html`
- Angle ID: `left_knee`
- Method: full upstairs action normalized from start to end

Important: the upstairs protocol is to climb 10 steps, but the `left_knee` signal shows about 7 strong peaks per session. Therefore `Upstairs.py` fits the full action curve instead of forcing exactly 10 step segments.

## How To Run

From the repository root:

```bash
python scripts/Walk.py --out-dir outputs/walking
python scripts/Squat.py --out-dir outputs/squat
python scripts/Upstairs.py --out-dir outputs/upstairs
```

Generate an AI-assisted rule-based recommendation from AuCloud API data:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-session-id 209 \
  --standard-csv outputs/walking/normal_knee_curve.csv \
  --out-json outputs/recommendations/walking/recommendation_walking_209.json \
  --out-txt outputs/recommendations/walking/recommendation_walking_209.txt \
  --out-html outputs/recommendations/walking/recommendation_walking_209.html \
  --out-average-csv outputs/recommendations/walking/recommendation_walking_209_average.csv \
  --out-segments-csv outputs/recommendations/walking/recommendation_walking_209_segments.csv \
  --out-metrics-csv outputs/recommendations/walking/recommendation_walking_209_metrics.csv
```

The scripts use:

```text
http://113.44.220.94:3000/measurements
```

as the default API endpoint.

Evaluate severity accuracy from labeled sessions:

```bash
python evaluate_recommendation_accuracy.py \
  --labels-csv data/labeled_sessions_template.csv \
  --out-dir outputs/accuracy_analysis
```

The accuracy evaluator expects clinician/S1-validated labels with:

```text
action,session_id,severity_label,injury_location,notes
```

For this phase, the measured accuracy target is severity only:

```text
normal, mild, severe
```

Injury location, such as knee or ankle, is stored as metadata only. Do not use the current `left_knee` curve-only module to claim ankle-vs-knee diagnostic accuracy.

## Outputs To Review First

Open these standard-curve validation files in a browser:

```text
outputs/walking/raw_vs_standard_cycles.html
outputs/squat/raw_vs_standard_repetitions.html
outputs/upstairs/raw_vs_standard_sessions.html
```

These are the best visual validation files because they overlay the raw curves with the fitted standard curve.

For patient recommendation examples, open:

```text
outputs/recommendations/walking/recommendation_walking_209.html
outputs/recommendations/upstairs/recommendation_upstairs_219.html
outputs/recommendations/squat/recommendation_squat_228.html
```

## Notes

- `Walk.py` is kept as the walking-only reference.
- `Squat.py` is separate and uses squat-specific repetition terminology.
- `Upstairs.py` is separate and uses full-action normalization.
- `generate_recommendation_from_curves.py` compares patient curves against saved standard curves and outputs preliminary JSON/TXT/HTML/CSV advice.
- Current recommendation algorithm version: `v0.6-clinical-advice-accuracy`.
- JSON output includes `status`, `confidence`, `componentStatus`, `metrics`, `segmentation`, `observations`, `recommendationText`, and `clinicalAdviceDraft`.
- `evaluate_recommendation_accuracy.py` calculates severity accuracy, per-action accuracy, confusion matrix, macro precision/recall/F1, failed sessions, and unclear sessions.
- Recommendation thresholds are preliminary engineering values and are not clinically validated.
- The recommendation output is AI-assisted support only, not a medical diagnosis.
- Real labeled patient data should not be committed to a public repository unless anonymized and approved for sharing.
