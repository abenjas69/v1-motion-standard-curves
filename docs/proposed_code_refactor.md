# Proposed Code Refactor

This document proposes a future refactor. Do not delete or overwrite `Walk.py`.

The goal is to keep `Walk.py` as the current walking reference and create a reusable standard-curve pipeline for walking, squat, and later stair climbing.

## 1. Proposed New Files

```text
scripts/
  standard_curve_builder.py
  compare_patient_to_standard.py
configs/
  walking.json
  squat.json
  stair_climbing.json
outputs/
  walking_left_knee/
  squat_left_knee/
  walking_patient_comparison/
```

If the team prefers to keep everything under `v1_standard_curves/`, use:

```text
v1_standard_curves/scripts/
v1_standard_curves/configs/
v1_standard_curves/outputs/
```

## 2. Functions From Walk.py That Can Move Mostly Unchanged

These functions are general enough to reuse:

- `parse_timestamp_ms`
- `fetch_json`
- `normalize_response_items`
- `build_session_curve`
- `interpolate_linear`
- `sample_std`
- `percentile`
- `moving_average`
- `smooth_curve`
- `fit_average_curve`
- `write_csv`

Small naming changes may be useful. For example, `curve` and `cycle` can become `segment` where the action may be walking, squat, or stair climbing.

## 3. Functions That Should Be Generalized

### `parse_args`

Add:

- `--action`
- `--session-ids`
- `--config`
- `--ignored-session-ids`
- `--cycle-detection`
- `--reference-repetitions`

Keep:

- `--start-session`
- `--end-session`
- `--angle-id`
- `--out-dir`

The script should reject unsafe combinations, such as squat ranges that include ignored session `117`.

### `summarize_cycle_durations`

Generalize naming from `cycle` to `segment` or `cycle_or_repetition`.

Keep IQR logic, but warn when the number of segments is very small.

### `build_standard_reference`

Generalize `repeat_count` to `reference_repetitions`.

Walking can use 15 repeated cycles. Squat should use 3 repetitions. Stair climbing should use 10 steps after data is available.

### `build_raw_cycle_comparison`

Generalize names from raw cycles to raw segments.

The output should work for walking cycles, squat repetitions, and stair steps.

### HTML writers

Generalize titles and labels:

- action name;
- angle ID;
- cycle/repetition/step terminology;
- source sessions;
- ignored sessions;
- generated version/date.

## 4. Functions That Need Action-Specific Behavior

### Segmentation

Current walking function:

- `find_local_minima`
- `extract_cycles_from_minima`

Future segmentation options:

- `find_local_minima`
- `find_local_maxima`
- `extract_segments_from_events`
- `detect_walking_cycles`
- `detect_squat_repetitions`
- `detect_stair_steps` after data is available

### Peak filtering

Current function:

- `get_cycle_peak_info`
- `filter_cycles_by_peak`

Future version should support:

- maximum peak validation;
- minimum valley validation;
- action-specific peak/valley windows;
- expected repetition count;
- quality labels.

### Edge trimming

Walking can keep `trim_edge_cycles=1`.

Squat should start with `trim_edge_cycles=0` because the task only has 3 repetitions.

Stair climbing should wait until data is reviewed.

## 5. Proposed CLI Commands

Walking:

```bash
python standard_curve_builder.py --action walking --angle-id left_knee --start-session 91 --end-session 115 --out-dir outputs/walking_left_knee
```

Squat:

```bash
python standard_curve_builder.py --action squat --angle-id left_knee --session-ids 116,118 --out-dir outputs/squat_left_knee
```

Patient walking comparison later:

```bash
python compare_patient_to_standard.py --action walking --angle-id left_knee --patient-start-session 33 --patient-end-session 57 --standard-csv outputs/walking_left_knee/standard_curve.csv --out-dir outputs/walking_patient_comparison
```

Stair climbing placeholder only:

```bash
python standard_curve_builder.py --action stair_climbing --angle-id left_knee --session-ids PUT_STAIR_SESSION_IDS_HERE --out-dir outputs/stair_left_knee
```

Do not run a real stair-climbing command until session IDs are known.

## 6. Proposed Test Strategy

### Walking reproduction test

Run the generalized builder with sessions `91-115` and compare outputs against `Walk.py`.

Expected result:

- similar number of detected cycles;
- similar average cycle duration;
- similar standard curve shape;
- generated CSV/HTML outputs.

### Squat smoke test

Run the builder with:

```text
session_ids = 116,118
ignored_session_ids = 117,119
trim_edge_cycles = 0
reference_repetitions = 3
```

Expected result:

- no request for sessions `117` or `119`;
- valid squat repetition candidates;
- output files created;
- visual report available for validation.

### Segmentation validation test

Inspect raw-vs-standard HTML for each action.

Expected result:

- walking cycles align around the standard curve;
- squat detects about 3 repetitions per clean session;
- rejected segments have understandable reasons.

### Patient comparison smoke test

Use walking patient/user sessions `33-57` against the walking standard.

Expected result:

- patient cycles are detected;
- metrics are generated;
- output JSON and HTML comparison are created;
- recommendation text uses cautious AI-assisted language.

## 7. Expected Outputs

For standard-curve building:

- `standard_curve.csv`
- `session_summary.csv`
- `cycle_or_repetition_summary.csv`
- `rejected_segments.csv`
- `duration_stats.csv`
- `raw_vs_standard.html`
- `standard_reference.html`
- `README.md`

For patient-vs-standard comparison:

- `comparison_summary.json`
- `comparison_metrics.csv`
- `patient_segments.csv`
- `patient_vs_standard.html`
- `README.md`

## 8. Refactor Rules

- Do not delete or overwrite `Walk.py`.
- Keep the existing walking behavior reproducible.
- Do not use sessions `117` or `119`.
- Do not implement stair climbing until session IDs are provided.
- Mark squat parameters as preliminary until visually validated.
- Do not claim medical diagnosis ability.
- Keep recommendations as rule-based or AI-assisted support for doctor review.

