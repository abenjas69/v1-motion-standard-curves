# Remaining Actions Implementation Plan

This document plans the remaining standard actions for Team V1: squat now and stair climbing later.

Known action requirements from Phase II:

- Squat: complete 3 consecutive repetitions in about 5 seconds.
- Walking: walk forward 5 meters.
- Stair climbing: climb 10 steps.

Known session information:

- Healthy walking standard sessions: `91-115`
- Processed walking user/patient sessions: `33-57`
- Squat sessions: `116` and `118`
- Ignored sessions: `117` and `119`
- Stair climbing sessions: unknown / pending

## 1. Walking Status

Walking already has a working example in `Walk.py`.

Current behavior:

- reads healthy sessions `91-115`;
- extracts `left_knee`;
- detects local minima;
- creates cycles between neighboring minima;
- filters cycles by duration, amplitude, peak angle, and peak timing;
- fits a standard walking cycle;
- writes CSV and HTML outputs.

Walking should be kept as the reference implementation while the pipeline is generalized.

The processed walking user/patient sessions `33-57` should not be mixed into the healthy standard curve. They may be used later for patient-vs-standard comparison.

## 2. Squat Implementation Plan

### Available data

Use only:

```text
116,118
```

Do not use:

```text
117,119
```

Sessions `117` and `119` must be ignored because they are explicitly listed as ignored sessions.

### Expected curve shape

A squat should create a large knee-angle change as the person bends down and returns to standing. For 3 repetitions in about 5 seconds, the curve should show 3 large repeated movement patterns.

Depending on the angle definition:

- standing may have a lower angle and squatting may have a higher angle;
- or standing may have a higher angle and squatting may have a lower angle.

This means V1 should not assume the repetition boundary direction until the raw squat curves are plotted.

### Likely useful angle ID

Start with:

```text
left_knee
```

This is consistent with `Walk.py`. However, squat should be visually validated. If `left_knee` is noisy or incomplete, V1 should inspect `right_knee` or compare both knees if available.

### Repetition detection

Possible segmentation methods:

- local minima to local minima;
- local maxima to local maxima;
- threshold crossing based on angle amplitude;
- full-session split into 3 repetitions if the session is clean and exactly follows the protocol.

The first implementation can adapt `Walk.py` by trying local minima and local maxima on the squat curves, then choosing the method that visually matches 3 repetitions.

### Should minima or maxima define repetitions?

This is not guaranteed yet.

If the squat curve rises during knee bending and falls during standing recovery, then local minima may correspond to standing positions and minima-to-minima segments may define one full squat repetition.

If the squat curve falls during knee bending and rises during standing recovery, then local maxima may correspond to standing positions and maxima-to-maxima segments may define one full squat repetition.

Therefore, the implementation should make the detection mode configurable instead of hardcoding local minima.

### Suggested initial thresholds

Initial estimates only:

```text
min_cycle_seconds: 0.7
max_cycle_seconds: 2.5
min_cycle_amplitude: 20.0
min_peak_angle: 45.0
peak_window_start: 20.0
peak_window_end: 80.0
trim_edge_cycles: 0
reference_repetitions: 3
```

These values must be visually validated. They are not final clinical or biomechanical thresholds.

### What must be visually validated

V1 should inspect:

- whether exactly 3 repetitions are detected per clean squat session;
- whether boundaries match the standing-to-squat-to-standing movement;
- whether peaks/valleys represent the deepest squat position;
- whether the first or last repetition is incomplete;
- whether sessions `116` and `118` have similar curve shapes;
- whether `left_knee` is stable enough;
- whether rejected segments are truly invalid.

### Expected output files

For squat, the output folder should contain:

- `standard_curve.csv`
- `session_summary.csv`
- `cycle_or_repetition_summary.csv`
- `rejected_segments.csv`
- `duration_stats.csv`
- `raw_vs_standard.html`
- `standard_reference.html`
- `README.md`

The README should clearly state that the curve is preliminary because only sessions `116` and `118` are currently available.

### How to adapt Walk.py logic for squat

Reusable parts:

- API fetch;
- response normalization;
- `left_knee` extraction;
- smoothing;
- interpolation;
- normalized curve fitting;
- CSV/HTML output style;
- duration statistics.

Required changes:

- support explicit `--session-ids 116,118`;
- support ignored sessions `117,119`;
- disable edge trimming by default for squat;
- make segmentation mode configurable;
- support local maxima as well as local minima;
- rename "cycle" to "repetition" in squat summaries;
- use 3 repeated standard repetitions, not 15 walking cycles;
- mark thresholds as preliminary.

## 3. Stair-Climbing Implementation Plan

### Available data

Stair-climbing session IDs are not provided yet.

Implementation should wait until real session IDs are available. Do not invent session IDs.

### Expected curve shape

Stair climbing should show repeated step-related knee motion. The curve may have stronger knee flexion than walking and may show more complex timing because each step includes lifting, placing, loading, and body transfer.

### Why it may be more complex than walking

Walking on flat ground is periodic and relatively symmetric. Stair climbing may differ because:

- knee flexion is larger;
- one step may not look exactly like the next;
- left and right knees may alternate strongly;
- movement may slow down over 10 steps;
- the first and last steps may be transitional;
- handrail use or hesitation may change the curve.

### Likely useful angle ID

Start by inspecting:

```text
left_knee
```

But stair climbing may require:

- `right_knee`;
- both knees;
- step detection from alternating left/right signals.

This cannot be decided safely until data is available.

### Cycle detection approach

Possible future approaches:

- local minima;
- local maxima;
- peak-to-peak step detection;
- alternating left/right knee event detection;
- manual validation on early data.

The implementation should not be finalized until the actual stair-climbing sessions are reviewed.

### Missing information

Still needed:

- stair-climbing session IDs;
- whether the participant starts with left or right leg;
- available angle IDs in the stair sessions;
- whether sessions include exactly 10 steps;
- whether there are healthy baseline sessions from multiple participants;
- whether first/last step should be trimmed.

## 4. Handling Ignored Sessions

Sessions `117` and `119` must be ignored.

The generalized builder should support an ignored-session list and should print/report ignored sessions in:

- terminal summary;
- `session_summary.csv`;
- output `README.md`;
- config snapshot.

The squat command should use explicit session IDs:

```bash
python standard_curve_builder.py --action squat --angle-id left_knee --session-ids 116,118 --out-dir outputs/squat_left_knee
```

Do not use `--start-session 116 --end-session 118` for squat because that would include ignored session `117`.

## 5. Documentation and Output Separation

Walking, squat, and stair climbing outputs should be separated.

Recommended output folders:

```text
outputs/walking_left_knee/
outputs/squat_left_knee/
outputs/stair_left_knee/
```

Each folder should include its own README and config snapshot. This prevents mixing healthy walking, squat, patient walking, and future stair-climbing data.

## 6. Implementation Order

1. Keep `Walk.py` unchanged as the working walking reference.
2. Build a generic `standard_curve_builder.py` from reusable `Walk.py` functions.
3. Reproduce walking output using sessions `91-115`.
4. Add explicit session-list support.
5. Add ignored-session support.
6. Implement squat with sessions `116` and `118`.
7. Visually validate squat segmentation.
8. Ask S1/S2/AuCloud for stair-climbing session IDs.
9. Implement stair climbing only after data is available.

