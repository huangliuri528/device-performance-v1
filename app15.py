# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-04（第 15 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 15 天新增与修改的源代码，不含第 1~14 天已有代码
#           本次迭代主题：新增多维自助分析模块（方案建档、多维聚合执行、结果快照、方案归档、执行日志）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 15 天新增业务模块 1：perf_analyze.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-04（第 15 天 / 共 15 天）
# 模块功能：多维自助分析管理模块。实现自助分析方案建档（分析名称、考核周期、
#           分析维度、绩效指标、汇总口径、科室范围与等级下限过滤）、按维度
#           对真实评分记录聚合计算（均值 / 最优 / 最差 / 总量四种口径）、
#           生成结果快照（维度值、聚合值、覆盖台数、组内排名与数值占比）、
#           重复执行刷新快照、方案归档封存全过程管理；执行分析须命中至少
#           一条评分记录，归档方案不可再执行，形成「方案建档 → 多维聚合 →
#           结果快照 → 排名占比 → 重复执行 → 归档封存」的自助分析闭环；
#           输出指标口径分布、方案状态分布、最新方案维度指标排行、覆盖
#           台数对比与各周期执行次数趋势五类统计图表
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime
import time

from perf_store import open_conn, fetch_department_names
from perf_ranking import fetch_period_options

# ------------------------------------------------------------------
# 分析维度字典：三种自助分析口径
# ------------------------------------------------------------------
DIM_TYPE_DICT = {
    "DEPT": "按科室聚合",
    "DEVICE": "按设备聚合",
    "GRADE": "按等级聚合",
}

# 绩效指标字典：（指标键，指标名称），键对应评分表列名
METRIC_FIELD_MAP = {
    "usage": "使用效率",
    "income": "经济效益",
    "maintain": "维保水平",
    "quality": "质量安全",
    "service": "服务效能",
    "total": "综合得分",
}

# 指标键到评分表列名的映射
METRIC_COLUMN_MAP = {
    "usage": "usage_rate",
    "income": "income_rate",
    "maintain": "maintain_rate",
    "quality": "quality_rate",
    "service": "service_rate",
    "total": "total_score",
}

# 汇总口径字典：四种聚合算法
AGGREGATE_DICT = {
    "AVG": "均值",
    "MAX": "最优",
    "MIN": "最差",
    "SUM": "总量",
}

# 方案状态字典：随执行、归档流转
CASE_STATE_DICT = {
    "DRAFT": "待执行",
    "RUN": "已执行",
    "ARCHIVED": "已归档",
}

# 日志动作字典
LOG_ACTION_DICT = {
    "SAVE": "方案建档",
    "RUN": "执行分析",
    "ARCHIVE": "归档封存",
}

# 单次结果最多保留的维度行数（超出截断，防设备维结果过长）
RESULT_ROW_LIMIT = 30

# 分页尺寸：分析方案独立分页
CASE_PAGE_SIZE = 8

# 演示方案清单
# （方案名称，周期序号，维度，指标，口径，科室范围，等级下限，目标状态）
DEMO_CASE_LIST = (
    ("全院科室综合绩效均衡分析", 0, "DEPT", "total", "AVG", "", "", "RUN"),
    ("各等级设备经济效益画像", 1, "GRADE", "income", "AVG", "", "", "RUN"),
    ("重点科室维保水平短板分析", 0, "DEPT", "maintain", "MIN",
     "手术室,急诊科,骨科", "", "RUN"),
    ("设备综合得分极值盘点", 0, "DEVICE", "total", "MAX", "", "B", "ARCHIVED"),
    ("质量安全维度全科画像", 0, "DEPT", "quality", "AVG", "", "", "DRAFT"),
)


