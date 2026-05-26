import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone
from html import escape
from urllib.error import URLError, HTTPError
from urllib.request import urlopen


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch knee-angle measurements and fit an average normal curve."
    )
    parser.add_argument("--base-url", default="http://113.44.220.94:3000/measurements")
    parser.add_argument("--start-session", type=int, default=91)
    parser.add_argument("--end-session", type=int, default=115)
    parser.add_argument("--angle-id", default="left_knee")
    parser.add_argument("--grid-points", type=int, default=101)
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument(
        "--minima-smooth-window",
        type=int,
        default=5,
        help="Moving-average window used before detecting local minima.",
    )
    parser.add_argument(
        "--min-cycle-seconds",
        type=float,
        default=0.8,
        help="Cycles shorter than this are ignored.",
    )
    parser.add_argument(
        "--max-cycle-seconds",
        type=float,
        default=3.5,
        help="Cycles longer than this are ignored.",
    )
    parser.add_argument(
        "--min-cycle-amplitude",
        type=float,
        default=15.0,
        help="Minimum angle rise between two minima. Increase this if noise creates false cycles.",
    )
    parser.add_argument(
        "--min-peak-angle",
        type=float,
        default=40.0,
        help="Remove cycles whose peak knee angle is lower than this value.",
    )
    parser.add_argument(
        "--peak-window-start",
        type=float,
        default=33.333333,
        help="Earliest allowed peak position inside a cycle, in percent.",
    )
    parser.add_argument(
        "--peak-window-end",
        type=float,
        default=66.666667,
        help="Latest allowed peak position inside a cycle, in percent.",
    )
    parser.add_argument(
        "--duration-outlier-iqr",
        type=float,
        default=1.5,
        help="IQR multiplier for excluding extreme cycle durations before averaging.",
    )
    parser.add_argument(
        "--reference-cycles",
        type=int,
        default=15,
        help="How many fitted cycles to repeat in the standard reference curve.",
    )
    parser.add_argument(
        "--comparison-max-cycles",
        type=int,
        default=0,
        help="Maximum raw cycles to draw in the comparison HTML. 0 means draw all cycles.",
    )
    parser.add_argument(
        "--trim-edge-cycles",
        type=int,
        default=1,
        help="Remove this many valid cycles from both the start and end of each session.",
    )
    parser.add_argument("--out-dir", default="output_python")
    return parser.parse_args()


def parse_timestamp_ms(value):
    # API timestamps look like 2026-05-22T14:46:58.568Z.
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


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

            t = parse_timestamp_ms(point["timestamp"])
            by_time.setdefault(t, []).append(float(point["angle"]))

    curve = []
    for t in sorted(by_time):
        values = by_time[t]
        curve.append((t, sum(values) / len(values)))

    if len(curve) < 2:
        return None
    return curve


def interpolate_linear(curve, percent):
    start_t = curve[0][0]
    end_t = curve[-1][0]
    target = start_t + (end_t - start_t) * percent / 100.0

    if target <= curve[0][0]:
        return curve[0][1]
    if target >= curve[-1][0]:
        return curve[-1][1]

    # The curves are short enough that a simple scan is clear and fast.
    for i in range(len(curve) - 1):
        left_t, left_angle = curve[i]
        right_t, right_angle = curve[i + 1]
        if left_t <= target <= right_t:
            ratio = (target - left_t) / (right_t - left_t)
            return left_angle + ratio * (right_angle - left_angle)

    return curve[-1][1]


def sample_std(values):
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def percentile(sorted_values, percent):
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]

    pos = (len(sorted_values) - 1) * percent / 100.0
    left = int(math.floor(pos))
    right = int(math.ceil(pos))
    if left == right:
        return sorted_values[left]

    ratio = pos - left
    return sorted_values[left] + ratio * (sorted_values[right] - sorted_values[left])


