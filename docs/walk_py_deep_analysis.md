# Walk.py Deep Analysis

Source file: `Documentos/Walk.py`

`Walk.py` is the current working example for Team V1's standard-curve pipeline. It builds a healthy walking reference curve from `left_knee` angle data, using healthy walking sessions `91-115` from the AuCloud measurements API.

## 1. Purpose of Walk.py

`Walk.py` solves one immediate Phase II problem: create a standard walking knee-angle curve from healthy-person motion data.

It reads measurement sessions from:

```text
http://113.44.220.94:3000/measurements/{session_id}
```

By default, it reads:

- session range: `91-115`;
- angle ID: `left_knee`;
- data field: `joint_angles`;
- measurement value: `angle`;
- measurement timestamp: `timestamp`.

The script extracts a time-angle curve for each session, detects walking cycles, normalizes cycles to a 0-100 percent time axis, averages the cycles, estimates a standard deviation band, and writes CSV/HTML outputs.

The main output is a fitted walking standard curve. This is useful for Phase II because M2 can eventually overlay patient walking curves against this standard curve.

## 2. Inputs and Parameters

### `--base-url`

Default: `http://113.44.220.94:3000/measurements`

Controls the API endpoint prefix. The script appends `/{session_id}` to this value.

For squat or stair climbing, this can stay the same if the same AuCloud endpoint stores all measurement sessions.

### `--start-session`

Default: `91`

Controls the first session ID in a continuous session range.

For walking, `91` is correct for the known healthy walking standard data. For squat, this is less useful because squat uses non-contiguous IDs `116` and `118`; a future generalized script should support `--session-ids`.

### `--end-session`

Default: `115`

Controls the last session ID in a continuous session range.

For walking, `115` is correct. For squat, using `116-118` would accidentally include session `117`, which must be ignored. A generalized script should avoid ranges that include ignored sessions.

### `--angle-id`

Default: `left_knee`

Controls which joint angle is extracted from `joint_angles`.

For walking, `left_knee` is the current working signal. Squat may also start with `left_knee`, but this should be visually validated. Stair climbing may need `left_knee`, `right_knee`, or both, depending on available data and how step cycles appear.

### `--grid-points`

Default: `101`

Controls how many normalized points each cycle is sampled into. With 101 points, the output has 0, 1, 2, ..., 100 percent.

This can probably stay the same for squat and stair climbing. It gives a simple standard curve shape independent of actual cycle duration.

### `--smooth-window`

Default: `5`

Controls the moving-average window used to smooth the fitted average curve after cycle averaging.

For noisy squat or stair-climbing data, this may need adjustment. Too small keeps noise; too large can flatten important motion features.

### `--minima-smooth-window`

Default: `5`

Controls the moving-average window applied before local minima detection.

This exists because raw IMU angle curves may contain noise that creates false minima. For squat, local minima may not be the correct segmentation event; for stair climbing, a different event detector may be needed.

### `--min-cycle-seconds`

Default: `0.8`

Rejects cycles shorter than this duration.

This prevents noise from being treated as a valid walking cycle. For squat, the full task is 3 repetitions in about 5 seconds, so one repetition may be roughly 1.0-2.0 seconds, but this must be validated visually. For stair climbing, step duration depends on the subject and stair task.

### `--max-cycle-seconds`

Default: `3.5`

Rejects cycles longer than this duration.

This prevents long pauses or incorrect segmentation from becoming valid cycles. Squat may need a different maximum depending on whether the segment represents one repetition or the full 3-repetition set.

### `--min-cycle-amplitude`

Default: `15.0`

Requires a minimum angle rise inside a cycle.

This removes tiny noise movements. For squat, the amplitude should probably be larger than walking because the knee bends more deeply. For stair climbing, amplitude may also be larger than walking but must be validated.

### `--min-peak-angle`

Default: `40.0`

Rejects cycles whose peak angle is below this value.

