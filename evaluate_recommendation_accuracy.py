import argparse
import csv
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError

import generate_recommendation_from_curves as rec


SEVERITY_LABELS = ["normal", "mild", "severe"]
STATUS_TO_SEVERITY = {
    "normal": "normal",
    "mild_deviation": "mild",
    "significant_deviation": "severe",
}
DEFAULT_STANDARD_CSVS = {
    "walking": "outputs/walking/normal_knee_curve.csv",
    "upstairs": "outputs/upstairs/standard_upstairs_curve.csv",
    "squat": "outputs/squat/standard_squat_curve.csv",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate recommendation severity accuracy using labeled AuCloud sessions."
    )
    parser.add_argument("--labels-csv", required=True, help="CSV with action, session_id, severity_label columns.")
    parser.add_argument("--out-dir", default="outputs/accuracy_analysis")
    parser.add_argument("--base-url", default=rec.DEFAULT_BASE_URL)
    parser.add_argument("--angle-id", default="left_knee")
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument("--segment-patient", choices=["auto", "always", "never"], default="auto")
    parser.add_argument("--standard-walking-csv", default=DEFAULT_STANDARD_CSVS["walking"])
    parser.add_argument("--standard-upstairs-csv", default=DEFAULT_STANDARD_CSVS["upstairs"])
    parser.add_argument("--standard-squat-csv", default=DEFAULT_STANDARD_CSVS["squat"])
    return parser.parse_args()


def normalize_label(value):
    return (value or "").strip().lower()


def status_to_severity(status):
    return STATUS_TO_SEVERITY.get(status)


