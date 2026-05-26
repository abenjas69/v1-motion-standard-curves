# Standard Curve Pipeline Generalization Plan

Goal: transform `Walk.py` into a reusable standard-curve pipeline for multiple rehabilitation actions while preserving the existing walking script.

The proposed future script is:

```text
standard_curve_builder.py
```

It should support:

- `walking`
- `squat`
- `stair_climbing`, marked as pending until session IDs are provided

## 1. Proposed Architecture

### Core API client

Responsible for fetching measurement sessions from:

```text
http://113.44.220.94:3000/measurements/{session_id}
```

It should support both continuous ranges and explicit session lists.

Required behavior:

- read one or more session IDs;
- skip ignored sessions;
- normalize API response shape;
- report failed or empty sessions clearly;
- keep source session IDs in all summaries.

### Curve extraction

Responsible for extracting a single joint angle curve from `joint_angles`.

Initial supported input:

- `angleID`, such as `left_knee`;
- timestamp;
- angle value.

The extraction function can initially reuse `build_session_curve` from `Walk.py`.

### Smoothing

Responsible for removing small noise before segmentation and smoothing the final fitted curve.

Initial implementation can reuse:

- `moving_average`;
- `smooth_curve`.

Different actions may need different smoothing windows.

### Cycle/repetition detection

Responsible for splitting each session into comparable motion segments.

Initial detection modes:

- `local_minima`: useful for current walking pipeline;
- `local_maxima`: possible future option for squat if the angle direction requires it;
- `manual_or_pending`: placeholder for stair climbing until data is available.

The detector should return a normalized segment format regardless of action:

```text
session_id
segment_no
points
duration_seconds
start_angle
end_angle
peak_angle
min_angle
amplitude
quality_status
reject_reason
```

### Action-specific configuration

Each action should define its own session IDs, detection method, thresholds, expected repetition count, and output settings.

This prevents walking-specific defaults from accidentally being applied to squat or stair climbing.

### Standard curve fitting

Responsible for turning accepted segments into a standard curve.

Initial reusable logic:

- normalize each segment to 0-100 percent;
- interpolate onto a fixed grid;
- calculate mean angle;
- calculate standard deviation;
- smooth the fitted mean;
- calculate average duration with IQR outlier handling where enough data exists.

### Comparison visualization

Responsible for generating visual files that let V1 validate the output.

Minimum visual outputs:

- raw accepted segments vs fitted standard curve;
- standard reference curve;
- standard deviation band.

### Output writing

Responsible for writing stable CSV, HTML, and per-output README files.

The output format should be consistent across actions so M2 and V2 can integrate it later.

## 2. Suggested Folder Structure

```text
v1_standard_curves/
  scripts/
    standard_curve_builder.py
    compare_patient_to_standard.py
  configs/
    walking.json
    squat.json
    stair_climbing.json
  outputs/
    walking/
    squat/
    stair_climbing/
  docs/
    ...
```

During the current documentation phase, no code files should be created yet. This structure is a proposal for the next implementation step.

## 3. Action-Specific Configuration

### General config fields

Example:

```json
{
  "action": "walking",
  "angle_id": "left_knee",
  "cycle_detection": "local_minima",
  "min_cycle_seconds": 0.8,
  "max_cycle_seconds": 3.5,
  "min_cycle_amplitude": 15.0,
  "min_peak_angle": 40.0,
  "peak_window_start": 33.3,
  "peak_window_end": 66.6,
  "trim_edge_cycles": 1
}
```

These parameters are initial estimates. They must be validated visually using raw-vs-standard plots.

### Initial walking config

```json
{
  "action": "walking",
  "description": "Walk forward 5 meters.",
  "base_url": "http://113.44.220.94:3000/measurements",
  "session_range": [91, 115],
  "ignored_session_ids": [],
  "angle_id": "left_knee",
  "cycle_detection": "local_minima",
  "grid_points": 101,
  "smooth_window": 5,
  "minima_smooth_window": 5,
  "min_cycle_seconds": 0.8,
  "max_cycle_seconds": 3.5,
  "min_cycle_amplitude": 15.0,
  "min_peak_angle": 40.0,
  "peak_window_start": 33.3,
  "peak_window_end": 66.6,
  "duration_outlier_iqr": 1.5,
  "reference_repetitions": 15,
  "trim_edge_cycles": 1
}
```

This matches the current `Walk.py` behavior.

### Initial squat config

