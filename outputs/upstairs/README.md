# Upstairs Standard Curve Output

Generated at: `2026-05-26T08:39:30.346306+00:00`

## Purpose

This folder contains a preliminary standard upstairs/stair-climbing curve for Team V1 Phase II. It fits the full action from start to end instead of forcing a fixed number of detected left-knee cycles.

## Source Data

- Action: `upstairs`
- Protocol: climb `10` steps
- Requested sessions: `146,147,148,149,150,151,152,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174`
- Processed sessions: `146,147,148,149,150,151,152,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174`
- Ignored sessions: `117,119`
- Angle ID: `left_knee`

## Parameters

- Grid points: `101`
- Smooth window: `5`
- Peak smooth window: `5`
- Peak min distance seconds: `0.55`
- Min peak angle: `45.0`
- Min action seconds: `6.0`
- Max action seconds: `14.0`
- Min action amplitude: `60.0`
- Duration outlier IQR: `1.5`

## Duration Summary

- Total sessions: `26`
- Used sessions: `26`
- Excluded duration outliers: `0`
- Average action seconds: `9.543577`
- Average strong peaks per session: `7.04`

## Validation Notes

The left knee signal usually shows fewer strong peaks than the 10-step protocol because one knee does not necessarily peak on every stair step. For that reason, this script builds a full-action standard curve rather than forcing exactly 10 step segments.

Open `raw_vs_standard_sessions.html` and verify that the red standard curve follows the center of the gray raw session curves.

This script does not provide a medical diagnosis or clinical recommendation.
