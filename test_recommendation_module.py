import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import generate_recommendation_from_curves as rec


STANDARD_POINTS = [
    (0.0, 10.0),
    (25.0, 20.0),
    (50.0, 60.0),
    (75.0, 20.0),
    (100.0, 10.0),
]


def write_standard_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["percent", "mean_angle", "smooth_angle", "sd_angle", "mean_minus_sd", "mean_plus_sd"],
        )
        writer.writeheader()
        for percent, angle in STANDARD_POINTS:
            writer.writerow(
                {
                    "percent": percent,
                    "mean_angle": angle,
                    "smooth_angle": angle,
                    "sd_angle": 5.0,
                    "mean_minus_sd": angle - 5.0,
                    "mean_plus_sd": angle + 5.0,
                }
            )


def write_patient_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["percent", "patient_angle"])
        writer.writeheader()
        for percent, angle in STANDARD_POINTS:
            writer.writerow({"percent": percent, "patient_angle": angle})


def synthetic_walking_curve(repetitions=6):
    values = [60.0, 20.0, 10.0, 20.0, 60.0] * repetitions
    return [
        {"time_seconds": index * 0.2, "angle": angle, "percent": None}
        for index, angle in enumerate(values)
    ]


def synthetic_low_peak_walking_curve(repetitions=6):
    values = [20.0, 0.0, -10.0, 0.0, 20.0] * repetitions
    return [
        {"time_seconds": index * 0.2, "angle": angle, "percent": None}
        for index, angle in enumerate(values)
    ]


class RecommendationModuleTests(unittest.TestCase):
    def test_full_curve_comparison_outputs_version_and_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard_path = Path(tmp) / "standard.csv"
            patient_path = Path(tmp) / "patient.csv"
            write_standard_csv(standard_path)
            write_patient_csv(patient_path)

            standard_rows = rec.load_standard_curve(str(standard_path))
            patient_curve = rec.load_patient_curve_from_csv(str(patient_path))
            patient_rows, metrics, segmentation, aligned_segments = rec.compare_patient_to_standard(
                patient_curve,
                standard_rows,
                "upstairs",
                "never",
                smooth_window=1,
            )

            status = rec.classify_status(metrics)
            confidence = rec.estimate_confidence("upstairs", metrics, segmentation)
            output = rec.build_output_json(
                "upstairs",
                "left_knee",
                "csv",
                str(patient_path),
                str(standard_path),
                metrics,
                status,
                confidence,
                rec.generate_observations("upstairs", metrics, segmentation),
                segmentation,
                rec.generate_quality_notes("upstairs", metrics, segmentation),
            )

            self.assertEqual(status, "normal")
            self.assertEqual(confidence, "high")
            self.assertEqual(output["comparisonVersion"], rec.COMPARISON_VERSION)
            self.assertEqual(output["confidence"], "high")
            self.assertEqual(output["componentStatus"]["overall"], "normal")
            self.assertEqual(output["comparisonMode"], "full_curve")
            self.assertEqual(len(patient_rows), len(standard_rows))
            self.assertEqual(aligned_segments, [])

    def test_walking_segmentation_detects_cycles(self):
        standard_rows = [
            {
                "percent": percent,
                "standard_angle": angle,
                "mean_angle": angle,
                "sd_angle": 5.0,
                "lower": angle - 5.0,
                "upper": angle + 5.0,
            }
            for percent, angle in STANDARD_POINTS
        ]

        patient_rows, metrics, segmentation, aligned_segments = rec.compare_patient_to_standard(
            synthetic_walking_curve(),
            standard_rows,
            "walking",
            "auto",
            smooth_window=1,
        )

        self.assertEqual(segmentation["method"], "local_minima_cycles")
        self.assertTrue(segmentation["used"])
        self.assertGreaterEqual(segmentation["segmentsUsed"], 3)
        self.assertEqual(len(aligned_segments), segmentation["segmentsUsed"])
        self.assertEqual(len(patient_rows), len(standard_rows))
        self.assertLess(metrics["rmse"], rec.NORMAL_RMSE_MAX)

    def test_walking_segmentation_keeps_low_peak_patient_cycles(self):
        standard_rows = [
            {
                "percent": percent,
                "standard_angle": angle,
                "mean_angle": angle,
                "sd_angle": 5.0,
                "lower": angle - 5.0,
                "upper": angle + 5.0,
            }
            for percent, angle in STANDARD_POINTS
        ]

        _patient_rows, _metrics, segmentation, aligned_segments = rec.compare_patient_to_standard(
            synthetic_low_peak_walking_curve(),
            standard_rows,
            "walking",
            "auto",
            smooth_window=1,
        )

        self.assertTrue(segmentation["used"])
        self.assertGreaterEqual(segmentation["segmentsUsed"], 3)
        self.assertNotIn("peak_angle_too_low", segmentation["rejectedReasonCounts"])
        self.assertEqual(len(aligned_segments), segmentation["segmentsUsed"])

    def test_segment_aggregation_uses_pointwise_median(self):
        standard_rows = [
            {"percent": percent, "standard_angle": angle}
            for percent, angle in STANDARD_POINTS
        ]
        aligned_segments = [
            [{"percent": percent, "angle": angle} for percent, angle in STANDARD_POINTS],
            [{"percent": percent, "angle": angle} for percent, angle in STANDARD_POINTS],
            [{"percent": percent, "angle": angle + 40.0} for percent, angle in STANDARD_POINTS],
        ]

        patient_rows = rec.median_aligned_segments(aligned_segments, standard_rows)

        self.assertEqual([row["angle"] for row in patient_rows], [angle for _percent, angle in STANDARD_POINTS])

    def test_cli_writes_optional_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            standard_path = tmp_path / "standard.csv"
            patient_path = tmp_path / "patient.csv"
            out_json = tmp_path / "result.json"
            out_txt = tmp_path / "result.txt"
            out_html = tmp_path / "result.html"
            out_average = tmp_path / "average.csv"
            out_segments = tmp_path / "segments.csv"
            out_metrics = tmp_path / "metrics.csv"
            write_standard_csv(standard_path)
            write_patient_csv(patient_path)

            script_path = Path(__file__).resolve().with_name("generate_recommendation_from_curves.py")
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--action",
                    "upstairs",
                    "--patient-csv",
                    str(patient_path),
                    "--standard-csv",
                    str(standard_path),
                    "--segment-patient",
                    "never",
                    "--out-json",
                    str(out_json),
                    "--out-txt",
                    str(out_txt),
                    "--out-html",
                    str(out_html),
                    "--out-average-csv",
                    str(out_average),
                    "--out-segments-csv",
                    str(out_segments),
                    "--out-metrics-csv",
                    str(out_metrics),
                    "--smooth-window",
                    "1",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Done.", result.stdout)
            for path in [out_json, out_txt, out_html, out_average, out_segments, out_metrics]:
                self.assertTrue(path.exists(), f"missing output: {path}")

            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["comparisonVersion"], rec.COMPARISON_VERSION)
            self.assertEqual(payload["confidence"], "high")
            self.assertIn("<svg", out_html.read_text(encoding="utf-8"))
            self.assertIn("comparisonVersion", out_metrics.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
