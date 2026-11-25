"""Read-only replay of the STS vessel loading and unloading efficiency report."""

import json
from collections import defaultdict
from math import isfinite
from statistics import mean, median
from uuid import uuid4


TITLE = "作业流程效率统计报表(新)-装卸船"
SNAPSHOT = "omniverse/data/sts_efficiency_report.local.json"
METRICS = {
    "task_duration": "任务用时(s)",
    "truck_wait": "等集卡时间(s)",
    "bay_change": "动贝用时(s)",
    "bay_scan": "贝扫用时(s)",
    "other_time": "杂项时间(s)",
}
TASK_LABELS = {"LOAD": "装船", "DSCH": "卸船", "OTHER": "其他"}
ANALYSIS_VIEWS = ("ranking", "by_crane", "by_task_type")
CHART_VALUES = {
    "mean_seconds": "平均",
    "median_seconds": "中位数",
    "min_seconds": "最短",
    "max_seconds": "最长",
    "row_count": "作业记录数",
}
GROUP_FIELDS = (
    "桥吊编号", "任务类型", "司机工号", "船名", "船型", "车道", "车号",
    "车类型", "箱尺寸", "作业工艺", "工作模式", "工作方式", "作业贝位号", "任务阻断标记",
)
MEASURE_FIELDS = tuple(METRICS.values())


def read_snapshot(project_root):
    try:
        with (project_root / SNAPSHOT).open(encoding="utf-8") as source:
            snapshot = json.load(source)
        columns, rows = snapshot["columns"], snapshot["rows"]
        start, end = snapshot["start"], snapshot["end"]
        missing = snapshot["missing_replies"]
        if (not isinstance(columns, list) or not columns or not all(isinstance(name, str) for name in columns)
                or not isinstance(start, str) or not isinstance(end, str) or type(missing) is not int or missing < 0
                or not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(columns)
                                                    or not isinstance(row[0], str) for row in rows)):
            raise ValueError("报表字段与明细格式不一致")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("无法读取桥吊统计演示数据，请检查本地日志回放文件。") from exc
    return snapshot


def command(runtime, args):
    action = args["action"]
    if action == "close":
        runtime.sts_statistics = None
        return {"visible": False}

    snapshot = read_snapshot(runtime.project_root)
    columns, rows = snapshot["columns"], snapshot["rows"]
    cranes = sorted({row[0] for row in rows})
    crane = "ALL" if action == "open" else args.get("crane", "ALL")
    if crane != "ALL" and crane not in cranes:
        raise ValueError(f"日志中没有 {crane} 的可用报表明细。可查询桥吊：{', '.join(cranes)}。")
    visible_rows = rows if crane == "ALL" else [row for row in rows if row[0] == crane]
    summary = {
        "visible": True, "revision": uuid4().hex, "title": TITLE,
        "source": "日志回放", "period": "今天昼夜", "start": snapshot["start"], "end": snapshot["end"],
        "crane": crane, "cranes": cranes, "row_count": len(visible_rows),
        "missing_replies": snapshot["missing_replies"],
    }
    runtime.sts_statistics = {**summary, "columns": columns, "rows": visible_rows}
    return summary


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        return None
    return seconds if isfinite(seconds) and seconds >= 0 else None


def _timing_summary(values):
    valid = [value for value in values if value is not None]
    return {
        "row_count": len(values),
        "valid_count": len(valid),
        "invalid_count": len(values) - len(valid),
        "mean_seconds": round(mean(valid), 2) if valid else None,
        "median_seconds": round(median(valid), 2) if valid else None,
        "min_seconds": min(valid) if valid else None,
        "max_seconds": max(valid) if valid else None,
    }


def _task_type(value):
    code = str(value).strip().rsplit("-", 1)[-1].strip().upper()
    return {"LOAD": "LOAD", "装船": "LOAD", "DSCH": "DSCH", "卸船": "DSCH"}.get(code, "OTHER")