def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def now_text():
    """返回当前时间文本 YYYY-MM-DD HH:MM:SS。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def round2(value):
    """把数值保留两位小数，空值与非数值按 0 处理。"""
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def round1(value):
    """把数值保留一位小数，空值与非数值按 0 处理。"""
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def next_seq_no(table_name):
    """读取指定业务表记录数并加一，用于拼装业务编码。"""
    conn = open_conn()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def build_case_code(seq_no):
    """生成分析方案编号：AC-0001。"""
    return "AC-%04d" % seq_no


def build_result_code(seq_no):
    """生成分析结果编号：AR-000001。"""
    return "AR-%06d" % seq_no


def build_log_code(seq_no):
    """生成分析日志编号：ALG-000001。"""
    return "ALG-%06d" % seq_no


def write_analyze_log(case_code, case_name, action_type, detail,
                      row_amount, cost_ms, operator):
    """写一条自助分析操作日志。"""
    conn = open_conn()
    try:
        conn.execute(
            "INSERT INTO pe_analyze_log (log_code, case_code, case_name, "
            "action_type, action_detail, row_amount, cost_ms, "
            "operator_name, log_time) VALUES (?,?,?,?,?,?,?,?,?)",
            (build_log_code(next_seq_no("pe_analyze_log")), case_code,
             case_name or "", action_type, detail or "",
             int(row_amount or 0), int(cost_ms or 0),
             (operator or "admin").strip(), now_text()))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_case_row(case_code):
    """按方案编号读取一条分析方案，不存在返回 None。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_analyze_case WHERE case_code=?",
            ((case_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def build_filter_sql(case):
    """按方案过滤条件构造评分记录查询的 WHERE 片段与参数。"""
    sql = "FROM pe_score_record WHERE period_tag=? "
    args = [case["period_tag"]]
    dept_list = [item.strip() for item in
                 (case["filter_depts"] or "").split(",") if item.strip()]
    if dept_list:
        marks = ",".join(["?"] * len(dept_list))
        sql += "AND dept_name IN (%s) " % marks
        args.extend(dept_list)
    grade_floor = (case["grade_floor"] or "").strip().upper()
    if grade_floor in ("A", "B", "C", "D"):
        order = ("A", "B", "C", "D")
        allowed = order[order.index(grade_floor):]
        marks = ",".join(["?"] * len(allowed))
        sql += "AND grade_level IN (%s) " % marks
        args.extend(allowed)
    return sql, args


# ------------------------------------------------------------------
# 方案建档
# ------------------------------------------------------------------
def save_analyze_case(case_code, case_name, period_tag, dim_type="DEPT",
                      metric_key="total", aggregate="AVG", filter_depts="",
                      grade_floor="", remark_text="", operator="admin"):
    """新建或编辑一套自助分析方案。

    :return: 含 success、message 与 case_code 的执行结果字典
    """
    case_name = (case_name or "").strip()
    if not case_name:
        return {"success": False, "message": "请填写分析方案名称"}
    if dim_type not in DIM_TYPE_DICT:
        return {"success": False, "message": "分析维度 %s 不合法" % dim_type}
    if metric_key not in METRIC_FIELD_MAP:
        return {"success": False, "message": "绩效指标 %s 不合法" % metric_key}
    if aggregate not in AGGREGATE_DICT:
        return {"success": False, "message": "汇总口径 %s 不合法" % aggregate}
    period_tag = (period_tag or "").strip()
    if period_tag not in fetch_period_options():
        return {"success": False,
                "message": "考核周期 %s 无评分记录" % (period_tag or "空")}
    grade_floor = (grade_floor or "").strip().upper()
    if grade_floor and grade_floor not in ("A", "B", "C", "D"):
        return {"success": False, "message": "等级下限须为 A/B/C/D"}

    dept_list = [item.strip() for item in (filter_depts or "").split(",")
                 if item.strip()]
    dept_names = [row["dept_name"] if isinstance(row, dict) else str(row)
                  for row in fetch_department_names()]
    for dept in dept_list:
        if dept not in dept_names:
            return {"success": False,
                    "message": "科室 %s 不在科室档案中" % dept}

    exist = None
    if (case_code or "").strip():
        exist = fetch_case_row(case_code)
        if exist and exist["case_state"] == "ARCHIVED":
            return {"success": False, "message": "已归档方案不可再编辑"}

    conn = open_conn()
    try:
        if exist is None:
            code = build_case_code(next_seq_no("pe_analyze_case"))
            conn.execute(
                "INSERT INTO pe_analyze_case (case_code, case_name, "
                "period_tag, dim_type, metric_key, aggregate, filter_depts, "
                "grade_floor, case_state, remark_text, operator_name) "
                "VALUES (?,?,?,?,?,?,?,?, 'DRAFT', ?, ?)",
                (code, case_name, period_tag, dim_type, metric_key, aggregate,
                 ",".join(dept_list), grade_floor, (remark_text or "").strip(),
                 (operator or "admin").strip()))
            conn.commit()
        else:
            code = exist["case_code"]
            conn.execute(
                "UPDATE pe_analyze_case SET case_name=?, period_tag=?, "
                "dim_type=?, metric_key=?, aggregate=?, filter_depts=?, "
                "grade_floor=?, case_state='DRAFT', remark_text=?, "
                "operator_name=? WHERE case_code=?",
                (case_name, period_tag, dim_type, metric_key, aggregate,
                 ",".join(dept_list), grade_floor, (remark_text or "").strip(),
                 (operator or "admin").strip(), code))
            conn.commit()
    finally:
        conn.close()
    write_analyze_log(code, case_name, "SAVE",
                      "方案 %s 建档（%s / %s / %s 口径）" % (
                          case_name, DIM_TYPE_DICT[dim_type],
                          METRIC_FIELD_MAP[metric_key],
                          AGGREGATE_DICT[aggregate]), 0, 0, operator)
    return {"success": True, "message": "分析方案 %s 已保存" % code,
            "case_code": code}


# ------------------------------------------------------------------
# 执行分析与结果快照
# ------------------------------------------------------------------
def run_analyze_case(case_code, operator="admin"):
    """执行一次自助分析：按方案维度聚合真实评分记录并生成结果快照。

    均值 / 最优 / 最差 / 总量四种口径；每次执行先清空该方案旧结果再写入
    新快照；须命中至少一条评分记录。

    :return: 含 success、message、row_amount 与 cost_ms 的结果字典
    """
    case = fetch_case_row(case_code)
    if case is None:
        return {"success": False, "message": "分析方案不存在"}
    if case["case_state"] == "ARCHIVED":
        return {"success": False, "message": "已归档方案不可再执行"}

    dim_column = {"DEPT": "dept_name", "DEVICE": "device_name",
                  "GRADE": "grade_level"}[case["dim_type"]]
    metric_column = METRIC_COLUMN_MAP[case["metric_key"]]
    func_map = {"AVG": "AVG", "MAX": "MAX", "MIN": "MIN", "SUM": "SUM"}
    sql_func = func_map[case["aggregate"]]

    where_sql, args = build_filter_sql(case)
    query = ("SELECT %s AS dim_name, %s(%s) AS metric_value, "
             "COUNT(1) AS device_amount %s GROUP BY %s ORDER BY metric_value DESC"
             % (dim_column, sql_func, metric_column, where_sql, dim_column))
    begin = time.time()
    conn = open_conn()
    try:
        rows = conn.execute(query, args).fetchall()
        if not rows:
            return {"success": False,
                    "message": "该方案条件下没有评分记录，请调整周期或过滤条件"}
        # 先清空旧结果快照，再写入新结果
        conn.execute("DELETE FROM pe_analyze_result WHERE case_code=?",
                     (case["case_code"],))
        # 先清空旧结果快照，再写入新结果；起始序号在当前连接内取一次，
        # 循环内内存自增，避免跨连接取号对未提交行不可见导致撞号
        base_no = conn.execute(
            "SELECT COALESCE(MAX(result_id), 0) FROM pe_analyze_result"
        ).fetchone()[0]
        total_value = sum(float(row["metric_value"] or 0) for row in rows)
        for index, row in enumerate(rows[:RESULT_ROW_LIMIT]):
            share = round1(float(row["metric_value"] or 0) * 100.0 /
                           total_value) if total_value else 0.0
            conn.execute(
                "INSERT INTO pe_analyze_result (result_code, case_code, "
                "dim_name, metric_value, device_amount, rank_no, share_rate, "
                "run_at) VALUES (?,?,?,?,?,?,?,?)",
                (build_result_code(base_no + index + 1),
                 case["case_code"], row["dim_name"],
                 round2(row["metric_value"]), int(row["device_amount"] or 0),
                 index + 1, share, now_text()))
        conn.execute(
            "UPDATE pe_analyze_case SET case_state='RUN', run_times="
            "run_times+1, row_amount=?, last_run_at=? WHERE case_code=?",
            (len(rows), now_text(), case["case_code"]))
        conn.commit()
    finally:
        conn.close()
    cost_ms = int((time.time() - begin) * 1000)
    write_analyze_log(case["case_code"], case["case_name"], "RUN",
                      "执行分析生成 %d 行结果（%s / %s / %s）" % (
                          len(rows), DIM_TYPE_DICT[case["dim_type"]],
                          METRIC_FIELD_MAP[case["metric_key"]],
                          AGGREGATE_DICT[case["aggregate"]]),
                      len(rows), cost_ms, operator)
    return {"success": True,
            "message": "分析完成，生成 %d 行结果快照，耗时 %d ms" % (
                len(rows), cost_ms),
            "row_amount": len(rows), "cost_ms": cost_ms}


def archive_analyze_case(case_code, operator="admin"):
    """归档封存一套分析方案：归档后不可再执行或编辑。"""
    case = fetch_case_row(case_code)
    if case is None:
        return {"success": False, "message": "分析方案不存在"}
    if case["case_state"] == "ARCHIVED":
        return {"success": False, "message": "该方案已归档"}
    if int(case["run_times"] or 0) < 1:
        return {"success": False,
                "message": "方案尚未执行过分析，不可归档"}
    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_analyze_case SET case_state='ARCHIVED' "
            "WHERE case_code=?", (case["case_code"],))
        conn.commit()
    finally:
        conn.close()
    write_analyze_log(case["case_code"], case["case_name"], "ARCHIVE",
                      "方案归档封存，保留 %d 行结果快照" % case["row_amount"],
                      case["row_amount"], 0, operator)
    return {"success": True, "message": "分析方案 %s 已归档" % case_code}


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_analyze_cases(keyword="", dim_type="", metric_key="",
                        case_state="", page=1):
    """按条件分页查询自助分析方案，返回分页结果与方案列表。"""
    sql = "SELECT * FROM pe_analyze_case WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (case_code LIKE ? OR case_name LIKE ? OR "
                "period_tag LIKE ? OR operator_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if dim_type:
        sql += "AND dim_type=? "
        args.append(dim_type)
    if metric_key:
        sql += "AND metric_key=? "
        args.append(metric_key)
    if case_state:
        sql += "AND case_state=? "
        args.append(case_state)
    conn = open_conn()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1),
            args).fetchone()["c"]
        page_amount = max(1, (total + CASE_PAGE_SIZE - 1) // CASE_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY case_id DESC LIMIT ? OFFSET ?",
            args + [CASE_PAGE_SIZE, (page_no - 1) * CASE_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["dim_label"] = DIM_TYPE_DICT.get(item["dim_type"],
                                                item["dim_type"])
        record["metric_label"] = METRIC_FIELD_MAP.get(item["metric_key"],
                                                      item["metric_key"])
        record["aggregate_label"] = AGGREGATE_DICT.get(item["aggregate"],
                                                       item["aggregate"])
        record["state_label"] = CASE_STATE_DICT.get(item["case_state"],
                                                    item["case_state"])
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_analyze_results(case_code="", limit=30):
    """查询分析结果快照，支持按方案过滤，按执行时间倒序。"""
    sql = "SELECT r.*, c.case_name, c.dim_type, c.metric_key, c.aggregate " \
          "FROM pe_analyze_result r JOIN pe_analyze_case c " \
          "ON r.case_code=c.case_code WHERE 1=1 "
    args = []
    if case_code:
        sql += "AND r.case_code=? "
        args.append(case_code)
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY r.case_code ASC, r.rank_no ASC LIMIT ?",
            args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["dim_label"] = DIM_TYPE_DICT.get(item["dim_type"],
                                                item["dim_type"])
        record["metric_label"] = METRIC_FIELD_MAP.get(item["metric_key"],
                                                      item["metric_key"])
        record["aggregate_label"] = AGGREGATE_DICT.get(item["aggregate"],
                                                       item["aggregate"])
        records.append(record)
    return records


def fetch_analyze_logs(case_code="", limit=30):
    """查询自助分析操作日志，支持按方案过滤。"""
    sql = "SELECT * FROM pe_analyze_log WHERE 1=1 "
    args = []
    if case_code:
        sql += "AND case_code=? "
        args.append(case_code)
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY log_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["action_label"] = LOG_ACTION_DICT.get(item["action_type"],
                                                     item["action_type"])
        records.append(record)
    return records


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_analyze_summary():
    """汇总自助分析核心指标：方案量、执行量、结果量与覆盖概况。"""
    conn = open_conn()
    try:
        case_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_case").fetchone()["c"]
        run_case = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_case WHERE "
            "case_state='RUN'").fetchone()["c"]
        archived_case = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_case WHERE "
            "case_state='ARCHIVED'").fetchone()["c"]
        draft_case = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_case WHERE "
            "case_state='DRAFT'").fetchone()["c"]
        run_times = conn.execute(
            "SELECT COALESCE(SUM(run_times),0) AS c FROM pe_analyze_case"
        ).fetchone()["c"]
        result_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_result").fetchone()["c"]
        top_row = conn.execute(
            "SELECT dim_name, metric_value FROM pe_analyze_result "
            "ORDER BY rank_no ASC, result_id ASC LIMIT 1").fetchone()
        avg_row = conn.execute(
            "SELECT COALESCE(AVG(metric_value),0) AS a FROM pe_analyze_result"
        ).fetchone()
        device_row = conn.execute(
            "SELECT COALESCE(SUM(device_amount),0) AS d FROM "
            "pe_analyze_result").fetchone()
        cost_row = conn.execute(
            "SELECT COALESCE(AVG(cost_ms),0) AS a FROM pe_analyze_log "
            "WHERE action_type='RUN'").fetchone()
    finally:
        conn.close()
    return {
        "case_total": case_total,
        "run_case": run_case,
        "archived_case": archived_case,
        "draft_case": draft_case,
        "run_times": run_times,
        "result_total": result_total,
        "top_dim": (top_row["dim_name"] if top_row else "--"),
        "top_value": round1(top_row["metric_value"]) if top_row else 0.0,
        "avg_value": round1(avg_row["a"]),
        "cover_device": int(device_row["d"] or 0),
        "avg_cost": round1(cost_row["a"]),
    }


