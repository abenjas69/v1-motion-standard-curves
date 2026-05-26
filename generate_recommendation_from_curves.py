import argparse
import csv
import json
import math
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


# Preliminary engineering thresholds. These values are not clinically validated.
NORMAL_RMSE_MAX = 8.0
MILD_RMSE_MAX = 18.0
NORMAL_AMPLITUDE_DIFF_MAX = 10.0
MILD_AMPLITUDE_DIFF_MAX = 25.0
NORMAL_OUTSIDE_BAND_MAX = 20.0
MILD_OUTSIDE_BAND_MAX = 50.0

DEFAULT_BASE_URL = "http://113.44.220.94:3000/measurements"
DOCTOR_REVIEW_NOTE = "This is AI-assisted analysis and should be reviewed by a doctor."
LIMITATIONS = [
    "This output is not a medical diagnosis.",
    "The analysis is based only on motion curve data.",
    "The result should be interpreted by a qualified clinician.",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare a patient motion curve against a saved standard curve and generate rule-based advice."
    )
    parser.add_argument("--action", required=True, choices=["walking", "squat", "upstairs"])
    parser.add_argument("--patient-csv", help="Path to patient/session curve CSV.")
    parser.add_argument("--patient-session-id", type=int, help="Patient/session ID to load from the API.")
    parser.add_argument("--standard-csv", required=True, help="Path to saved standard curve CSV.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--angle-id", default="left_knee")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-txt", required=True)
    parser.add_argument("--grid-points", type=int, default=101)
    parser.add_argument("--smooth-window", type=int, default=5)
    return parser.parse_args()