def analyze(project_root, args):
    snapshot = read_snapshot(project_root)
    columns = {
        "".join(label.replace("（", "(").replace("）", ")").split()): index
        for index, label in enumerate(snapshot["columns"])
    }
    metric = args.get("metric", "task_duration")
    metric_label = METRICS[metric]
    for label in ("任务类型", metric_label):
        if label not in columns:
            raise ValueError(f"报表缺少「{label}」列，无法完成该项分析。")

    crane = args.get("crane", "ALL")
    task_type = args.get("task_type", "ALL")
    top_n = args.get("top_n", 5)
    by_crane = defaultdict(list)
    by_task_type = defaultdict(list)
    values = []
    ranked = []
    for row in snapshot["rows"]:
        if crane != "ALL" and row[0] != crane:
            continue
        code = _task_type(row[columns["任务类型"]])
        if task_type != "ALL" and code != task_type:
            continue
        seconds = _seconds(row[columns[metric_label]])
        values.append(seconds)
        by_crane[row[0]].append(seconds)
        by_task_type[code].append(seconds)
        if top_n and seconds is not None:
            ranked.append((seconds, row, code))

    longest = sorted(ranked, key=lambda record: record[0], reverse=True)[:top_n]
    return {
        "title": TITLE, "source": "日志回放", "period": "今天昼夜",
        "start": snapshot["start"], "end": snapshot["end"],
        "snapshot_missing_replies": snapshot["missing_replies"],
        "covered_cranes": sorted({row[0] for row in snapshot["rows"]}),
        "filters": {"crane": crane, "task_type": task_type},
        "metric": metric, "metric_label": metric_label,
        "overall": _timing_summary(values),
        "by_crane": {key: _timing_summary(group) for key, group in sorted(by_crane.items())},
        "by_task_type": {key: _timing_summary(group) for key, group in sorted(by_task_type.items())},
        "longest": [
            {"crane": row[0], "task_type": code, "seconds": seconds,
             **{key: row[columns[label]] for key, label in (
                 ("start", "开始时间"), ("end", "结束时间"), ("block_marker", "任务阻断标记")
             ) if label in columns}}
            for seconds, row, code in longest
        ],
    }


def show_analysis_report(runtime, result, args):
    view = args.get("report_view", "by_crane")
    report_format = args.get("report_format", "table")
    metric_name = result["metric_label"].removesuffix("(s)")
    series = []
    unit = "s"
    if view == "ranking":
        limit = args.get("top_n", 5)
        if not limit:
            raise ValueError("耗时排行至少需要 1 条，请将 top_n 设为 1–20。")
        title = f"{metric_name}排行 · 前 {limit} 票"
        columns = ["排名", "桥吊编号", "任务类型", result["metric_label"], "开始时间", "结束时间", "任务阻断标记"]
        rows = [
            [rank, item["crane"], TASK_LABELS[item["task_type"]], item["seconds"],
             item.get("start"), item.get("end"), item.get("block_marker")]
            for rank, item in enumerate(result["longest"], start=1)
        ]
        if report_format == "chart":
            series = [{"label": f"{rank}. {crane} · {task}", "value": seconds, "detail": start or ""}
                      for rank, crane, task, seconds, start, _, _ in rows]
    else:
        by_crane = view == "by_crane"
        group_label = "桥吊" if by_crane else "任务类型"
        title = f"{metric_name}对比 · 按{group_label}"
        columns = [group_label, "作业记录", "有效样本", "平均(s)", "中位数(s)", "最短(s)", "最长(s)"]
        fields = ("row_count", "valid_count", "mean_seconds", "median_seconds", "min_seconds", "max_seconds")
        rows = [[group if by_crane else TASK_LABELS[group], *(summary[field] for field in fields)]
                for group, summary in result[view].items()]
        if report_format == "chart":
            chart_value = args.get("chart_value", "mean_seconds")
            chart_label = CHART_VALUES[chart_value]
            if chart_value == "row_count":
                title = f"{chart_label} · 按{group_label}"
                unit = "条"
            else:
                title = f"{chart_label}{metric_name} · 按{group_label}"
            series = [{"label": label, "value": values[fields.index(chart_value)],
                       "detail": f"{values[1]} 条有效样本"}
                      for label, *values in rows]

    crane = result["filters"]["crane"]
    task_type = result["filters"]["task_type"]
    runtime.sts_analysis_report = {
        "visible": True, "revision": uuid4().hex, "title": title, "view": view, "format": report_format,
        "start": result["start"], "end": result["end"], "source": result["source"],
        "scope": f"{'全部桥吊' if crane == 'ALL' else crane} · {TASK_LABELS.get(task_type, '全部任务')}",
        "missing_replies": result["snapshot_missing_replies"],
        "overall": result["overall"], "columns": columns, "rows": rows,
        "series": series, "unit": unit,
    }
    return {"visible": True, "title": title, "view": view, "format": report_format, "row_count": len(rows)}


