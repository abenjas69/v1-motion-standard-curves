import argparse
import csv
from html import escape
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

COMPARISON_VERSION = "v0.6-clinical-advice-accuracy"

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
        "min_peak_angle": None,
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
        "min_peak_angle": None,
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
    parser.add_argument("--out-html", help="Optional patient-vs-standard HTML visualization output path.")
    parser.add_argument("--out-average-csv", help="Optional normalized patient-vs-standard average curve CSV output path.")
    parser.add_argument("--out-segments-csv", help="Optional detected segment/repetition summary CSV output path.")
    parser.add_argument("--out-metrics-csv", help="Optional key metrics CSV output path.")
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
    min_peak_angle = config.get("min_peak_angle")

    if duration is None or duration <= 0:
        return False, "invalid_duration"
    if duration < config["min_duration_seconds"]:
        return False, "duration_too_short"
    if duration > config["max_duration_seconds"]:
        return False, "duration_too_long"
    if amplitude is None or amplitude < config["min_amplitude_degrees"]:
        return False, "amplitude_too_low"
    if min_peak_angle is not None and (peak is None or peak < min_peak_angle):
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
    summary["segments"] = [
        {**strip_segment_rows(record), "usedSegmentIndex": index}
        for index, record in enumerate(segments, start=1)
    ]
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
    mean_signed_deviation = safe_mean(deviations)
    offset_corrected_deviations = [
        (patient - mean_signed_deviation) - standard
        for patient, standard in zip(patient_values, standard_values)
    ]
    offset_corrected_squared = [value * value for value in offset_corrected_deviations]

    patient_peak = max(patient_values)
    standard_peak = max(standard_values)
    patient_peak_index = max(range(len(patient_values)), key=lambda index: patient_values[index])
    standard_peak_index = max(range(len(standard_values)), key=lambda index: standard_values[index])
    patient_peak_percent = patient_rows[patient_peak_index]["percent"]
    standard_peak_percent = standard_rows[standard_peak_index]["percent"]
    patient_min = min(patient_values)
    standard_min = min(standard_values)
    patient_amplitude = patient_peak - patient_min
    standard_amplitude = standard_peak - standard_min
    amplitude_ratio = patient_amplitude / standard_amplitude if standard_amplitude else None
    correlation = compute_correlation(patient_values, standard_values)

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
        "patientPeakPercent": round(patient_peak_percent, 6),
        "standardPeakPercent": round(standard_peak_percent, 6),
        "peakTimingDifferencePercent": round(patient_peak_percent - standard_peak_percent, 6),
        "patientMinAngle": round(patient_min, 6),
        "standardMinAngle": round(standard_min, 6),
        "minAngleDifference": round(patient_min - standard_min, 6),
        "patientAmplitude": round(patient_amplitude, 6),
        "standardAmplitude": round(standard_amplitude, 6),
        "amplitudeDifference": round(patient_amplitude - standard_amplitude, 6),
        "amplitudeRatio": round(amplitude_ratio, 6) if amplitude_ratio is not None else None,
        "rangeOfMotionPercentOfStandard": round(amplitude_ratio * 100.0, 6) if amplitude_ratio is not None else None,
        "mae": round(safe_mean(abs_deviations), 6),
        "rmse": round(math.sqrt(safe_mean(squared_deviations)), 6),
        "shapeRmseAfterOffsetCorrection": round(math.sqrt(safe_mean(offset_corrected_squared)), 6),
        "maxAbsoluteDeviation": round(max(abs_deviations), 6),
        "meanSignedDeviation": round(mean_signed_deviation, 6),
        "correlation": round(correlation, 6) if correlation is not None else None,
        "outsideStandardBandPercent": round(outside_percent, 6) if outside_percent is not None else None,
    }