def build_analyze_charts():
    """构建五类图表数据：指标口径分布、方案状态分布、最新方案维度指标
    排行、覆盖台数对比与各周期执行次数趋势。"""
    conn = open_conn()
    try:
        metric_rows = conn.execute(
            "SELECT metric_key, COUNT(1) AS c FROM pe_analyze_case "
            "GROUP BY metric_key").fetchall()
        state_rows = conn.execute(
            "SELECT case_state, COUNT(1) AS c FROM pe_analyze_case "
            "GROUP BY case_state").fetchall()
        latest_row = conn.execute(
            "SELECT case_code FROM pe_analyze_case WHERE case_state='RUN' "
            "ORDER BY last_run_at DESC, case_id DESC LIMIT 1").fetchone()
        latest_rank = []
        latest_cover = []
        if latest_row is not None:
            latest_rank = conn.execute(
                "SELECT dim_name, metric_value FROM pe_analyze_result "
                "WHERE case_code=? ORDER BY rank_no ASC LIMIT 8",
                (latest_row["case_code"],)).fetchall()
            latest_cover = conn.execute(
                "SELECT dim_name, device_amount FROM pe_analyze_result "
                "WHERE case_code=? ORDER BY rank_no ASC LIMIT 8",
                (latest_row["case_code"],)).fetchall()
        period_rows = conn.execute(
            "SELECT period_tag, COALESCE(SUM(run_times),0) AS c FROM "
            "pe_analyze_case GROUP BY period_tag").fetchall()
    finally:
        conn.close()

    metric_counter = {item["metric_key"]: item["c"] for item in metric_rows}
    metric_distribution = [
        {"name": METRIC_FIELD_MAP.get(code, code),
         "value": metric_counter.get(code, 0)}
        for code in METRIC_FIELD_MAP]
    state_counter = {item["case_state"]: item["c"] for item in state_rows}
    state_distribution = [
        {"name": CASE_STATE_DICT.get(code, code),
         "value": state_counter.get(code, 0)}
        for code in CASE_STATE_DICT]
    latest_rank_chart = [{"name": item["dim_name"],
                          "value": round2(item["metric_value"])}
                         for item in latest_rank]
    latest_cover_chart = [{"name": item["dim_name"],
                           "value": int(item["device_amount"] or 0)}
                          for item in latest_cover]
    period_trend = [{"name": item["period_tag"],
                     "value": int(item["c"] or 0)}
                    for item in period_rows]
    return {
        "metric_distribution": metric_distribution,
        "state_distribution": state_distribution,
        "latest_rank": latest_rank_chart,
        "latest_cover": latest_cover_chart,
        "period_trend": period_trend,
    }