def summarize_cycle_durations(cycle_rows, iqr_multiplier):
    durations = sorted(float(row["duration_seconds"]) for row in cycle_rows)
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
        "total_cycles": len(durations),
        "used_cycles": len(kept),
        "excluded_cycles": excluded,
        "outlier_method": f"IQR x {iqr_multiplier:g}",
        "lower_bound_seconds": round(lower, 6),
        "upper_bound_seconds": round(upper, 6),
        "min_used_seconds": round(min(kept), 6),
        "max_used_seconds": round(max(kept), 6),
        "q1_seconds": round(q1, 6),
        "median_seconds": round(median, 6),
        "q3_seconds": round(q3, 6),
        "average_cycle_seconds": round(mean_duration, 6),
        "sd_cycle_seconds": round(sd_duration, 6),
        "average_cycles_per_minute": round(60.0 / mean_duration, 6),
    }


def moving_average(values, window):
    if window <= 1:
        return list(values)
    if window % 2 == 0:
        window += 1

    half = window // 2
    out = []
    for i in range(len(values)):
        left = max(0, i - half)
        right = min(len(values), i + half + 1)
        chunk = values[left:right]
        out.append(sum(chunk) / len(chunk))
    return out


def smooth_curve(curve, window):
    values = [angle for _, angle in curve]
    smoothed = moving_average(values, window)
    return [(curve[i][0], smoothed[i]) for i in range(len(curve))]


def find_local_minima(curve, smooth_window, min_distance_seconds):
    smoothed = smooth_curve(curve, smooth_window)
    candidates = []

    for i in range(1, len(smoothed) - 1):
        prev_angle = smoothed[i - 1][1]
        angle = smoothed[i][1]
        next_angle = smoothed[i + 1][1]
        if angle <= prev_angle and angle <= next_angle:
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
            if smoothed[idx][1] < smoothed[last_idx][1]:
                selected[-1] = idx
        else:
            selected.append(idx)

    return selected


def extract_cycles_from_minima(
    curve,
    minima_indexes,
    min_cycle_seconds,
    max_cycle_seconds,
    min_cycle_amplitude,
):
    cycles = []

    for cycle_no, (start_idx, end_idx) in enumerate(
        zip(minima_indexes, minima_indexes[1:]), start=1
    ):
        segment = curve[start_idx : end_idx + 1]
        if len(segment) < 5:
            continue

        duration_seconds = (segment[-1][0] - segment[0][0]) / 1000.0
        if duration_seconds < min_cycle_seconds or duration_seconds > max_cycle_seconds:
            continue

        max_angle = max(angle for _, angle in segment)
        baseline = max(segment[0][1], segment[-1][1])
        amplitude = max_angle - baseline
        if amplitude < min_cycle_amplitude:
            continue

        cycles.append(
            {
                "cycle_no": cycle_no,
                "points": segment,
                "duration_seconds": duration_seconds,
                "start_angle": segment[0][1],
                "end_angle": segment[-1][1],
                "max_angle": max_angle,
                "amplitude": amplitude,
            }
        )

    return cycles


def get_cycle_peak_info(cycle):
    points = cycle["points"]
    peak_index, peak_point = max(enumerate(points), key=lambda item: item[1][1])
    start_t = points[0][0]
    end_t = points[-1][0]
    duration = end_t - start_t
    if duration <= 0:
        peak_percent = 0.0
    else:
        peak_percent = (peak_point[0] - start_t) / duration * 100.0

    return {
        "peak_index": peak_index,
        "peak_time": peak_point[0],
        "peak_angle": peak_point[1],
        "peak_percent": peak_percent,
    }


def filter_cycles_by_peak(cycles, min_peak_angle, peak_window_start, peak_window_end):
    kept = []
    rejected = []

    for cycle in cycles:
        peak = get_cycle_peak_info(cycle)
        reject_reasons = []

        if peak["peak_angle"] < min_peak_angle:
            reject_reasons.append("peak_too_low")
        if peak["peak_percent"] < peak_window_start:
            reject_reasons.append("peak_too_early")
        if peak["peak_percent"] > peak_window_end:
            reject_reasons.append("peak_too_late")

        enriched = {
            **cycle,
            **peak,
            "quality_status": "rejected" if reject_reasons else "kept",
            "reject_reason": ";".join(reject_reasons),
        }

        if reject_reasons:
            rejected.append(enriched)
        else:
            kept.append(enriched)

    return kept, rejected


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
                "n_curves": len(curves),
            }
        )
    return rows