def average_aligned_segments(aligned_segments, standard_rows):
    averaged = []
    for point_index, standard_row in enumerate(standard_rows):
        values = [segment[point_index]["angle"] for segment in aligned_segments]
        averaged.append({"percent": standard_row["percent"], "angle": safe_mean(values)})
    return averaged


def median_aligned_segments(aligned_segments, standard_rows):
    averaged = []
    for point_index, standard_row in enumerate(standard_rows):
        values = [segment[point_index]["angle"] for segment in aligned_segments]
        averaged.append({"percent": standard_row["percent"], "angle": median(values)})
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
        patient_rows = median_aligned_segments(aligned_segments, standard_rows)
        segmentation["aggregationMethod"] = "pointwise_median"
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
        return patient_rows, metrics, segmentation, aligned_segments

    patient_rows = align_patient_to_standard(patient_curve, standard_rows, smooth_window)
    metrics = compute_metrics(patient_rows, standard_rows)
    return patient_rows, metrics, segmentation, []


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


def classify_threshold_value(value, normal_max, mild_max, use_absolute=True):
    if value is None:
        return "unclear"
    comparable = abs(value) if use_absolute else value
    if comparable <= normal_max:
        return "normal"
    if comparable <= mild_max:
        return "mild_deviation"
    return "significant_deviation"


def build_component_status(metrics):
    return {
        "overall": classify_status(metrics),
        "shape": classify_threshold_value(
            metrics.get("shapeRmseAfterOffsetCorrection"),
            NORMAL_RMSE_MAX,
            MILD_RMSE_MAX,
            use_absolute=False,
        ),
        "rangeOfMotion": classify_threshold_value(
            metrics.get("amplitudeDifference"),
            NORMAL_AMPLITUDE_DIFF_MAX,
            MILD_AMPLITUDE_DIFF_MAX,
        ),
        "verticalOffset": classify_threshold_value(
            metrics.get("meanSignedDeviation"),
            NORMAL_AMPLITUDE_DIFF_MAX,
            MILD_AMPLITUDE_DIFF_MAX,
        ),
        "standardBand": classify_threshold_value(
            metrics.get("outsideStandardBandPercent"),
            NORMAL_OUTSIDE_BAND_MAX,
            MILD_OUTSIDE_BAND_MAX,
            use_absolute=False,
        ),
    }


def estimate_confidence(action, metrics, segmentation=None):
    if metrics is None:
        return "low"

    confidence_score = 2
    if metrics.get("correlation") is None:
        confidence_score -= 1
    if metrics.get("outsideStandardBandPercent") is None:
        confidence_score -= 1

    if segmentation:
        if segmentation.get("used"):
            used = segmentation.get("segmentsUsed") or 0
            detected = segmentation.get("segmentsDetected") or 0
            rejected = segmentation.get("segmentsRejected") or 0
            if action in ("walking", "squat") and used < 3:
                confidence_score -= 1
            if detected and rejected / detected > 0.4:
                confidence_score -= 1
        elif action in ("walking", "squat") and segmentation.get("fallbackReason"):
            confidence_score -= 1

    if abs(metrics.get("meanSignedDeviation") or 0.0) > 25.0:
        confidence_score -= 1

    if confidence_score >= 2:
        return "high"
    if confidence_score == 1:
        return "medium"
    return "low"


def generate_observations(action, metrics, segmentation=None):
    observations = []
    peak_diff = metrics["peakAngleDifference"]
    amplitude_diff = metrics["amplitudeDifference"]
    rmse = metrics["rmse"]
    shape_rmse = metrics.get("shapeRmseAfterOffsetCorrection", rmse)
    correlation = metrics.get("correlation")
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
        if shape_rmse > NORMAL_RMSE_MAX:
            observations.append("The walking curve differs from the standard curve shape.")
    elif action == "squat":
        if peak_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower peak knee angle may suggest reduced squat depth or reduced knee flexion.")
        if amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower movement amplitude may suggest incomplete squat movement.")
        if shape_rmse > NORMAL_RMSE_MAX:
            observations.append("The squat pattern differs from the healthy standard curve.")
    elif action == "upstairs":
        if peak_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower peak knee angle may suggest reduced knee lift during stair climbing.")
        if amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
            observations.append("Lower movement amplitude may suggest limited movement range.")
        if shape_rmse > NORMAL_RMSE_MAX:
            observations.append("The stair-climbing curve differs from the standard curve shape.")

    if correlation is not None and correlation >= 0.85 and shape_rmse <= MILD_RMSE_MAX and amplitude_diff < -NORMAL_AMPLITUDE_DIFF_MAX:
        observations.append(
            "The curve shape is relatively consistent with the standard, but the range of motion is lower."
        )

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