```json
{
  "action": "squat",
  "description": "Complete 3 consecutive squat repetitions in about 5 seconds.",
  "base_url": "http://113.44.220.94:3000/measurements",
  "session_ids": [116, 118],
  "ignored_session_ids": [117, 119],
  "angle_id": "left_knee",
  "cycle_detection": "local_minima_or_local_maxima_pending_validation",
  "grid_points": 101,
  "smooth_window": 5,
  "minima_smooth_window": 5,
  "min_cycle_seconds": 0.7,
  "max_cycle_seconds": 2.5,
  "min_cycle_amplitude": 20.0,
  "min_peak_angle": 45.0,
  "peak_window_start": 20.0,
  "peak_window_end": 80.0,
  "duration_outlier_iqr": 1.5,
  "reference_repetitions": 3,
  "trim_edge_cycles": 0
}
```

These squat values are not final. They are only starting estimates. Squat data must be plotted and visually checked before accepting the segmentation.

Important: sessions `117` and `119` must be ignored.

### Initial stair-climbing config

```json
{
  "action": "stair_climbing",
  "description": "Climb 10 steps.",
  "base_url": "http://113.44.220.94:3000/measurements",
  "session_ids": "PENDING",
  "ignored_session_ids": [117, 119],
  "angle_id": "left_knee",
  "cycle_detection": "pending_until_data_available",
  "grid_points": 101,
  "smooth_window": 5,
  "minima_smooth_window": 5,
  "min_cycle_seconds": "PENDING",
  "max_cycle_seconds": "PENDING",
  "min_cycle_amplitude": "PENDING",
  "min_peak_angle": "PENDING",
  "peak_window_start": "PENDING",
  "peak_window_end": "PENDING",
  "duration_outlier_iqr": 1.5,
  "reference_repetitions": 10,
  "trim_edge_cycles": 0
}
```

Stair climbing must remain pending because session IDs were not provided.

## 4. Proposed CLI Usage

Walking:

```bash
python standard_curve_builder.py --action walking --angle-id left_knee --start-session 91 --end-session 115 --out-dir output/walking_left_knee
```

Squat:

```bash
python standard_curve_builder.py --action squat --angle-id left_knee --session-ids 116,118 --out-dir output/squat_left_knee
```

Stair climbing placeholder only:

```bash
python standard_curve_builder.py --action stair_climbing --angle-id left_knee --session-ids PUT_STAIR_SESSION_IDS_HERE --out-dir output/stair_left_knee
```

Do not run a real stair-climbing command until session IDs are provided.

## 5. Expected Outputs

Each action output folder should contain:

### `standard_curve.csv`

Normalized standard curve with percent, mean angle, smoothed standard angle, SD angle, lower band, upper band, and number of source segments.

### `session_summary.csv`

One row per source session with point count, duration, detected segments, accepted segments, rejected segments, and quality notes.

### `cycle_or_repetition_summary.csv`

One row per accepted walking cycle, squat repetition, or stair step segment.

### `rejected_segments.csv`

Rejected candidate segments with reason codes.

### `duration_stats.csv`

Average duration, duration SD, IQR bounds, outlier counts, and frequency/cadence metrics where meaningful.

### `raw_vs_standard.html`

Visual overlay of accepted raw segments and the fitted standard curve.

### `standard_reference.html`

Visual standard reference curve for the action.

### `README.md`

Human-readable output notes:

- action;
- angle ID;
- source sessions;
- ignored sessions;
- generation date;
- parameters used;
- validation notes;
- known limitations.

## 6. What Should Be Stored or Sent to M2/V2

The most useful integration outputs are:

- action name;
- angle ID;
- standard curve points;
- mean angle;
- standard angle or smoothed fit;
- standard deviation band;
- average duration;
- source healthy session IDs;
- ignored session IDs;
- curve version;
- generation date;
- number of accepted segments;
- number of rejected segments;
- basic quality statistics;
- parameter/config snapshot.

M2 needs these values for display. V2 needs them for storage, query, versioning, and linking patient comparisons to the correct standard curve.

## 7. Risks

- Data may still be noisy before acquisition is stabilized.
- Different action types require different segmentation logic.
- Walking is repeated and periodic, so minima-to-minima segmentation works better there than it may for other actions.
- Squat currently has only sessions `116` and `118`, which is a small baseline.
- Squat may require repetition-based segmentation rather than continuous walking-style cycle detection.
- Sessions `117` and `119` must be ignored.
- Stair-climbing data is not available yet.
- Too few healthy participants may produce weak reference curves.
- Patient comparison should not be treated as a medical diagnosis.
- Any AI or rule-based recommendation must be described as AI-assisted support for doctor review.

