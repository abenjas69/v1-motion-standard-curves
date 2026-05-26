import argparse
import json
import os
import time
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


DEFAULT_UPSTAIRS_SESSIONS = (
    "146,147,148,149,150,151,152,156,157,158,159,160,161,162,163,164,"
    "165,166,167,168,169,170,171,172,173,174"
)


def parse_id_list(value):
    ids = []
    for part in value.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return ids


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch upstairs/stair-climbing measurements and fit a standard full-action curve."
    )
    parser.add_argument("--base-url", default="http://113.44.220.94:3000/measurements")
    parser.add_argument(
        "--session-ids",
        default=DEFAULT_UPSTAIRS_SESSIONS,
        help="Comma-separated upstairs session IDs.",
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
        "--peak-smooth-window",
        type=int,
        default=5,
        help="Moving-average window used before counting strong local peaks.",
    )
    parser.add_argument(
        "--peak-min-distance-seconds",
        type=float,
        default=0.55,
        help="Minimum time between counted local peaks.",
    )
    parser.add_argument(
        "--min-peak-angle",
        type=float,
        default=45.0,
        help="Minimum smoothed local peak angle counted as a strong upstairs knee-lift peak.",
    )
    parser.add_argument(
        "--min-action-seconds",
        type=float,
        default=6.0,
        help="Sessions shorter than this are rejected.",
    )
    parser.add_argument(
        "--max-action-seconds",
        type=float,
        default=14.0,
        help="Sessions longer than this are rejected.",
    )
    parser.add_argument(
        "--min-action-amplitude",
        type=float,
        default=60.0,
        help="Minimum full-session angle range.",
    )
    parser.add_argument(
        "--duration-outlier-iqr",
        type=float,
        default=1.5,
        help="IQR multiplier for excluding extreme action durations before time scaling.",
    )
    parser.add_argument(
        "--comparison-max-sessions",
        type=int,
        default=0,
        help="Maximum raw sessions to draw in comparison HTML. 0 means draw all sessions.",
    )
    parser.add_argument("--expected-steps", type=int, default=10)
    parser.add_argument(
        "--fetch-retries",
        type=int,
        default=3,
        help="Number of fetch attempts per session before skipping it.",
    )
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=1.0,
        help="Seconds to wait between fetch retries.",
    )
    parser.add_argument("--out-dir", default="output_upstairs_python")
    return parser.parse_args()


def fetch_json_with_retries(url, retries, sleep_seconds):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fetch_json(url)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                print(f"WARNING: fetch attempt {attempt}/{retries} failed: {exc}")
                time.sleep(sleep_seconds)
    raise last_exc


def find_local_peaks(curve, smooth_window, min_distance_seconds):
    smoothed = smooth_curve(curve, smooth_window)
    candidates = []

    for i in range(1, len(smoothed) - 1):
        prev_angle = smoothed[i - 1][1]
        angle = smoothed[i][1]
        next_angle = smoothed[i + 1][1]
        if angle >= prev_angle and angle >= next_angle:
            candidates.append(i)

    min_distance_ms = min_distance_seconds * 1000.0
    selected = []
    for idx in candidates:
        if not selected:
            selected.append(idx)
            continue

        last_idx = selected[-1]
        too_close = curve[idx][0] - curve[last_idx][0] < min_distance_ms
        if too_close:
            if smoothed[idx][1] > smoothed[last_idx][1]:
                selected[-1] = idx
        else:
            selected.append(idx)

    return selected, smoothed


def summarize_action_durations(session_rows, iqr_multiplier):
    durations = sorted(float(row["duration_seconds"]) for row in session_rows)
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
        "total_sessions": len(durations),
        "used_sessions": len(kept),
        "excluded_sessions": excluded,
        "outlier_method": f"IQR x {iqr_multiplier:g}",
        "lower_bound_seconds": round(lower, 6),
        "upper_bound_seconds": round(upper, 6),
        "min_used_seconds": round(min(kept), 6),
        "max_used_seconds": round(max(kept), 6),
        "q1_seconds": round(q1, 6),
        "median_seconds": round(median, 6),
        "q3_seconds": round(q3, 6),
        "average_action_seconds": round(mean_duration, 6),
        "sd_action_seconds": round(sd_duration, 6),
    }


def fit_average_curve(curves, grid_points, smooth_window):
    mean_values = []
    sd_values = []

    for i in range(grid_points):
        percent = 0.0 if grid_points == 1 else 100.0 * i / (grid_points - 1)
        values = [interpolate_linear(curve["points"], percent) for curve in curves]
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
                "n_sessions": len(curves),
            }
        )
    return rows


