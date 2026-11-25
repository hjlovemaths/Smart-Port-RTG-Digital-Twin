"""Administrator-imported tables for the Agent's read-only analytical queries."""

import csv
import io
import json
import math
import re
import sqlite3
import time
from pathlib import Path
from uuid import uuid4
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile


MAX_BYTES = 2 * 1024 * 1024
MAX_XLSX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLUMNS = 80
MAX_CELLS = 300000
RESULT_LIMIT = 200
NUMBER = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
FUNCTIONS = frozenset("""
    abs avg coalesce count date datetime dense_rank first_value ifnull instr julianday
    lag last_value lead length like lower ltrim max min nullif rank replace round row_number
    rtrim strftime substr sum time total trim typeof upper
""".split())


def _records(records):
    if not isinstance(records, list) or not records or len(records) > MAX_ROWS:
        raise ValueError("JSON 需要非空的记录数组，最多 20000 行")
    columns = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("每条 JSON 记录都应是含字段名的对象")
        columns.update(dict.fromkeys(record))
        if len(columns) > MAX_COLUMNS or len(columns) * len(records) > MAX_CELLS:
            raise ValueError("数据超过列数或单元格上限")
    return list(columns), ([record.get(name) for name in columns] for record in records)


def _number(value):
    if isinstance(value, bool) or not NUMBER.fullmatch(str(value)):
        return None
    try:
        if "." not in str(value) and "e" not in str(value).lower():
            integer = int(value)
            return integer if -(2**63) <= integer < 2**63 else None
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


