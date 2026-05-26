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

# Preliminary engineering segmentation settings. These values are action-specific
# starting points and should be validated visually with real patient data.
ACTION_SEGMENT_CONFIGS = {
    "walking": {
        "enabled": True,
        "method": "local_minima_cycles",
        "label": "cycles",
        "min_duration_seconds": 0.6,
        "max_duration_seconds": 3.5,
        "min_amplitude_degrees": 15.0,
        "min_peak_angle": 30.0,
        "min_extrema_distance_seconds": 0.35,
        "trim_edge_segments": 1,
    },
    "squat": {
        "enabled": True,
        "method": "peak_centered_repetitions",
        "label": "repetitions",
        "min_duration_seconds": 1.0,
        "max_duration_seconds": 8.0,
        "min_amplitude_degrees": 20.0,
        "min_peak_angle": 30.0,
        "min_extrema_distance_seconds": 0.8,
        "trim_edge_segments": 0,
    },
    "upstairs": {
        "enabled": False,
        "method": "whole_action",
        "label": "full_action",
        "reason": "The current upstairs standard curve represents the full stair-climbing action, not one step cycle.",
    },
}

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
    parser.add_argument(
        "--segment-patient",
        choices=["auto", "always", "never"],
        default="auto",
        help=(
            "Segment patient data before comparison when possible. "
            "auto segments walking/squat only when time data is available; "
            "always fails if segmentation is impossible; never uses the full curve."
        ),
    )
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


def median(values):
    if not values:
        return None
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def has_complete_time_axis(curve):
    return bool(curve) and all(row.get("time_seconds") is not None for row in curve)


def curve_duration_seconds(curve):
    if not has_complete_time_axis(curve):
        return None
    times = [row["time_seconds"] for row in curve]
    return max(times) - min(times)


def estimate_sample_interval_seconds(rows):
    if not has_complete_time_axis(rows) or len(rows) < 2:
        return None
    times = sorted(row["time_seconds"] for row in rows)
    diffs = [right - left for left, right in zip(times, times[1:]) if right > left]
    return median(diffs)


def sort_curve_by_time(curve):
    return sorted((dict(row) for row in curve), key=lambda row: row["time_seconds"])


def local_extrema_indices(values, kind, window_points):
    if len(values) < window_points * 2 + 1:
        return []

    candidates = []
    for index in range(window_points, len(values) - window_points):
        center = values[index]
        chunk = values[index - window_points : index + window_points + 1]
        if kind == "min":
            if center == min(chunk) and center < values[index - 1] and center <= values[index + 1]:
                candidates.append(index)
        elif kind == "max":
            if center == max(chunk) and center > values[index - 1] and center >= values[index + 1]:
                candidates.append(index)
        else:
            raise ValueError(f"Unknown extrema kind: {kind}")
    return candidates


def enforce_extrema_distance(indices, values, min_distance_points, kind):
    if min_distance_points <= 1 or len(indices) < 2:
        return sorted(indices)

    reverse = kind == "max"
    ranked = sorted(indices, key=lambda index: values[index], reverse=reverse)
    selected = []
    for index in ranked:
        if all(abs(index - existing) >= min_distance_points for existing in selected):
            selected.append(index)
    return sorted(selected)


