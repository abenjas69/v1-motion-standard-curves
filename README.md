# V1 Motion Standard Curves

Team V1 - AI & Motion Recognition  
Phase II rehabilitation standard-curve prototype

This repository contains the current Python scripts and generated outputs for building healthy standard motion curves for:

- Walking
- Squat
- Upstairs / stair climbing

The current Phase II goal is practical: build healthy standard curves first, then compare patient curves against those standards later. This repository does not provide medical diagnosis or final AI recommendations.

## Repository Structure

```text
scripts/
  Walk.py
  Squat.py
  Upstairs.py

outputs/
  walking/
  squat/
  upstairs/
  recommendations/

docs/
  Phase II analysis and implementation notes

requirements/
  Requirement-2nd.pdf
  Requirement-2nd.docx

assets/
  walk_output_screenshot.png
```

## Data Used

### Walking

- Healthy standard sessions: `91-115`
- Script: `scripts/Walk.py`
- Main output: `outputs/walking/raw_vs_standard_cycles.html`
- Angle ID: `left_knee`
- Method: repeated walking cycles segmented between local minima

### Squat

- Healthy standard sessions: `116,118`
- Ignored sessions: `117,119`
- Script: `scripts/Squat.py`
- Main output: `outputs/squat/raw_vs_standard_repetitions.html`
- Angle ID: `left_knee`
- Method: squat repetitions segmented between local minima

### Upstairs / Stair Climbing

- Healthy standard sessions: `146,147,148,149,150,151,152,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174`
- Script: `scripts/Upstairs.py`
- Main output: `outputs/upstairs/raw_vs_standard_sessions.html`
- Angle ID: `left_knee`
- Method: full upstairs action normalized from start to end

Important: the upstairs protocol is to climb 10 steps, but the `left_knee` signal shows about 7 strong peaks per session. Therefore `Upstairs.py` fits the full action curve instead of forcing exactly 10 step segments.

## How To Run

From the repository root:

```bash
python scripts/Walk.py --out-dir outputs/walking
python scripts/Squat.py --out-dir outputs/squat
python scripts/Upstairs.py --out-dir outputs/upstairs
```

Generate an AI-assisted rule-based recommendation from curve data:

```bash
python generate_recommendation_from_curves.py \
  --action walking \
  --patient-session-id 33 \
  --standard-csv outputs/walking/normal_knee_curve.csv \
  --out-json outputs/recommendations/recommendation_walking_session33.json \
  --out-txt outputs/recommendations/recommendation_walking_session33.txt
```

The scripts use:

```text
http://113.44.220.94:3000/measurements
```

as the default API endpoint.

## Outputs To Review First

Open these files in a browser:

```text
outputs/walking/raw_vs_standard_cycles.html
outputs/squat/raw_vs_standard_repetitions.html
outputs/upstairs/raw_vs_standard_sessions.html
```

These are the best visual validation files because they overlay the raw curves with the fitted standard curve.

## Notes

- `Walk.py` is kept as the walking-only reference.
- `Squat.py` is separate and uses squat-specific repetition terminology.
- `Upstairs.py` is separate and uses full-action normalization.
- `generate_recommendation_from_curves.py` compares patient curves against saved standard curves and outputs preliminary JSON/TXT advice.
- Recommendation thresholds are preliminary engineering values and are not clinically validated.
- The recommendation output is AI-assisted support only, not a medical diagnosis.
