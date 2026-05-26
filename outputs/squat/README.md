# Squat Standard Curve Output

Generated at: `2026-05-26T09:44:55.660285+00:00`

## Purpose

This folder contains a preliminary standard squat curve for Team V1 Phase II. It uses healthy squat sessions only and is intended for visual validation before patient comparison.

## Source Data

- Action: `squat`
- Protocol: 3 consecutive squat repetitions in about 5 seconds
- Requested sessions: `116,118,126,127,128,129,130,131,133,134,135,136,137,138,139,140,141,143,144,145`
- Processed sessions: `116,118,126,127,128,129,130,131,133,134,135,136,137,138,139,140,141,143,144,145`
- Ignored sessions: `117,119`
- Angle ID: `left_knee`

Sessions `117` and `119` must remain ignored.

## Parameters

- Boundary event type: `peak_centered`
- Grid points: `101`
- Smooth window: `5`
- Event smooth window: `5`
- Boundary angle margin: `20.0`
- Min repetition seconds: `1.5`
- Max repetition seconds: `4.0`
- Min repetition amplitude: `50.0`
- Min peak angle: `80.0`
- Peak window: `20.0` to `80.0` percent
- Duration outlier IQR: `1.5`
- Reference repetitions: `3`
- Trim edge repetitions: `0`

## Duration Summary

- Total repetitions: `60`
- Used repetitions: `60`
- Excluded duration outliers: `0`
- Average repetition seconds: `2.7602`
- Average repetitions per minute: `21.737555`

## Validation Notes

This standard curve is preliminary because only sessions `116` and `118` are currently available. Open `raw_vs_standard_repetitions.html` and verify that detected repetitions follow the expected low-high-low squat pattern.

This script does not provide a medical diagnosis or clinical recommendation.