def query_data(runtime, args):
    snapshot = read_snapshot(runtime.project_root)
    normalized = lambda name: "".join(name.replace("（", "(").replace("）", ")").split())
    columns = {normalized(name): index for index, name in enumerate(snapshot["columns"])}
    group = args.get("group_by", "桥吊编号")
    measure = args.get("measure", "任务用时(s)")
    operation = args.get("operation", "mean")
    filter_field = args.get("filter_field")
    for field in (group, filter_field, measure if operation != "count" else None):
        if field and normalized(field) not in columns and field != "开始小时":
            raise ValueError(f"报表中没有「{field}」列。")
    if group == "开始小时" and "开始时间" not in columns:
        raise ValueError("报表中没有「开始时间」列。")
    if filter_field and "filter_value" not in args:
        raise ValueError("按字段筛选时请提供 filter_value。")
    if "filter_value" in args and not filter_field:
        raise ValueError("filter_value 需要指定 filter_field。")

    groups = defaultdict(list)
    accepted = 0
    for row in snapshot["rows"]:
        if filter_field and args["filter_value"].casefold() not in str(row[columns[normalized(filter_field)]]).casefold():
            continue
        accepted += 1
        key = row[columns[normalized(group)]] if group != "开始小时" else row[columns["开始时间"]][:13]
        groups[str(key)].append(_seconds(row[columns[normalized(measure)]]) if operation != "count" else 1)

    def calculate(values):
        usable = [value for value in values if value is not None]
        if operation == "count":
            return len(values)
        if not usable:
            return None
        return round({"sum": sum, "mean": mean, "median": median, "min": min, "max": max}[operation](usable), 2)

    points = [{"label": label, "value": calculate(values), "sample_count": len(values),
               "valid_count": sum(value is not None for value in values)}
              for label, values in groups.items()]
    reverse = args.get("sort", "descending") == "descending"
    points.sort(key=lambda point: (point["value"] is not None, point["value"] if point["value"] is not None else 0),
                reverse=reverse)
    limit = args.get("limit", 20)
    points = points[:limit]
    title = f"按{group}统计{measure if operation != 'count' else '作业记录数'}"
    unit = "条" if operation == "count" else "s"
    result = {
        "title": title, "source": "日志回放", "start": snapshot["start"], "end": snapshot["end"],
        "missing_replies": snapshot["missing_replies"], "matched_rows": accepted,
        "group_by": group, "measure": measure, "operation": operation,
        "filter": {filter_field: args["filter_value"]} if filter_field else {},
        "points": points, "unit": unit,
    }
    if args.get("report_format"):
        runtime.sts_analysis_report = {
            "visible": True, "revision": uuid4().hex, "title": title, "format": args["report_format"],
            "source": result["source"], "start": result["start"], "end": result["end"],
            "scope": f"{group} · {operation}", "missing_replies": result["missing_replies"],
            "overall": {"row_count": sum(point["sample_count"] for point in points),
                        "valid_count": sum(point["valid_count"] for point in points),
                        "invalid_count": sum(point["sample_count"] - point["valid_count"] for point in points)},
            "columns": [group, "结果", "样本数", "有效值数"],
            "rows": [[point["label"], point["value"], point["sample_count"], point["valid_count"]] for point in points],
            "series": [{"label": point["label"], "value": point["value"],
                        "detail": f"{point['valid_count']} 条有效值"} for point in points],
            "unit": unit,
        }
        result["report"] = {"visible": True, "format": args["report_format"], "row_count": len(points)}
    return result


def install_routes(app, prefix, execute):
    from fastapi import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    def require_admin(request):
        if request.scope.get("smartport_session", {}).get("role") != "admin":
            raise HTTPException(403, "仅管理员可查看桥吊统计报表")

    async def report(request: Request):
        require_admin(request)
        return JSONResponse(await execute("sts-statistics-data", {}), headers={"Cache-Control": "private, no-store"})

    async def update(request: Request, payload: dict):
        require_admin(request)
        return await execute("sts-statistics-control", payload)

    async def analysis(request: Request):
        require_admin(request)
        return JSONResponse(await execute("sts-analysis-data", {}), headers={"Cache-Control": "private, no-store"})

    async def close_analysis(request: Request, payload: dict):
        require_admin(request)
        if payload.get("action") != "close":
            raise HTTPException(400, "仅支持关闭分析报表")
        return await execute("sts-analysis-close", payload)

    app.add_api_route(prefix + "/sts-statistics", report, methods=["GET"])
    app.add_api_route(prefix + "/sts-statistics", update, methods=["POST"])
    app.add_api_route(prefix + "/sts-analysis", analysis, methods=["GET"])
    app.add_api_route(prefix + "/sts-analysis", close_analysis, methods=["POST"])