def generate_clinical_advice_draft(action, status, component_status, metrics):
    action_text = {
        "walking": "walking",
        "squat": "squat",
        "upstairs": "stair-climbing",
    }[action]
    focus_areas = []

    if component_status.get("rangeOfMotion") in ("mild_deviation", "significant_deviation"):
        focus_areas.append("range_of_motion")
    if component_status.get("shape") in ("mild_deviation", "significant_deviation"):
        focus_areas.append("movement_pattern")
    if component_status.get("verticalOffset") in ("mild_deviation", "significant_deviation"):
        focus_areas.append("angle_calibration_or_baseline_offset")
    if component_status.get("standardBand") in ("mild_deviation", "significant_deviation"):
        focus_areas.append("deviation_from_healthy_reference_band")

    if status == "normal":
        review_priority = "routine_review"
        draft_advice = (
            f"The {action_text} curve is close to the current healthy reference. "
            "A clinician may consider continuing the current rehabilitation plan and monitoring symptoms and function over time."
        )
    elif status == "mild_deviation":
        review_priority = "non_urgent_clinical_review"
        draft_advice = (
            f"The {action_text} curve shows mild deviation from the healthy reference. "
            "A clinician or physiotherapist may review pain, swelling, range of motion, strength, and movement technique before increasing activity intensity."
        )
    elif status == "significant_deviation":
        review_priority = "clinical_review_recommended"
        draft_advice = (
            f"The {action_text} curve shows significant deviation from the healthy reference. "
            "A clinician or physiotherapist should review the movement before progression, especially if the patient reports pain, instability, swelling, or reduced function."
        )
    else:
        review_priority = "data_quality_review"
        draft_advice = (
            "The movement analysis is unclear. A clinician should review the raw motion data and repeat the measurement if needed before using this output."
        )

    return {
        "reviewPriority": review_priority,
        "focusAreas": focus_areas,
        "draftAdvice": draft_advice,
        "safetyNote": (
            "This is a draft clinical-support message for qualified review. "
            "It is not a diagnosis, prescription, or standalone treatment plan."
        ),
        "rangeOfMotionPercentOfStandard": metrics.get("rangeOfMotionPercentOfStandard"),
    }


def build_threshold_summary():
    return {
        "validationStatus": "preliminary_engineering_thresholds_not_clinically_validated",
        "normalRmseMax": NORMAL_RMSE_MAX,
        "mildRmseMax": MILD_RMSE_MAX,
        "normalAmplitudeDifferenceMax": NORMAL_AMPLITUDE_DIFF_MAX,
        "mildAmplitudeDifferenceMax": MILD_AMPLITUDE_DIFF_MAX,
        "normalOutsideStandardBandMax": NORMAL_OUTSIDE_BAND_MAX,
        "mildOutsideStandardBandMax": MILD_OUTSIDE_BAND_MAX,
    }