class Dataset:
    def __init__(self, name, columns, records):
        if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_COLUMNS:
            raise ValueError("表头需要 1 至 80 个字段")
        if any(not isinstance(col, str) or not col.strip() or len(col) > 128 or "\x00" in col for col in columns):
            raise ValueError("字段名应为非空文本，最多 128 字符")
        columns = [col.strip() for col in columns]
        if len(set(col.casefold() for col in columns)) != len(columns):
            raise ValueError("表中有重名字段，请调整表头")
        rows = []
        numeric = [True] * len(columns)
        present = [0] * len(columns)
        for record in records:
            if len(rows) >= MAX_ROWS or (len(rows) + 1) * len(columns) > MAX_CELLS:
                raise ValueError("最多支持 20000 行、300000 个单元格")
            if not isinstance(record, (tuple, list)) or len(record) != len(columns):
                raise ValueError(f"第 {len(rows) + 1} 条记录的字段数量与表头不同")
            row = []
            for i, value in enumerate(record):
                if isinstance(value, (dict, list)) or len(str(value)) > 4096:
                    raise ValueError("单元格需为标量，最多 4096 字符")
                if value is None or value == "":
                    row.append(None)
                else:
                    present[i] += 1
                    numeric[i] &= _number(value) is not None
                    row.append(str(value))
            rows.append(row)
        self.name = name
        self.id = uuid4().hex
        self.count = len(rows)
        self.fields = []
        definitions = []
        for i, column in enumerate(columns):
            kind = "NUMERIC" if numeric[i] and present[i] else "TEXT"
            self.fields.append({"name": column, "type": kind, "nulls": self.count - present[i]})
            definitions.append('"' + column.replace('"', '""') + '" ' + kind)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("CREATE TABLE data (" + ", ".join(definitions) + ")")
            self.db.executemany("INSERT INTO data VALUES (" + ",".join("?" for _ in columns) + ")", rows)
            self.db.commit()
            self.db.execute("PRAGMA query_only=ON")
            if hasattr(self.db, "setconfig") and hasattr(sqlite3, "SQLITE_DBCONFIG_DQS_DML"):
                self.db.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
            if hasattr(self.db, "setlimit"):
                for limit, value in ((sqlite3.SQLITE_LIMIT_LENGTH, 524288), (sqlite3.SQLITE_LIMIT_COLUMN, MAX_COLUMNS),
                                     (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 40), (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 8)):
                    self.db.setlimit(limit, value)
        except Exception:
            self.db.close()
            raise

    def describe(self):
        return {"dataset_id": self.id, "source": self.name, "table": "data", "row_count": self.count,
                "fields": self.fields}

    def analyze(self, args):
        if args["dataset_id"] != self.id:
            raise ValueError("已切换数据源，请重新读取字段")
        sql = args["sql"].strip()
        if len(sql) > 3000 or not re.match(r"^(SELECT|WITH)\b", sql, re.I):
            raise ValueError("仅支持针对 data 的单条 SELECT 查询")
        read = False

        def authorize(op, table, column, database, trigger):
            nonlocal read
            if op == sqlite3.SQLITE_READ and table == "data" and database in ("main", None):
                read = True
                return sqlite3.SQLITE_OK
            if op == sqlite3.SQLITE_SELECT or (op == sqlite3.SQLITE_FUNCTION and column in FUNCTIONS):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        end = time.monotonic() + 0.5
        self.db.set_authorizer(authorize)
        self.db.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        cursor = self.db.cursor()
        try:
            cursor.execute(sql)
            if not read:
                raise ValueError("查询必须读取当前数据表 data")
            columns = [item[0] for item in cursor.description]
            if len(columns) != len(set(columns)):
                raise ValueError("请为重名的结果列设置不同的别名")
            visible = []
            truncated = False
            budget = 150000
            for row in cursor:
                if any(isinstance(v, bytes) or (isinstance(v, float) and not math.isfinite(v)) for v in row):
                    raise ValueError("结果应为文本、有限数值或空值")
                budget -= len(json.dumps(row, ensure_ascii=False))
                if len(visible) == RESULT_LIMIT or budget < 0:
                    if not visible:
                        raise ValueError("单行结果过大，请减少返回字段")
                    truncated = True
                    break
                visible.append(row)
        except sqlite3.Error as exc:
            raise ValueError(f"数据查询失败：{exc}") from exc
        finally:
            cursor.close()
            self.db.set_progress_handler(None, 0)
            self.db.set_authorizer(None)
        fmt = args.get("format", "table")
        series = []
        if fmt in ("chart", "line", "pie"):
            if args.get("x") not in columns or args.get("y") not in columns:
                raise ValueError("图表的 x、y 必须对应查询结果的列名")
            x, y = columns.index(args["x"]), columns.index(args["y"])
            for row in visible:
                if row[y] is not None and not isinstance(row[y], (int, float)):
                    raise ValueError("图表的 y 列需要是数值")
                series.append({"label": str(row[x]) if row[x] is not None else "（空）",
                               "value": row[y], "detail": args["y"]})
        if fmt == "pie":
            if truncated:
                raise ValueError("类别过多，请将其余类别合并为「其他」后再画饼图")
            values = [point["value"] for point in series]
            if any(value is None or value < 0 for value in values):
                raise ValueError("饼图需要非负的有效数值，负值对比可用条形图")
            scale = max(values, default=0)
            if scale == 0:
                raise ValueError("饼图各类别的合计必须大于零")
            total = math.fsum(value / scale for value in values)
            for point in series:
                point["share"] = (point["value"] / scale) / total
        return {"visible": True, "revision": uuid4().hex, "view": "dataset", "format": fmt,
                "source": self.name, "input_rows": self.count, "title": args["title"], "columns": columns,
                "rows": visible, "series": series, "unit": args.get("unit", ""), "truncated": truncated}