def read_labeled_sessions(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"action", "session_id", "severity_label"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Labels CSV missing required columns: {', '.join(sorted(missing))}")

        rows = []
        for line_number, row in enumerate(reader, start=2):
            action = normalize_label(row.get("action"))
            severity = normalize_label(row.get("severity_label"))
            session_id_text = (row.get("session_id") or "").strip()

            if not any((value or "").strip() for value in row.values()):
                continue
            if action not in DEFAULT_STANDARD_CSVS:
                raise ValueError(f"Line {line_number}: unsupported action '{action}'")
            if severity not in SEVERITY_LABELS:
                raise ValueError(f"Line {line_number}: unsupported severity_label '{severity}'")
            try:
                session_id = int(session_id_text)
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: invalid session_id '{session_id_text}'") from exc

            rows.append(
                {
                    "action": action,
                    "session_id": session_id,
                    "severity_label": severity,
                    "injury_location": normalize_label(row.get("injury_location")) or "unknown",
                    "notes": (row.get("notes") or "").strip(),
                }
            )
    return rows


def standard_csv_map_from_args(args):
    return {
        "walking": args.standard_walking_csv,
        "upstairs": args.standard_upstairs_csv,
        "squat": args.standard_squat_csv,
    }


def load_standard_cache(standard_csvs):
    cache = {}
    for action, path in standard_csvs.items():
        cache[action] = {
            "path": path,
            "rows": rec.load_standard_curve(path),
        }
    return cache


def evaluate_labeled_session(label_row, standard_cache, args):
    action = label_row["action"]
    session_id = label_row["session_id"]
    result = {
        "action": action,
        "session_id": session_id,
        "expected_severity": label_row["severity_label"],
        "injury_location": label_row.get("injury_location", "unknown"),
        "notes": label_row.get("notes", ""),
        "prediction_status": "not_started",
        "predicted_status": "",
        "predicted_severity": "",
        "correct": "",
        "confidence": "",
        "comparison_mode": "",
        "component_shape": "",
        "component_range_of_motion": "",
        "component_standard_band": "",
        "rmse": "",
        "shape_rmse": "",
        "amplitude_difference": "",
        "outside_standard_band_percent": "",
        "error_message": "",
    }

    try:
        patient_curve = rec.load_patient_curve_from_api(args.base_url, session_id, args.angle_id)
        patient_rows, metrics, segmentation, _aligned_segments = rec.compare_patient_to_standard(
            patient_curve,
            standard_cache[action]["rows"],
            action,
            args.segment_patient,
            args.smooth_window,
        )
        del patient_rows
        status = rec.classify_status(metrics)
        predicted_severity = status_to_severity(status)
        component_status = rec.build_component_status(metrics)
        result.update(
            {
                "prediction_status": "processed" if predicted_severity else "unclear",
                "predicted_status": status,
                "predicted_severity": predicted_severity or "",
                "correct": str(predicted_severity == label_row["severity_label"]).lower()
                if predicted_severity
                else "",
                "confidence": rec.estimate_confidence(action, metrics, segmentation),
                "comparison_mode": "segmented" if segmentation and segmentation.get("used") else "full_curve",
                "component_shape": component_status.get("shape", ""),
                "component_range_of_motion": component_status.get("rangeOfMotion", ""),
                "component_standard_band": component_status.get("standardBand", ""),
                "rmse": metrics.get("rmse", ""),
                "shape_rmse": metrics.get("shapeRmseAfterOffsetCorrection", ""),
                "amplitude_difference": metrics.get("amplitudeDifference", ""),
                "outside_standard_band_percent": metrics.get("outsideStandardBandPercent", ""),
            }
        )
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        result["prediction_status"] = "failed"
        result["error_message"] = f"{type(exc).__name__}: {exc}"
    return result


def empty_confusion_matrix():
    return {
        expected: {predicted: 0 for predicted in SEVERITY_LABELS}
        for expected in SEVERITY_LABELS
    }


def build_confusion_matrix(rows):
    matrix = empty_confusion_matrix()
    for row in rows:
        expected = row.get("expected_severity")
        predicted = row.get("predicted_severity")
        if expected in SEVERITY_LABELS and predicted in SEVERITY_LABELS:
            matrix[expected][predicted] += 1
    return matrix


def safe_divide(numerator, denominator):
    return numerator / denominator if denominator else None


def round_or_none(value):
    return round(value, 6) if value is not None else None


def classification_metrics(matrix):
    by_label = {}
    f1_values = []
    precision_values = []
    recall_values = []

    for label in SEVERITY_LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[expected][label] for expected in SEVERITY_LABELS if expected != label)
        fn = sum(matrix[label][predicted] for predicted in SEVERITY_LABELS if predicted != label)
        precision = safe_divide(tp, tp + fp)
        recall = safe_divide(tp, tp + fn)
        f1 = safe_divide(2 * precision * recall, precision + recall) if precision is not None and recall is not None else None
        by_label[label] = {
            "precision": round_or_none(precision),
            "recall": round_or_none(recall),
            "f1": round_or_none(f1),
            "support": sum(matrix[label].values()),
        }
        if precision is not None:
            precision_values.append(precision)
        if recall is not None:
            recall_values.append(recall)
        if f1 is not None:
            f1_values.append(f1)

    return {
        "byLabel": by_label,
        "macroPrecision": round_or_none(sum(precision_values) / len(precision_values) if precision_values else None),
        "macroRecall": round_or_none(sum(recall_values) / len(recall_values) if recall_values else None),
        "macroF1": round_or_none(sum(f1_values) / len(f1_values) if f1_values else None),
    }


def summarize_accuracy(rows):
    processed = [row for row in rows if row.get("prediction_status") == "processed"]
    correct = [row for row in processed if row.get("correct") == "true"]
    failed = [row for row in rows if row.get("prediction_status") == "failed"]
    unclear = [row for row in rows if row.get("prediction_status") == "unclear"]
    matrix = build_confusion_matrix(processed)

    per_action = {}
    for action in sorted({row["action"] for row in rows}):
        action_rows = [row for row in rows if row["action"] == action]
        action_processed = [row for row in action_rows if row.get("prediction_status") == "processed"]
        action_correct = [row for row in action_processed if row.get("correct") == "true"]
        per_action[action] = {
            "total": len(action_rows),
            "processed": len(action_processed),
            "failed": sum(1 for row in action_rows if row.get("prediction_status") == "failed"),
            "unclear": sum(1 for row in action_rows if row.get("prediction_status") == "unclear"),
            "accuracy": round_or_none(safe_divide(len(action_correct), len(action_processed))),
        }

    return {
        "comparisonVersion": rec.COMPARISON_VERSION,
        "accuracyTarget": "severity",
        "labels": SEVERITY_LABELS,
        "totalSessions": len(rows),
        "processedSessions": len(processed),
        "failedSessions": len(failed),
        "unclearSessions": len(unclear),
        "correctPredictions": len(correct),
        "accuracy": round_or_none(safe_divide(len(correct), len(processed))),
        "perAction": per_action,
        "classificationMetrics": classification_metrics(matrix),
        "confusionMatrix": matrix,
    }