def fetch_analyze_options():
    """自助分析页面下拉选项：字典项、周期候选与科室候选。"""
    dept_list = [row["dept_name"] if isinstance(row, dict) else str(row)
                 for row in fetch_department_names()]
    return {
        "dept_list": dept_list,
        "period_list": fetch_period_options(),
        "dim_options": [{"code": k, "label": v}
                        for k, v in DIM_TYPE_DICT.items()],
        "metric_options": [{"code": k, "label": v}
                           for k, v in METRIC_FIELD_MAP.items()],
        "aggregate_options": [{"code": k, "label": v}
                              for k, v in AGGREGATE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in CASE_STATE_DICT.items()],
        "grade_options": [{"code": k, "label": "仅统计 %s 级及以上" % k}
                          for k in ("A", "B", "C", "D")],
    }


def build_analyze_payload(keyword="", dim_type="", metric_key="",
                          case_state="", result_case="", log_case="", page=1):
    """组装多维自助分析页面所需的全部数据。"""
    pager = fetch_analyze_cases(keyword, dim_type, metric_key, case_state,
                                page)
    return {
        "summary": build_analyze_summary(),
        "charts": build_analyze_charts(),
        "cases": pager["records"],
        "case_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "results": fetch_analyze_results(result_case),
        "logs": fetch_analyze_logs(log_case),
        "options": fetch_analyze_options(),
        "result": "OK",
    }


