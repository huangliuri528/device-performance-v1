# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-28（第 8 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 8 天新增与修改的源代码，不含第 1~7 天已有代码
#           本次迭代主题：绩效报告导出模块 + 报告导出页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 8 天新增业务模块 1：perf_export.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-28（第 8 天 / 共 15 天）
# 模块功能：绩效报告导出模块。负责导出模板的维护、导出任务的创建与执行、
#           多格式报告文件的生成落盘、导出文件的在线预览与下载，以及导出
#           留痕的查询统计；支持按绩效评分明细、设备效益明细、低效预警清单
#           三路数据源，套用可配置字段的导出模板，输出 CSV、JSON、HTML 与
#           纯文本四种格式的报告文件，并按模板类型、文件格式、导出周期与
#           任务状态组合筛选查询，形成「选模板 → 定范围 → 生成文件 → 留痕
#           下载」的绩效报告交付闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）、json（Python 内置）

import datetime
import json
import os

from perf_config import BASE_DIR, DATA_DIR
from perf_store import open_conn, dict_rows
from perf_ranking import current_period_tag, fetch_period_rows
from perf_report import fetch_benefit_rows

# ------------------------------------------------------------------
# 导出文件存放目录：报告文件统一落盘到 data/export_files 下，便于留痕与下载
# ------------------------------------------------------------------
EXPORT_DIR = os.path.join(DATA_DIR, "export_files")

# 导出数据源字典：三路数据源分别对应系统已有的三类绩效数据
DATA_SOURCE_DICT = {
    "SCORE": "绩效评分明细",
    "BENEFIT": "设备效益明细",
    "WARN": "低效预警清单",
}

# 导出文件格式字典：四种输出格式，CSV 供 Excel 打开，HTML 可直接打印
FILE_FORMAT_DICT = {
    "CSV": "CSV 表格",
    "JSON": "JSON 数据",
    "HTML": "HTML 报表",
    "TXT": "纯文本报告",
}

# 导出任务状态字典
TASK_STATE_DICT = {
    "DONE": "已完成",
    "FAIL": "生成失败",
    "RUN": "生成中",
}

# 各数据源可导出的字段清单：字段编码 → 中文列名，导出模板在此范围内选取
SOURCE_FIELD_MAP = {
    "SCORE": [
        ("device_code", "设备编号"),
        ("device_name", "设备名称"),
        ("dept_name", "所属科室"),
        ("period_tag", "考核周期"),
        ("usage_rate", "使用效率"),
        ("income_rate", "经济效益"),
        ("maintain_rate", "维保水平"),
        ("quality_rate", "质量安全"),
        ("service_rate", "服务效能"),
        ("total_score", "综合得分"),
        ("grade_level", "绩效等级"),
    ],
    "BENEFIT": [
        ("device_code", "设备编号"),
        ("device_name", "设备名称"),
        ("dept_name", "所属科室"),
        ("period_tag", "考核周期"),
        ("income_amount", "检查收入"),
        ("cost_depreciate", "设备折旧"),
        ("cost_maintain", "维保支出"),
        ("cost_consumable", "耗材支出"),
        ("cost_manpower", "人力成本"),
        ("cost_total", "成本合计"),
        ("net_benefit", "净效益"),
        ("profit_rate", "收益率"),
        ("level_code", "效益等级"),
    ],
    "WARN": [
        ("device_code", "设备编号"),
        ("device_name", "设备名称"),
        ("dept_name", "所属科室"),
        ("period_tag", "考核周期"),
        ("dim_label", "预警维度"),
        ("metric_value", "维度取值"),
        ("warn_score", "预警线"),
        ("warn_level", "预警等级"),
        ("continue_periods", "连续低效期数"),
        ("trigger_text", "预警描述"),
        ("handle_state", "处置状态"),
    ],
}

