import argparse
import json
import os
from datetime import datetime, timezone
from html import escape
from urllib.error import HTTPError, URLError

from Walk import (
    build_session_curve,
    fetch_json,
    interpolate_linear,
    moving_average,
    normalize_response_items,
    percentile,
    sample_std,
    smooth_curve,
    write_csv,
)


def parse_id_list(value):
    ids = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch squat knee-angle measurements and fit a standard squat curve."
    )
    parser.add_argument("--base-url", default="http://113.44.220.94:3000/measurements")
    parser.add_argument(
        "--session-ids",
        default="116,118",
        help="Comma-separated squat session IDs. Defaults to the known squat sessions.",
    )
    parser.add_argument(
        "--ignored-session-ids",
        default="117,119",
        help="Comma-separated session IDs that must not be processed.",
    )
    parser.add_argument("--angle-id", default="left_knee")
    parser.add_argument("--grid-points", type=int, default=101)
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument(
        "--event-smooth-window",
        type=int,
        default=5,
        help="Moving-average window used before detecting repetition boundary events.",
    )
    parser.add_argument(
        "--event-type",
        choices=["peak_centered", "minima", "maxima"],
        default="peak_centered",
        help=(
            "Segmentation method. peak_centered uses strong squat peaks as repetition centers "
            "and valleys/session edges as boundaries."
        ),
    )
    parser.add_argument(
        "--boundary-angle-margin",
        type=float,
        default=20.0,
        help="For peak_centered mode, accept session start/end as repetition boundaries when they are close to the session minimum.",
    )
    parser.add_argument(
        "--min-repetition-seconds",
        type=float,
        default=1.5,
        help="Repetitions shorter than this are ignored.",
    )
    parser.add_argument(
        "--max-repetition-seconds",
        type=float,
        default=4.0,
        help="Repetitions longer than this are ignored.",
    )
    parser.add_argument(
        "--min-repetition-amplitude",
        type=float,
        default=50.0,
        help="Minimum angle range inside one squat repetition.",
    )
    parser.add_argument(
        "--min-peak-angle",
        type=float,
        default=80.0,
        help="Minimum max knee angle for minima-to-minima squat repetitions.",
    )
    parser.add_argument(
        "--peak-window-start",
        type=float,
        default=20.0,
        help="Earliest allowed peak position inside a repetition, in percent.",
    )
    parser.add_argument(
        "--peak-window-end",
        type=float,
        default=80.0,
        help="Latest allowed peak position inside a repetition, in percent.",
    )
    parser.add_argument(
        "--duration-outlier-iqr",
        type=float,
        default=1.5,
        help="IQR multiplier for excluding extreme repetition durations before averaging.",
    )
    parser.add_argument(
        "--reference-repetitions",
        type=int,
        default=3,
        help="How many fitted squat repetitions to repeat in the standard reference curve.",
    )
    parser.add_argument(
        "--comparison-max-repetitions",
        type=int,
        default=0,
        help="Maximum raw repetitions to draw in comparison HTML. 0 means draw all repetitions.",
    )
    parser.add_argument(
        "--trim-edge-repetitions",
        type=int,
        default=0,
        help="Remove this many valid repetitions from both start and end of each session.",
    )
    parser.add_argument("--out-dir", default="output_squat_python")
    return parser.parse_args()


def find_local_events(curve, smooth_window, min_distance_seconds, event_type):
    smoothed = smooth_curve(curve, smooth_window)
    candidates = []

    for i in range(1, len(smoothed) - 1):
        prev_angle = smoothed[i - 1][1]
        angle = smoothed[i][1]
        next_angle = smoothed[i + 1][1]
        if event_type == "minima" and angle <= prev_angle and angle <= next_angle:
            candidates.append(i)
        elif event_type == "maxima" and angle >= prev_angle and angle >= next_angle:
            candidates.append(i)

    if not candidates:
        return []

    min_distance_ms = min_distance_seconds * 1000.0
    selected = []

    for idx in candidates:
        if not selected:
            selected.append(idx)
            continue

        last_idx = selected[-1]
        too_close = curve[idx][0] - curve[last_idx][0] < min_distance_ms
        if too_close:
            if event_type == "minima" and smoothed[idx][1] < smoothed[last_idx][1]:
                selected[-1] = idx
            elif event_type == "maxima" and smoothed[idx][1] > smoothed[last_idx][1]:
                selected[-1] = idx
        else:
            selected.append(idx)

    return selected


def extract_repetitions_from_events(
    curve,
    event_indexes,
    min_repetition_seconds,
    max_repetition_seconds,
    min_repetition_amplitude,
):
    repetitions = []

    for repetition_no, (start_idx, end_idx) in enumerate(
        zip(event_indexes, event_indexes[1:]), start=1
    ):
        segment = curve[start_idx : end_idx + 1]
        if len(segment) < 5:
            continue

        duration_seconds = (segment[-1][0] - segment[0][0]) / 1000.0
        if (
            duration_seconds < min_repetition_seconds
            or duration_seconds > max_repetition_seconds
        ):
            continue

        angles = [angle for _, angle in segment]
        max_angle = max(angles)
        min_angle = min(angles)
        amplitude = max_angle - min_angle
        if amplitude < min_repetition_amplitude:
            continue

        repetitions.append(
            {
                "repetition_no": repetition_no,
                "points": segment,
                "duration_seconds": duration_seconds,
                "start_angle": segment[0][1],
                "end_angle": segment[-1][1],
                "min_angle": min_angle,
                "max_angle": max_angle,
                "amplitude": amplitude,
            }
        )

    return repetitions