def build_analyze_brief():
    """多维自助分析看板摘要，供绩效大屏嵌入。"""
    summary = build_analyze_summary()
    return {
        "case_total": summary["case_total"],
        "run_times": summary["run_times"],
        "result_total": summary["result_total"],
        "avg_value": summary["avg_value"],
        "avg_cost": summary["avg_cost"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_analyze():
    """灌入多维自助分析演示数据：方案建档、执行分析生成结果快照与归档
    封存，覆盖三种维度、多种口径与全部方案状态。

    :return: 生成的分析方案条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_analyze_case").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    period_options = fetch_period_options()
    for (name, period_index, dim_type, metric_key, aggregate,
         filter_depts, grade_floor, target) in DEMO_CASE_LIST:
        period_tag = period_options[period_index] \
            if period_index < len(period_options) else \
            (period_options[0] if period_options else "")
        created = save_analyze_case(
            "", name, period_tag, dim_type, metric_key, aggregate,
            filter_depts, grade_floor, "演示分析方案", "绩效办-秦主任")
        if not created.get("success"):
            continue
        case_code = created["case_code"]
        if target in ("RUN", "ARCHIVED"):
            run_analyze_case(case_code, "绩效办-秦主任")
            # 重跑一次，演示重复执行刷新快照与执行计数
            if target == "RUN" and "均衡" in name:
                run_analyze_case(case_code, "绩效办-秦主任")
        if target == "ARCHIVED":
            archive_analyze_case(case_code, "绩效办-秦主任")
    return len(DEMO_CASE_LIST)

# --------------------------------------------------------------------------
# 第 15 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY15_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY15_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 15 天新增：多维自助分析三表
-- ==================================================================
-- 自助分析方案表：按「一个分析主题一套方案」登记分析名称、考核周期、分析
-- 维度、绩效指标、汇总口径与过滤条件，支撑方案建档、执行、归档全周期管理
CREATE TABLE IF NOT EXISTS pe_analyze_case (
    case_id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 方案主键
    case_code       TEXT    NOT NULL UNIQUE,           -- 方案编号（AC-序号）
    case_name       TEXT    DEFAULT '',                -- 分析方案名称
    period_tag      TEXT    DEFAULT '',                -- 考核周期（如 2026-Q3）
    dim_type        TEXT    DEFAULT 'DEPT',            -- 分析维度（见 DIM_TYPE_DICT）
    metric_key      TEXT    DEFAULT 'total',           -- 绩效指标键（见 METRIC_FIELD_MAP）
    aggregate       TEXT    DEFAULT 'AVG',             -- 汇总口径（见 AGGREGATE_DICT）
    filter_depts    TEXT    DEFAULT '',                -- 科室范围（逗号分隔，空为全部）
    grade_floor     TEXT    DEFAULT '',                -- 等级下限过滤（A/B/C/D，空为不过滤）
    case_state      TEXT    DEFAULT 'DRAFT',           -- 方案状态（见 CASE_STATE_DICT）
    run_times       INTEGER DEFAULT 0,                 -- 累计执行次数
    row_amount      INTEGER DEFAULT 0,                 -- 最近一次结果行数
    last_run_at     TEXT    DEFAULT '',                -- 最近执行时间
    remark_text     TEXT    DEFAULT '',                -- 方案说明
    operator_name   TEXT    DEFAULT '',                -- 编制人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 自助分析结果表：一次执行按维度聚合生成一组结果快照，逐行记录维度值、
-- 指标聚合值、覆盖台数、排名与占比
CREATE TABLE IF NOT EXISTS pe_analyze_result (
    result_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 结果主键
    result_code     TEXT    NOT NULL UNIQUE,           -- 结果编号（AR-序号）
    case_code       TEXT    DEFAULT '',                -- 所属方案编号
    dim_name        TEXT    DEFAULT '',                -- 维度值（科室/设备/等级）
    metric_value    REAL    DEFAULT 0,                 -- 指标聚合值
    device_amount   INTEGER DEFAULT 0,                 -- 覆盖设备台数
    rank_no         INTEGER DEFAULT 0,                 -- 组内排名
    share_rate      REAL    DEFAULT 0,                 -- 数值占比（%）
    run_at          TEXT    DEFAULT '',                -- 执行时间
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 自助分析日志表：记录方案建档、执行、归档等动作与耗时，支撑审计追溯
CREATE TABLE IF NOT EXISTS pe_analyze_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT, -- 日志主键
    log_code        TEXT    NOT NULL UNIQUE,           -- 日志编号（ALG-序号）
    case_code       TEXT    DEFAULT '',                -- 所属方案编号
    case_name       TEXT    DEFAULT '',                -- 方案名称快照
    action_type     TEXT    DEFAULT 'RUN',             -- 动作类型（SAVE/RUN/ARCHIVE）
    action_detail   TEXT    DEFAULT '',                -- 动作说明
    row_amount      INTEGER DEFAULT 0,                 -- 结果行数
    cost_ms         INTEGER DEFAULT 0,                 -- 执行耗时（毫秒）
    operator_name   TEXT    DEFAULT '',                -- 操作人
    log_time        TEXT    DEFAULT '',                -- 动作时间
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_acase_code        ON pe_analyze_case (case_code);
CREATE INDEX IF NOT EXISTS idx_acase_period      ON pe_analyze_case (period_tag);
CREATE INDEX IF NOT EXISTS idx_acase_dim         ON pe_analyze_case (dim_type);
CREATE INDEX IF NOT EXISTS idx_acase_state       ON pe_analyze_case (case_state);
CREATE INDEX IF NOT EXISTS idx_aresult_code      ON pe_analyze_result (result_code);
CREATE INDEX IF NOT EXISTS idx_aresult_case      ON pe_analyze_result (case_code);
CREATE INDEX IF NOT EXISTS idx_aresult_rank      ON pe_analyze_result (rank_no);
CREATE INDEX IF NOT EXISTS idx_alog_code         ON pe_analyze_log (log_code);
CREATE INDEX IF NOT EXISTS idx_alog_case         ON pe_analyze_log (case_code);
CREATE INDEX IF NOT EXISTS idx_alog_action       ON pe_analyze_log (action_type);
"""


def apply_day15_schema(conn):
    """把第 15 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY15_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 15 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from perf_analyze import (
    build_analyze_payload,
    build_analyze_brief,
    save_analyze_case,
    run_analyze_case,
    archive_analyze_case,
    seed_demo_analyze,
    DIM_TYPE_DICT,
    METRIC_FIELD_MAP,
    AGGREGATE_DICT,
    CASE_STATE_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 15 天新增：大屏叠加多维自助分析摘要，供自助分析概况卡渲染
#    payload["analyze_brief"] = build_analyze_brief()

@app.route("/pf/analyze")
def page_analyze():
    """多维自助分析页面：方案建档、执行快照与归档封存（第 15 天）。"""
    return render_template(
        "page_analyze.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/analyze/panel")
def api_analyze_panel():
    """自助分析面板数据接口：概览指标、五类图表、方案、结果与日志。"""
    payload = build_analyze_payload(
        keyword=request.args.get("keyword", ""),
        dim_type=request.args.get("dim_type", ""),
        metric_key=request.args.get("metric_key", ""),
        case_state=request.args.get("case_state", ""),
        result_case=request.args.get("result_case", ""),
        log_case=request.args.get("log_case", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/pf/analyze/case/save", methods=["POST"])
def api_analyze_case_save():
    """分析方案建档接口：维度 × 指标 × 口径 × 过滤条件自由组合。"""
    body = request.get_json(silent=True) or {}
    result = save_analyze_case(
        body.get("case_code") or "",
        body.get("case_name") or "",
        body.get("period_tag") or "",
        body.get("dim_type") or "DEPT",
        body.get("metric_key") or "total",
        body.get("aggregate") or "AVG",
        body.get("filter_depts") or "",
        body.get("grade_floor") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/analyze/run", methods=["POST"])
def api_analyze_run():
    """执行分析接口：按维度聚合真实评分记录并生成结果快照。"""
    body = request.get_json(silent=True) or {}
    result = run_analyze_case(
        body.get("case_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/analyze/archive", methods=["POST"])
def api_analyze_archive():
    """方案归档接口：归档封存后不可再执行或编辑。"""
    body = request.get_json(silent=True) or {}
    result = archive_analyze_case(
        body.get("case_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/analyze/options")
def api_analyze_options():
    """自助分析下拉选项接口：维度、指标、口径与方案状态字典。"""
    return jsonify({
        "result": "OK",
        "dim_options": [{"code": k, "label": v}
                        for k, v in DIM_TYPE_DICT.items()],
        "metric_options": [{"code": k, "label": v}
                           for k, v in METRIC_FIELD_MAP.items()],
        "aggregate_options": [{"code": k, "label": v}
                              for k, v in AGGREGATE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in CASE_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_analyze()

# 【函数内插入】if __name__ == "__main__":
#    print("自助分析：   http://127.0.0.1:%d/pf/analyze" % PORT)

# --------------------------------------------------------------------------
# 第 15 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_analyze.js           新增 416 行
#   [新增] templates/page_analyze.html      新增 233 行
#   [修改] templates/screen_base.html       新增 1 行