# 内置导出模板：首次启动时写入，覆盖三种数据源的常见交付口径
BUILTIN_TEMPLATE_LIST = [
    ("TPL-SCORE-ALL", "全院绩效评分总表", "SCORE",
     "device_code,device_name,dept_name,period_tag,total_score,grade_level",
     "total_score", "desc", "CSV"),
    ("TPL-SCORE-DEPT", "科室绩效明细表", "SCORE",
     "dept_name,device_code,device_name,usage_rate,income_rate,total_score",
     "total_score", "desc", "HTML"),
    ("TPL-BENEFIT-ALL", "设备效益汇总表", "BENEFIT",
     "device_code,device_name,dept_name,income_amount,cost_total,net_benefit,profit_rate,level_code",
     "net_benefit", "desc", "CSV"),
    ("TPL-WARN-LIST", "低效设备预警清单", "WARN",
     "device_code,device_name,dept_name,dim_label,metric_value,warn_level,trigger_text",
     "metric_value", "asc", "TXT"),
]

# 单份报告预览最大字符数，避免超大文件把前端卡死
PREVIEW_MAX_CHARS = 3000

# 导出任务分页尺寸
EXPORT_PAGE_SIZE = 10

# 各数据源对应的底层数据表，用于推断「最近一次有数据的考核周期」
SOURCE_TABLE_MAP = {
    "SCORE": "pe_score_record",
    "BENEFIT": "pe_benefit_detail",
    "WARN": "pe_warn_record",
}


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def latest_period_tag(data_source):
    """推断指定数据源最近一次有数据的考核周期。

    评分、效益与预警三路数据的灌入节奏不同，统一按各数据源自身的最新
    周期取数，避免导出时因周期口径不一致而取到空表。
    """
    table = SOURCE_TABLE_MAP.get(data_source)
    if not table:
        return current_period_tag()
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT period_tag FROM %s ORDER BY period_tag DESC LIMIT 1" % table
        ).fetchone()
    finally:
        conn.close()
    return dict(row)["period_tag"] if row is not None else current_period_tag()


def ensure_export_dir():
    """确保导出文件目录存在。"""
    if not os.path.isdir(EXPORT_DIR):
        os.makedirs(EXPORT_DIR)
    return EXPORT_DIR


def field_label(data_source, field_code):
    """把字段编码翻译为中文列名。"""
    for code, label in SOURCE_FIELD_MAP.get(data_source, []):
        if code == field_code:
            return label
    return field_code


def build_task_code(seq_no):
    """生成导出任务编号：EXP-年月日-三位序号。"""
    return "EXP-%s-%03d" % (datetime.date.today().strftime("%Y%m%d"), seq_no)


def next_task_seq():
    """读取下一个导出任务序号。"""
    conn = open_conn()
    try:
        amount = conn.execute("SELECT COUNT(1) AS c FROM pe_export_task").fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


# ------------------------------------------------------------------
# 模板维护
# ------------------------------------------------------------------
def ensure_export_templates():
    """写入内置导出模板，已存在同名模板 code 时跳过。"""
    conn = open_conn()
    amount = 0
    try:
        for item in BUILTIN_TEMPLATE_LIST:
            exist = conn.execute(
                "SELECT template_id FROM pe_export_template WHERE template_code=?",
                (item[0],)).fetchone()
            if exist is not None:
                continue
            conn.execute(
                "INSERT INTO pe_export_template "
                "(template_code, template_name, data_source, field_text, "
                "sort_field, sort_order, file_format) VALUES (?,?,?,?,?,?,?)",
                item)
            amount += 1
        if amount:
            conn.commit()
    finally:
        conn.close()
    return amount


def fetch_export_templates(only_enabled=False):
    """读取导出模板列表。"""
    sql = "SELECT * FROM pe_export_template"
    if only_enabled:
        sql += " WHERE enabled_flag=1"
    sql += " ORDER BY template_id"
    conn = open_conn()
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    result = []
    for item in dict_rows(rows):
        item["source_label"] = DATA_SOURCE_DICT.get(item["data_source"], item["data_source"])
        item["format_label"] = FILE_FORMAT_DICT.get(item["file_format"], item["file_format"])
        item["field_amount"] = len([x for x in (item["field_text"] or "").split(",") if x])
        result.append(item)
    return result