def extract_peak_centered_repetitions(
    curve,
    min_repetition_seconds,
    max_repetition_seconds,
    min_repetition_amplitude,
    min_peak_angle,
    boundary_angle_margin,
    smooth_window,
    min_distance_seconds,
):
    valleys = find_local_events(
        curve,
        smooth_window=smooth_window,
        min_distance_seconds=min_distance_seconds,
        event_type="minima",
    )
    peaks = find_local_events(
        curve,
        smooth_window=smooth_window,
        min_distance_seconds=min_distance_seconds,
        event_type="maxima",
    )
    smoothed = smooth_curve(curve, smooth_window)
    global_min = min(angle for _, angle in curve)

    boundaries = list(valleys)
    if curve[0][1] <= global_min + boundary_angle_margin:
        boundaries.append(0)
    if curve[-1][1] <= global_min + boundary_angle_margin:
        boundaries.append(len(curve) - 1)
    boundaries = sorted(set(boundaries))

    strong_peaks = [idx for idx in peaks if smoothed[idx][1] >= min_peak_angle]
    repetitions = []
    used_pairs = set()

    for peak_idx in strong_peaks:
        left_candidates = [idx for idx in boundaries if idx < peak_idx]
        right_candidates = [idx for idx in boundaries if idx > peak_idx]
        if not left_candidates or not right_candidates:
            continue

        start_idx = left_candidates[-1]
        end_idx = right_candidates[0]
        pair = (start_idx, end_idx)
        if pair in used_pairs:
            continue

        segment = curve[start_idx : end_idx + 1]
        if len(segment) < 5:
            continue

        duration_seconds = (segment[-1][0] - segment[0][0]) / 1000.0
        if (
            duration_seconds < min_repetition_seconds
            or duration_seconds > max_repetition_seconds
        ):
            continue

        angles = [angle for _, angle in segment]
        max_angle = max(angles)
        min_angle = min(angles)
        amplitude = max_angle - min_angle
        if amplitude < min_repetition_amplitude:
            continue

        used_pairs.add(pair)
        repetitions.append(
            {
                "repetition_no": len(repetitions) + 1,
                "points": segment,
                "duration_seconds": duration_seconds,
                "start_angle": segment[0][1],
                "end_angle": segment[-1][1],
                "min_angle": min_angle,
                "max_angle": max_angle,
                "amplitude": amplitude,
            }
        )

    return repetitions, boundaries, strong_peaks


def get_point_percent(points, point_index):
    start_t = points[0][0]
    end_t = points[-1][0]
    duration = end_t - start_t
    if duration <= 0:
        return 0.0
    return (points[point_index][0] - start_t) / duration * 100.0


def get_repetition_extreme_info(repetition, event_type):
    points = repetition["points"]
    peak_index, peak_point = max(enumerate(points), key=lambda item: item[1][1])
    valley_index, valley_point = min(enumerate(points), key=lambda item: item[1][1])

    if event_type == "maxima":
        movement_index = valley_index
        movement_point = valley_point
        movement_type = "valley"
    else:
        movement_index = peak_index
        movement_point = peak_point
        movement_type = "peak"

    return {
        "peak_index": peak_index,
        "peak_time": peak_point[0],
        "peak_angle": peak_point[1],
        "peak_percent": get_point_percent(points, peak_index),
        "valley_index": valley_index,
        "valley_time": valley_point[0],
        "valley_angle": valley_point[1],
        "valley_percent": get_point_percent(points, valley_index),
        "movement_extreme_type": movement_type,
        "movement_extreme_angle": movement_point[1],
        "movement_extreme_percent": get_point_percent(points, movement_index),
    }


def filter_repetitions_by_shape(
    repetitions,
    event_type,
    min_peak_angle,
    peak_window_start,
    peak_window_end,
):
    kept = []
    rejected = []

    for repetition in repetitions:
        extreme = get_repetition_extreme_info(repetition, event_type)
        reject_reasons = []

        if event_type == "minima" and extreme["peak_angle"] < min_peak_angle:
            reject_reasons.append("peak_too_low")

        if extreme["movement_extreme_percent"] < peak_window_start:
            reject_reasons.append("movement_extreme_too_early")
        if extreme["movement_extreme_percent"] > peak_window_end:
            reject_reasons.append("movement_extreme_too_late")

        enriched = {
            **repetition,
            **extreme,
            "quality_status": "rejected" if reject_reasons else "kept",
            "reject_reason": ";".join(reject_reasons),
        }

        if reject_reasons:
            rejected.append(enriched)
        else:
            kept.append(enriched)

    return kept, rejected


def summarize_repetition_durations(repetition_rows, iqr_multiplier):
    durations = sorted(float(row["duration_seconds"]) for row in repetition_rows)
    q1 = percentile(durations, 25)
    median = percentile(durations, 50)
    q3 = percentile(durations, 75)
    iqr = q3 - q1
    lower = q1 - iqr_multiplier * iqr
    upper = q3 + iqr_multiplier * iqr

    kept = [value for value in durations if lower <= value <= upper]
    excluded = len(durations) - len(kept)
    mean_duration = sum(kept) / len(kept)
    sd_duration = sample_std(kept)

    return {
        "total_repetitions": len(durations),
        "used_repetitions": len(kept),
        "excluded_repetitions": excluded,
        "outlier_method": f"IQR x {iqr_multiplier:g}",
        "lower_bound_seconds": round(lower, 6),
        "upper_bound_seconds": round(upper, 6),
        "min_used_seconds": round(min(kept), 6),
        "max_used_seconds": round(max(kept), 6),
        "q1_seconds": round(q1, 6),
        "median_seconds": round(median, 6),
        "q3_seconds": round(q3, 6),
        "average_repetition_seconds": round(mean_duration, 6),
        "sd_repetition_seconds": round(sd_duration, 6),
        "average_repetitions_per_minute": round(60.0 / mean_duration, 6),
    }


def fit_average_curve(repetitions, grid_points, smooth_window):
    mean_values = []
    sd_values = []

    for i in range(grid_points):
        percent = 0.0 if grid_points == 1 else 100.0 * i / (grid_points - 1)
        values = [interpolate_linear(repetition["points"], percent) for repetition in repetitions]
        mean = sum(values) / len(values)
        sd = sample_std(values)
        mean_values.append(mean)
        sd_values.append(sd)

    smooth_values = moving_average(mean_values, smooth_window)

    rows = []
    for i in range(grid_points):
        percent = 0.0 if grid_points == 1 else 100.0 * i / (grid_points - 1)
        mean = mean_values[i]
        sd = sd_values[i]
        rows.append(
            {
                "percent": round(percent, 6),
                "mean_angle": round(mean, 6),
                "smooth_angle": round(smooth_values[i], 6),
                "sd_angle": round(sd, 6),
                "mean_minus_sd": round(mean - sd, 6),
                "mean_plus_sd": round(mean + sd, 6),
                "n_repetitions": len(repetitions),
            }
        )
    return rows