def format_csv_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value


def write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_csv_value(row.get(field, "")) for field in fieldnames})


def write_confusion_matrix_csv(path, matrix):
    rows = []
    for expected in SEVERITY_LABELS:
        row = {"expected": expected}
        for predicted in SEVERITY_LABELS:
            row[f"predicted_{predicted}"] = matrix[expected][predicted]
        rows.append(row)
    write_csv(path, ["expected"] + [f"predicted_{label}" for label in SEVERITY_LABELS], rows)


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def markdown_table(rows):
    headers = ["action", "session_id", "expected_severity", "predicted_severity", "correct", "confidence", "error_message"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        values = []
        for header in headers:
            value = str(format_csv_value(row.get(header, ""))).replace("|", "\\|").replace("\n", " ")
            values.append(value)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_markdown_report(path, summary, rows):
    accuracy = summary["accuracy"]
    accuracy_text = f"{accuracy * 100:.2f}%" if accuracy is not None else "N/A"
    lines = [
        "# Recommendation Severity Accuracy Report",
        "",
        f"- Comparison version: `{summary['comparisonVersion']}`",
        "- Accuracy target: severity (`normal`, `mild`, `severe`)",
        f"- Total sessions: {summary['totalSessions']}",
        f"- Processed sessions: {summary['processedSessions']}",
        f"- Failed sessions: {summary['failedSessions']}",
        f"- Unclear sessions: {summary['unclearSessions']}",
        f"- Correct predictions: {summary['correctPredictions']}",
        f"- Accuracy: {accuracy_text}",
        f"- Macro F1: {summary['classificationMetrics']['macroF1']}",
        "",
        "Unclear outputs are excluded from accuracy and reported separately. Failed API or comparison sessions are also excluded from accuracy.",
        "",
        "## Per-Session Results",
        "",
        markdown_table(rows),
        "",
    ]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


DETAIL_FIELDNAMES = [
    "action",
    "session_id",
    "expected_severity",
    "predicted_severity",
    "predicted_status",
    "correct",
    "confidence",
    "comparison_mode",
    "component_shape",
    "component_range_of_motion",
    "component_standard_band",
    "rmse",
    "shape_rmse",
    "amplitude_difference",
    "outside_standard_band_percent",
    "injury_location",
    "notes",
    "prediction_status",
    "error_message",
]


def evaluate_labels(label_rows, standard_cache, args):
    return [evaluate_labeled_session(row, standard_cache, args) for row in label_rows]


def main():
    args = parse_args()
    standard_cache = load_standard_cache(standard_csv_map_from_args(args))
    label_rows = read_labeled_sessions(args.labels_csv)
    result_rows = evaluate_labels(label_rows, standard_cache, args)
    summary = summarize_accuracy(result_rows)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(str(out_dir / "accuracy_report.json"), {"summary": summary, "sessions": result_rows})
    write_csv(str(out_dir / "accuracy_report.csv"), DETAIL_FIELDNAMES, result_rows)
    write_confusion_matrix_csv(str(out_dir / "confusion_matrix.csv"), summary["confusionMatrix"])
    write_markdown_report(str(out_dir / "accuracy_report.md"), summary, result_rows)

    accuracy = summary["accuracy"]
    accuracy_text = f"{accuracy * 100:.2f}%" if accuracy is not None else "N/A"
    print("Done.")
    print(f"Total sessions: {summary['totalSessions']}")
    print(f"Processed sessions: {summary['processedSessions']}")
    print(f"Failed sessions: {summary['failedSessions']}")
    print(f"Unclear sessions: {summary['unclearSessions']}")
    print(f"Accuracy: {accuracy_text}")
    print(f"Report: {out_dir / 'accuracy_report.md'}")


if __name__ == "__main__":
    main()