This removes incomplete or weak walking cycles. For squat, the minimum peak angle may need to be higher, depending on the angle definition. It should not be finalized without viewing the curves.

### `--peak-window-start`

Default: `33.333333`

Earliest allowed peak position inside a normalized cycle, as a percent.

For walking, the peak is expected near the middle of a minima-to-minima segment. For squat, peak timing may also be near the middle if a repetition is standing-to-squat-to-standing, but this depends on whether the repetition is segmented by minima or maxima.

### `--peak-window-end`

Default: `66.666667`

Latest allowed peak position inside a normalized cycle, as a percent.

This pairs with `--peak-window-start` to reject cycles whose peak is too early or too late. Squat and stair climbing may need different windows.

### `--duration-outlier-iqr`

Default: `1.5`

Controls IQR-based duration outlier removal before computing average cycle duration.

This exists because valid-looking cycles can still have abnormal durations. It is reusable for squat and stair climbing if enough repetitions exist. With very few squat repetitions, IQR statistics may be weak.

### `--reference-cycles`

Default: `15`

Controls how many times the fitted standard cycle is repeated in the repeated reference output.

For walking, repeated cycles make sense because walking is periodic. For squat, repeating the standard repetition can also be useful, but the required action is exactly 3 repetitions, so a squat output may prefer `reference_repetitions=3`. For stair climbing, repeated steps may be useful once the session format is known.

### `--comparison-max-cycles`

Default: `0`

Controls how many raw detected cycles are drawn in the raw-vs-standard comparison HTML. `0` means draw all cycles.

This is mainly for visualization performance. For actions with many cycles or noisy data, limiting the displayed cycles can make HTML easier to inspect.

### `--trim-edge-cycles`

Default: `1`

Removes this many valid cycles from both the start and end of each session.

This exists because the first and last cycles may include starting, stopping, or unstable movement. For walking, trimming one edge cycle is reasonable. For squat with only 3 repetitions, trimming one from each side would remove most of the data, so this must be changed or disabled.

### `--out-dir`

Default: `output_python`

Controls where CSV, HTML, and optional PNG outputs are written.

For a generalized pipeline, each action and angle should use a separate output directory, such as `outputs/walking_left_knee` or `outputs/squat_left_knee`.

## 3. Main Workflow

1. Parse command-line arguments.
2. Create the output directory.
3. Loop through sessions from `start-session` to `end-session`.
4. Fetch each session from the API.
5. Normalize the API response into a list of items.
6. Extract the selected angle ID from `joint_angles`.
7. Build a time-angle curve by grouping duplicate timestamps and averaging their angle values.
8. Smooth the curve for local-minima detection.
9. Detect local minima, enforcing a minimum distance between selected minima.
10. Extract cycles between neighboring minima.
11. Filter cycles by duration and amplitude.
12. Trim edge cycles from the start and end of each session.
13. Filter remaining cycles by peak angle and peak timing.
14. Save accepted cycle metadata and rejected cycle metadata.
15. Normalize each accepted cycle to a 0-100 percent grid.
16. Average all accepted cycles at each grid percent.
17. Compute standard deviation at each grid percent.
18. Smooth the fitted mean curve.
19. Remove duration outliers with the IQR method when computing the average cycle duration.
20. Build a single standard cycle using the average duration.
21. Repeat the standard cycle for the configured number of reference cycles.
22. Generate CSV outputs.
23. Generate HTML visualizations.
24. Generate a PNG plot if `matplotlib` is installed.
25. Print a terminal summary.

## 4. Function-by-Function Explanation

### `parse_args`

Input: command-line arguments.

Output: an `argparse.Namespace`.

It defines all runtime parameters, including API URL, session range, angle ID, smoothing windows, cycle thresholds, output directory, and visualization options.

Limitation: it only supports a continuous session range. It cannot directly accept `116,118` for squat without also reading ignored session `117`.

### `parse_timestamp_ms`

