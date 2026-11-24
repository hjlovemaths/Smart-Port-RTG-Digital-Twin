"""Read-only replay of the STS vessel loading and unloading efficiency report."""

import json
from uuid import uuid4


TITLE = "作业流程效率统计报表(新)-装卸船"
SNAPSHOT = "omniverse/data/sts_efficiency_report.local.json"


def command(runtime, args):
    action = args["action"]
    if action == "close":
        runtime.sts_statistics = None
        return {"visible": False}

    try:
        with (runtime.project_root / SNAPSHOT).open(encoding="utf-8") as source:
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

    cranes = sorted({row[0] for row in rows})
    crane = "ALL" if action == "open" else args.get("crane", "ALL")
    if crane != "ALL" and crane not in cranes:
        raise ValueError(f"日志中没有 {crane} 的可用报表明细。可查询桥吊：{', '.join(cranes)}。")
    visible_rows = rows if crane == "ALL" else [row for row in rows if row[0] == crane]
    summary = {
        "visible": True, "revision": uuid4().hex, "title": TITLE,
        "source": "日志回放", "period": "今天昼夜", "start": start, "end": end,
        "crane": crane, "cranes": cranes, "row_count": len(visible_rows),
        "missing_replies": missing,
    }
    runtime.sts_statistics = {**summary, "columns": columns, "rows": visible_rows}
    return summary


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

    app.add_api_route(prefix + "/sts-statistics", report, methods=["GET"])
    app.add_api_route(prefix + "/sts-statistics", update, methods=["POST"])