def find_extrema_indices(rows, kind, config, smooth_window):
    values = moving_average([row["angle"] for row in rows], max(3, smooth_window))
    sample_interval = estimate_sample_interval_seconds(rows)
    if sample_interval is None or sample_interval <= 0:
        return []

    min_distance_seconds = config.get("min_extrema_distance_seconds", 0.3)
    min_distance_points = max(1, int(round(min_distance_seconds / sample_interval)))
    window_points = max(1, min_distance_points // 2)
    candidates = local_extrema_indices(values, kind, window_points)
    return enforce_extrema_distance(candidates, values, min_distance_points, kind)


def build_segment_record(rows, index, source):
    duration = curve_duration_seconds(rows)
    values = [row["angle"] for row in rows]
    return {
        "index": index,
        "source": source,
        "rows": rows,
        "durationSeconds": duration,
        "amplitudeDegrees": max(values) - min(values) if values else None,
        "peakAngle": max(values) if values else None,
        "startTimeSeconds": rows[0]["time_seconds"] if rows else None,
        "endTimeSeconds": rows[-1]["time_seconds"] if rows else None,
        "pointCount": len(rows),
    }


def validate_segment_record(record, config):
    duration = record["durationSeconds"]
    amplitude = record["amplitudeDegrees"]
    peak = record["peakAngle"]

    if duration is None or duration <= 0:
        return False, "invalid_duration"
    if duration < config["min_duration_seconds"]:
        return False, "duration_too_short"
    if duration > config["max_duration_seconds"]:
        return False, "duration_too_long"
    if amplitude is None or amplitude < config["min_amplitude_degrees"]:
        return False, "amplitude_too_low"
    if peak is None or peak < config["min_peak_angle"]:
        return False, "peak_angle_too_low"
    if record["pointCount"] < 5:
        return False, "too_few_points"
    return True, None


def strip_segment_rows(record):
    return {
        key: value
        for key, value in record.items()
        if key != "rows"
    }


def segment_by_local_minima(curve, config, smooth_window):
    rows = sort_curve_by_time(curve)
    minima = find_extrema_indices(rows, "min", config, smooth_window)
    valid = []
    rejected = []
    detected = 0

    for index, (start_idx, end_idx) in enumerate(zip(minima, minima[1:]), start=1):
        if end_idx <= start_idx:
            continue
        detected += 1
        record = build_segment_record(rows[start_idx : end_idx + 1], index, "local_minima")
        is_valid, reason = validate_segment_record(record, config)
        if is_valid:
            valid.append(record)
        else:
            rejected.append({**strip_segment_rows(record), "reason": reason})

    trim = config.get("trim_edge_segments", 0)
    if trim and len(valid) > trim * 2:
        trimmed = valid[:trim] + valid[-trim:]
        rejected.extend({**strip_segment_rows(record), "reason": "edge_segment_trimmed"} for record in trimmed)
        valid = valid[trim:-trim]

    return valid, rejected, detected, len(minima)


def min_index_between(rows, start_idx, end_idx):
    if end_idx < start_idx:
        return start_idx
    return min(range(start_idx, end_idx + 1), key=lambda index: rows[index]["angle"])


def segment_by_peak_centered_repetitions(curve, config, smooth_window):
    rows = sort_curve_by_time(curve)
    peaks = find_extrema_indices(rows, "max", config, smooth_window)
    valid = []
    rejected = []
    detected = 0

    for position, peak_idx in enumerate(peaks):
        left_search_start = 0 if position == 0 else peaks[position - 1]
        right_search_end = len(rows) - 1 if position == len(peaks) - 1 else peaks[position + 1]
        start_idx = min_index_between(rows, left_search_start, peak_idx)
        end_idx = min_index_between(rows, peak_idx, right_search_end)
        if end_idx <= start_idx:
            continue

        detected += 1
        record = build_segment_record(rows[start_idx : end_idx + 1], detected, "peak_centered")
        is_valid, reason = validate_segment_record(record, config)
        if is_valid:
            valid.append(record)
        else:
            rejected.append({**strip_segment_rows(record), "reason": reason})

    return valid, rejected, detected, len(peaks)


def count_rejection_reasons(rejected):
    counts = {}
    for item in rejected:
        reason = item.get("reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def segment_patient_curve(curve, action, segment_mode, smooth_window):
    config = ACTION_SEGMENT_CONFIGS[action]
    summary = {
        "requestedMode": segment_mode,
        "attempted": False,
        "used": False,
        "method": config["method"],
        "label": config["label"],
        "segmentsDetected": 0,
        "segmentsUsed": 0,
        "segmentsRejected": 0,
        "rejectedReasonCounts": {},
        "averageSegmentDurationSeconds": None,
        "segmentDurationSeconds": [],
        "fallbackReason": None,
    }

    if segment_mode == "never":
        summary["fallbackReason"] = "segmentation_disabled_by_cli"
        return [], summary

    if not config.get("enabled"):
        summary["fallbackReason"] = config.get("reason", "segmentation_not_enabled_for_action")
        if segment_mode == "always":
            raise ValueError(summary["fallbackReason"])
        return [], summary

    if not has_complete_time_axis(curve):
        summary["fallbackReason"] = "patient_curve_has_no_complete_time_seconds_axis"
        if segment_mode == "always":
            raise ValueError(summary["fallbackReason"])
        return [], summary

    summary["attempted"] = True
    if config["method"] == "local_minima_cycles":
        segments, rejected, detected, extrema_count = segment_by_local_minima(curve, config, smooth_window)
        summary["extremaCount"] = extrema_count
    elif config["method"] == "peak_centered_repetitions":
        segments, rejected, detected, extrema_count = segment_by_peak_centered_repetitions(curve, config, smooth_window)
        summary["extremaCount"] = extrema_count
    else:
        segments, rejected, detected = [], [], 0

    summary["segmentsDetected"] = detected
    summary["segmentsRejected"] = len(rejected)
    summary["rejectedReasonCounts"] = count_rejection_reasons(rejected)

    if not segments:
        summary["fallbackReason"] = "no_valid_segments_detected"
        if segment_mode == "always":
            raise ValueError(summary["fallbackReason"])
        return [], summary

    durations = [record["durationSeconds"] for record in segments if record["durationSeconds"] is not None]
    summary["used"] = True
    summary["segmentsUsed"] = len(segments)
    summary["averageSegmentDurationSeconds"] = round(sum(durations) / len(durations), 6) if durations else None
    summary["segmentDurationSeconds"] = [round(duration, 6) for duration in durations]
    summary["segments"] = [strip_segment_rows(record) for record in segments]
    return [record["rows"] for record in segments], summary


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


def safe_stdev(values):
    if len(values) < 2:
        return 0.0 if values else None
    mean = safe_mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


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


def average_aligned_segments(aligned_segments, standard_rows):
    averaged = []
    for point_index, standard_row in enumerate(standard_rows):
        values = [segment[point_index]["angle"] for segment in aligned_segments]
        averaged.append({"percent": standard_row["percent"], "angle": safe_mean(values)})
    return averaged


def summarize_segment_metrics(segment_metrics):
    if not segment_metrics:
        return {}

    rmse_values = [item["metrics"]["rmse"] for item in segment_metrics if item["metrics"].get("rmse") is not None]
    amplitude_diffs = [
        item["metrics"]["amplitudeDifference"]
        for item in segment_metrics
        if item["metrics"].get("amplitudeDifference") is not None
    ]
    peak_diffs = [
        item["metrics"]["peakAngleDifference"]
        for item in segment_metrics
        if item["metrics"].get("peakAngleDifference") is not None
    ]
    outside_values = [
        item["metrics"]["outsideStandardBandPercent"]
        for item in segment_metrics
        if item["metrics"].get("outsideStandardBandPercent") is not None
    ]

    summary = {
        "segmentRmseMean": round(safe_mean(rmse_values), 6) if rmse_values else None,
        "segmentRmseStd": round(safe_stdev(rmse_values), 6) if rmse_values else None,
        "segmentAmplitudeDifferenceMean": round(safe_mean(amplitude_diffs), 6) if amplitude_diffs else None,
        "segmentPeakAngleDifferenceMean": round(safe_mean(peak_diffs), 6) if peak_diffs else None,
        "segmentOutsideStandardBandPercentMean": round(safe_mean(outside_values), 6) if outside_values else None,
    }
    return summary


def compare_patient_to_standard(patient_curve, standard_rows, action, segment_mode, smooth_window):
    segments, segmentation = segment_patient_curve(patient_curve, action, segment_mode, smooth_window)

    if segments:
        aligned_segments = [align_patient_to_standard(segment, standard_rows, smooth_window) for segment in segments]
        patient_rows = average_aligned_segments(aligned_segments, standard_rows)
        metrics = compute_metrics(patient_rows, standard_rows)

        segment_metrics = []
        for index, aligned_segment in enumerate(aligned_segments, start=1):
            per_segment_metrics = compute_metrics(aligned_segment, standard_rows)
            segment_metrics.append(
                {
                    "index": index,
                    "status": classify_status(per_segment_metrics),
                    "metrics": {
                        "rmse": per_segment_metrics["rmse"],
                        "mae": per_segment_metrics["mae"],
                        "peakAngleDifference": per_segment_metrics["peakAngleDifference"],
                        "amplitudeDifference": per_segment_metrics["amplitudeDifference"],
                        "outsideStandardBandPercent": per_segment_metrics["outsideStandardBandPercent"],
                    },
                }
            )
        segmentation["segmentMetricSummary"] = summarize_segment_metrics(segment_metrics)
        segmentation["segmentMetrics"] = segment_metrics
        return patient_rows, metrics, segmentation

    patient_rows = align_patient_to_standard(patient_curve, standard_rows, smooth_window)
    metrics = compute_metrics(patient_rows, standard_rows)
    return patient_rows, metrics, segmentation


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


def generate_observations(action, metrics, segmentation=None):
    observations = []
    peak_diff = metrics["peakAngleDifference"]
    amplitude_diff = metrics["amplitudeDifference"]
    rmse = metrics["rmse"]
    outside = metrics["outsideStandardBandPercent"]

    if segmentation and segmentation.get("used"):
        observations.append(
            f"The comparison used {segmentation['segmentsUsed']} detected {segmentation['label']} from the patient curve."
        )
    elif segmentation and segmentation.get("attempted") and segmentation.get("fallbackReason"):
        observations.append(
            f"Patient segmentation was attempted but the full curve was used because: {segmentation['fallbackReason']}."
        )

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


def build_output_json(
    action,
    angle_id,
    input_type,
    patient_source,
    standard_source,
    metrics,
    status,
    observations,
    segmentation=None,
):
    return {
        "action": action,
        "angleID": angle_id,
        "inputType": input_type,
        "patientSource": patient_source,
        "standardSource": standard_source,
        "comparisonMode": "segmented" if segmentation and segmentation.get("used") else "full_curve",
        "segmentation": segmentation,
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
        f"Comparison mode: {data.get('comparisonMode', 'full_curve')}",
        f"Status: {data['status']}",
        "",
    ]

    segmentation = data.get("segmentation") or {}
    if segmentation:
        lines.extend(
            [
                "Segmentation:",
                f"- Requested mode: {segmentation.get('requestedMode')}",
                f"- Method: {segmentation.get('method')}",
                f"- Label: {segmentation.get('label')}",
                f"- Attempted: {segmentation.get('attempted')}",
                f"- Used: {segmentation.get('used')}",
                f"- Segments detected: {segmentation.get('segmentsDetected')}",
                f"- Segments used: {segmentation.get('segmentsUsed')}",
                f"- Segments rejected: {segmentation.get('segmentsRejected')}",
                f"- Average segment duration: {segmentation.get('averageSegmentDurationSeconds')} s",
                f"- Fallback reason: {segmentation.get('fallbackReason')}",
                "",
            ]
        )
        segment_metric_summary = segmentation.get("segmentMetricSummary") or {}
        if segment_metric_summary:
            lines.extend(
                [
                    "Segment metric summary:",
                    f"- Segment RMSE mean: {segment_metric_summary.get('segmentRmseMean')} deg",
                    f"- Segment RMSE std: {segment_metric_summary.get('segmentRmseStd')} deg",
                    f"- Segment amplitude difference mean: {segment_metric_summary.get('segmentAmplitudeDifferenceMean')} deg",
                    f"- Segment peak angle difference mean: {segment_metric_summary.get('segmentPeakAngleDifferenceMean')} deg",
                    f"- Segment outside standard band mean: {segment_metric_summary.get('segmentOutsideStandardBandPercentMean')}",
                    "",
                ]
            )

    lines.extend(
        [
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
    )
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

    try:
        _patient_rows, metrics, segmentation = compare_patient_to_standard(
            patient_curve,
            standard_rows,
            args.action,
            args.segment_patient,
            args.smooth_window,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: failed to compare patient curve: {exc}")

    status = classify_status(metrics)
    observations = generate_observations(args.action, metrics, segmentation)
    output = build_output_json(
        args.action,
        args.angle_id,
        input_type,
        patient_source,
        args.standard_csv,
        metrics,
        status,
        observations,
        segmentation,
    )

    write_json(args.out_json, output)
    write_txt(args.out_txt, output)

    print("Done.")
    print(f"Action: {args.action}")
    print(f"Comparison mode: {output['comparisonMode']}")
    if segmentation:
        print(f"Segments used: {segmentation.get('segmentsUsed', 0)}")
        if segmentation.get("fallbackReason"):
            print(f"Segmentation fallback: {segmentation['fallbackReason']}")
    print(f"Status: {status}")
    print(f"RMSE: {metrics['rmse']} deg")
    print(f"Amplitude difference: {metrics['amplitudeDifference']} deg")
    print(f"Outside standard band percent: {metrics['outsideStandardBandPercent']}")
    print(f"JSON: {args.out_json}")
    print(f"TXT: {args.out_txt}")


if __name__ == "__main__":
    main()