Input: timestamp string, such as `2026-05-22T14:46:58.568Z`.

Output: Unix timestamp in milliseconds.

It converts API timestamps into numeric time values so the script can calculate durations and interpolate curves.

Limitation: it assumes ISO timestamps compatible with Python `datetime.fromisoformat` after replacing `Z` with `+00:00`.

### `fetch_json`

Input: URL and optional timeout.

Output: parsed JSON payload.

It uses Python standard library `urlopen` to fetch data and `json.loads` to parse it.

Limitation: it has simple error handling only in `main`. It does not retry failed requests.

### `normalize_response_items`

Input: API payload.

Output: list of item dictionaries.

It handles three possible response shapes:

- `None` becomes an empty list;
- a list is returned as-is;
- a dictionary with `value` returns `payload["value"]`;
- any other payload becomes a one-item list.

This makes the rest of the script less dependent on a single API wrapper format.

### `build_session_curve`

Input: normalized session items and `angle_id`.

Output: list of `(timestamp_ms, angle)` pairs, or `None` if fewer than two valid points exist.

It searches each item's `joint_angles` list, keeps points whose `angleID` matches the requested angle, skips missing timestamps or angles, converts timestamps, and averages duplicate timestamps.

Limitation: it only extracts one angle ID at a time. It does not combine left/right joints or read accelerometer/gyroscope data.

### `interpolate_linear`

Input: one curve and a normalized percent.

Output: interpolated angle value.

It maps a percent value from 0-100 onto the curve's time range and linearly interpolates between neighboring points.

Limitation: it scans the curve linearly each time. This is clear and acceptable for small curves but not optimized for very large data.

### `sample_std`

Input: list of numeric values.

Output: sample standard deviation.

It returns `0.0` for one or zero values. For multiple values, it uses sample variance with denominator `n - 1`.

This is used to produce the standard deviation band around the fitted curve.

### `percentile`

Input: sorted numeric values and percentile value.

Output: interpolated percentile.

It supports percentile calculations for IQR duration filtering.

Limitation: the caller must pass values that are already sorted.

### `summarize_cycle_durations`

Input: accepted cycle summary rows and IQR multiplier.

Output: dictionary of duration statistics.

It calculates Q1, median, Q3, IQR bounds, kept durations, excluded outlier count, average cycle seconds, standard deviation, and average cycles per minute.

Limitation: it assumes at least one kept duration. With very small datasets, IQR filtering may not be meaningful.

### `moving_average`

Input: list of values and window size.

Output: smoothed list.

It uses a centered moving average. If the window is even, it increases it by one so the window is odd.

Limitation: edge points use smaller windows, which can make edge behavior slightly different.

### `smooth_curve`

Input: curve and window size.

Output: curve with the same timestamps and smoothed angle values.

It extracts angles, smooths them with `moving_average`, and reattaches timestamps.

### `find_local_minima`

Input: curve, smoothing window, and minimum distance in seconds.

Output: list of selected local-minimum indexes.

It smooths the curve, finds points that are lower than or equal to neighboring points, then removes minima that are too close together. If two minima are too close, it keeps the deeper one.

Limitation: this assumes cycle boundaries are local minima. That works for the current walking logic but may not work for squat or stair climbing.

### `extract_cycles_from_minima`

Input: curve, minima indexes, duration thresholds, and amplitude threshold.

Output: list of cycle dictionaries.

It creates one cycle from each pair of neighboring minima. It rejects segments with fewer than 5 points, invalid duration, or insufficient amplitude.

Each accepted cycle includes points, duration, start angle, end angle, max angle, and amplitude.

Limitation: amplitude is measured as `max_angle - max(start_angle, end_angle)`, which assumes the meaningful movement is an upward peak between two lower boundary points.

### `get_cycle_peak_info`

Input: cycle dictionary.

Output: peak index, peak timestamp, peak angle, and peak percent.

It finds the maximum angle inside the cycle and calculates where that peak occurs in the cycle.