def _import_xlsx(name, raw):
    try:
        from defusedxml.common import DefusedXmlException
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:
        raise ValueError("Excel 导入组件未加载，请重启 Kit 完成依赖安装") from exc

    stream = io.BytesIO(raw)
    try:
        with ZipFile(stream) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_XLSX_EXPANDED_BYTES:
                raise ValueError("Excel 文件解压后超过 32 MiB")
        stream.seek(0)
        workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
        try:
            sheet = next((sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"), None)
            if sheet is None:
                raise ValueError("Excel 工作簿没有可见工作表")
            # Some exporters write A1 as the dimension even when the sheet is populated.
            sheet.reset_dimensions()
            rows = enumerate(sheet.values, 1)
            for index, row in rows:
                if index > MAX_ROWS + 1:
                    raise ValueError("Excel 表格最多支持 20000 条数据行")
                if any(value not in (None, "") for value in row):
                    break
            else:
                raise ValueError("Excel 工作表没有表头")

            last_column = max(i for i, value in enumerate(row, 1) if value not in (None, ""))
            columns = list(row[:last_column])
            if last_column > MAX_COLUMNS:
                raise ValueError("Excel 表头最多支持 80 列")

            def records():
                for index, values in rows:
                    if index > MAX_ROWS + 1:
                        raise ValueError("Excel 表格最多支持 20000 条数据行")
                    if any(value not in (None, "") for value in values[last_column:]):
                        raise ValueError(f"Excel 第 {index} 行在表头范围之外有数据")
                    if any(value not in (None, "") for value in values):
                        yield values[:last_column] + (None,) * max(0, last_column - len(values))

            return Dataset(name, columns, records())
        finally:
            workbook.close()
    except (BadZipFile, DefusedXmlException, InvalidFileException, KeyError, OSError, ParseError) as exc:
        raise ValueError("无法读取 Excel 工作簿，请检查 .xlsx 文件是否完整") from exc


def import_file(name, raw):
    if not isinstance(name, str) or not 0 < len(name) <= 180 or len(raw) > MAX_BYTES:
        raise ValueError("请选择不超过 2 MiB 的数据文件")
    suffix = Path(name).suffix.lower()
    if suffix == ".xlsx":
        return _import_xlsx(name, raw)
    if suffix not in (".csv", ".tsv", ".json", ".jsonl", ".ndjson"):
        raise ValueError("支持 Excel（.xlsx）、CSV、TSV、JSON 和 JSONL 数据表")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("无法读取文本编码，请将文件保存为 UTF-8 或 GB18030 后重新导入") from exc
    if suffix in (".csv", ".tsv"):
        reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t" if suffix == ".tsv" else ",", strict=True)
        columns = next(reader, [])
        rows = (row for row in reader if row)
    elif suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "columns" in data and isinstance(data.get("rows"), list):
            columns, rows = data["columns"], data["rows"]
        else:
            columns, rows = _records(data)
    else:
        columns, rows = _records([json.loads(line) for line in text.splitlines() if line.strip()])
    return Dataset(name, columns, rows)


def execute(runtime, name, args):
    dataset = runtime.dataset
    if name == "describe_dataset" and args.get("source") == "sts_replay":
        from .sts_statistics import read_snapshot
        snapshot = read_snapshot(runtime.project_root)
        replay = Dataset(f"桥吊日志回放 · {snapshot['start']} 至 {snapshot['end']}", snapshot["columns"], snapshot["rows"])
        if runtime.replay_dataset:
            runtime.replay_dataset.db.close()
        runtime.replay_dataset = replay
        dataset = replay
    if name == "analyze_dataset" and runtime.replay_dataset and args["dataset_id"] == runtime.replay_dataset.id:
        dataset = runtime.replay_dataset
    if dataset is None:
        raise ValueError("尚未导入数据，请在管理员网页点击「导入数据」选择数据表")
    if name == "describe_dataset":
        return dataset.describe()
    report = dataset.analyze(args)
    publish = report["format"] != "preview"
    if publish:
        if len(runtime.dataset_reports) >= 12:
            raise ValueError("一次提问最多显示 12 张分析报表")
        runtime.dataset_reports.append(report)
        runtime.sts_analysis_report = (
            {**report, "reports": list(runtime.dataset_reports)}
            if len(runtime.dataset_reports) > 1 else report
        )
    sample = []
    remaining = 8000
    for row in report["rows"][:20]:
        remaining -= len(json.dumps(row, ensure_ascii=False))
        if remaining < 0:
            break
        sample.append(row)
    return {"source": dataset.name, "input_rows": dataset.count, "columns": report["columns"],
            "result_preview": sample, "displayed_rows": len(report["rows"]), "truncated": report["truncated"],
            "report": {"visible": publish, "title": report["title"], "format": report["format"]}}


def install_routes(app, prefix, execute_command):
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request

    async def upload(request: Request):
        if request.scope.get("smartport_session", {}).get("role") != "admin":
            raise HTTPException(403, "仅管理员可以导入分析数据")
        raw = bytearray()
        async for block in request.stream():
            if len(raw) + len(block) > MAX_BYTES:
                raise HTTPException(413, "文件超过 2 MiB")
            raw.extend(block)
        try:
            dataset = await asyncio.to_thread(import_file, request.query_params.get("name"), raw)
        except (ValueError, csv.Error, RecursionError, sqlite3.Error) as exc:
            raise HTTPException(400, str(exc)) from exc
        return await execute_command("dataset-import", {"dataset": dataset})

    app.add_api_route(prefix + "/dataset", upload, methods=["POST"])