def build_standard_repetition_reference(rows, average_repetition_seconds, repeat_count):
    single_repetition = []
    repeated = []

    for row in rows:
        time_seconds = row["percent"] / 100.0 * average_repetition_seconds
        single_repetition.append(
            {
                "time_seconds": round(time_seconds, 6),
                "percent": row["percent"],
                "standard_angle": row["smooth_angle"],
                "mean_angle": row["mean_angle"],
                "sd_angle": row["sd_angle"],
                "repetition_duration_seconds": round(average_repetition_seconds, 6),
            }
        )

    for repetition_index in range(repeat_count):
        for i, row in enumerate(rows):
            if repetition_index > 0 and i == 0:
                continue

            local_time = row["percent"] / 100.0 * average_repetition_seconds
            repeated.append(
                {
                    "repetition_index": repetition_index + 1,
                    "time_seconds": round(
                        repetition_index * average_repetition_seconds + local_time, 6
                    ),
                    "repetition_time_seconds": round(local_time, 6),
                    "percent": row["percent"],
                    "standard_angle": row["smooth_angle"],
                    "mean_angle": row["mean_angle"],
                    "sd_angle": row["sd_angle"],
                    "repetition_duration_seconds": round(average_repetition_seconds, 6),
                }
            )

    return single_repetition, repeated


def build_raw_repetition_comparison(repetitions, rows, average_repetition_seconds, max_repetitions=0):
    selected = repetitions if max_repetitions <= 0 else repetitions[:max_repetitions]
    raw_rows = []

    for repetition in selected:
        for row in rows:
            percent = row["percent"]
            raw_rows.append(
                {
                    "session_id": repetition["session_id"],
                    "repetition_no": repetition["repetition_no"],
                    "time_seconds": round(percent / 100.0 * average_repetition_seconds, 6),
                    "percent": percent,
                    "angle": round(interpolate_linear(repetition["points"], percent), 6),
                }
            )

    return raw_rows


def line_path(rows, x_key, y_key, x_expr, y_expr):
    parts = []
    for i, row in enumerate(rows):
        command = "L" if i else "M"
        parts.append(f"{command}{x_expr(row[x_key]):.2f},{y_expr(row[y_key]):.2f}")
    return " ".join(parts)