def generate_quality_notes(action, metrics, segmentation=None):
    notes = []

    if abs(metrics.get("meanSignedDeviation") or 0.0) > 25.0:
        notes.append(
            "Large mean signed deviation detected. Check whether patient and standard curves use the same angle convention, calibration, and action definition."
        )
    if abs(metrics.get("peakAngleDifference") or 0.0) > 40.0:
        notes.append(
            "Large peak-angle difference detected. Review the raw curve visually before interpreting this as a movement finding."
        )

    if segmentation and segmentation.get("used"):
        detected = segmentation.get("segmentsDetected") or 0
        rejected = segmentation.get("segmentsRejected") or 0
        if detected and rejected / detected > 0.4:
            notes.append(
                "Many detected segments were rejected by engineering filters. Inspect the raw patient signal and segmentation settings."
            )
        if segmentation.get("segmentsUsed", 0) < 3 and action in ("walking", "squat"):
            notes.append(
                "Few valid segments were available. The comparison may be sensitive to noise or incomplete execution."
            )
    elif segmentation and segmentation.get("fallbackReason"):
        notes.append(
            "The comparison used the full patient curve because segmentation was not available or not appropriate for this action."
        )

    if action == "upstairs":
        notes.append(
            "Upstairs is currently compared as a full stair-climbing action because the current standard curve is full-action based."
        )

    if not notes:
        notes.append("No major engineering data-quality warnings were detected.")
    return notes


