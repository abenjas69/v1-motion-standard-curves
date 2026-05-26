# Patient-vs-Standard Comparison Design

This document defines how Team V1 should compare patient motion curves against standard healthy curves.

The comparison should support doctor review. It should not claim to make a medical diagnosis.

## 1. Inputs

### Patient session curve

The patient curve should come from a measurement session and should include:

- session ID;
- action type;
- angle ID;
- timestamped angle values.

Known useful walking patient/user data:

```text
sessions 33-57
```

These sessions are processed walking data from user Dcuk and may be useful later for walking patient-vs-standard comparison.

### Standard curve CSV

The standard curve should come from the healthy-person pipeline.

Minimum fields:

- percent;
- standard angle or smoothed angle;
- mean angle;
- standard deviation angle;
- lower standard band;
- upper standard band;
- average cycle/repetition duration.

### Action type

Examples:

- `walking`
- `squat`
- `stair_climbing`

The action type determines segmentation logic and interpretation.

### Angle ID

Example:

```text
left_knee
```

The patient curve and standard curve should use the same angle ID unless a comparison method explicitly supports multi-angle comparison.

## 2. Preprocessing

### Smooth patient curve

Apply a small moving average or action-specific smoothing method to reduce noise before segmentation.

The smoothing window should be stored in the comparison metadata.

### Detect cycles or repetitions

Use action-specific segmentation:

- walking: cycles between local minima, based on current `Walk.py`;
- squat: repetitions based on validated minima or maxima logic;
- stair climbing: pending until real data exists.

### Normalize each cycle to 0-100 percent

Each detected patient cycle or repetition should be resampled to the same percent grid as the standard curve, usually 101 points from 0 to 100.

This allows direct point-by-point comparison even when patient motion is slower or faster.

### Align with the standard curve

Initial alignment can use normalized percent. Later versions may add phase alignment if patient cycles are shifted in time.

The comparison should keep both:

- normalized shape comparison;
- real duration comparison.

Normalized shape comparison shows motion pattern differences. Duration comparison shows rhythm or speed differences.

## 3. Metrics

### Peak angle difference

Difference between patient peak angle and standard peak angle.

May indicate reduced or excessive motion range, depending on action and clinical context.

### Minimum angle difference

Difference between patient minimum angle and standard minimum angle.

Useful for detecting whether the patient returns to a similar start/end posture.

### Amplitude difference

Difference between patient range of motion and standard range of motion.

Amplitude is:

```text
max_angle - min_angle
```

Reduced amplitude may indicate limited range of motion, but it should be reviewed by a doctor.

### Cycle duration difference

Difference between patient cycle/repetition duration and standard average duration.

For walking, this can show slower or faster gait cycles. For squat, this can show slower or incomplete repetitions.

### Cadence/frequency difference

For periodic actions, compare cycles per minute.

This is most meaningful for walking. For squat, repetitions per second or total completion time may be more appropriate.

### RMSE against standard curve

Root mean squared error between the normalized patient curve and standard curve.

This measures overall shape difference and penalizes larger deviations.

### MAE against standard curve

Mean absolute error between the normalized patient curve and standard curve.

This is easier to interpret than RMSE because it is an average absolute angle difference.

### Correlation

Correlation between the normalized patient curve and standard curve.

High correlation means similar shape even if amplitude differs. Low correlation may suggest abnormal or poorly segmented movement.

### Phase delay

Estimated shift in percent where patient and standard curves best align.

This can show whether the patient reaches peak motion earlier or later than the standard pattern.

### Percentage outside standard band

Percentage of patient curve points outside the healthy mean +/- standard deviation band.

This is useful for M2 visualization and doctor review.

## 4. Visual Outputs

### Patient curve vs standard curve

Display one patient cycle or average patient cycle over the standard curve and SD band.

### Patient cycles vs standard curve

Overlay all patient cycles/repetitions against the standard curve.

This shows consistency and variability.

### Deviation area plot

Highlight areas where the patient curve is above or below the standard curve.

This helps doctors see where in the movement the difference happens.

### Summary cards

M2 can show compact cards such as:

- peak angle difference;
- amplitude difference;
- average duration difference;
- RMSE;
- outside band percentage;
- status: normal range / needs review.

The status label should be cautious and should not be a diagnosis.

## 5. Rule-Based Recommendation Logic

Rule-based logic can be added before full AI. It should use cautious language.

Example categories:

### Reduced range of motion

Condition:

```text
patient_amplitude < standard_amplitude - threshold
```

Possible text:

```text
The measured range of motion is lower than the healthy reference. This may indicate limited movement and should be reviewed by a doctor.
```

### Slower movement rhythm

Condition:

```text
patient_duration > standard_duration + threshold
```

Possible text:

```text
The movement rhythm is slower than the healthy reference. This could suggest reduced movement speed or hesitation and should be reviewed by a doctor.
```

### Excessive variability

Condition:

```text
patient_cycle_duration_sd is high
or patient_amplitude_sd is high
```

Possible text:

```text
The repetitions show high variability. This may indicate unstable movement control and should be reviewed by a doctor.
```

### Abnormal curve shape

Condition:

```text
rmse is high
or correlation is low
or outside_standard_band_percent is high
```

Possible text:

```text
The curve shape differs from the healthy reference. This could suggest an abnormal movement pattern and should be reviewed by a doctor.
```

### Incomplete repetition

Condition:

```text
detected_repetitions < expected_repetitions
or peak angle below minimum action threshold
```

Possible text:

```text
One or more repetitions may be incomplete. The measurement should be checked by a doctor or repeated if the acquisition was unstable.
```

### Large deviation from healthy baseline

Condition:

```text
outside_standard_band_percent > threshold
```

Possible text:

```text
The movement has a large deviation from the healthy baseline. This is an AI-assisted analysis result and should be interpreted by a doctor.
```

## 6. Output JSON Proposal

V1 can send a structured comparison result to V2/M2.

Example:

```json
{
  "sessionId": 123,
  "action": "walking",
  "angleID": "left_knee",
  "comparisonVersion": "v0.1",
  "standardCurveVersion": "walking_left_knee_2026-05-25_v0.1",
  "sourceStandardSessions": [91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115],
  "patientSegmentsUsed": 8,
  "metrics": {
    "peakAngleDifference": "12.4 deg",
    "minimumAngleDifference": "3.1 deg",
    "amplitudeDifference": "-9.3 deg",
    "cycleDurationDifference": "0.42 s",
    "cadenceDifference": "-8.5 cycles/min",
    "rmse": "10.8 deg",
    "mae": "7.6 deg",
    "correlation": "0.82",
    "phaseDelayPercent": "6.0",
    "outsideStandardBandPercent": "28.5"
  },
  "status": "needs_review",
  "recommendationText": "The movement curve differs from the healthy reference and may indicate reduced range of motion. This AI-assisted result should be reviewed by a doctor."
}
```

Notes:

- Metric values should eventually be numeric values plus units, not only strings, if V2/M2 needs filtering or sorting.
- `status` should use cautious labels such as `within_reference_range`, `needs_review`, or `measurement_quality_issue`.
- Recommendation text must not make clinical diagnosis claims.