Limitation: it only looks for a maximum peak. If an action is better described by a minimum valley, this function must be generalized.

### `filter_cycles_by_peak`

Input: cycles, minimum peak angle, earliest peak percent, latest peak percent.

Output: `(kept, rejected)` cycle lists.

It rejects cycles when the peak is too low, too early, or too late.

Limitation: this is walking-specific quality filtering. Squat may need different logic, and stair climbing may need separate step-event validation.

### `fit_average_curve`

Input: accepted cycles, grid point count, smoothing window.

Output: fitted curve rows.

For each normalized percent, it interpolates every cycle, calculates mean angle and standard deviation, then smooths the mean curve.

Output columns include percent, mean angle, smooth angle, standard deviation, mean minus SD, mean plus SD, and number of curves.

This is one of the most reusable parts of the script.

### `build_standard_reference`

Input: fitted rows, average cycle duration, repeat count.

Output: `(single_cycle, repeated_cycles)`.

It converts normalized percent into actual time using the average cycle duration. It creates one standard cycle and a repeated reference curve.

Limitation: repeated cycles are most natural for walking. Squat should probably repeat only 3 times for the required task.

### `build_raw_cycle_comparison`

Input: accepted cycles, fitted rows, average cycle duration, optional max cycle count.

Output: raw cycle comparison rows.

It resamples raw accepted cycles onto the same normalized percent grid and maps them onto the average cycle duration. This allows many raw cycles to be overlaid against the fitted standard.

### `write_csv`

Input: output path, row dictionaries, and field names.

Output: CSV file.

It writes UTF-8 with BOM (`utf-8-sig`) so spreadsheet software can open the files more reliably.

### `write_html_plot`

Input: output path, fitted rows, angle ID.

Output: `normal_knee_curve.html`.

It writes a standalone SVG-based HTML chart showing mean curve, smoothed curve, and standard deviation band over one normalized cycle.

Limitation: labels in the current file are partly Chinese and should be standardized if the output is integrated into M2.

### `write_reference_html`

Input: output path, single standard cycle, repeated standard cycles, duration stats, angle ID.

Output: `standard_reference_curve.html`.

It writes an HTML report with metrics, one standard cycle, and a repeated 15-cycle reference curve.

Limitation: fixed repeated-cycle design is walking-oriented.

### `write_comparison_html`

Input: output path, raw cycle rows, fitted rows, standard single cycle, duration stats, angle ID.

Output: `raw_vs_standard_cycles.html`.

It overlays detected raw cycles, the mean curve, the standard deviation band, and the final standard fitted curve.

This is important for visual validation because it shows whether the detected cycles actually align with the fitted standard.

### `write_png_if_possible`

Input: output path, fitted rows, angle ID.

Output: `True` if a PNG was written, otherwise `False`.

It tries to import `matplotlib`. If available, it writes a PNG plot of mean, smoothed fit, and standard deviation band.

Limitation: PNG output depends on optional local Python packages.

### `main`

Input: command-line arguments.

Output: files in the output directory and terminal summary.

It coordinates the full pipeline: fetch sessions, build curves, segment cycles, filter cycles, fit the standard curve, calculate duration stats, generate outputs, and print paths.

Limitation: it is currently a walking-specific script even though many pieces are reusable.

## 5. Outputs Generated by Walk.py

### `normal_knee_curve.csv`

Contains the normalized fitted curve with percent, mean angle, smoothed angle, standard deviation, lower/upper SD band, and number of cycles.

Useful for V1 standard curve construction and for M2 chart display.

### `session_summary.csv`

Contains one row per loaded session, including point count, local minima count, valid cycle counts, rejected counts, start/end time, and duration.

Useful for V1 data quality review and for identifying sessions with poor segmentation.

### `cycle_summary.csv`

Contains one row per accepted cycle, including duration, start/end angles, peak angle, peak percent, and amplitude.

