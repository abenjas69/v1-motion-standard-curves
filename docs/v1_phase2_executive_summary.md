# V1 Phase II Executive Summary

## 1. What AuCloud Already Did

AuCloud provided `Walk.py`, which creates a standard walking curve.

Current behavior:

- uses sessions `91-115`;
- extracts `left_knee` from `joint_angles`;
- detects walking cycles between local minima;
- filters cycles by duration, amplitude, peak angle, and peak timing;
- fits an average standard curve;
- generates CSV and HTML outputs;
- optionally generates a PNG if `matplotlib` is installed.

The most useful visual output is `raw_vs_standard_cycles.html` because it shows raw detected cycles over the fitted standard curve.

## 2. Available Data Now

- Healthy walking: sessions `91-115`.
- Processed walking user/patient data: sessions `33-57`.
- Squat: sessions `116` and `118`.
- Ignore: sessions `117` and `119`.
- Stair climbing: session IDs unknown / pending.

Do not invent stair-climbing session IDs.

## 3. What I Need To Do Next

- Analyze `Walk.py`.
- Generalize it into a reusable standard-curve builder.
- Implement squat using sessions `116` and `118`.
- Do not implement stair climbing until session IDs are provided.
- Design patient-vs-standard comparison.
- Prepare simple rule-based recommendations later.

## 4. What This Means For The Old V1 Pipeline

This does not replace the original IMU-based AI pipeline.

It is a practical Phase II version focused on standard curves, data organization, and doctor-facing comparison. Advanced deep learning can come later when the system has more healthy data, more patient data, better labels, and a stable workflow.

For now, V1 should avoid promising automatic diagnosis. The current target is AI-assisted or rule-based support for doctor review.

## 5. Immediate Recommended Next Steps

- Run `Walk.py` locally.
- Inspect `raw_vs_standard_cycles.html`.
- Validate `normal_knee_curve.csv`.
- Create generic `standard_curve_builder.py`.
- Reproduce walking output with sessions `91-115`.
- Implement explicit session-list support.
- Implement squat with sessions `116` and `118`.
- Make sure sessions `117` and `119` are ignored.
- Ask S1/S2/AuCloud for stair-climbing session IDs.
- Implement patient-vs-standard comparison script.