def fetch_template_row(template_code):
    """按模板编码读取一条模板记录。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_export_template WHERE template_code=?",
            (template_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def save_export_template(template_code, template_name, data_source, field_text,
                         sort_field="", sort_order="desc", file_format="CSV",
                         enabled_flag=1):
    """新增或修改一条导出模板，字段清单必须落在数据源允许范围内。"""
    template_code = (template_code or "").strip()
    if not template_code:
        return {"success": False, "message": "模板编码不能为空"}
    if not (template_name or "").strip():
        return {"success": False, "message": "模板名称不能为空"}
    if data_source not in DATA_SOURCE_DICT:
        return {"success": False, "message": "数据源 %s 不合法" % data_source}
    if file_format not in FILE_FORMAT_DICT:
        return {"success": False, "message": "导出格式 %s 不合法" % file_format}
    allow = [code for code, _ in SOURCE_FIELD_MAP[data_source]]
    fields = [x.strip() for x in (field_text or "").split(",") if x.strip()]
    if not fields:
        return {"success": False, "message": "导出字段不能为空"}
    for code in fields:
        if code not in allow:
            return {"success": False, "message": "字段 %s 不属于该数据源" % code}
    if sort_field and sort_field not in allow:
        return {"success": False, "message": "排序字段 %s 不属于该数据源" % sort_field}
    if sort_order not in ("asc", "desc"):
        return {"success": False, "message": "排序方向只能是 asc 或 desc"}

    conn = open_conn()
    try:
        exist = conn.execute(
            "SELECT template_id FROM pe_export_template WHERE template_code=?",
            (template_code,)).fetchone()
        if exist is None:
            conn.execute(
                "INSERT INTO pe_export_template "
                "(template_code, template_name, data_source, field_text, "
                "sort_field, sort_order, file_format, enabled_flag) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (template_code, template_name.strip(), data_source,
                 ",".join(fields), sort_field, sort_order, file_format,
                 1 if enabled_flag else 0))
            tip = "新增导出模板 %s" % template_code
        else:
            conn.execute(
                "UPDATE pe_export_template SET template_name=?, data_source=?, "
                "field_text=?, sort_field=?, sort_order=?, file_format=?, "
                "enabled_flag=?, updated_at=? WHERE template_code=?",
                (template_name.strip(), data_source, ",".join(fields),
                 sort_field, sort_order, file_format, 1 if enabled_flag else 0,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 template_code))
            tip = "更新导出模板 %s" % template_code
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "%s 成功，共 %d 个导出字段" % (tip, len(fields))}


# ------------------------------------------------------------------
# 取数与渲染
# ------------------------------------------------------------------
def build_export_rows(data_source, period_tag="", dept_name=""):
    """按数据源与筛选范围取导出数据行。"""
    if data_source == "SCORE":
        rows = fetch_period_rows(period_tag) if period_tag else []
        if dept_name:
            rows = [x for x in rows if x.get("dept_name") == dept_name]
        return rows
    if data_source == "BENEFIT":
        return fetch_benefit_rows(period_tag, dept_name)
    if data_source == "WARN":
        sql = "SELECT * FROM pe_warn_record"
        where = []
        params = []
        if period_tag:
            where.append("period_tag=?")
            params.append(period_tag)
        if dept_name:
            where.append("dept_name=?")
            params.append(dept_name)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY record_id DESC"
        conn = open_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return dict_rows(rows)
    return []


def sort_export_rows(rows, sort_field, sort_order="desc"):
    """按模板配置的排序字段与方向排序，无法比较时回退为原序。"""
    if not sort_field:
        return rows

    def sort_key(item):
        value = item.get(sort_field)
        if isinstance(value, (int, float)):
            return value
        return str(value or "")

    try:
        return sorted(rows, key=sort_key, reverse=(sort_order == "desc"))
    except TypeError:
        return rows


def render_export_text(rows, fields, data_source, file_format, title="绩效报告"):
    """把数据行按指定格式渲染为报告文本。

    :return: (文本内容, 建议文件扩展名)
    """
    labels = [field_label(data_source, code) for code in fields]
    if file_format == "CSV":
        lines = [",".join(labels)]
        for item in rows:
            lines.append(",".join(str(item.get(code, "")) for code in fields))
        return "\n".join(lines), "csv"
    if file_format == "JSON":
        payload = {
            "title": title,
            "source": DATA_SOURCE_DICT.get(data_source, data_source),
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "amount": len(rows),
            "records": [{code: item.get(code, "") for code in fields} for item in rows],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), "json"
    if file_format == "HTML":
        head = ["<!DOCTYPE html><html><head><meta charset=\"utf-8\">",
                "<title>%s</title>" % title,
                "<style>body{font-family:'Microsoft YaHei',sans-serif;font-size:13px;",
                "color:#1f2937;margin:24px}h2{font-size:18px;margin-bottom:6px}",
                "table{border-collapse:collapse;width:100%}",
                "th,td{border:1px solid #cbd5e1;padding:6px 8px;text-align:left}",
                "th{background:#0ea5e9;color:#fff}",
                "tr:nth-child(even){background:#f1f5f9}</style></head><body>",
                "<h2>%s</h2>" % title,
                "<div>导出时间：%s · 共 %d 条</div><br>"
                % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(rows)),
                "<table><tr>" + "".join("<th>%s</th>" % x for x in labels) + "</tr>"]
        body = []
        for item in rows:
            cells = "".join("<td>%s</td>" % (item.get(code, "")) for code in fields)
            body.append("<tr>%s</tr>" % cells)
        tail = ["</table></body></html>"]
        return "\n".join(head + body + tail), "html"
    # 纯文本：字段按「列名：取值」逐行罗列，条目之间用分隔线隔开
    blocks = ["%s" % title,
              "数据源：%s" % DATA_SOURCE_DICT.get(data_source, data_source),
              "导出时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "记录条数：%d" % len(rows), "=" * 56]
    for index, item in enumerate(rows, start=1):
        blocks.append("%d." % index)
        for code, label in zip(fields, labels):
            blocks.append("    %s：%s" % (label, item.get(code, "")))
        blocks.append("-" * 56)
    return "\n".join(blocks), "txt"


# ------------------------------------------------------------------
# 导出任务
# ------------------------------------------------------------------
def create_export_task(template_code, period_tag="", dept_name="",
                       operator="admin", file_format=""):
    """按模板生成一份绩效报告并落盘，登记导出任务留痕。

    :return: 含 success、message、task_code 与文件信息的执行结果字典
    """
    template = fetch_template_row((template_code or "").strip())
    if template is None:
        return {"success": False, "message": "导出模板 %s 不存在" % template_code}
    if not template["enabled_flag"]:
        return {"success": False, "message": "导出模板 %s 已停用" % template_code}
    period_tag = (period_tag or "").strip() or latest_period_tag(template["data_source"])
    dept_name = (dept_name or "").strip()
    file_format = (file_format or "").strip() or template["file_format"]
    if file_format not in FILE_FORMAT_DICT:
        return {"success": False, "message": "导出格式 %s 不合法" % file_format}

    rows = build_export_rows(template["data_source"], period_tag, dept_name)
    if not rows:
        return {"success": False,
                "message": "周期 %s 下没有可导出的%s数据"
                           % (period_tag, DATA_SOURCE_DICT[template["data_source"]])}
    fields = [x for x in (template["field_text"] or "").split(",") if x]
    rows = sort_export_rows(rows, template["sort_field"], template["sort_order"])
    title = "%s（%s%s）" % (template["template_name"], period_tag,
                           "·" + dept_name if dept_name else "")
    text, ext = render_export_text(rows, fields, template["data_source"],
                                   file_format, title)

    ensure_export_dir()
    task_seq = next_task_seq()
    task_code = build_task_code(task_seq)
    file_name = "%s.%s" % (task_code, ext)
    file_path = os.path.join(EXPORT_DIR, file_name)
    try:
        encoding = "utf-8-sig" if ext == "csv" else "utf-8"
        with open(file_path, "w", encoding=encoding) as fp:
            fp.write(text)
    except OSError as err:
        conn = open_conn()
        try:
            conn.execute(
                "INSERT INTO pe_export_task "
                "(task_code, period_tag, dept_name, template_code, template_name, "
                "data_source, file_format, condition_text, record_amount, "
                "file_name, file_path, file_size, task_state, operator_name, "
                "error_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_code, period_tag, dept_name or "全院", template["template_code"],
                 template["template_name"], template["data_source"], file_format,
                 "周期=%s, 科室=%s" % (period_tag, dept_name or "全院"),
                 len(rows), "", "", 0, "FAIL", operator, str(err)))
            conn.commit()
        finally:
            conn.close()
        return {"success": False, "message": "报告文件写入失败：%s" % err}

    size = os.path.getsize(file_path)
    conn = open_conn()
    try:
        conn.execute(
            "INSERT INTO pe_export_task "
            "(task_code, period_tag, dept_name, template_code, template_name, "
            "data_source, file_format, condition_text, record_amount, "
            "file_name, file_path, file_size, task_state, operator_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_code, period_tag, dept_name or "全院", template["template_code"],
             template["template_name"], template["data_source"], file_format,
             "周期=%s, 科室=%s" % (period_tag, dept_name or "全院"),
             len(rows), file_name, file_path, size, "DONE", operator))
        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "message": "报告导出成功，%d 条记录已写入 %s（%.1f KB）"
                   % (len(rows), file_name, size / 1024.0),
        "task_code": task_code,
        "file_name": file_name,
        "file_size": size,
        "record_amount": len(rows),
    }


def fetch_export_tasks(keyword="", data_source="", file_format="",
                       task_state="", period_tag="", page=1):
    """按组合条件分页查询导出任务留痕。"""
    where = []
    params = []
    if keyword:
        where.append("(task_code LIKE ? OR template_name LIKE ? OR operator_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    if data_source:
        where.append("data_source=?")
        params.append(data_source)
    if file_format:
        where.append("file_format=?")
        params.append(file_format)
    if task_state:
        where.append("task_state=?")
        params.append(task_state)
    if period_tag:
        where.append("period_tag=?")
        params.append(period_tag)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * EXPORT_PAGE_SIZE

    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_export_task" + clause, params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM pe_export_task" + clause +
            " ORDER BY export_time DESC, task_id DESC LIMIT ? OFFSET ?",
            params + [EXPORT_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    result = []
    for item in dict_rows(rows):
        item["source_label"] = DATA_SOURCE_DICT.get(item["data_source"], item["data_source"])
        item["format_label"] = FILE_FORMAT_DICT.get(item["file_format"], item["file_format"])
        item["state_label"] = TASK_STATE_DICT.get(item["task_state"], item["task_state"])
        item["size_text"] = "%.1f KB" % (float(item["file_size"] or 0) / 1024.0)
        item["file_exists"] = bool(item["file_path"]) and os.path.isfile(item["file_path"])
        result.append(item)
    return {
        "records": result,
        "total": total,
        "page": page_no,
        "page_amount": max(1, (total + EXPORT_PAGE_SIZE - 1) // EXPORT_PAGE_SIZE),
    }


def fetch_export_preview(task_code):
    """读取导出文件的正文预览，超长部分截断。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_export_task WHERE task_code=?", (task_code,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"success": False, "message": "导出任务 %s 不存在" % task_code,
                "text": ""}
    task = dict(row)
    if not task["file_path"] or not os.path.isfile(task["file_path"]):
        return {"success": False, "message": "报告文件已不存在，可能已被清理",
                "text": ""}
    try:
        with open(task["file_path"], "r", encoding="utf-8-sig") as fp:
            text = fp.read(PREVIEW_MAX_CHARS + 200)
    except OSError as err:
        return {"success": False, "message": "报告文件读取失败：%s" % err, "text": ""}
    truncated = len(text) > PREVIEW_MAX_CHARS
    return {
        "success": True,
        "message": "报告 %s 预览%s" % (task_code, "（已截断）" if truncated else ""),
        "text": text[:PREVIEW_MAX_CHARS],
        "truncated": truncated,
        "file_name": task["file_name"],
        "file_format": task["file_format"],
    }