Useful for V1 validation and possible doctor-facing summary metrics.

### `rejected_cycle_summary.csv`

Contains rejected cycles after peak filtering, including reject reason.

Useful for debugging thresholds and understanding whether valid motion is being accidentally rejected.

### `cycle_duration_stats.csv`

Contains duration summary statistics after IQR outlier handling, including average cycle duration and cycles per minute.

Useful for walking cadence and for constructing time-based standard reference curves.

### `raw_cycle_comparison.csv`

Contains resampled raw cycle points aligned onto the average cycle duration.

Useful for raw-vs-standard visualization and for checking whether the standard curve represents the source data.

### `standard_single_cycle.csv`

Contains a single time-based standard cycle with standard angle, mean angle, SD angle, and cycle duration.

Useful for V1, V2, and M2 integration because it is the compact standard reference.

### `standard_15_cycles.csv`

Contains the standard cycle repeated 15 times.

Useful for walking visualization, but the repeat count may not be suitable for squat.

### `normal_knee_curve.html`

Standalone HTML chart for the average normalized cycle.

Useful for V1 review. M2 may reimplement this visualization inside the doctor web UI.

### `standard_reference_curve.html`

Standalone HTML chart showing one standard cycle and repeated standard cycles.

Useful for visual validation and demonstrations.

### `raw_vs_standard_cycles.html`

Standalone HTML chart overlaying raw cycles against the fitted standard curve and SD band.

This is probably the most important validation output for V1 because it shows whether segmentation and averaging worked.

### `normal_knee_curve.png`

Optional PNG output if `matplotlib` is installed.

Useful for reports or quick sharing, but HTML/CSV are more important for pipeline integration.

## 6. Limitations of Walk.py

- It is hardcoded around walking assumptions.
- It only supports continuous session ranges.
- It assumes cycles are bounded by local minima.
- It assumes the key signal is `left_knee` by default.
- It looks for a maximum peak inside each minima-to-minima cycle.
- It uses walking-specific default thresholds.
- It trims edge cycles, which is dangerous for squat because squat may only have 3 repetitions.
- It does not compare patient curves against standard curves yet.
- It does not generate recommendations.
- It does not store standard curves back into the database.
- It does not attach enough metadata for production standard-curve versioning.
- HTML outputs are useful but are standalone files, not integrated with M2.
- Duration outlier filtering may be weak for very small datasets.
- It does not explicitly ignore sessions `117` and `119`; this must be handled by future squat logic.

## 7. Reusable Components

The following components can be reused safely for squat and stair climbing:

- API reading through `fetch_json`;
- response normalization through `normalize_response_items`;
- single-angle curve extraction through `build_session_curve`;
- timestamp parsing through `parse_timestamp_ms`;
- smoothing through `moving_average` and `smooth_curve`;
- linear interpolation through `interpolate_linear`;
- sample standard deviation through `sample_std`;
- percentile and IQR duration summary logic;
- normalized average curve fitting through `fit_average_curve`;
- standard reference construction with some repeat-count changes;
- CSV writing through `write_csv`;
- HTML generation concepts, though labels and action terminology should be generalized.

## 8. Action-Specific Logic

The following parts may need different logic per action:

- cycle or repetition detection method;
- whether segment boundaries are local minima, local maxima, or another event;
- whether the key metric is peak angle, minimum angle, or both;
- selected `angleID`;
- minimum and maximum duration thresholds;
- minimum amplitude threshold;
- peak position constraints;
- edge-cycle trimming;
- number of expected repetitions;
- interpretation of repeated output;
- quality filtering rules.

For walking, local minima between repeated knee flexion cycles currently works.

For squat, repetitions may need to be detected as standing-to-squat-to-standing movements. Local minima may or may not define boundaries depending on angle direction. Edge trimming should probably be disabled or set to `0`.

For stair climbing, segmentation may need step-event logic. The implementation should wait until stair-climbing session IDs are available.