def build_standard_reference(rows, average_cycle_seconds, repeat_count):
    single_cycle = []
    repeated = []

    for row in rows:
        time_seconds = row["percent"] / 100.0 * average_cycle_seconds
        single_cycle.append(
            {
                "time_seconds": round(time_seconds, 6),
                "percent": row["percent"],
                "standard_angle": row["smooth_angle"],
                "mean_angle": row["mean_angle"],
                "sd_angle": row["sd_angle"],
                "cycle_duration_seconds": round(average_cycle_seconds, 6),
            }
        )

    for cycle_index in range(repeat_count):
        for i, row in enumerate(rows):
            if cycle_index > 0 and i == 0:
                continue

            local_time = row["percent"] / 100.0 * average_cycle_seconds
            repeated.append(
                {
                    "cycle_index": cycle_index + 1,
                    "time_seconds": round(cycle_index * average_cycle_seconds + local_time, 6),
                    "cycle_time_seconds": round(local_time, 6),
                    "percent": row["percent"],
                    "standard_angle": row["smooth_angle"],
                    "mean_angle": row["mean_angle"],
                    "sd_angle": row["sd_angle"],
                    "cycle_duration_seconds": round(average_cycle_seconds, 6),
                }
            )

    return single_cycle, repeated


def build_raw_cycle_comparison(cycles, rows, average_cycle_seconds, max_cycles=0):
    selected = cycles if max_cycles <= 0 else cycles[:max_cycles]
    raw_rows = []

    for cycle in selected:
        for row in rows:
            percent = row["percent"]
            raw_rows.append(
                {
                    "session_id": cycle["session_id"],
                    "cycle_no": cycle["cycle_no"],
                    "time_seconds": round(percent / 100.0 * average_cycle_seconds, 6),
                    "percent": percent,
                    "angle": round(interpolate_linear(cycle["points"], percent), 6),
                }
            )

    return raw_rows


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_html_plot(path, rows, angle_id):
    data_json = json.dumps(rows, ensure_ascii=False)
    title = f"Average knee-angle lifting cycle: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, "Microsoft YaHei", sans-serif; margin: 24px; color: #1f2933; }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    .meta {{ color: #52606d; margin-bottom: 16px; }}
    svg {{ width: 100%; max-width: 980px; height: 560px; border: 1px solid #d9e2ec; background: #fff; }}
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
  <div class="meta">X axis is one normalized lifting cycle from one local minimum to the next. Blue is mean, red is smoothed fit, and the light band is +/- 1 SD.</div>
  <svg id="chart" viewBox="0 0 980 560" role="img" aria-label="Normal knee angle curve"></svg>
  <script>
    const rows = {data_json};
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
      const n = document.createElementNS("http://www.w3.org/2000/svg", name);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      svg.appendChild(n);
      return n;
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
    el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = "周期百分比 (%)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "角度 (deg)";
    el("rect", {{ x: 720, y: 28, width: 18, height: 10, class: "band" }});
    el("text", {{ x: 744, y: 38, class: "legend" }}).textContent = "平均 ± 标准差";
    el("line", {{ x1: 720, y1: 58, x2: 738, y2: 58, class: "mean" }});
    el("text", {{ x: 744, y: 62, class: "legend" }}).textContent = "平均曲线";
    el("line", {{ x1: 720, y1: 82, x2: 738, y2: 82, class: "smooth" }});
    el("text", {{ x: 744, y: 86, class: "legend" }}).textContent = "平滑拟合";
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_reference_html(path, single_cycle, repeated_cycles, duration_stats, angle_id):
    single_json = json.dumps(single_cycle, ensure_ascii=False)
    repeated_json = json.dumps(repeated_cycles, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Standard knee-angle reference curve: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{
      margin: 24px;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      color: #1f2933;
      background: #f8fafc;
    }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    h2 {{ margin: 28px 0 10px; font-size: 16px; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      max-width: 1120px;
      margin: 16px 0 22px;
    }}
    .metric {{
      border: 1px solid #d9e2ec;
      background: #fff;
      padding: 12px 14px;
      border-radius: 6px;
    }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 19px; }}
    svg {{
      width: 100%;
      max-width: 1120px;
      height: 440px;
      background: #fff;
      border: 1px solid #d9e2ec;
      border-radius: 6px;
    }}
    .axis {{ stroke: #9fb3c8; stroke-width: 1; }}
    .grid {{ stroke: #e4e7eb; stroke-width: 1; }}
    .curve {{ fill: none; stroke: #0b69a3; stroke-width: 2.4; }}
    .band {{ fill: #dceefb; opacity: 0.85; }}
    .marker {{ stroke: #d64545; stroke-width: 1; stroke-dasharray: 4 5; }}
    .label {{ font-size: 12px; fill: #52606d; }}
    .caption {{ max-width: 1120px; color: #52606d; margin: 0 0 12px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="caption">每个周期由相邻两个局部最低点切分得到；周期时长先用 IQR 法剔除极端值，再求平均。</p>
  <section class="meta" id="metrics"></section>
  <h2>单个标准周期</h2>
  <svg id="single" viewBox="0 0 1120 440" role="img" aria-label="single standard cycle"></svg>
  <h2>连续 15 次标准参考曲线</h2>
  <svg id="repeated" viewBox="0 0 1120 440" role="img" aria-label="15 repeated standard cycles"></svg>

  <script>
    const single = {single_json};
    const repeated = {repeated_json};
    const stats = {stats_json};

    const metrics = [
      ["平均单周期时间", `${{stats.average_cycle_seconds}} s`],
      ["平均步频", `${{stats.average_cycles_per_minute}} cycles/min`],
      ["有效周期", `${{stats.used_cycles}} / ${{stats.total_cycles}}`],
      ["剔除极端周期", `${{stats.excluded_cycles}}`],
      ["保留时长范围", `${{stats.min_used_seconds}} - ${{stats.max_used_seconds}} s`],
      ["IQR 边界", `${{stats.lower_bound_seconds}} - ${{stats.upper_bound_seconds}} s`],
    ];

    document.getElementById("metrics").innerHTML = metrics.map(([label, value]) =>
      `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`
    ).join("");

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
      const el = (name, attrs = {{}}, parent = svg) => {{
        const node = document.createElementNS("http://www.w3.org/2000/svg", name);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
        parent.appendChild(node);
        return node;
      }};
      const path = rows.map((d, i) => `${{i ? "L" : "M"}}${{x(d[xKey]).toFixed(2)}},${{y(d[yKey]).toFixed(2)}}`).join(" ");

      const xTicks = options.xTicks;
      for (const tick of xTicks) {{
        el("line", {{ x1: x(tick), y1: m.top, x2: x(tick), y2: H - m.bottom, class: "grid" }});
        el("text", {{ x: x(tick), y: H - 18, "text-anchor": "middle", class: "label" }}).textContent = tick.toFixed(options.tickDecimals ?? 0);
      }}
      const yStep = Math.max(5, Math.ceil((yMax - yMin) / 7 / 5) * 5);
      for (let v = yMin; v <= yMax; v += yStep) {{
        el("line", {{ x1: m.left, y1: y(v), x2: W - m.right, y2: y(v), class: "grid" }});
        el("text", {{ x: 52, y: y(v) + 4, "text-anchor": "end", class: "label" }}).textContent = v;
      }}

      if (options.cycleDuration) {{
        for (let t = options.cycleDuration; t < xMax; t += options.cycleDuration) {{
          el("line", {{ x1: x(t), y1: m.top, x2: x(t), y2: H - m.bottom, class: "marker" }});
        }}
      }}

      el("line", {{ x1: m.left, y1: H - m.bottom, x2: W - m.right, y2: H - m.bottom, class: "axis" }});
      el("line", {{ x1: m.left, y1: m.top, x2: m.left, y2: H - m.bottom, class: "axis" }});
      el("path", {{ d: path, class: "curve" }});
      el("text", {{ x: W / 2, y: H - 4, "text-anchor": "middle", class: "label" }}).textContent = options.xLabel;
      el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "角度 (deg)";
    }}

    const oneDuration = stats.average_cycle_seconds;
    drawChart("single", single, "time_seconds", "standard_angle", {{
      xLabel: "时间 (s)",
      xTicks: Array.from({{ length: 6 }}, (_, i) => oneDuration * i / 5),
      tickDecimals: 2,
    }});

    const totalDuration = oneDuration * 15;
    drawChart("repeated", repeated, "time_seconds", "standard_angle", {{
      xLabel: "时间 (s)",
      xTicks: Array.from({{ length: 16 }}, (_, i) => oneDuration * i),
      tickDecimals: 1,
      cycleDuration: oneDuration,
    }});
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_comparison_html(
    path,
    raw_cycle_rows,
    fitted_rows,
    standard_single_cycle,
    duration_stats,
    angle_id,
):
    raw_json = json.dumps(raw_cycle_rows, ensure_ascii=False)
    fitted_json = json.dumps(fitted_rows, ensure_ascii=False)
    standard_json = json.dumps(standard_single_cycle, ensure_ascii=False)
    stats_json = json.dumps(duration_stats, ensure_ascii=False)
    title = f"Raw cycles vs standard fitted curve: {escape(angle_id)}"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{
      margin: 24px;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      color: #1f2933;
      background: #f8fafc;
    }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    .caption {{ max-width: 1120px; color: #52606d; margin: 0 0 14px; line-height: 1.5; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      max-width: 1120px;
      margin: 16px 0 18px;
    }}
    .metric {{
      border: 1px solid #d9e2ec;
      background: #fff;
      padding: 12px 14px;
      border-radius: 6px;
    }}
    .metric span {{ display: block; color: #627d98; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 19px; }}
    .legend {{
      display: flex;
      gap: 18px;
      align-items: center;
      flex-wrap: wrap;
      max-width: 1120px;
      margin: 0 0 10px;
      color: #334e68;
      font-size: 13px;
    }}
    .key {{ display: inline-flex; align-items: center; gap: 7px; }}
    .swatch {{ width: 24px; height: 4px; display: inline-block; }}
    .raw-key {{ background: rgba(82, 96, 109, 0.25); }}
    .std-key {{ background: #d64545; }}
    .mean-key {{ background: #0b69a3; }}
    .band-key {{ width: 24px; height: 12px; background: #dceefb; border: 1px solid #b6d6ee; }}
    svg {{
      width: 100%;
      max-width: 1120px;
      height: 560px;
      background: #fff;
      border: 1px solid #d9e2ec;
      border-radius: 6px;
    }}
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
  <p class="caption">灰色线为检测到的原始抬腿周期，全部按剔除极端数据后的平均周期时间重新映射；红色线为最终标准拟合曲线。</p>
  <section class="metrics" id="metrics"></section>
  <div class="legend">
    <span class="key"><i class="swatch raw-key"></i>原始周期</span>
    <span class="key"><i class="swatch band-key"></i>平均 ± 标准差</span>
    <span class="key"><i class="swatch mean-key"></i>平均曲线</span>
    <span class="key"><i class="swatch std-key"></i>标准拟合曲线</span>
  </div>
  <svg id="chart" viewBox="0 0 1120 560" role="img" aria-label="raw cycles compared with standard fitted curve"></svg>

  <script>
    const rawRows = {raw_json};
    const fittedRows = {fitted_json};
    const standardRows = {standard_json};
    const stats = {stats_json};

    const metrics = [
      ["原始周期数量", `${{stats.total_cycles}}`],
      ["用于平均时间", `${{stats.used_cycles}}`],
      ["剔除极端周期", `${{stats.excluded_cycles}}`],
      ["平均周期时间", `${{stats.average_cycle_seconds}} s`],
      ["平均步频", `${{stats.average_cycles_per_minute}} cycles/min`],
    ];
    document.getElementById("metrics").innerHTML = metrics.map(([label, value]) =>
      `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`
    ).join("");

    const svg = document.getElementById("chart");
    const W = 1120, H = 560;
    const m = {{ left: 64, right: 26, top: 26, bottom: 52 }};
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const xMax = stats.average_cycle_seconds;
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
      const key = `${{row.session_id}}-${{row.cycle_no}}`;
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
    el("text", {{ x: W / 2, y: H - 5, "text-anchor": "middle", class: "label" }}).textContent = "时间 (s)";
    el("text", {{ x: 18, y: H / 2, transform: `rotate(-90 18 ${{H / 2}})`, "text-anchor": "middle", class: "label" }}).textContent = "角度 (deg)";
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
    plt.plot(x, smooth, color="#d64545", linewidth=2.5, label="smoothed fit")
    plt.title(f"Average knee angle curve: {angle_id}")
    plt.xlabel("Normalized cycle (%)")
    plt.ylabel("Angle (deg)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()
    return True


def main():
    args = parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    cycles = []
    summary_rows = []
    cycle_rows = []
    rejected_cycle_rows = []

    for session_id in range(args.start_session, args.end_session + 1):
        url = f"{args.base_url}/{session_id}"
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

        minima_indexes = find_local_minima(
            curve,
            smooth_window=args.minima_smooth_window,
            min_distance_seconds=args.min_cycle_seconds,
        )
        session_cycles = extract_cycles_from_minima(
            curve,
            minima_indexes,
            min_cycle_seconds=args.min_cycle_seconds,
            max_cycle_seconds=args.max_cycle_seconds,
            min_cycle_amplitude=args.min_cycle_amplitude,
        )
        raw_valid_cycle_count = len(session_cycles)
        edge_trim_count = min(args.trim_edge_cycles, raw_valid_cycle_count // 2)
        if edge_trim_count > 0:
            edge_trimmed_cycles = session_cycles[edge_trim_count:-edge_trim_count]
        else:
            edge_trimmed_cycles = session_cycles

        fitting_cycles, rejected_cycles = filter_cycles_by_peak(
            edge_trimmed_cycles,
            min_peak_angle=args.min_peak_angle,
            peak_window_start=args.peak_window_start,
            peak_window_end=args.peak_window_end,
        )

        for rejected in rejected_cycles:
            rejected_cycle_rows.append(
                {
                    "session_id": session_id,
                    "cycle_no_in_session": rejected["cycle_no"],
                    "points": len(rejected["points"]),
                    "start_time": datetime.fromtimestamp(
                        rejected["points"][0][0] / 1000, timezone.utc
                    ).isoformat(),
                    "end_time": datetime.fromtimestamp(
                        rejected["points"][-1][0] / 1000, timezone.utc
                    ).isoformat(),
                    "duration_seconds": round(rejected["duration_seconds"], 3),
                    "max_angle": round(rejected["max_angle"], 6),
                    "peak_angle": round(rejected["peak_angle"], 6),
                    "peak_percent": round(rejected["peak_percent"], 6),
                    "amplitude": round(rejected["amplitude"], 6),
                    "reject_reason": rejected["reject_reason"],
                }
            )

        for cycle in fitting_cycles:
            global_cycle_no = len(cycles) + 1
            cycles.append(
                {
                    "session_id": session_id,
                    "cycle_no": cycle["cycle_no"],
                    "points": cycle["points"],
                }
            )
            cycle_rows.append(
                {
                    "global_cycle_no": global_cycle_no,
                    "session_id": session_id,
                    "cycle_no_in_session": cycle["cycle_no"],
                    "points": len(cycle["points"]),
                    "start_time": datetime.fromtimestamp(
                        cycle["points"][0][0] / 1000, timezone.utc
                    ).isoformat(),
                    "end_time": datetime.fromtimestamp(
                        cycle["points"][-1][0] / 1000, timezone.utc
                    ).isoformat(),
                    "duration_seconds": round(cycle["duration_seconds"], 3),
                    "start_angle": round(cycle["start_angle"], 6),
                    "end_angle": round(cycle["end_angle"], 6),
                    "max_angle": round(cycle["max_angle"], 6),
                    "peak_angle": round(cycle["peak_angle"], 6),
                    "peak_percent": round(cycle["peak_percent"], 6),
                    "amplitude": round(cycle["amplitude"], 6),
                }
            )

        summary_rows.append(
            {
                "session_id": session_id,
                "angle_points": len(curve),
                "local_minima": len(minima_indexes),
                "valid_cycles_before_edge_trim": raw_valid_cycle_count,
                "edge_trimmed_cycles": raw_valid_cycle_count - len(edge_trimmed_cycles),
                "cycles_after_edge_trim": len(edge_trimmed_cycles),
                "peak_filtered_cycles": len(rejected_cycles),
                "valid_cycles": len(fitting_cycles),
                "start_time": datetime.fromtimestamp(curve[0][0] / 1000, timezone.utc).isoformat(),
                "end_time": datetime.fromtimestamp(curve[-1][0] / 1000, timezone.utc).isoformat(),
                "duration_seconds": round((curve[-1][0] - curve[0][0]) / 1000, 3),
            }
        )

    if not cycles:
        raise SystemExit(
            "No valid cycles were detected. Try lowering --min-cycle-amplitude "
            "or adjusting --min-cycle-seconds/--max-cycle-seconds."
        )

    rows = fit_average_curve(cycles, args.grid_points, args.smooth_window)
    duration_stats = summarize_cycle_durations(
        cycle_rows, iqr_multiplier=args.duration_outlier_iqr
    )
    standard_single_cycle, standard_15_cycles = build_standard_reference(
        rows,
        average_cycle_seconds=duration_stats["average_cycle_seconds"],
        repeat_count=args.reference_cycles,
    )
    raw_cycle_comparison = build_raw_cycle_comparison(
        cycles,
        rows,
        average_cycle_seconds=duration_stats["average_cycle_seconds"],
        max_cycles=args.comparison_max_cycles,
    )

    curve_csv = os.path.join(args.out_dir, "normal_knee_curve.csv")
    summary_csv = os.path.join(args.out_dir, "session_summary.csv")
    cycle_csv = os.path.join(args.out_dir, "cycle_summary.csv")
    rejected_cycle_csv = os.path.join(args.out_dir, "rejected_cycle_summary.csv")
    duration_stats_csv = os.path.join(args.out_dir, "cycle_duration_stats.csv")
    raw_comparison_csv = os.path.join(args.out_dir, "raw_cycle_comparison.csv")
    standard_single_csv = os.path.join(args.out_dir, "standard_single_cycle.csv")
    standard_repeated_csv = os.path.join(
        args.out_dir, f"standard_{args.reference_cycles}_cycles.csv"
    )
    html_path = os.path.join(args.out_dir, "normal_knee_curve.html")
    reference_html_path = os.path.join(args.out_dir, "standard_reference_curve.html")
    comparison_html_path = os.path.join(args.out_dir, "raw_vs_standard_cycles.html")
    png_path = os.path.join(args.out_dir, "normal_knee_curve.png")

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
            "n_curves",
        ],
    )
    write_csv(
        summary_csv,
        summary_rows,
        [
            "session_id",
            "angle_points",
            "local_minima",
            "valid_cycles_before_edge_trim",
            "edge_trimmed_cycles",
            "cycles_after_edge_trim",
            "peak_filtered_cycles",
            "valid_cycles",
            "start_time",
            "end_time",
            "duration_seconds",
        ],
    )
    write_csv(
        cycle_csv,
        cycle_rows,
        [
            "global_cycle_no",
            "session_id",
            "cycle_no_in_session",
            "points",
            "start_time",
            "end_time",
            "duration_seconds",
            "start_angle",
            "end_angle",
            "max_angle",
            "peak_angle",
            "peak_percent",
            "amplitude",
        ],
    )
    write_csv(
        rejected_cycle_csv,
        rejected_cycle_rows,
        [
            "session_id",
            "cycle_no_in_session",
            "points",
            "start_time",
            "end_time",
            "duration_seconds",
            "max_angle",
            "peak_angle",
            "peak_percent",
            "amplitude",
            "reject_reason",
        ],
    )
    write_csv(
        duration_stats_csv,
        [duration_stats],
        [
            "total_cycles",
            "used_cycles",
            "excluded_cycles",
            "outlier_method",
            "lower_bound_seconds",
            "upper_bound_seconds",
            "min_used_seconds",
            "max_used_seconds",
            "q1_seconds",
            "median_seconds",
            "q3_seconds",
            "average_cycle_seconds",
            "sd_cycle_seconds",
            "average_cycles_per_minute",
        ],
    )
    write_csv(
        standard_single_csv,
        standard_single_cycle,
        [
            "time_seconds",
            "percent",
            "standard_angle",
            "mean_angle",
            "sd_angle",
            "cycle_duration_seconds",
        ],
    )
    write_csv(
        raw_comparison_csv,
        raw_cycle_comparison,
        ["session_id", "cycle_no", "time_seconds", "percent", "angle"],
    )
    write_csv(
        standard_repeated_csv,
        standard_15_cycles,
        [
            "cycle_index",
            "time_seconds",
            "cycle_time_seconds",
            "percent",
            "standard_angle",
            "mean_angle",
            "sd_angle",
            "cycle_duration_seconds",
        ],
    )
    write_html_plot(html_path, rows, args.angle_id)
    write_reference_html(
        reference_html_path,
        standard_single_cycle,
        standard_15_cycles,
        duration_stats,
        args.angle_id,
    )
    write_comparison_html(
        comparison_html_path,
        raw_cycle_comparison,
        rows,
        standard_single_cycle,
        duration_stats,
        args.angle_id,
    )
    png_written = write_png_if_possible(png_path, rows, args.angle_id)

    print()
    print("Done.")
    print(f"Loaded sessions: {len(summary_rows)}")
    print(f"Detected valid cycles: {len(cycles)}")
    print(
        "Average cycle duration after outlier removal: "
        f"{duration_stats['average_cycle_seconds']} s "
        f"({duration_stats['average_cycles_per_minute']} cycles/min)"
    )
    print(
        "Duration outliers excluded: "
        f"{duration_stats['excluded_cycles']} / {duration_stats['total_cycles']}"
    )
    print(f"Average curve CSV: {curve_csv}")
    print(f"Session summary CSV: {summary_csv}")
    print(f"Cycle summary CSV: {cycle_csv}")
    print(f"Rejected cycle summary CSV: {rejected_cycle_csv}")
    print(f"Cycle duration stats CSV: {duration_stats_csv}")
    print(f"Raw cycle comparison CSV: {raw_comparison_csv}")
    print(f"Standard single-cycle CSV: {standard_single_csv}")
    print(f"Standard repeated-cycle CSV: {standard_repeated_csv}")
    print(f"HTML plot: {html_path}")
    print(f"Standard reference HTML: {reference_html_path}")
    print(f"Raw-vs-standard HTML: {comparison_html_path}")
    if png_written:
        print(f"PNG plot: {png_path}")
    else:
        print("PNG plot skipped: matplotlib is not installed.")


if __name__ == "__main__":
    main()