def write_standard_curve_html(path, rows, duration_stats, angle_id, session_ids):
    data_json = json.dumps(rows, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Standard squat curve: {escape(angle_id)}"
    sessions = ", ".join(str(session_id) for session_id in session_ids)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2933; background: #f8fafc; }}
    h1 {{ font-size: 22px; margin: 0 0 8px; }}
    .meta {{ color: #52606d; margin-bottom: 16px; line-height: 1.5; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; max-width: 980px; margin: 16px 0; }}
    .metric {{ border: 1px solid #d9e2ec; background: #fff; padding: 12px 14px; border-radius: 6px; }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 18px; }}
    svg {{ width: 100%; max-width: 980px; height: 560px; border: 1px solid #d9e2ec; border-radius: 6px; background: #fff; }}
    .axis {{ stroke: #9fb3c8; stroke-width: 1; }}
    .grid {{ stroke: #e4e7eb; stroke-width: 1; }}
    .band {{ fill: #dceefb; opacity: 0.8; }}
    .mean {{ fill: none; stroke: #0b69a3; stroke-width: 3; }}
    .smooth {{ fill: none; stroke: #d64545; stroke-width: 3; }}
    .label {{ font-size: 12px; fill: #52606d; }}
    .legend {{ font-size: 13px; fill: #334e68; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">Source squat sessions: {escape(sessions)}. X axis is one normalized squat repetition from one boundary event to the next.</div>
  <section class="metrics" id="metrics"></section>
  <svg id="chart" viewBox="0 0 980 560" role="img" aria-label="Standard squat curve"></svg>
  <script>
    const rows = {data_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Used repetitions", `${{stats.used_repetitions}} / ${{stats.total_repetitions}}`],
      ["Average repetition", `${{stats.average_repetition_seconds}} s`],
      ["Repetitions/min", `${{stats.average_repetitions_per_minute}}`],
      ["Duration SD", `${{stats.sd_repetition_seconds}} s`],
    ].map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");

    const svg = document.getElementById("chart");
    const W = 980, H = 560;
    const m = {{ left: 64, right: 28, top: 28, bottom: 54 }};
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const minY = Math.floor(Math.min(...rows.map(d => d.mean_minus_sd)) / 5) * 5;
    const maxY = Math.ceil(Math.max(...rows.map(d => d.mean_plus_sd)) / 5) * 5;
    const x = p => m.left + p / 100 * iw;
    const y = v => m.top + (maxY - v) / (maxY - minY || 1) * ih;
    const el = (name, attrs = {{}}) => {{
      const node = document.createElementNS("http://www.w3.org/2000/svg", name);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      svg.appendChild(node);
      return node;
    }};
    const path = (data, key) => data.map((d, i) => `${{i ? "L" : "M"}}${{x(d.percent).toFixed(2)}},${{y(d[key]).toFixed(2)}}`).join(" ");
    const band = path(rows, "mean_plus_sd") + " " + [...rows].reverse().map(d => `L${{x(d.percent).toFixed(2)}},${{y(d.mean_minus_sd).toFixed(2)}}`).join(" ") + " Z";

    for (let p = 0; p <= 100; p += 10) {{
      el("line", {{ x1: x(p), y1: m.top, x2: x(p), y2: H - m.bottom, class: "grid" }});
      el("text", {{ x: x(p), y: H - 22, "text-anchor": "middle", class: "label" }}).textContent = p;
    }}
    const yStep = Math.max(5, Math.ceil((maxY - minY) / 8 / 5) * 5);
    for (let v = minY; v <= maxY; v += yStep) {{
      el("line", {{ x1: m.left, y1: y(v), x2: W - m.right, y2: y(v), class: "grid" }});
      el("text", {{ x: 52, y: y(v) + 4, "text-anchor": "end", class: "label" }}).textContent = v;
    }}
    el("line", {{ x1: m.left, y1: H - m.bottom, x2: W - m.right, y2: H - m.bottom, class: "axis" }});
    el("line", {{ x1: m.left, y1: m.top, x2: m.left, y2: H - m.bottom, class: "axis" }});
    el("path", {{ d: band, class: "band" }});
    el("path", {{ d: path(rows, "mean_angle"), class: "mean" }});
    el("path", {{ d: path(rows, "smooth_angle"), class: "smooth" }});
    el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = "Normalized repetition (%)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "Angle (deg)";
    el("rect", {{ x: 720, y: 28, width: 18, height: 10, class: "band" }});
    el("text", {{ x: 744, y: 38, class: "legend" }}).textContent = "Mean +/- SD";
    el("line", {{ x1: 720, y1: 58, x2: 738, y2: 58, class: "mean" }});
    el("text", {{ x: 744, y: 62, class: "legend" }}).textContent = "Mean";
    el("line", {{ x1: 720, y1: 82, x2: 738, y2: 82, class: "smooth" }});
    el("text", {{ x: 744, y: 86, class: "legend" }}).textContent = "Smoothed standard";
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_reference_html(path, single_repetition, repeated_repetitions, duration_stats, angle_id):
    single_json = json.dumps(single_repetition, ensure_ascii=False)
    repeated_json = json.dumps(repeated_repetitions, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Standard squat reference: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ margin: 24px; font-family: Arial, sans-serif; color: #1f2933; background: #f8fafc; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    h2 {{ margin: 28px 0 10px; font-size: 16px; }}
    .caption {{ max-width: 1120px; color: #52606d; margin: 0 0 12px; line-height: 1.5; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; max-width: 1120px; margin: 16px 0 22px; }}
    .metric {{ border: 1px solid #d9e2ec; background: #fff; padding: 12px 14px; border-radius: 6px; }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 19px; }}
    svg {{ width: 100%; max-width: 1120px; height: 440px; background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; }}
    .axis {{ stroke: #9fb3c8; stroke-width: 1; }}
    .grid {{ stroke: #e4e7eb; stroke-width: 1; }}
    .curve {{ fill: none; stroke: #0b69a3; stroke-width: 2.4; }}
    .marker {{ stroke: #d64545; stroke-width: 1; stroke-dasharray: 4 5; }}
    .label {{ font-size: 12px; fill: #52606d; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="caption">This reference repeats the fitted squat repetition three times, matching the Phase II squat protocol.</p>
  <section class="metrics" id="metrics"></section>
  <h2>Single standard repetition</h2>
  <svg id="single" viewBox="0 0 1120 440" role="img" aria-label="single standard squat repetition"></svg>
  <h2>Three standard repetitions</h2>
  <svg id="repeated" viewBox="0 0 1120 440" role="img" aria-label="three standard squat repetitions"></svg>
  <script>
    const single = {single_json};
    const repeated = {repeated_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Average repetition", `${{stats.average_repetition_seconds}} s`],
      ["Repetitions/min", `${{stats.average_repetitions_per_minute}}`],
      ["Used repetitions", `${{stats.used_repetitions}} / ${{stats.total_repetitions}}`],
      ["Duration range", `${{stats.min_used_seconds}} - ${{stats.max_used_seconds}} s`],
    ].map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");

    function drawChart(svgId, rows, xKey, yKey, options) {{
      const svg = document.getElementById(svgId);
      const W = 1120, H = 440;
      const m = {{ left: 64, right: 24, top: 24, bottom: 48 }};
      const iw = W - m.left - m.right;
      const ih = H - m.top - m.bottom;
      const xMax = Math.max(...rows.map(d => d[xKey]));
      const yValues = rows.flatMap(d => [d.standard_angle, d.mean_angle ?? d.standard_angle]);
      const yMin = Math.floor(Math.min(...yValues) / 5) * 5;
      const yMax = Math.ceil(Math.max(...yValues) / 5) * 5;
      const x = v => m.left + v / xMax * iw;
      const y = v => m.top + (yMax - v) / (yMax - yMin || 1) * ih;
      const el = (name, attrs = {{}}) => {{
        const node = document.createElementNS("http://www.w3.org/2000/svg", name);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        svg.appendChild(node);
        return node;
      }};
      const path = rows.map((d, i) => `${{i ? "L" : "M"}}${{x(d[xKey]).toFixed(2)}},${{y(d[yKey]).toFixed(2)}}`).join(" ");
      for (const tick of options.xTicks) {{
        el("line", {{ x1: x(tick), y1: m.top, x2: x(tick), y2: H - m.bottom, class: "grid" }});
        el("text", {{ x: x(tick), y: H - 18, "text-anchor": "middle", class: "label" }}).textContent = tick.toFixed(options.tickDecimals ?? 0);
      }}
      const yStep = Math.max(5, Math.ceil((yMax - yMin) / 7 / 5) * 5);
      for (let v = yMin; v <= yMax; v += yStep) {{
        el("line", {{ x1: m.left, y1: y(v), x2: W - m.right, y2: y(v), class: "grid" }});
        el("text", {{ x: 52, y: y(v) + 4, "text-anchor": "end", class: "label" }}).textContent = v;
      }}
      if (options.repetitionDuration) {{
        for (let t = options.repetitionDuration; t < xMax; t += options.repetitionDuration) {{
          el("line", {{ x1: x(t), y1: m.top, x2: x(t), y2: H - m.bottom, class: "marker" }});
        }}
      }}
      el("line", {{ x1: m.left, y1: H - m.bottom, x2: W - m.right, y2: H - m.bottom, class: "axis" }});
      el("line", {{ x1: m.left, y1: m.top, x2: m.left, y2: H - m.bottom, class: "axis" }});
      el("path", {{ d: path, class: "curve" }});
      el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = options.xLabel;
      el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "Angle (deg)";
    }}

    const oneDuration = stats.average_repetition_seconds;
    drawChart("single", single, "time_seconds", "standard_angle", {{
      xLabel: "Time (s)",
      xTicks: Array.from({{ length: 6 }}, (_, i) => oneDuration * i / 5),
      tickDecimals: 2,
    }});
    drawChart("repeated", repeated, "time_seconds", "standard_angle", {{
      xLabel: "Time (s)",
      xTicks: Array.from({{ length: 4 }}, (_, i) => oneDuration * i),
      tickDecimals: 1,
      repetitionDuration: oneDuration,
    }});
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_comparison_html(path, raw_rows, fitted_rows, standard_single_repetition, duration_stats, angle_id):
    raw_json = json.dumps(raw_rows, ensure_ascii=False)
    fitted_json = json.dumps(fitted_rows, ensure_ascii=False)
    standard_json = json.dumps(standard_single_repetition, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Raw repetitions vs standard fitted curve: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ margin: 24px; font-family: Arial, sans-serif; color: #1f2933; background: #f8fafc; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .caption {{ max-width: 1120px; color: #52606d; margin: 0 0 14px; line-height: 1.5; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; max-width: 1120px; margin: 16px 0 18px; }}
    .metric {{ border: 1px solid #d9e2ec; background: #fff; padding: 12px 14px; border-radius: 6px; }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 19px; }}
    .legend {{ display: flex; gap: 18px; align-items: center; flex-wrap: wrap; max-width: 1120px; margin: 0 0 10px; color: #334e68; font-size: 13px; }}
    .key {{ display: inline-flex; align-items: center; gap: 7px; }}
    .swatch {{ width: 24px; height: 4px; display: inline-block; }}
    .raw-key {{ background: rgba(82, 96, 109, 0.25); }}
    .std-key {{ background: #d64545; }}
    .mean-key {{ background: #0b69a3; }}
    .band-key {{ width: 24px; height: 12px; background: #dceefb; border: 1px solid #b6d6ee; }}
    svg {{ width: 100%; max-width: 1120px; height: 560px; background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; }}
    .axis {{ stroke: #9fb3c8; stroke-width: 1; }}
    .grid {{ stroke: #e4e7eb; stroke-width: 1; }}
    .band {{ fill: #dceefb; opacity: 0.72; }}
    .raw {{ fill: none; stroke: rgba(82, 96, 109, 0.2); stroke-width: 1; }}
    .mean {{ fill: none; stroke: #0b69a3; stroke-width: 2.3; }}
    .standard {{ fill: none; stroke: #d64545; stroke-width: 3.2; }}
    .label {{ font-size: 12px; fill: #52606d; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="caption">Gray lines are detected raw squat repetitions. All repetitions are remapped to the average repetition duration after outlier filtering. The red line is the final standard fitted curve.</p>
  <section class="metrics" id="metrics"></section>
  <div class="legend">
    <span class="key"><i class="swatch raw-key"></i>Raw repetitions</span>
    <span class="key"><i class="swatch band-key"></i>Mean +/- standard deviation</span>
    <span class="key"><i class="swatch mean-key"></i>Mean curve</span>
    <span class="key"><i class="swatch std-key"></i>Standard fitted curve</span>
  </div>
  <svg id="chart" viewBox="0 0 1120 560" role="img" aria-label="raw repetitions compared with standard fitted curve"></svg>
  <script>
    const rawRows = {raw_json};
    const fittedRows = {fitted_json};
    const standardRows = {standard_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Raw repetition count", `${{stats.total_repetitions}}`],
      ["Used for average time", `${{stats.used_repetitions}}`],
      ["Excluded duration outliers", `${{stats.excluded_repetitions}}`],
      ["Average repetition time", `${{stats.average_repetition_seconds}} s`],
      ["Average frequency", `${{stats.average_repetitions_per_minute}} repetitions/min`],
    ].map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");

    const svg = document.getElementById("chart");
    const W = 1120, H = 560;
    const m = {{ left: 64, right: 26, top: 26, bottom: 52 }};
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const xMax = stats.average_repetition_seconds;
    const allAngles = [
      ...rawRows.map(d => d.angle),
      ...fittedRows.map(d => d.mean_minus_sd),
      ...fittedRows.map(d => d.mean_plus_sd),
    ];
    const yMin = Math.floor(Math.min(...allAngles) / 5) * 5;
    const yMax = Math.ceil(Math.max(...allAngles) / 5) * 5;
    const x = v => m.left + v / xMax * iw;
    const y = v => m.top + (yMax - v) / (yMax - yMin || 1) * ih;
    const el = (name, attrs = {{}}) => {{
      const node = document.createElementNS("http://www.w3.org/2000/svg", name);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      svg.appendChild(node);
      return node;
    }};
    const linePath = (rows, xKey, yKey) =>
      rows.map((d, i) => `${{i ? "L" : "M"}}${{x(d[xKey]).toFixed(2)}},${{y(d[yKey]).toFixed(2)}}`).join(" ");

    for (let i = 0; i <= 10; i++) {{
      const tick = xMax * i / 10;
      el("line", {{ x1: x(tick), y1: m.top, x2: x(tick), y2: H - m.bottom, class: "grid" }});
      el("text", {{ x: x(tick), y: H - 20, "text-anchor": "middle", class: "label" }}).textContent = tick.toFixed(2);
    }}
    const yStep = Math.max(5, Math.ceil((yMax - yMin) / 8 / 5) * 5);
    for (let v = yMin; v <= yMax; v += yStep) {{
      el("line", {{ x1: m.left, y1: y(v), x2: W - m.right, y2: y(v), class: "grid" }});
      el("text", {{ x: 52, y: y(v) + 4, "text-anchor": "end", class: "label" }}).textContent = v;
    }}

    const bandRows = fittedRows.map(row => ({{
      time_seconds: row.percent / 100 * xMax,
      upper: row.mean_plus_sd,
      lower: row.mean_minus_sd,
    }}));
    const bandPath =
      linePath(bandRows, "time_seconds", "upper") + " " +
      [...bandRows].reverse().map(d => `L${{x(d.time_seconds).toFixed(2)}},${{y(d.lower).toFixed(2)}}`).join(" ") +
      " Z";
    el("path", {{ d: bandPath, class: "band" }});

    const grouped = new Map();
    for (const row of rawRows) {{
      const key = `${{row.session_id}}-${{row.repetition_no}}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(row);
    }}
    for (const rows of grouped.values()) {{
      el("path", {{ d: linePath(rows, "time_seconds", "angle"), class: "raw" }});
    }}

    const meanRows = fittedRows.map(row => ({{
      time_seconds: row.percent / 100 * xMax,
      mean_angle: row.mean_angle,
    }}));
    el("path", {{ d: linePath(meanRows, "time_seconds", "mean_angle"), class: "mean" }});
    el("path", {{ d: linePath(standardRows, "time_seconds", "standard_angle"), class: "standard" }});
    el("line", {{ x1: m.left, y1: H - m.bottom, x2: W - m.right, y2: H - m.bottom, class: "axis" }});
    el("line", {{ x1: m.left, y1: m.top, x2: m.left, y2: H - m.bottom, class: "axis" }});
    el("text", {{ x: W / 2, y: H - 5, "text-anchor": "middle", class: "label" }}).textContent = "Time (s)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "Angle (deg)";
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_png_if_possible(path, rows, angle_id):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    x = [row["percent"] for row in rows]
    mean = [row["mean_angle"] for row in rows]
    smooth = [row["smooth_angle"] for row in rows]
    lower = [row["mean_minus_sd"] for row in rows]
    upper = [row["mean_plus_sd"] for row in rows]

    plt.figure(figsize=(10, 5.8))
    plt.fill_between(x, lower, upper, color="#dceefb", label="mean +/- sd")
    plt.plot(x, mean, color="#0b69a3", linewidth=2.5, label="mean")
    plt.plot(x, smooth, color="#d64545", linewidth=2.5, label="smoothed standard")
    plt.title(f"Standard squat curve: {angle_id}")
    plt.xlabel("Normalized repetition (%)")
    plt.ylabel("Angle (deg)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()
    return True


def write_output_readme(path, args, session_ids, processed_session_ids, ignored_session_ids, duration_stats):
    now = datetime.now(timezone.utc).isoformat()
    content = f"""# Squat Standard Curve Output

Generated at: `{now}`

## Purpose

This folder contains a preliminary standard squat curve for Team V1 Phase II. It uses healthy squat sessions only and is intended for visual validation before patient comparison.

## Source Data

- Action: `squat`
- Protocol: 3 consecutive squat repetitions in about 5 seconds
- Requested sessions: `{",".join(str(session_id) for session_id in session_ids)}`
- Processed sessions: `{",".join(str(session_id) for session_id in processed_session_ids)}`
- Ignored sessions: `{",".join(str(session_id) for session_id in ignored_session_ids)}`
- Angle ID: `{args.angle_id}`

Sessions `117` and `119` must remain ignored.

## Parameters

- Boundary event type: `{args.event_type}`
- Grid points: `{args.grid_points}`
- Smooth window: `{args.smooth_window}`
- Event smooth window: `{args.event_smooth_window}`
- Boundary angle margin: `{args.boundary_angle_margin}`
- Min repetition seconds: `{args.min_repetition_seconds}`
- Max repetition seconds: `{args.max_repetition_seconds}`
- Min repetition amplitude: `{args.min_repetition_amplitude}`
- Min peak angle: `{args.min_peak_angle}`
- Peak window: `{args.peak_window_start}` to `{args.peak_window_end}` percent
- Duration outlier IQR: `{args.duration_outlier_iqr}`
- Reference repetitions: `{args.reference_repetitions}`
- Trim edge repetitions: `{args.trim_edge_repetitions}`

## Duration Summary

- Total repetitions: `{duration_stats["total_repetitions"]}`
- Used repetitions: `{duration_stats["used_repetitions"]}`
- Excluded duration outliers: `{duration_stats["excluded_repetitions"]}`
- Average repetition seconds: `{duration_stats["average_repetition_seconds"]}`
- Average repetitions per minute: `{duration_stats["average_repetitions_per_minute"]}`

## Validation Notes

This standard curve is preliminary because only sessions `116` and `118` are currently available. Open `raw_vs_standard_repetitions.html` and verify that detected repetitions follow the expected low-high-low squat pattern.

This script does not provide a medical diagnosis or clinical recommendation.
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    args = parse_args()
    session_ids = parse_id_list(args.session_ids)
    ignored_session_ids = set(parse_id_list(args.ignored_session_ids))
    os.makedirs(args.out_dir, exist_ok=True)

    repetitions = []
    summary_rows = []
    repetition_rows = []
    rejected_repetition_rows = []
    processed_session_ids = []

    for session_id in session_ids:
        if session_id in ignored_session_ids:
            print(f"Ignoring session {session_id}: configured as ignored")
            continue

        url = f"{args.base_url.rstrip('/')}/{session_id}"
        print(f"Reading {url}")
        try:
            payload = fetch_json(url)
            items = normalize_response_items(payload)
            curve = build_session_curve(items, args.angle_id)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"WARNING: session {session_id} failed: {exc}")
            continue

        if curve is None:
            print(f"WARNING: session {session_id} has fewer than 2 valid {args.angle_id} points")
            continue

        processed_session_ids.append(session_id)
        if args.event_type == "peak_centered":
            session_repetitions, event_indexes, strong_peak_indexes = (
                extract_peak_centered_repetitions(
                    curve,
                    min_repetition_seconds=args.min_repetition_seconds,
                    max_repetition_seconds=args.max_repetition_seconds,
                    min_repetition_amplitude=args.min_repetition_amplitude,
                    min_peak_angle=args.min_peak_angle,
                    boundary_angle_margin=args.boundary_angle_margin,
                    smooth_window=args.event_smooth_window,
                    min_distance_seconds=args.min_repetition_seconds,
                )
            )
            local_event_count = len(event_indexes)
            strong_peak_count = len(strong_peak_indexes)
        else:
            event_indexes = find_local_events(
                curve,
                smooth_window=args.event_smooth_window,
                min_distance_seconds=args.min_repetition_seconds,
                event_type=args.event_type,
            )
            session_repetitions = extract_repetitions_from_events(
                curve,
                event_indexes,
                min_repetition_seconds=args.min_repetition_seconds,
                max_repetition_seconds=args.max_repetition_seconds,
                min_repetition_amplitude=args.min_repetition_amplitude,
            )
            local_event_count = len(event_indexes)
            strong_peak_count = ""
        raw_valid_repetition_count = len(session_repetitions)
        edge_trim_count = min(args.trim_edge_repetitions, raw_valid_repetition_count // 2)
        if edge_trim_count > 0:
            edge_trimmed_repetitions = session_repetitions[
                edge_trim_count:-edge_trim_count
            ]
        else:
            edge_trimmed_repetitions = session_repetitions

        fitting_repetitions, rejected_repetitions = filter_repetitions_by_shape(
            edge_trimmed_repetitions,
            event_type=args.event_type,
            min_peak_angle=args.min_peak_angle,
            peak_window_start=args.peak_window_start,
            peak_window_end=args.peak_window_end,
        )

        for rejected in rejected_repetitions:
            rejected_repetition_rows.append(
                {
                    "session_id": session_id,
                    "repetition_no_in_session": rejected["repetition_no"],
                    "points": len(rejected["points"]),
                    "start_time": datetime.fromtimestamp(
                        rejected["points"][0][0] / 1000, timezone.utc
                    ).isoformat(),
                    "end_time": datetime.fromtimestamp(
                        rejected["points"][-1][0] / 1000, timezone.utc
                    ).isoformat(),
                    "duration_seconds": round(rejected["duration_seconds"], 3),
                    "min_angle": round(rejected["min_angle"], 6),
                    "max_angle": round(rejected["max_angle"], 6),
                    "peak_angle": round(rejected["peak_angle"], 6),
                    "peak_percent": round(rejected["peak_percent"], 6),
                    "movement_extreme_type": rejected["movement_extreme_type"],
                    "movement_extreme_angle": round(
                        rejected["movement_extreme_angle"], 6
                    ),
                    "movement_extreme_percent": round(
                        rejected["movement_extreme_percent"], 6
                    ),
                    "amplitude": round(rejected["amplitude"], 6),
                    "reject_reason": rejected["reject_reason"],
                }
            )

        for repetition in fitting_repetitions:
            global_repetition_no = len(repetitions) + 1
            repetitions.append(
                {
                    "session_id": session_id,
                    "repetition_no": repetition["repetition_no"],
                    "points": repetition["points"],
                }
            )
            repetition_rows.append(
                {
                    "global_repetition_no": global_repetition_no,
                    "session_id": session_id,
                    "repetition_no_in_session": repetition["repetition_no"],
                    "points": len(repetition["points"]),
                    "start_time": datetime.fromtimestamp(
                        repetition["points"][0][0] / 1000, timezone.utc
                    ).isoformat(),
                    "end_time": datetime.fromtimestamp(
                        repetition["points"][-1][0] / 1000, timezone.utc
                    ).isoformat(),
                    "duration_seconds": round(repetition["duration_seconds"], 3),
                    "start_angle": round(repetition["start_angle"], 6),
                    "end_angle": round(repetition["end_angle"], 6),
                    "min_angle": round(repetition["min_angle"], 6),
                    "max_angle": round(repetition["max_angle"], 6),
                    "peak_angle": round(repetition["peak_angle"], 6),
                    "peak_percent": round(repetition["peak_percent"], 6),
                    "movement_extreme_type": repetition["movement_extreme_type"],
                    "movement_extreme_angle": round(
                        repetition["movement_extreme_angle"], 6
                    ),
                    "movement_extreme_percent": round(
                        repetition["movement_extreme_percent"], 6
                    ),
                    "amplitude": round(repetition["amplitude"], 6),
                }
            )

        summary_rows.append(
            {
                "session_id": session_id,
                "angle_points": len(curve),
                "event_type": args.event_type,
                "local_events": local_event_count,
                "strong_peaks": strong_peak_count,
                "valid_repetitions_before_edge_trim": raw_valid_repetition_count,
                "edge_trimmed_repetitions": raw_valid_repetition_count
                - len(edge_trimmed_repetitions),
                "repetitions_after_edge_trim": len(edge_trimmed_repetitions),
                "shape_filtered_repetitions": len(rejected_repetitions),
                "valid_repetitions": len(fitting_repetitions),
                "start_time": datetime.fromtimestamp(
                    curve[0][0] / 1000, timezone.utc
                ).isoformat(),
                "end_time": datetime.fromtimestamp(
                    curve[-1][0] / 1000, timezone.utc
                ).isoformat(),
                "duration_seconds": round((curve[-1][0] - curve[0][0]) / 1000, 3),
            }
        )

    if not repetitions:
        raise SystemExit(
            "No valid squat repetitions were detected. Try --event-type maxima, "
            "lower --min-repetition-amplitude, or adjust duration thresholds."
        )

    rows = fit_average_curve(repetitions, args.grid_points, args.smooth_window)
    duration_stats = summarize_repetition_durations(
        repetition_rows, iqr_multiplier=args.duration_outlier_iqr
    )
    standard_single_repetition, standard_repeated_repetitions = (
        build_standard_repetition_reference(
            rows,
            average_repetition_seconds=duration_stats["average_repetition_seconds"],
            repeat_count=args.reference_repetitions,
        )
    )
    raw_repetition_comparison = build_raw_repetition_comparison(
        repetitions,
        rows,
        average_repetition_seconds=duration_stats["average_repetition_seconds"],
        max_repetitions=args.comparison_max_repetitions,
    )

    curve_csv = os.path.join(args.out_dir, "standard_squat_curve.csv")
    summary_csv = os.path.join(args.out_dir, "session_summary.csv")
    repetition_csv = os.path.join(args.out_dir, "repetition_summary.csv")
    rejected_repetition_csv = os.path.join(args.out_dir, "rejected_repetition_summary.csv")
    duration_stats_csv = os.path.join(args.out_dir, "repetition_duration_stats.csv")
    raw_comparison_csv = os.path.join(args.out_dir, "raw_repetition_comparison.csv")
    standard_single_csv = os.path.join(args.out_dir, "standard_single_repetition.csv")
    standard_repeated_csv = os.path.join(
        args.out_dir, f"standard_{args.reference_repetitions}_repetitions.csv"
    )
    html_path = os.path.join(args.out_dir, "standard_squat_curve.html")
    reference_html_path = os.path.join(args.out_dir, "standard_squat_reference.html")
    comparison_html_path = os.path.join(args.out_dir, "raw_vs_standard_repetitions.html")
    png_path = os.path.join(args.out_dir, "standard_squat_curve.png")
    readme_path = os.path.join(args.out_dir, "README.md")

    write_csv(
        curve_csv,
        rows,
        [
            "percent",
            "mean_angle",
            "smooth_angle",
            "sd_angle",
            "mean_minus_sd",
            "mean_plus_sd",
            "n_repetitions",
        ],
    )
    write_csv(
        summary_csv,
        summary_rows,
        [
            "session_id",
            "angle_points",
            "event_type",
            "local_events",
            "strong_peaks",
            "valid_repetitions_before_edge_trim",
            "edge_trimmed_repetitions",
            "repetitions_after_edge_trim",
            "shape_filtered_repetitions",
            "valid_repetitions",
            "start_time",
            "end_time",
            "duration_seconds",
        ],
    )
    write_csv(
        repetition_csv,
        repetition_rows,
        [
            "global_repetition_no",
            "session_id",
            "repetition_no_in_session",
            "points",
            "start_time",
            "end_time",
            "duration_seconds",
            "start_angle",
            "end_angle",
            "min_angle",
            "max_angle",
            "peak_angle",
            "peak_percent",
            "movement_extreme_type",
            "movement_extreme_angle",
            "movement_extreme_percent",
            "amplitude",
        ],
    )
    write_csv(
        rejected_repetition_csv,
        rejected_repetition_rows,
        [
            "session_id",
            "repetition_no_in_session",
            "points",
            "start_time",
            "end_time",
            "duration_seconds",
            "min_angle",
            "max_angle",
            "peak_angle",
            "peak_percent",
            "movement_extreme_type",
            "movement_extreme_angle",
            "movement_extreme_percent",
            "amplitude",
            "reject_reason",
        ],
    )
    write_csv(
        duration_stats_csv,
        [duration_stats],
        [
            "total_repetitions",
            "used_repetitions",
            "excluded_repetitions",
            "outlier_method",
            "lower_bound_seconds",
            "upper_bound_seconds",
            "min_used_seconds",
            "max_used_seconds",
            "q1_seconds",
            "median_seconds",
            "q3_seconds",
            "average_repetition_seconds",
            "sd_repetition_seconds",
            "average_repetitions_per_minute",
        ],
    )
    write_csv(
        standard_single_csv,
        standard_single_repetition,
        [
            "time_seconds",
            "percent",
            "standard_angle",
            "mean_angle",
            "sd_angle",
            "repetition_duration_seconds",
        ],
    )
    write_csv(
        raw_comparison_csv,
        raw_repetition_comparison,
        ["session_id", "repetition_no", "time_seconds", "percent", "angle"],
    )
    write_csv(
        standard_repeated_csv,
        standard_repeated_repetitions,
        [
            "repetition_index",
            "time_seconds",
            "repetition_time_seconds",
            "percent",
            "standard_angle",
            "mean_angle",
            "sd_angle",
            "repetition_duration_seconds",
        ],
    )
    write_standard_curve_html(html_path, rows, duration_stats, args.angle_id, processed_session_ids)
    write_reference_html(
        reference_html_path,
        standard_single_repetition,
        standard_repeated_repetitions,
        duration_stats,
        args.angle_id,
    )
    write_comparison_html(
        comparison_html_path,
        raw_repetition_comparison,
        rows,
        standard_single_repetition,
        duration_stats,
        args.angle_id,
    )
    png_written = write_png_if_possible(png_path, rows, args.angle_id)
    write_output_readme(
        readme_path,
        args,
        session_ids,
        processed_session_ids,
        ignored_session_ids,
        duration_stats,
    )

    print()
    print("Done.")
    print(f"Loaded sessions: {len(summary_rows)}")
    print(f"Detected valid repetitions: {len(repetitions)}")
    print(
        "Average repetition duration after outlier removal: "
        f"{duration_stats['average_repetition_seconds']} s "
        f"({duration_stats['average_repetitions_per_minute']} repetitions/min)"
    )
    print(
        "Duration outliers excluded: "
        f"{duration_stats['excluded_repetitions']} / {duration_stats['total_repetitions']}"
    )
    print(f"Standard squat curve CSV: {curve_csv}")
    print(f"Session summary CSV: {summary_csv}")
    print(f"Repetition summary CSV: {repetition_csv}")
    print(f"Rejected repetition summary CSV: {rejected_repetition_csv}")
    print(f"Repetition duration stats CSV: {duration_stats_csv}")
    print(f"Raw repetition comparison CSV: {raw_comparison_csv}")
    print(f"Standard single-repetition CSV: {standard_single_csv}")
    print(f"Standard repeated-repetition CSV: {standard_repeated_csv}")
    print(f"HTML plot: {html_path}")
    print(f"Standard reference HTML: {reference_html_path}")
    print(f"Raw-vs-standard HTML: {comparison_html_path}")
    print(f"README: {readme_path}")
    if png_written:
        print(f"PNG plot: {png_path}")
    else:
        print("PNG plot skipped: matplotlib is not installed.")


if __name__ == "__main__":
    main()