def read_export_file(task_code):
    """按任务编号定位报告文件路径，供下载接口使用。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT file_name, file_path FROM pe_export_task WHERE task_code=?",
            (task_code,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, None
    return dict(row)["file_name"], dict(row)["file_path"]


def remove_export_task(task_code):
    """删除一条导出任务留痕，并同步清理已落盘的报告文件。"""
    file_name, file_path = read_export_file(task_code)
    if file_name is None:
        return {"success": False, "message": "导出任务 %s 不存在" % task_code}
    if file_path and os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    conn = open_conn()
    try:
        conn.execute("DELETE FROM pe_export_task WHERE task_code=?", (task_code,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "导出任务 %s 已删除，报告文件同步清理" % task_code}


# ------------------------------------------------------------------
# 统计与面板
# ------------------------------------------------------------------
def build_export_summary():
    """汇总导出概况：任务总量、今日导出、成功失败分布与文件占用。"""
    conn = open_conn()
    try:
        total = conn.execute("SELECT COUNT(1) AS c FROM pe_export_task").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_export_task WHERE date(export_time)=?",
            (today_text(),)).fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_export_task WHERE task_state='DONE'"
        ).fetchone()["c"]
        fail = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_export_task WHERE task_state='FAIL'"
        ).fetchone()["c"]
        size_row = conn.execute(
            "SELECT COALESCE(SUM(file_size),0) AS s FROM pe_export_task").fetchone()
        record_row = conn.execute(
            "SELECT COALESCE(SUM(record_amount),0) AS s FROM pe_export_task").fetchone()
    finally:
        conn.close()
    return {
        "total_task": total,
        "today_amount": today,
        "done_amount": done,
        "fail_amount": fail,
        "size_total": round(float(size_row["s"] or 0) / 1024.0, 1),
        "record_total": int(record_row["s"] or 0),
        "template_amount": len(fetch_export_templates()),
    }


def build_export_charts():
    """构建导出页三类图表：模板数据源分布、文件格式分布与近 6 个月导出趋势。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT data_source, file_format, export_time, record_amount "
            "FROM pe_export_task").fetchall()
    finally:
        conn.close()
    source_counter = {}
    format_counter = {}
    for item in rows:
        source_counter[item["data_source"]] = source_counter.get(item["data_source"], 0) + 1
        format_counter[item["file_format"]] = format_counter.get(item["file_format"], 0) + 1

    today = datetime.date.today()
    month_keys = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    month_counter = {key: 0 for key in month_keys}
    for item in rows:
        text = (item["export_time"] or "")[:7]
        if text in month_counter:
            month_counter[text] += 1

    return {
        "source_distribution": [
            {"name": DATA_SOURCE_DICT.get(code, code), "value": amount}
            for code, amount in source_counter.items()
        ],
        "format_distribution": [
            {"name": FILE_FORMAT_DICT.get(code, code), "value": amount}
            for code, amount in format_counter.items()
        ],
        "month_trend": {
            "months": month_keys,
            "values": [month_counter[key] for key in month_keys],
        },
    }


