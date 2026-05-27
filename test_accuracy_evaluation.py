import csv
import json
import tempfile
import unittest
from pathlib import Path

import evaluate_recommendation_accuracy as evaluator


class AccuracyEvaluationTests(unittest.TestCase):
    def test_status_to_severity_mapping(self):
        self.assertEqual(evaluator.status_to_severity("normal"), "normal")
        self.assertEqual(evaluator.status_to_severity("mild_deviation"), "mild")
        self.assertEqual(evaluator.status_to_severity("significant_deviation"), "severe")
        self.assertIsNone(evaluator.status_to_severity("unclear"))

    def test_read_labeled_sessions_validates_and_normalizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["action", "session_id", "severity_label", "injury_location", "notes"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "action": "Walking",
                        "session_id": "209",
                        "severity_label": "Severe",
                        "injury_location": "Knee",
                        "notes": "validated",
                    }
                )

            rows = evaluator.read_labeled_sessions(str(path))

            self.assertEqual(
                rows,
                [
                    {
                        "action": "walking",
                        "session_id": 209,
                        "severity_label": "severe",
                        "injury_location": "knee",
                        "notes": "validated",
                    }
                ],
            )

    def test_summarize_accuracy_excludes_failed_and_unclear(self):
        rows = [
            {
                "action": "walking",
                "expected_severity": "normal",
                "predicted_severity": "normal",
                "prediction_status": "processed",
                "correct": "true",
            },
            {
                "action": "walking",
                "expected_severity": "mild",
                "predicted_severity": "severe",
                "prediction_status": "processed",
                "correct": "false",
            },
            {
                "action": "squat",
                "expected_severity": "severe",
                "predicted_severity": "",
                "prediction_status": "unclear",
                "correct": "",
            },
            {
                "action": "upstairs",
                "expected_severity": "mild",
                "predicted_severity": "",
                "prediction_status": "failed",
                "correct": "",
            },
        ]

        summary = evaluator.summarize_accuracy(rows)

        self.assertEqual(summary["totalSessions"], 4)
        self.assertEqual(summary["processedSessions"], 2)
        self.assertEqual(summary["failedSessions"], 1)
        self.assertEqual(summary["unclearSessions"], 1)
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(summary["confusionMatrix"]["normal"]["normal"], 1)
        self.assertEqual(summary["confusionMatrix"]["mild"]["severe"], 1)
        self.assertEqual(summary["perAction"]["walking"]["accuracy"], 0.5)

    def test_writes_report_files(self):
        rows = [
            {
                "action": "walking",
                "session_id": 209,
                "expected_severity": "severe",
                "predicted_severity": "severe",
                "predicted_status": "significant_deviation",
                "correct": "true",
                "confidence": "medium",
                "comparison_mode": "segmented",
                "component_shape": "mild_deviation",
                "component_range_of_motion": "significant_deviation",
                "component_standard_band": "significant_deviation",
                "rmse": 18.2,
                "shape_rmse": 12.1,
                "amplitude_difference": -24.0,
                "outside_standard_band_percent": 83.0,
                "injury_location": "knee",
                "notes": "",
                "prediction_status": "processed",
                "error_message": "",
            }
        ]
        summary = evaluator.summarize_accuracy(rows)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            evaluator.write_json(str(out_dir / "accuracy_report.json"), {"summary": summary, "sessions": rows})
            evaluator.write_csv(str(out_dir / "accuracy_report.csv"), evaluator.DETAIL_FIELDNAMES, rows)
            evaluator.write_confusion_matrix_csv(str(out_dir / "confusion_matrix.csv"), summary["confusionMatrix"])
            evaluator.write_markdown_report(str(out_dir / "accuracy_report.md"), summary, rows)

            self.assertTrue((out_dir / "accuracy_report.json").exists())
            self.assertTrue((out_dir / "accuracy_report.csv").exists())
            self.assertTrue((out_dir / "confusion_matrix.csv").exists())
            self.assertTrue((out_dir / "accuracy_report.md").exists())
            payload = json.loads((out_dir / "accuracy_report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