def build_standard_action_reference(rows, average_action_seconds):
    standard_action = []
    for row in rows:
        time_seconds = row["percent"] / 100.0 * average_action_seconds
        standard_action.append(
            {
                "time_seconds": round(time_seconds, 6),
                "percent": row["percent"],
                "standard_angle": row["smooth_angle"],
                "mean_angle": row["mean_angle"],
                "sd_angle": row["sd_angle"],
                "action_duration_seconds": round(average_action_seconds, 6),
            }
        )
    return standard_action


def build_raw_session_comparison(curves, rows, average_action_seconds, max_sessions=0):
    selected = curves if max_sessions <= 0 else curves[:max_sessions]
    raw_rows = []

    for curve in selected:
        for row in rows:
            percent = row["percent"]
            raw_rows.append(
                {
                    "session_id": curve["session_id"],
                    "time_seconds": round(percent / 100.0 * average_action_seconds, 6),
                    "percent": percent,
                    "angle": round(interpolate_linear(curve["points"], percent), 6),
                }
            )

    return raw_rows


def write_curve_html(path, rows, duration_stats, angle_id, session_ids, average_strong_peaks):
    data_json = json.dumps(rows, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Standard upstairs curve: {escape(angle_id)}"
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
  <div class="meta">Source upstairs sessions: {escape(sessions)}. X axis is one normalized full upstairs action from start to end.</div>
  <section class="metrics" id="metrics"></section>
  <svg id="chart" viewBox="0 0 980 560" role="img" aria-label="Standard upstairs curve"></svg>
  <script>
    const rows = {data_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Used sessions", `${{stats.used_sessions}} / ${{stats.total_sessions}}`],
      ["Average action time", `${{stats.average_action_seconds}} s`],
      ["Duration SD", `${{stats.sd_action_seconds}} s`],
      ["Average strong peaks/session", "{average_strong_peaks:.2f}"],
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
    el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = "Normalized action (%)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "Angle (deg)";
    el("rect", {{ x: 700, y: 28, width: 18, height: 10, class: "band" }});
    el("text", {{ x: 724, y: 38, class: "legend" }}).textContent = "Mean +/- SD";
    el("line", {{ x1: 700, y1: 58, x2: 718, y2: 58, class: "mean" }});
    el("text", {{ x: 724, y: 62, class: "legend" }}).textContent = "Mean";
    el("line", {{ x1: 700, y1: 82, x2: 718, y2: 82, class: "smooth" }});
    el("text", {{ x: 724, y: 86, class: "legend" }}).textContent = "Smoothed standard";
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_reference_html(path, standard_action, duration_stats, angle_id, expected_steps):
    standard_json = json.dumps(standard_action, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Standard upstairs reference: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ margin: 24px; font-family: Arial, sans-serif; color: #1f2933; background: #f8fafc; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .caption {{ max-width: 1120px; color: #52606d; margin: 0 0 12px; line-height: 1.5; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; max-width: 1120px; margin: 16px 0 22px; }}
    .metric {{ border: 1px solid #d9e2ec; background: #fff; padding: 12px 14px; border-radius: 6px; }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 19px; }}
    svg {{ width: 100%; max-width: 1120px; height: 500px; background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; }}
    .axis {{ stroke: #9fb3c8; stroke-width: 1; }}
    .grid {{ stroke: #e4e7eb; stroke-width: 1; }}
    .curve {{ fill: none; stroke: #d64545; stroke-width: 3; }}
    .label {{ font-size: 12px; fill: #52606d; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="caption">This is one full upstairs action reference normalized to the average duration. The Phase II protocol is to climb {expected_steps} steps.</p>
  <section class="metrics" id="metrics"></section>
  <svg id="chart" viewBox="0 0 1120 500" role="img" aria-label="standard upstairs action"></svg>
  <script>
    const rows = {standard_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Average action time", `${{stats.average_action_seconds}} s`],
      ["Duration SD", `${{stats.sd_action_seconds}} s`],
      ["Used sessions", `${{stats.used_sessions}} / ${{stats.total_sessions}}`],
      ["Protocol", "{expected_steps} steps"],
    ].map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");
    const svg = document.getElementById("chart");
    const W = 1120, H = 500;
    const m = {{ left: 64, right: 24, top: 24, bottom: 48 }};
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const xMax = Math.max(...rows.map(d => d.time_seconds));
    const yMin = Math.floor(Math.min(...rows.map(d => d.standard_angle)) / 5) * 5;
    const yMax = Math.ceil(Math.max(...rows.map(d => d.standard_angle)) / 5) * 5;
    const x = v => m.left + v / xMax * iw;
    const y = v => m.top + (yMax - v) / (yMax - yMin || 1) * ih;
    const el = (name, attrs = {{}}) => {{
      const node = document.createElementNS("http://www.w3.org/2000/svg", name);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      svg.appendChild(node);
      return node;
    }};
    const path = rows.map((d, i) => `${{i ? "L" : "M"}}${{x(d.time_seconds).toFixed(2)}},${{y(d.standard_angle).toFixed(2)}}`).join(" ");
    for (let i = 0; i <= 10; i++) {{
      const tick = xMax * i / 10;
      el("line", {{ x1: x(tick), y1: m.top, x2: x(tick), y2: H - m.bottom, class: "grid" }});
      el("text", {{ x: x(tick), y: H - 18, "text-anchor": "middle", class: "label" }}).textContent = tick.toFixed(1);
    }}
    const yStep = Math.max(5, Math.ceil((yMax - yMin) / 7 / 5) * 5);
    for (let v = yMin; v <= yMax; v += yStep) {{
      el("line", {{ x1: m.left, y1: y(v), x2: W - m.right, y2: y(v), class: "grid" }});
      el("text", {{ x: 52, y: y(v) + 4, "text-anchor": "end", class: "label" }}).textContent = v;
    }}
    el("line", {{ x1: m.left, y1: H - m.bottom, x2: W - m.right, y2: H - m.bottom, class: "axis" }});
    el("line", {{ x1: m.left, y1: m.top, x2: m.left, y2: H - m.bottom, class: "axis" }});
    el("path", {{ d: path, class: "curve" }});
    el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = "Time (s)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "Angle (deg)";
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_comparison_html(path, raw_rows, fitted_rows, standard_action, duration_stats, angle_id, average_strong_peaks):
    raw_json = json.dumps(raw_rows, ensure_ascii=False)
    fitted_json = json.dumps(fitted_rows, ensure_ascii=False)
    standard_json = json.dumps(standard_action, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Raw upstairs sessions vs standard fitted curve: {escape(angle_id)}"
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
  <p class="caption">Gray lines are raw upstairs sessions. Each full session is remapped to the average action duration after outlier filtering. The red line is the final standard fitted curve.</p>
  <section class="metrics" id="metrics"></section>
  <div class="legend">
    <span class="key"><i class="swatch raw-key"></i>Raw sessions</span>
    <span class="key"><i class="swatch band-key"></i>Mean +/- standard deviation</span>
    <span class="key"><i class="swatch mean-key"></i>Mean curve</span>
    <span class="key"><i class="swatch std-key"></i>Standard fitted curve</span>
  </div>
  <svg id="chart" viewBox="0 0 1120 560" role="img" aria-label="raw upstairs sessions compared with standard fitted curve"></svg>
  <script>
    const rawRows = {raw_json};
    const fittedRows = {fitted_json};
    const standardRows = {standard_json};
    const stats = {stats_json};
    document.getElementById("metrics").innerHTML = [
      ["Raw session count", `${{stats.total_sessions}}`],
      ["Used for average time", `${{stats.used_sessions}}`],
      ["Excluded duration outliers", `${{stats.excluded_sessions}}`],
      ["Average action time", `${{stats.average_action_seconds}} s`],
      ["Average strong peaks/session", "{average_strong_peaks:.2f}"],
    ].map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");

    const svg = document.getElementById("chart");
    const W = 1120, H = 560;
    const m = {{ left: 64, right: 26, top: 26, bottom: 52 }};
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const xMax = stats.average_action_seconds;
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
      const key = `${{row.session_id}}`;
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
    plt.title(f"Standard upstairs curve: {angle_id}")
    plt.xlabel("Normalized action (%)")
    plt.ylabel("Angle (deg)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()
    return True


def write_output_readme(path, args, session_ids, processed_session_ids, ignored_session_ids, duration_stats, average_strong_peaks):
    now = datetime.now(timezone.utc).isoformat()
    content = f"""# Upstairs Standard Curve Output

Generated at: `{now}`

## Purpose

This folder contains a preliminary standard upstairs/stair-climbing curve for Team V1 Phase II. It fits the full action from start to end instead of forcing a fixed number of detected left-knee cycles.

## Source Data

- Action: `upstairs`
- Protocol: climb `{args.expected_steps}` steps
- Requested sessions: `{",".join(str(session_id) for session_id in session_ids)}`
- Processed sessions: `{",".join(str(session_id) for session_id in processed_session_ids)}`
- Ignored sessions: `{",".join(str(session_id) for session_id in ignored_session_ids)}`
- Angle ID: `{args.angle_id}`

## Parameters

- Grid points: `{args.grid_points}`
- Smooth window: `{args.smooth_window}`
- Peak smooth window: `{args.peak_smooth_window}`
- Peak min distance seconds: `{args.peak_min_distance_seconds}`
- Min peak angle: `{args.min_peak_angle}`
- Min action seconds: `{args.min_action_seconds}`
- Max action seconds: `{args.max_action_seconds}`
- Min action amplitude: `{args.min_action_amplitude}`
- Duration outlier IQR: `{args.duration_outlier_iqr}`

## Duration Summary

- Total sessions: `{duration_stats["total_sessions"]}`
- Used sessions: `{duration_stats["used_sessions"]}`
- Excluded duration outliers: `{duration_stats["excluded_sessions"]}`
- Average action seconds: `{duration_stats["average_action_seconds"]}`
- Average strong peaks per session: `{average_strong_peaks:.2f}`

## Validation Notes

The left knee signal usually shows fewer strong peaks than the 10-step protocol because one knee does not necessarily peak on every stair step. For that reason, this script builds a full-action standard curve rather than forcing exactly 10 step segments.

Open `raw_vs_standard_sessions.html` and verify that the red standard curve follows the center of the gray raw session curves.

This script does not provide a medical diagnosis or clinical recommendation.
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    args = parse_args()
    session_ids = parse_id_list(args.session_ids)
    ignored_session_ids = set(parse_id_list(args.ignored_session_ids))
    os.makedirs(args.out_dir, exist_ok=True)

    curves = []
    session_rows = []
    rejected_rows = []
    processed_session_ids = []

    for session_id in session_ids:
        if session_id in ignored_session_ids:
            print(f"Ignoring session {session_id}: configured as ignored")
            continue

        url = f"{args.base_url.rstrip('/')}/{session_id}"
        print(f"Reading {url}")
        try:
            payload = fetch_json_with_retries(
                url,
                retries=args.fetch_retries,
                sleep_seconds=args.retry_sleep_seconds,
            )
            items = normalize_response_items(payload)
            curve = build_session_curve(items, args.angle_id)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"WARNING: session {session_id} failed: {exc}")
            continue

        if curve is None:
            print(f"WARNING: session {session_id} has fewer than 2 valid {args.angle_id} points")
            continue

        duration_seconds = (curve[-1][0] - curve[0][0]) / 1000.0
        angles = [angle for _, angle in curve]
        min_angle = min(angles)
        max_angle = max(angles)
        amplitude = max_angle - min_angle
        peak_indexes, smoothed = find_local_peaks(
            curve,
            smooth_window=args.peak_smooth_window,
            min_distance_seconds=args.peak_min_distance_seconds,
        )
        strong_peak_count = sum(1 for idx in peak_indexes if smoothed[idx][1] >= args.min_peak_angle)

        reject_reasons = []
        if duration_seconds < args.min_action_seconds:
            reject_reasons.append("duration_too_short")
        if duration_seconds > args.max_action_seconds:
            reject_reasons.append("duration_too_long")
        if amplitude < args.min_action_amplitude:
            reject_reasons.append("amplitude_too_low")

        row = {
            "session_id": session_id,
            "angle_points": len(curve),
            "start_time": datetime.fromtimestamp(curve[0][0] / 1000, timezone.utc).isoformat(),
            "end_time": datetime.fromtimestamp(curve[-1][0] / 1000, timezone.utc).isoformat(),
            "duration_seconds": round(duration_seconds, 3),
            "min_angle": round(min_angle, 6),
            "max_angle": round(max_angle, 6),
            "amplitude": round(amplitude, 6),
            "local_peak_count": len(peak_indexes),
            "strong_peak_count": strong_peak_count,
            "quality_status": "rejected" if reject_reasons else "kept",
            "reject_reason": ";".join(reject_reasons),
        }

        if reject_reasons:
            rejected_rows.append(row)
            continue

        processed_session_ids.append(session_id)
        curves.append({"session_id": session_id, "points": curve})
        session_rows.append(row)

    if not curves:
        raise SystemExit(
            "No valid upstairs sessions were detected. Try lowering --min-action-amplitude "
            "or adjusting --min-action-seconds/--max-action-seconds."
        )

    rows = fit_average_curve(curves, args.grid_points, args.smooth_window)
    duration_stats = summarize_action_durations(
        session_rows, iqr_multiplier=args.duration_outlier_iqr
    )
    standard_action = build_standard_action_reference(
        rows,
        average_action_seconds=duration_stats["average_action_seconds"],
    )
    raw_session_comparison = build_raw_session_comparison(
        curves,
        rows,
        average_action_seconds=duration_stats["average_action_seconds"],
        max_sessions=args.comparison_max_sessions,
    )
    average_strong_peaks = sum(float(row["strong_peak_count"]) for row in session_rows) / len(session_rows)

    curve_csv = os.path.join(args.out_dir, "standard_upstairs_curve.csv")
    summary_csv = os.path.join(args.out_dir, "session_summary.csv")
    rejected_csv = os.path.join(args.out_dir, "rejected_session_summary.csv")
    duration_stats_csv = os.path.join(args.out_dir, "action_duration_stats.csv")
    raw_comparison_csv = os.path.join(args.out_dir, "raw_session_comparison.csv")
    standard_action_csv = os.path.join(args.out_dir, "standard_upstairs_action.csv")
    html_path = os.path.join(args.out_dir, "standard_upstairs_curve.html")
    reference_html_path = os.path.join(args.out_dir, "standard_upstairs_reference.html")
    comparison_html_path = os.path.join(args.out_dir, "raw_vs_standard_sessions.html")
    png_path = os.path.join(args.out_dir, "standard_upstairs_curve.png")
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
            "n_sessions",
        ],
    )
    write_csv(
        summary_csv,
        session_rows,
        [
            "session_id",
            "angle_points",
            "start_time",
            "end_time",
            "duration_seconds",
            "min_angle",
            "max_angle",
            "amplitude",
            "local_peak_count",
            "strong_peak_count",
            "quality_status",
            "reject_reason",
        ],
    )
    write_csv(
        rejected_csv,
        rejected_rows,
        [
            "session_id",
            "angle_points",
            "start_time",
            "end_time",
            "duration_seconds",
            "min_angle",
            "max_angle",
            "amplitude",
            "local_peak_count",
            "strong_peak_count",
            "quality_status",
            "reject_reason",
        ],
    )
    write_csv(
        duration_stats_csv,
        [duration_stats],
        [
            "total_sessions",
            "used_sessions",
            "excluded_sessions",
            "outlier_method",
            "lower_bound_seconds",
            "upper_bound_seconds",
            "min_used_seconds",
            "max_used_seconds",
            "q1_seconds",
            "median_seconds",
            "q3_seconds",
            "average_action_seconds",
            "sd_action_seconds",
        ],
    )
    write_csv(
        raw_comparison_csv,
        raw_session_comparison,
        ["session_id", "time_seconds", "percent", "angle"],
    )
    write_csv(
        standard_action_csv,
        standard_action,
        [
            "time_seconds",
            "percent",
            "standard_angle",
            "mean_angle",
            "sd_angle",
            "action_duration_seconds",
        ],
    )
    write_curve_html(html_path, rows, duration_stats, args.angle_id, processed_session_ids, average_strong_peaks)
    write_reference_html(reference_html_path, standard_action, duration_stats, args.angle_id, args.expected_steps)
    write_comparison_html(
        comparison_html_path,
        raw_session_comparison,
        rows,
        standard_action,
        duration_stats,
        args.angle_id,
        average_strong_peaks,
    )
    png_written = write_png_if_possible(png_path, rows, args.angle_id)
    write_output_readme(
        readme_path,
        args,
        session_ids,
        processed_session_ids,
        ignored_session_ids,
        duration_stats,
        average_strong_peaks,
    )

    print()
    print("Done.")
    print(f"Loaded sessions: {len(session_rows)}")
    print(f"Rejected sessions: {len(rejected_rows)}")
    print(
        "Average action duration after outlier removal: "
        f"{duration_stats['average_action_seconds']} s"
    )
    print(f"Average strong peaks per session: {average_strong_peaks:.2f}")
    print(f"Standard upstairs curve CSV: {curve_csv}")
    print(f"Session summary CSV: {summary_csv}")
    print(f"Rejected session summary CSV: {rejected_csv}")
    print(f"Action duration stats CSV: {duration_stats_csv}")
    print(f"Raw session comparison CSV: {raw_comparison_csv}")
    print(f"Standard full-action CSV: {standard_action_csv}")
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