def fetch_export_options():
    """导出页下拉选项：模板清单、周期清单、科室清单与各类字典。"""
    conn = open_conn()
    try:
        period_rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_export_task ORDER BY period_tag DESC"
        ).fetchall()
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM pe_export_task "
            "WHERE dept_name<>'' ORDER BY dept_name").fetchall()
    finally:
        conn.close()
    periods = [dict(item)["period_tag"] for item in period_rows]
    for code in SOURCE_TABLE_MAP:
        tag = latest_period_tag(code)
        if tag not in periods:
            periods.append(tag)
    if current_period_tag() not in periods:
        periods.append(current_period_tag())
    periods.sort(reverse=True)
    return {
        "template_list": fetch_export_templates(),
        "period_list": periods,
        "dept_list": [dict(item)["dept_name"] for item in dept_rows],
        "source_options": [{"code": k, "label": v} for k, v in DATA_SOURCE_DICT.items()],
        "format_options": [{"code": k, "label": v} for k, v in FILE_FORMAT_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in TASK_STATE_DICT.items()],
        "field_map": {key: [{"code": c, "label": l} for c, l in value]
                      for key, value in SOURCE_FIELD_MAP.items()},
    }


def build_export_payload(keyword="", data_source="", file_format="",
                         task_state="", period_tag="", page=1):
    """组装报告导出页面所需的全部数据：汇总指标、三类图表、分页明细与下拉选项。"""
    pager = fetch_export_tasks(keyword, data_source, file_format, task_state,
                               period_tag, page)
    return {
        "summary": build_export_summary(),
        "charts": build_export_charts(),
        "records": pager["records"],
        "total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "options": fetch_export_options(),
        "result": "OK",
    }