def parse_timestamp_ms(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def fetch_json(url, timeout=30):
    with urlopen(url, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def normalize_response_items(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "value" in payload:
        return payload["value"] or []
    return [payload]


def build_session_curve(items, angle_id):
    by_time = {}
    for item in items:
        for point in item.get("joint_angles") or []:
            if point.get("angleID") != angle_id:
                continue
            if point.get("timestamp") is None or point.get("angle") is None:
                continue
            timestamp_ms = parse_timestamp_ms(point["timestamp"])
            by_time.setdefault(timestamp_ms, []).append(float(point["angle"]))

    curve = []
    for timestamp_ms in sorted(by_time):
        values = by_time[timestamp_ms]
        curve.append((timestamp_ms, sum(values) / len(values)))
    if len(curve) < 2:
        raise ValueError(f"API session has fewer than 2 valid {angle_id} points")
    return curve


def read_csv_rows(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def get_first_number(row, names):
    for name in names:
        if name in row and row[name] not in (None, ""):
            try:
                return float(row[name])
            except ValueError:
                continue
    return None


def load_standard_curve(path):
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"Standard CSV is empty: {path}")

    parsed = []
    for row in rows:
        percent = get_first_number(row, ["percent"])
        time_seconds = get_first_number(row, ["time_seconds", "cycle_time_seconds", "repetition_time_seconds"])
        angle = get_first_number(row, ["smooth_angle", "standard_angle", "mean_angle", "angle"])
        mean_angle = get_first_number(row, ["mean_angle"])
        sd_angle = get_first_number(row, ["sd_angle"])
        lower = get_first_number(row, ["mean_minus_sd", "lower", "lower_band"])
        upper = get_first_number(row, ["mean_plus_sd", "upper", "upper_band"])
        if angle is None:
            continue
        parsed.append(
            {
                "percent": percent,
                "time_seconds": time_seconds,
                "standard_angle": angle,
                "mean_angle": mean_angle,
                "sd_angle": sd_angle,
                "lower": lower,
                "upper": upper,
            }
        )

    if len(parsed) < 2:
        raise ValueError(f"Standard CSV needs at least 2 valid curve points: {path}")

    if any(row["percent"] is None for row in parsed):
        parsed = add_percent_from_time_or_index(parsed)
    parsed.sort(key=lambda row: row["percent"])

    for row in parsed:
        if row["lower"] is None and row["upper"] is None and row["sd_angle"] is not None:
            center = row["mean_angle"] if row["mean_angle"] is not None else row["standard_angle"]
            row["lower"] = center - row["sd_angle"]
            row["upper"] = center + row["sd_angle"]
    return parsed


def load_patient_curve_from_csv(path):
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"Patient CSV is empty: {path}")

    parsed = []
    for row in rows:
        percent = get_first_number(row, ["percent"])
        time_seconds = get_first_number(row, ["time_seconds", "cycle_time_seconds", "repetition_time_seconds"])
        angle = get_first_number(row, ["patient_angle", "angle", "standard_angle", "smooth_angle", "mean_angle"])
        if angle is None:
            continue
        parsed.append({"percent": percent, "time_seconds": time_seconds, "angle": angle})

    if len(parsed) < 2:
        raise ValueError(f"Patient CSV needs at least 2 valid curve points: {path}")
    return parsed


def load_patient_curve_from_api(base_url, session_id, angle_id):
    url = f"{base_url.rstrip('/')}/{session_id}"
    payload = fetch_json(url)
    items = normalize_response_items(payload)
    curve = build_session_curve(items, angle_id)
    start_t = curve[0][0]
    return [
        {
            "time_seconds": (timestamp_ms - start_t) / 1000.0,
            "angle": angle,
            "percent": None,
        }
        for timestamp_ms, angle in curve
    ]


def add_percent_from_time_or_index(rows):
    if all(row.get("time_seconds") is not None for row in rows):
        min_t = min(row["time_seconds"] for row in rows)
        max_t = max(row["time_seconds"] for row in rows)
        duration = max_t - min_t
        if duration <= 0:
            raise ValueError("Cannot normalize curve with zero time duration")
        normalized = []
        for row in rows:
            updated = dict(row)
            updated["percent"] = (row["time_seconds"] - min_t) / duration * 100.0
            normalized.append(updated)
        return normalized

    if len(rows) < 2:
        raise ValueError("Cannot normalize curve with fewer than 2 points")
    normalized = []
    for index, row in enumerate(rows):
        updated = dict(row)
        updated["percent"] = 100.0 * index / (len(rows) - 1)
        normalized.append(updated)
    return normalized


def normalize_curve_to_percent(curve, grid_points):
    if len(curve) < 2:
        raise ValueError("Curve needs at least 2 points")
    rows = [dict(row) for row in curve]
    if any(row.get("percent") is None for row in rows):
        rows = add_percent_from_time_or_index(rows)
    rows.sort(key=lambda row: row["percent"])
    percent_grid = [0.0 if grid_points == 1 else 100.0 * i / (grid_points - 1) for i in range(grid_points)]
    return [{"percent": percent, "angle": interpolate_curve(rows, percent, "angle")} for percent in percent_grid]


def interpolate_curve(rows, percent, angle_key):
    rows = sorted(rows, key=lambda row: row["percent"])
    if percent <= rows[0]["percent"]:
        return rows[0][angle_key]
    if percent >= rows[-1]["percent"]:
        return rows[-1][angle_key]

    for index in range(len(rows) - 1):
        left = rows[index]
        right = rows[index + 1]
        if left["percent"] <= percent <= right["percent"]:
            span = right["percent"] - left["percent"]
            if span == 0:
                return left[angle_key]
            ratio = (percent - left["percent"]) / span
            return left[angle_key] + ratio * (right[angle_key] - left[angle_key])
    return rows[-1][angle_key]


def moving_average(values, window):
    if window <= 1:
        return list(values)
    if window % 2 == 0:
        window += 1
    half = window // 2
    out = []
    for index in range(len(values)):
        left = max(0, index - half)
        right = min(len(values), index + half + 1)
        chunk = values[left:right]
        out.append(sum(chunk) / len(chunk))
    return out


def align_patient_to_standard(patient_curve, standard_rows, smooth_window):
    patient_rows = normalize_curve_to_percent(patient_curve, len(standard_rows))
    target_percents = [row["percent"] for row in standard_rows]
    aligned = [{"percent": percent, "angle": interpolate_curve(patient_rows, percent, "angle")} for percent in target_percents]
    smoothed = moving_average([row["angle"] for row in aligned], smooth_window)
    for index, row in enumerate(aligned):
        row["angle"] = smoothed[index]
    return aligned


def safe_mean(values):
    return sum(values) / len(values) if values else None


def compute_correlation(left_values, right_values):
    if len(left_values) < 2 or len(left_values) != len(right_values):
        return None
    left_mean = safe_mean(left_values)
    right_mean = safe_mean(right_values)
    left_diffs = [value - left_mean for value in left_values]
    right_diffs = [value - right_mean for value in right_values]
    left_ss = sum(value * value for value in left_diffs)
    right_ss = sum(value * value for value in right_diffs)
    if left_ss <= 0 or right_ss <= 0:
        return None
    return sum(a * b for a, b in zip(left_diffs, right_diffs)) / math.sqrt(left_ss * right_ss)


def compute_metrics(patient_rows, standard_rows):
    if len(patient_rows) != len(standard_rows) or len(patient_rows) < 2:
        raise ValueError("Patient and standard curves must have the same length and at least 2 points")

    patient_values = [row["angle"] for row in patient_rows]
    standard_values = [row["standard_angle"] for row in standard_rows]
    deviations = [patient - standard for patient, standard in zip(patient_values, standard_values)]
    abs_deviations = [abs(value) for value in deviations]
    squared_deviations = [value * value for value in deviations]

    patient_peak = max(patient_values)
    standard_peak = max(standard_values)
    patient_min = min(patient_values)
    standard_min = min(standard_values)
    patient_amplitude = patient_peak - patient_min
    standard_amplitude = standard_peak - standard_min

    outside_percent = None
    has_band = all(row.get("lower") is not None and row.get("upper") is not None for row in standard_rows)
    if has_band:
        outside_count = 0
        for patient, standard in zip(patient_values, standard_rows):
            if patient < standard["lower"] or patient > standard["upper"]:
                outside_count += 1
        outside_percent = outside_count / len(patient_values) * 100.0

    return {
        "patientPeakAngle": round(patient_peak, 6),
        "standardPeakAngle": round(standard_peak, 6),
        "peakAngleDifference": round(patient_peak - standard_peak, 6),
        "patientMinAngle": round(patient_min, 6),
        "standardMinAngle": round(standard_min, 6),
        "minAngleDifference": round(patient_min - standard_min, 6),
        "patientAmplitude": round(patient_amplitude, 6),
        "standardAmplitude": round(standard_amplitude, 6),
        "amplitudeDifference": round(patient_amplitude - standard_amplitude, 6),
        "mae": round(safe_mean(abs_deviations), 6),
        "rmse": round(math.sqrt(safe_mean(squared_deviations)), 6),
        "maxAbsoluteDeviation": round(max(abs_deviations), 6),
        "meanSignedDeviation": round(safe_mean(deviations), 6),
        "correlation": round(compute_correlation(patient_values, standard_values), 6)
        if compute_correlation(patient_values, standard_values) is not None
        else None,
        "outsideStandardBandPercent": round(outside_percent, 6) if outside_percent is not None else None,
    }


def classify_status(metrics):
    if metrics is None:
        return "unclear"
    rmse = metrics.get("rmse")
    amplitude_diff = metrics.get("amplitudeDifference")
    outside = metrics.get("outsideStandardBandPercent")
    if rmse is None or amplitude_diff is None:
        return "unclear"

    abs_amplitude_diff = abs(amplitude_diff)
    outside_for_status = outside if outside is not None else 0.0

    if (
        rmse > MILD_RMSE_MAX
        or abs_amplitude_diff > MILD_AMPLITUDE_DIFF_MAX
        or outside_for_status > MILD_OUTSIDE_BAND_MAX
    ):
        return "significant_deviation"
    if (
        rmse <= NORMAL_RMSE_MAX
        and abs_amplitude_diff <= NORMAL_AMPLITUDE_DIFF_MAX
        and outside_for_status <= NORMAL_OUTSIDE_BAND_MAX
    ):
        return "normal"
    return "mild_deviation"


def generate_observations(action, metrics):
    observations = []
    peak_diff = metrics["peakAngleDifference"]
    amplitude_diff = metrics["amplitudeDifference"]
    rmse = metrics["rmse"]
    outside = metrics["outsideStandardBandPercent"]

    if action == "walking":
        if peak_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower peak knee angle may indicate reduced knee flexion during walking.")
        if amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower movement amplitude may suggest reduced walking range of motion.")
        if rmse > NORMAL_RMSE_MAX:
            observations.append("The walking curve differs from the standard curve shape.")
    elif action == "squat":
        if peak_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower peak knee angle may suggest reduced squat depth or reduced knee flexion.")
        if amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower movement amplitude may suggest incomplete squat movement.")
        if rmse > NORMAL_RMSE_MAX:
            observations.append("The squat pattern differs from the healthy standard curve.")
    elif action == "upstairs":
        if peak_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower peak knee angle may suggest reduced knee lift during stair climbing.")
        if amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower movement amplitude may suggest limited movement range.")
        if rmse > NORMAL_RMSE_MAX:
            observations.append("The stair-climbing curve differs from the standard curve shape.")

    if outside is not None and outside > NORMAL_OUTSIDE_BAND_MAX:
        observations.append("A notable portion of the curve is outside the healthy standard deviation band.")
    if metrics["meanSignedDeviation"] > NORMAL_AMPLITUDE_DIFF_MAX:
        observations.append("The patient curve is generally above the standard curve.")
    elif metrics["meanSignedDeviation"] < -NORMAL_AMPLITUDE_DIFF_MAX:
        observations.append("The patient curve is generally below the standard curve.")

    if not observations:
        observations.append("The patient curve is close to the current healthy reference under the preliminary thresholds.")
    return observations


def generate_recommendation_text(action, status, metrics, observations):
    action_text = {
        "walking": "walking",
        "squat": "squat",
        "upstairs": "stair-climbing",
    }[action]

    if status == "normal":
        lead = f"The {action_text} curve is close to the current healthy reference."
    elif status == "mild_deviation":
        lead = f"The {action_text} curve shows mild deviation from the current healthy reference."
    elif status == "significant_deviation":
        lead = f"The {action_text} curve shows significant deviation from the current healthy reference."
    else:
        lead = "The comparison result is unclear because the available curve data is insufficient or incomplete."

    observation_text = " ".join(observations)
    return (
        f"{lead} {observation_text} This may indicate a movement difference, "
        "but it is not a final medical diagnosis and should be reviewed by a doctor."
    )


def build_output_json(action, angle_id, input_type, patient_source, standard_source, metrics, status, observations):
    return {
        "action": action,
        "angleID": angle_id,
        "inputType": input_type,
        "patientSource": patient_source,
        "standardSource": standard_source,
        "status": status,
        "metrics": metrics,
        "observations": observations,
        "recommendationText": generate_recommendation_text(action, status, metrics, observations),
        "doctorReviewNote": DOCTOR_REVIEW_NOTE,
        "limitations": LIMITATIONS,
    }


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_txt(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    metrics = data["metrics"]
    lines = [
        "AI-Assisted Motion Curve Recommendation",
        "",
        f"Action: {data['action']}",
        f"Angle ID: {data['angleID']}",
        f"Input type: {data['inputType']}",
        f"Patient source: {data['patientSource']}",
        f"Standard source: {data['standardSource']}",
        f"Status: {data['status']}",
        "",
        "Key metrics:",
        f"- Patient peak angle: {metrics['patientPeakAngle']} deg",
        f"- Standard peak angle: {metrics['standardPeakAngle']} deg",
        f"- Peak angle difference: {metrics['peakAngleDifference']} deg",
        f"- Patient min angle: {metrics['patientMinAngle']} deg",
        f"- Standard min angle: {metrics['standardMinAngle']} deg",
        f"- Min angle difference: {metrics['minAngleDifference']} deg",
        f"- Patient amplitude: {metrics['patientAmplitude']} deg",
        f"- Standard amplitude: {metrics['standardAmplitude']} deg",
        f"- Amplitude difference: {metrics['amplitudeDifference']} deg",
        f"- MAE: {metrics['mae']} deg",
        f"- RMSE: {metrics['rmse']} deg",
        f"- Max absolute deviation: {metrics['maxAbsoluteDeviation']} deg",
        f"- Mean signed deviation: {metrics['meanSignedDeviation']} deg",
        f"- Correlation: {metrics['correlation']}",
        f"- Outside standard band percent: {metrics['outsideStandardBandPercent']}",
        "",
        "Observations:",
    ]
    lines.extend(f"- {observation}" for observation in data["observations"])
    lines.extend(
        [
            "",
            "Recommendation:",
            data["recommendationText"],
            "",
            "Doctor review note:",
            data["doctorReviewNote"],
            "",
            "Limitations:",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in data["limitations"])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def main():
    args = parse_args()
    if not args.patient_csv and args.patient_session_id is None:
        raise SystemExit("ERROR: provide either --patient-csv or --patient-session-id.")

    if args.patient_csv and args.patient_session_id is not None:
        print("WARNING: both --patient-csv and --patient-session-id were provided. Using --patient-csv.")

    standard_rows = load_standard_curve(args.standard_csv)
    if args.patient_csv:
        patient_curve = load_patient_curve_from_csv(args.patient_csv)
        input_type = "csv"
        patient_source = args.patient_csv
    else:
        try:
            patient_curve = load_patient_curve_from_api(args.base_url, args.patient_session_id, args.angle_id)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise SystemExit(f"ERROR: failed to load patient API session {args.patient_session_id}: {exc}")
        input_type = "api"
        patient_source = f"{args.base_url.rstrip('/')}/{args.patient_session_id}"

    patient_rows = align_patient_to_standard(patient_curve, standard_rows, args.smooth_window)
    metrics = compute_metrics(patient_rows, standard_rows)
    status = classify_status(metrics)
    observations = generate_observations(args.action, metrics)
    output = build_output_json(
        args.action,
        args.angle_id,
        input_type,
        patient_source,
        args.standard_csv,
        metrics,
        status,
        observations,
    )

    write_json(args.out_json, output)
    write_txt(args.out_txt, output)

    print("Done.")
    print(f"Action: {args.action}")
    print(f"Status: {status}")
    print(f"RMSE: {metrics['rmse']} deg")
    print(f"Amplitude difference: {metrics['amplitudeDifference']} deg")
    print(f"Outside standard band percent: {metrics['outsideStandardBandPercent']}")
    print(f"JSON: {args.out_json}")
    print(f"TXT: {args.out_txt}")


if __name__ == "__main__":
    main()