def build_output_json(
    action,
    angle_id,
    input_type,
    patient_source,
    standard_source,
    metrics,
    status,
    confidence,
    observations,
    segmentation=None,
    quality_notes=None,
):
    component_status = build_component_status(metrics)
    return {
        "action": action,
        "angleID": angle_id,
        "comparisonVersion": COMPARISON_VERSION,
        "inputType": input_type,
        "patientSource": patient_source,
        "standardSource": standard_source,
        "comparisonMode": "segmented" if segmentation and segmentation.get("used") else "full_curve",
        "segmentation": segmentation,
        "status": status,
        "confidence": confidence,
        "componentStatus": component_status,
        "engineeringThresholds": build_threshold_summary(),
        "metrics": metrics,
        "qualityNotes": quality_notes or [],
        "observations": observations,
        "recommendationText": generate_recommendation_text(action, status, metrics, observations),
        "clinicalAdviceDraft": generate_clinical_advice_draft(action, status, component_status, metrics),
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
        f"Comparison version: {data.get('comparisonVersion')}",
        f"Input type: {data['inputType']}",
        f"Patient source: {data['patientSource']}",
        f"Standard source: {data['standardSource']}",
        f"Comparison mode: {data.get('comparisonMode', 'full_curve')}",
        f"Status: {data['status']}",
        f"Confidence: {data.get('confidence')}",
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
                f"- Aggregation method: {segmentation.get('aggregationMethod')}",
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

    component_status = data.get("componentStatus") or {}
    if component_status:
        lines.extend(
            [
                "Component status:",
                f"- Overall: {component_status.get('overall')}",
                f"- Shape: {component_status.get('shape')}",
                f"- Range of motion: {component_status.get('rangeOfMotion')}",
                f"- Vertical offset: {component_status.get('verticalOffset')}",
                f"- Standard band: {component_status.get('standardBand')}",
                "",
            ]
        )

    lines.extend(
        [
        "Key metrics:",
        f"- Patient peak angle: {metrics['patientPeakAngle']} deg",
        f"- Standard peak angle: {metrics['standardPeakAngle']} deg",
        f"- Peak angle difference: {metrics['peakAngleDifference']} deg",
        f"- Peak timing difference: {metrics.get('peakTimingDifferencePercent')} percentage points",
        f"- Patient min angle: {metrics['patientMinAngle']} deg",
        f"- Standard min angle: {metrics['standardMinAngle']} deg",
        f"- Min angle difference: {metrics['minAngleDifference']} deg",
        f"- Patient amplitude: {metrics['patientAmplitude']} deg",
        f"- Standard amplitude: {metrics['standardAmplitude']} deg",
        f"- Amplitude difference: {metrics['amplitudeDifference']} deg",
        f"- Range of motion percent of standard: {metrics.get('rangeOfMotionPercentOfStandard')}",
        f"- MAE: {metrics['mae']} deg",
        f"- RMSE: {metrics['rmse']} deg",
        f"- Shape RMSE after offset correction: {metrics.get('shapeRmseAfterOffsetCorrection')} deg",
        f"- Max absolute deviation: {metrics['maxAbsoluteDeviation']} deg",
        f"- Mean signed deviation: {metrics['meanSignedDeviation']} deg",
        f"- Correlation: {metrics['correlation']}",
        f"- Outside standard band percent: {metrics['outsideStandardBandPercent']}",
        "",
        "Observations:",
        ]
    )
    lines.extend(f"- {observation}" for observation in data["observations"])
    lines.extend(["", "Quality notes:"])
    lines.extend(f"- {note}" for note in data.get("qualityNotes", []))
    lines.extend(
        [
            "",
            "Recommendation:",
            data["recommendationText"],
            "",
            "Clinical advice draft:",
            data.get("clinicalAdviceDraft", {}).get("draftAdvice", ""),
            f"Review priority: {data.get('clinicalAdviceDraft', {}).get('reviewPriority', '')}",
            f"Focus areas: {', '.join(data.get('clinicalAdviceDraft', {}).get('focusAreas', []))}",
            f"Safety note: {data.get('clinicalAdviceDraft', {}).get('safetyNote', '')}",
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


def format_csv_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value


def write_csv_dicts(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_csv_value(row.get(field)) for field in fieldnames})


def write_average_curve_csv(path, patient_rows, standard_rows):
    rows = []
    for patient_row, standard_row in zip(patient_rows, standard_rows):
        lower = standard_row.get("lower")
        upper = standard_row.get("upper")
        patient_angle = patient_row["angle"]
        outside_band = ""
        if lower is not None and upper is not None:
            outside_band = patient_angle < lower or patient_angle > upper
        rows.append(
            {
                "percent": standard_row["percent"],
                "patient_angle": patient_angle,
                "standard_angle": standard_row["standard_angle"],
                "deviation": patient_angle - standard_row["standard_angle"],
                "standard_lower_band": lower,
                "standard_upper_band": upper,
                "outside_standard_band": outside_band,
            }
        )

    write_csv_dicts(
        path,
        [
            "percent",
            "patient_angle",
            "standard_angle",
            "deviation",
            "standard_lower_band",
            "standard_upper_band",
            "outside_standard_band",
        ],
        rows,
    )


def write_segments_csv(path, data):
    segmentation = data.get("segmentation") or {}
    segments = segmentation.get("segments") or []
    metrics_by_used_index = {
        metric["index"]: metric.get("metrics", {})
        for metric in segmentation.get("segmentMetrics", [])
    }

    rows = []
    for segment in segments:
        used_index = segment.get("usedSegmentIndex")
        metric = metrics_by_used_index.get(used_index, {})
        rows.append(
            {
                "used_segment_index": used_index,
                "detected_segment_index": segment.get("index"),
                "source": segment.get("source"),
                "start_time_seconds": segment.get("startTimeSeconds"),
                "end_time_seconds": segment.get("endTimeSeconds"),
                "duration_seconds": segment.get("durationSeconds"),
                "point_count": segment.get("pointCount"),
                "amplitude_degrees": segment.get("amplitudeDegrees"),
                "peak_angle": segment.get("peakAngle"),
                "status": next(
                    (
                        item.get("status")
                        for item in segmentation.get("segmentMetrics", [])
                        if item.get("index") == used_index
                    ),
                    "",
                ),
                "rmse": metric.get("rmse"),
                "mae": metric.get("mae"),
                "peak_angle_difference": metric.get("peakAngleDifference"),
                "amplitude_difference": metric.get("amplitudeDifference"),
                "outside_standard_band_percent": metric.get("outsideStandardBandPercent"),
            }
        )

    write_csv_dicts(
        path,
        [
            "used_segment_index",
            "detected_segment_index",
            "source",
            "start_time_seconds",
            "end_time_seconds",
            "duration_seconds",
            "point_count",
            "amplitude_degrees",
            "peak_angle",
            "status",
            "rmse",
            "mae",
            "peak_angle_difference",
            "amplitude_difference",
            "outside_standard_band_percent",
        ],
        rows,
    )


def write_metrics_csv(path, data):
    rows = []
    for key, value in data.get("metrics", {}).items():
        rows.append({"section": "metrics", "name": key, "value": value})
    for key, value in (data.get("segmentation", {}).get("segmentMetricSummary") or {}).items():
        rows.append({"section": "segmentMetricSummary", "name": key, "value": value})
    for key, value in data.get("componentStatus", {}).items():
        rows.append({"section": "componentStatus", "name": key, "value": value})
    for key, value in data.get("engineeringThresholds", {}).items():
        rows.append({"section": "engineeringThresholds", "name": key, "value": value})
    rows.append({"section": "classification", "name": "status", "value": data.get("status")})
    rows.append({"section": "classification", "name": "confidence", "value": data.get("confidence")})
    rows.append({"section": "classification", "name": "comparisonMode", "value": data.get("comparisonMode")})
    rows.append({"section": "metadata", "name": "comparisonVersion", "value": data.get("comparisonVersion")})
    write_csv_dicts(path, ["section", "name", "value"], rows)


def chart_points(rows, value_key, x_min, x_max, y_min, y_max, width, height, pad):
    points = []
    x_span = x_max - x_min or 1.0
    y_span = y_max - y_min or 1.0
    for row in rows:
        value = row.get(value_key)
        if value is None:
            continue
        x = pad + ((row["percent"] - x_min) / x_span) * (width - pad * 2)
        y = height - pad - ((value - y_min) / y_span) * (height - pad * 2)
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def band_polygon_points(standard_rows, x_min, x_max, y_min, y_max, width, height, pad):
    upper_rows = [{"percent": row["percent"], "value": row.get("upper")} for row in standard_rows]
    lower_rows = [{"percent": row["percent"], "value": row.get("lower")} for row in reversed(standard_rows)]
    upper = chart_points(upper_rows, "value", x_min, x_max, y_min, y_max, width, height, pad)
    lower = chart_points(lower_rows, "value", x_min, x_max, y_min, y_max, width, height, pad)
    return f"{upper} {lower}".strip()


def svg_axis_labels(x_min, x_max, y_min, y_max, width, height, pad):
    labels = []
    for percent in [0, 25, 50, 75, 100]:
        x = pad + ((percent - x_min) / (x_max - x_min or 1.0)) * (width - pad * 2)
        labels.append(f'<line x1="{x:.2f}" y1="{pad}" x2="{x:.2f}" y2="{height - pad}" class="grid" />')
        labels.append(f'<text x="{x:.2f}" y="{height - 18}" class="tick" text-anchor="middle">{percent}%</text>')
    for value in [y_min, (y_min + y_max) / 2.0, y_max]:
        y = height - pad - ((value - y_min) / (y_max - y_min or 1.0)) * (height - pad * 2)
        labels.append(f'<line x1="{pad}" y1="{y:.2f}" x2="{width - pad}" y2="{y:.2f}" class="grid" />')
        labels.append(f'<text x="{pad - 10}" y="{y + 4:.2f}" class="tick" text-anchor="end">{value:.1f}</text>')
    return "\n".join(labels)


def write_html_report(path, data, patient_rows, standard_rows, aligned_segments):
    width = 1000
    height = 560
    pad = 70
    x_min = 0.0
    x_max = 100.0

    values = [row["angle"] for row in patient_rows] + [row["standard_angle"] for row in standard_rows]
    for row in standard_rows:
        if row.get("lower") is not None:
            values.append(row["lower"])
        if row.get("upper") is not None:
            values.append(row["upper"])
    for segment in aligned_segments:
        values.extend(row["angle"] for row in segment)

    y_min = math.floor((min(values) - 5.0) / 5.0) * 5.0
    y_max = math.ceil((max(values) + 5.0) / 5.0) * 5.0

    patient_plot_rows = [{"percent": row["percent"], "patient": row["angle"]} for row in patient_rows]
    standard_plot_rows = [{"percent": row["percent"], "standard": row["standard_angle"]} for row in standard_rows]
    patient_points = chart_points(patient_plot_rows, "patient", x_min, x_max, y_min, y_max, width, height, pad)
    standard_points = chart_points(standard_plot_rows, "standard", x_min, x_max, y_min, y_max, width, height, pad)
    band_points = band_polygon_points(standard_rows, x_min, x_max, y_min, y_max, width, height, pad)

    segment_lines = []
    for segment in aligned_segments:
        segment_plot_rows = [{"percent": row["percent"], "angle": row["angle"]} for row in segment]
        segment_points = chart_points(segment_plot_rows, "angle", x_min, x_max, y_min, y_max, width, height, pad)
        segment_lines.append(f'<polyline points="{segment_points}" class="segment-line" />')

    metrics = data["metrics"]
    cards = [
        ("Status", data["status"]),
        ("Confidence", data.get("confidence", "unknown")),
        ("Comparison", data.get("comparisonMode", "full_curve")),
        ("RMSE", f"{metrics['rmse']} deg"),
        ("Shape RMSE", f"{metrics.get('shapeRmseAfterOffsetCorrection')} deg"),
        ("Amplitude diff", f"{metrics['amplitudeDifference']} deg"),
        ("Outside band", f"{metrics['outsideStandardBandPercent']}%"),
    ]
    card_html = "\n".join(
        f'<div class="card"><div class="label">{escape(label)}</div><div class="value">{escape(str(value))}</div></div>'
        for label, value in cards
    )
    observation_html = "\n".join(f"<li>{escape(item)}</li>" for item in data.get("observations", []))
    quality_html = "\n".join(f"<li>{escape(item)}</li>" for item in data.get("qualityNotes", []))

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Patient vs Standard Curve - {escape(data['action'])}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #172033; background: #f5f7fb; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    .subtitle {{ margin: 0 0 22px; color: #526176; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .card {{ background: #fff; border: 1px solid #d9e2ee; border-radius: 8px; padding: 16px; }}
    .label {{ color: #607086; font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-weight: 700; font-size: 20px; }}
    .panel {{ background: #fff; border: 1px solid #d9e2ee; border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .legend {{ display: flex; gap: 20px; flex-wrap: wrap; margin: 8px 0 16px; color: #33445c; }}
    .key {{ display: inline-flex; align-items: center; gap: 8px; }}
    .swatch {{ width: 28px; height: 4px; display: inline-block; }}
    svg {{ width: 100%; height: auto; display: block; background: #fff; }}
    .grid {{ stroke: #e5eaf1; stroke-width: 1; }}
    .axis {{ stroke: #a9b6c8; stroke-width: 1.2; }}
    .tick {{ fill: #526176; font-size: 12px; }}
    .band {{ fill: #8ec5ff; opacity: 0.22; }}
    .segment-line {{ fill: none; stroke: #7d8796; stroke-width: 1.1; opacity: 0.22; }}
    .standard-line {{ fill: none; stroke: #d64550; stroke-width: 4; }}
    .patient-line {{ fill: none; stroke: #0b6da8; stroke-width: 4; }}
    .section-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    li {{ margin-bottom: 8px; }}
    @media (max-width: 900px) {{ .cards, .section-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>Patient vs Standard Curve: {escape(data['action'])} / {escape(data['angleID'])}</h1>
  <p class="subtitle">AI-assisted motion analysis. This is not a medical diagnosis and should be reviewed by a doctor.</p>
  <section class="cards">{card_html}</section>
  <section class="panel">
    <div class="legend">
      <span class="key"><span class="swatch" style="background:#d64550"></span>Standard curve</span>
      <span class="key"><span class="swatch" style="background:#0b6da8"></span>Patient average</span>
      <span class="key"><span class="swatch" style="background:#8ec5ff"></span>Standard band</span>
      <span class="key"><span class="swatch" style="background:#7d8796"></span>Patient segments</span>
    </div>
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="Patient curve compared with standard curve">
      {svg_axis_labels(x_min, x_max, y_min, y_max, width, height, pad)}
      <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" class="axis" />
      <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" class="axis" />
      <text x="{width / 2}" y="{height - 8}" class="tick" text-anchor="middle">Normalized action progress (%)</text>
      <text transform="translate(20 {height / 2}) rotate(-90)" class="tick" text-anchor="middle">Angle (deg)</text>
      <polygon points="{band_points}" class="band" />
      {"".join(segment_lines)}
      <polyline points="{standard_points}" class="standard-line" />
      <polyline points="{patient_points}" class="patient-line" />
    </svg>
  </section>
  <section class="section-grid">
    <div class="panel">
      <h2>Observations</h2>
      <ul>{observation_html}</ul>
    </div>
    <div class="panel">
      <h2>Quality Notes</h2>
      <ul>{quality_html}</ul>
    </div>
  </section>
  <section class="panel">
    <h2>Recommendation</h2>
    <p>{escape(data['recommendationText'])}</p>
  </section>
</main>
</body>
</html>
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


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
        patient_rows, metrics, segmentation, aligned_segments = compare_patient_to_standard(
            patient_curve,
            standard_rows,
            args.action,
            args.segment_patient,
            args.smooth_window,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: failed to compare patient curve: {exc}")

    status = classify_status(metrics)
    confidence = estimate_confidence(args.action, metrics, segmentation)
    observations = generate_observations(args.action, metrics, segmentation)
    quality_notes = generate_quality_notes(args.action, metrics, segmentation)
    output = build_output_json(
        args.action,
        args.angle_id,
        input_type,
        patient_source,
        args.standard_csv,
        metrics,
        status,
        confidence,
        observations,
        segmentation,
        quality_notes,
    )

    write_json(args.out_json, output)
    write_txt(args.out_txt, output)
    if args.out_html:
        write_html_report(args.out_html, output, patient_rows, standard_rows, aligned_segments)
    if args.out_average_csv:
        write_average_curve_csv(args.out_average_csv, patient_rows, standard_rows)
    if args.out_segments_csv:
        write_segments_csv(args.out_segments_csv, output)
    if args.out_metrics_csv:
        write_metrics_csv(args.out_metrics_csv, output)

    print("Done.")
    print(f"Action: {args.action}")
    print(f"Comparison mode: {output['comparisonMode']}")
    if segmentation:
        print(f"Segments used: {segmentation.get('segmentsUsed', 0)}")
        if segmentation.get("fallbackReason"):
            print(f"Segmentation fallback: {segmentation['fallbackReason']}")
    print(f"Status: {status}")
    print(f"Confidence: {confidence}")
    print(f"RMSE: {metrics['rmse']} deg")
    print(f"Amplitude difference: {metrics['amplitudeDifference']} deg")
    print(f"Outside standard band percent: {metrics['outsideStandardBandPercent']}")
    print(f"JSON: {args.out_json}")
    print(f"TXT: {args.out_txt}")
    if args.out_html:
        print(f"HTML: {args.out_html}")
    if args.out_average_csv:
        print(f"Average CSV: {args.out_average_csv}")
    if args.out_segments_csv:
        print(f"Segments CSV: {args.out_segments_csv}")
    if args.out_metrics_csv:
        print(f"Metrics CSV: {args.out_metrics_csv}")


if __name__ == "__main__":
    main()