def build_export_brief():
    """报告导出大屏摘要，供绩效分析大屏嵌入。"""
    summary = build_export_summary()
    return {
        "total_task": summary["total_task"],
        "today_amount": summary["today_amount"],
        "record_total": summary["record_total"],
        "template_amount": summary["template_amount"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_export():
    """生成演示导出任务：按内置模板各导出一份历史报告，形成留痕样例。

    :return: 生成的导出任务条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_export_task").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()
    ensure_export_templates()
    amount = 0
    operators = ("绩效办-赵主任", "绩效办-钱专员", "设备科-孙科长")
    for index, template in enumerate(fetch_export_templates(only_enabled=True)):
        result = create_export_task(
            template["template_code"],
            latest_period_tag(template["data_source"]), "",
            operators[index % len(operators)])
        if result.get("success"):
            amount += 1
    # 追加一份按科室口径导出的样例
    result = create_export_task("TPL-SCORE-DEPT", latest_period_tag("SCORE"),
                                "放射科", "绩效办-钱专员")
    if result.get("success"):
        amount += 1
    return amount

# --------------------------------------------------------------------------
# 第 8 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY8_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY8_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 8 天新增：绩效报告导出模板表
-- ==================================================================
-- 导出模板表：按「一套交付口径一个模板」维护报告导出配置，含数据来源、
-- 导出字段清单、排序字段与方向、默认文件格式与启用状态，使同一份绩效数据
-- 可以按不同管理口径反复导出，避免每次手工挑选字段
CREATE TABLE IF NOT EXISTS pe_export_template (
    template_id   INTEGER PRIMARY KEY AUTOINCREMENT,   -- 模板主键
    template_code TEXT    NOT NULL UNIQUE,             -- 模板编码
    template_name TEXT    NOT NULL,                    -- 模板名称
    data_source   TEXT    DEFAULT 'SCORE',             -- 数据来源（见 DATA_SOURCE_DICT）
    field_text    TEXT    DEFAULT '',                  -- 导出字段清单（逗号分隔编码）
    sort_field    TEXT    DEFAULT '',                  -- 排序字段编码
    sort_order    TEXT    DEFAULT 'desc',              -- 排序方向：asc / desc
    file_format   TEXT    DEFAULT 'CSV',               -- 默认导出格式（见 FILE_FORMAT_DICT）
    enabled_flag  INTEGER DEFAULT 1,                   -- 是否启用：1 启用 / 0 停用
    updated_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 8 天新增：绩效报告导出任务表
-- ==================================================================
-- 导出任务表：按「一次导出一条」记录报告生成全过程，含套用模板、统计范围、
-- 文件格式、记录条数、落盘路径、文件大小与任务状态，支撑报告在线预览、
-- 文件下载与导出留痕审计
CREATE TABLE IF NOT EXISTS pe_export_task (
    task_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 任务主键
    task_code      TEXT    DEFAULT '',                 -- 任务编号（EXP-日期-序号）
    period_tag     TEXT    DEFAULT '',                 -- 考核周期
    dept_name      TEXT    DEFAULT '',                 -- 统计科室（全院时为「全院」）
    template_code  TEXT    DEFAULT '',                 -- 套用模板编码
    template_name  TEXT    DEFAULT '',                 -- 套用模板名称快照
    data_source    TEXT    DEFAULT 'SCORE',            -- 数据来源快照
    file_format    TEXT    DEFAULT 'CSV',              -- 导出文件格式
    condition_text TEXT    DEFAULT '',                 -- 导出条件摘要
    record_amount  INTEGER DEFAULT 0,                  -- 导出记录条数
    file_name      TEXT    DEFAULT '',                 -- 报告文件名
    file_path      TEXT    DEFAULT '',                 -- 报告落盘路径
    file_size      INTEGER DEFAULT 0,                  -- 报告文件大小（字节）
    task_state     TEXT    DEFAULT 'DONE',             -- 任务状态（见 TASK_STATE_DICT）
    operator_name  TEXT    DEFAULT 'admin',            -- 导出操作人
    error_text     TEXT    DEFAULT '',                 -- 失败原因
    export_time    TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_exptpl_code    ON pe_export_template (template_code);
CREATE INDEX IF NOT EXISTS idx_exptask_code   ON pe_export_task (task_code);
CREATE INDEX IF NOT EXISTS idx_exptask_period ON pe_export_task (period_tag);
CREATE INDEX IF NOT EXISTS idx_exptask_source ON pe_export_task (data_source);
CREATE INDEX IF NOT EXISTS idx_exptask_state  ON pe_export_task (task_state);
CREATE INDEX IF NOT EXISTS idx_exptask_time   ON pe_export_task (export_time);
"""


def apply_day8_schema(conn):
    """把第 8 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY8_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 8 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-28（第 8 天 / 共 15 天）
#           绩效对比、可视化图表、效益报表、低效设备预警与绩效报告导出
#           八类页面路由，启动时完成建表、等级规则初始化、科室初始化与
#           演示评分、上期对照、效益明细、低效预警、导出模板与导出留痕
#           数据灌入，并提供评分试算、评分入库、评分明细、权重元信息、
#           榜单、科室横向对比、等级批量重判、ECharts 图表 option、多维
#           度排行筛选查询、效益报表核算归档、低效预警规则维护扫描整改，
#           以及导出模板维护、报告生成落盘、文件预览下载与留痕删除接口
import os

from flask import Flask, render_template, jsonify, redirect, url_for, request, send_file

from perf_export import (
    build_export_payload,
    build_export_brief,
    create_export_task,
    save_export_template,
    fetch_export_preview,
    read_export_file,
    remove_export_task,
    seed_demo_export,
    DATA_SOURCE_DICT,
    FILE_FORMAT_DICT,
    TASK_STATE_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 8 天新增：大屏叠加报告导出摘要，供导出概况卡渲染
#    payload["export_brief"] = build_export_brief()

@app.route("/pf/export")
def page_export():
    """绩效报告导出页面：导出模板、报告文件生成与导出留痕管理。"""
    return render_template(
        "page_export.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/export/panel")
def api_export_panel():
    """报告导出数据接口：汇总指标、三类图表、模板清单、分页留痕与下拉选项。"""
    payload = build_export_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        data_source=(request.args.get("data_source") or "").strip(),
        file_format=(request.args.get("file_format") or "").strip(),
        task_state=(request.args.get("task_state") or "").strip(),
        period_tag=(request.args.get("period_tag") or "").strip(),
        page=int(request.args.get("page", 1)),
    )
    return jsonify(payload)


@app.route("/api/pf/export/task", methods=["POST"])
def api_export_task():
    """按模板生成一份绩效报告并落盘，登记导出留痕。"""
    body = request.get_json(silent=True) or {}
    result = create_export_task(
        (body.get("template_code") or "").strip(),
        (body.get("period_tag") or "").strip(),
        (body.get("dept_name") or "").strip(),
        (body.get("operator") or "admin").strip(),
        (body.get("file_format") or "").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/export/template", methods=["POST"])
def api_export_template():
    """新增或修改一条导出模板。"""
    body = request.get_json(silent=True) or {}
    result = save_export_template(
        (body.get("template_code") or "").strip(),
        (body.get("template_name") or "").strip(),
        (body.get("data_source") or "").strip(),
        (body.get("field_text") or "").strip(),
        (body.get("sort_field") or "").strip(),
        (body.get("sort_order") or "desc").strip(),
        (body.get("file_format") or "CSV").strip(),
        body.get("enabled_flag", 1),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/export/preview")
def api_export_preview():
    """读取导出报告文件的正文预览。"""
    result = fetch_export_preview((request.args.get("task_code") or "").strip())
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/export/download")
def api_export_download():
    """下载已生成的绩效报告文件。"""
    file_name, file_path = read_export_file((request.args.get("task_code") or "").strip())
    if not file_path or not os.path.isfile(file_path):
        return jsonify({"result": "FAIL", "message": "报告文件不存在或已被清理"})
    return send_file(file_path, as_attachment=True, download_name=file_name)


@app.route("/api/pf/export/remove", methods=["POST"])
def api_export_remove():
    """删除一条导出留痕并同步清理报告文件。"""
    body = request.get_json(silent=True) or {}
    result = remove_export_task((body.get("task_code") or "").strip())
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)



# 【函数内插入】def api_health_check():
#    """启动引导：建表 → 初始化规则与科室 → 灌入演示评分、上期对照、效益
#    明细、低效预警与报告导出留痕。"""
#    seed_demo_export()

# 【函数内插入】if __name__ == "__main__":
#    print("报告导出：   http://127.0.0.1:%d/pf/export" % PORT)

# --------------------------------------------------------------------------
# 第 8 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_export.js            新增 390 行
#   [新增] templates/page_export.html       新增 218 行
#   [修改] templates/screen_base.html       新增 1 行
