# Recommendation Module README

## 1. What The Script Does

`generate_recommendation_from_curves.py` compares one patient/session motion curve against a saved local standard curve and generates AI-assisted, rule-based advice.

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
  --standard-csv output_python/normal_knee_curve.csv \
  --out-json output/recommendation_walking.json \
  --out-txt output/recommendation_walking.txt \
  --out-html output/recommendation_walking.html \
  --out-average-csv output/recommendation_walking_average.csv \
  --out-segments-csv output/recommendation_walking_segments.csv \
  --out-metrics-csv output/recommendation_walking_metrics.csv
```

Squat:

```bash
python generate_recommendation_from_curves.py \
  --action squat \
  --patient-csv path/to/patient_curve.csv \
  --standard-csv output_squat_python_new_data/standard_squat_curve.csv \
  --out-json output/recommendation_squat.json \
  --out-txt output/recommendation_squat.txt
```

Upstairs:

```bash
python generate_recommendation_from_curves.py \
  --action upstairs \
  --patient-csv path/to/patient_curve.csv \
  --standard-csv output_upstairs_python/standard_upstairs_curve.csv \
  --out-json output/recommendation_upstairs.json \
  --out-txt output/recommendation_upstairs.txt
```

API input example:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-session-id 33 \
  --standard-csv output_python/normal_knee_curve.csv \
  --out-json output/recommendation_walking.json \
  --out-txt output/recommendation_walking.txt
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
- confidence;
- engineering thresholds;
- numeric metrics;
- quality notes;
- observations;
- recommendation text;
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

## 6. How Status Is Calculated

The script uses simple preliminary engineering thresholds:

- RMSE;
- amplitude difference;
- percentage outside the standard deviation band.

These thresholds are constants at the top of `generate_recommendation_from_curves.py` and are easy to change.

They are not clinically validated.

When segmentation is used, the main status is calculated from the average normalized patient cycle/repetition compared with the standard curve. The JSON also includes per-segment metric summaries so the web team or doctors can inspect variability between cycles/repetitions.

The HTML visualization shows:

- the standard curve;
- the patient average curve;
- the healthy standard-deviation band when available;
- individual patient segments when segmentation was used;
- observations and data-quality notes.

Confidence is an engineering confidence estimate, not a clinical confidence score. It is reduced when important data is missing, segmentation is weak, too many detected segments are rejected, or the patient curve appears strongly offset from the standard curve.

## 7. Automated Tests

Run the local tests with:

```bash
python -m unittest test_recommendation_module.py
```

The tests use only standard-library Python and temporary CSV files. They check:

- full-curve comparison;
- walking cycle segmentation on synthetic data;
- JSON/TXT/HTML/CSV output generation;
- `comparisonVersion`;
- `confidence`.

## 8. Limitations

- This is not a medical diagnosis.
- The result is based only on motion curve data.
- The thresholds are preliminary engineering values.
- The output should be interpreted by a qualified clinician.
- The standard curve quality depends on the healthy baseline data.
- The first version does not use chart images.
- Walking/squat segmentation uses preliminary engineering thresholds and should be visually validated.
- Upstairs is currently compared as a full action because the current standard curve is full-action based.

## 9. Future Improvements

- Use more clinical data.
- Validate thresholds with doctors.
- Integrate directly with V2/M2.
- Improve the HTML report with interactive charts if M2 needs richer UI.
- Store normalized comparison curves and segment summaries directly in V2.
- Optionally add LLM text generation later.
- Optionally accept chart images later if the web team needs that workflow.
