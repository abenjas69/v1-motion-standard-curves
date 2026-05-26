# Squat Standard Curve Output

Generated at: `2026-05-25T21:23:06.718897+00:00`

## Purpose

This folder contains a preliminary standard squat curve for Team V1 Phase II. It uses healthy squat sessions only and is intended for visual validation before patient comparison.

## Source Data

- Action: `squat`
- Protocol: 3 consecutive squat repetitions in about 5 seconds
- Requested sessions: `116,118`
- Processed sessions: `116,118`
- Ignored sessions: `117,119`
- Angle ID: `left_knee`

Sessions `117` and `119` must remain ignored.

## Parameters

- Boundary event type: `minima`
- Grid points: `101`
- Smooth window: `5`
- Event smooth window: `5`
- Min repetition seconds: `1.5`
- Max repetition seconds: `4.0`
- Min repetition amplitude: `50.0`
- Min peak angle: `80.0`
- Peak window: `20.0` to `80.0` percent
- Duration outlier IQR: `1.5`
- Reference repetitions: `3`
- Trim edge repetitions: `0`

## Duration Summary

- Total repetitions: `6`
- Used repetitions: `6`
- Excluded duration outliers: `0`
- Average repetition seconds: `2.9525`
- Average repetitions per minute: `20.321761`

## Validation Notes

This standard curve is preliminary because only sessions `116` and `118` are currently available. Open `raw_vs_standard_repetitions.html` and verify that detected repetitions follow the expected low-high-low squat pattern.

This script does not provide a medical diagnosis or clinical recommendation.
