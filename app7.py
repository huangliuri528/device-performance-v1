# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-27（第 7 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 7 天新增与修改的源代码，不含第 1~6 天已有代码
#           本次迭代主题：低效设备预警模块 + 低效预警页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 7 天新增业务模块 1：perf_warn.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-27（第 7 天 / 共 15 天）
# 模块功能：低效设备预警模块。实现按考核周期扫描全院设备绩效评分，依据
#           可配置的「预警维度 + 黄色预警线 + 红色预警线 + 连续低效期数」
#           规则识别低效设备，自动判定预警等级（黄色关注 / 红色严重），
#           支持预警规则增删改、扫描重算、组合筛选分页查询、预警确认与
#           整改闭环，输出预警等级分布、预警维度分布、科室预警分布、连续
#           低效期数分布与低效设备排行五类图表，并针对每台设备的最短板
#           维度生成差异化整改建议，形成「扫描 → 预警 → 处置 → 复核」闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from perf_config import LOW_BENEFIT_ALERT_SCORE
from perf_store import open_conn, dict_rows, fetch_department_names
from perf_ranking import current_period_tag, fetch_period_rows, RANK_DIMENSION_MAP

# 预警判定维度：键为维度编码，值为（维度名称，评分表字段名）
WARN_DIMENSION_MAP = {
    "total": ("综合得分", "total_score"),
    "usage": ("使用效率", "usage_rate"),
    "income": ("经济效益", "income_rate"),
    "maintain": ("维保水平", "maintain_rate"),
    "quality": ("质量安全", "quality_rate"),
    "service": ("服务效能", "service_rate"),
}

# 预警等级字典：黄色为关注级，红色为严重级
WARN_LEVEL_DICT = {
    "YELLOW": "黄色关注",
    "RED": "红色严重",
}

# 预警处置状态字典：待处理 → 已确认 → 已整改关闭
WARN_STATE_DICT = {
    "OPEN": "待处理",
    "CONFIRM": "已确认",
    "CLOSED": "已整改关闭",
}

# 默认预警规则：黄色预警线与红色预警线，以及升级为红色所需的连续低效期数
DEFAULT_WARN_RULES = [
    ("total", LOW_BENEFIT_ALERT_SCORE, 60.0, 2),
    ("usage", 65.0, 50.0, 2),
    ("income", 65.0, 50.0, 2),
    ("maintain", 65.0, 50.0, 3),
    ("quality", 70.0, 55.0, 1),
    ("service", 65.0, 50.0, 3),
]

# 单页条数
WARN_PAGE_SIZE = 10

# 预警编号前缀，编号形如 WRN-2026Q4-0001
WARN_CODE_PREFIX = "WRN"

# 连续低效期数达到该值即视为长期低效，单独标注
LONG_TERM_PERIODS = 3


def round2(value):
    """保留两位小数，空值与非数值按 0 处理。"""
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def period_code(period_tag):
    """把周期标签转为编号片段，如 2026-Q4 → 2026Q4。"""
    return (period_tag or "").replace("-", "")


def latest_score_period():
    """返回已有绩效评分的最新考核周期；无数据时回退为当前所属周期。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT period_tag FROM pe_score_record "
            "ORDER BY substr(period_tag,1,4) DESC, substr(period_tag,-1) DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row["period_tag"] if row else current_period_tag()


def previous_period_tags(period_tag, amount=4):
    """返回指定周期之前的若干期标签，用于统计连续低效期数。"""
    result = []
    text = (period_tag or "").strip()
    if "-" not in text:
        return result
    year_text, quarter_text = text.split("-", 1)
    try:
        year_value = int(year_text)
        quarter_value = int(quarter_text.upper().replace("Q", ""))
    except ValueError:
        return result
    for _ in range(amount):
        quarter_value -= 1
        if quarter_value <= 0:
            quarter_value = 4
            year_value -= 1
        result.append("%d-Q%d" % (year_value, quarter_value))
    return result


def count_continue_periods(device_code, dim_key, period_tag, warn_score, period_rows_map):
    """统计设备在某维度上「截至当期」连续低于预警线的期数（含当期）。"""
    field_name = WARN_DIMENSION_MAP.get(dim_key, ("", "total_score"))[1]
    continue_amount = 0
    tags = [period_tag] + previous_period_tags(period_tag, 4)
    for tag in tags:
        rows = period_rows_map.get(tag) or []
        hit = None
        for row in rows:
            if row["device_code"] == device_code:
                hit = row
                break
        if hit is None:
            break
        value = hit.get(field_name)
        if value is None:
            break
        if float(value) >= float(warn_score):
            break
        continue_amount += 1
    return continue_amount


def judge_warn_level(value, warn_score, danger_score, continue_amount, continue_rule):
    """判定预警等级：低于红色线、或连续低效期数达到阈值即红色，否则黄色。"""
    if float(value) < float(danger_score):
        return "RED"
    if continue_rule and continue_amount >= int(continue_rule):
        return "RED"
    return "YELLOW"


# ==================================================================
# 预警规则维护
# ==================================================================
def fetch_warn_rules():
    """读取全部预警规则，附加维度名称。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_warn_rule ORDER BY rule_id").fetchall()
    finally:
        conn.close()
    result = []
    for item in dict_rows(rows):
        item["dim_label"] = WARN_DIMENSION_MAP.get(
            item["dim_key"], (item["dim_key"], ""))[0]
        result.append(item)
    return result


def save_warn_rule(payload):
    """新增或更新一条预警规则，返回统一结果字典。"""
    rule_id = payload.get("rule_id")
    dim_key = (payload.get("dim_key") or "").strip()
    if dim_key not in WARN_DIMENSION_MAP:
        return {"success": False, "message": "预警维度编码不合法"}
    try:
        warn_score = float(payload.get("warn_score"))
        danger_score = float(payload.get("danger_score"))
        continue_rule = int(payload.get("continue_periods", 2))
    except (TypeError, ValueError):
        return {"success": False, "message": "阈值与连续期数必须是数字"}
    if not (0 <= danger_score <= warn_score <= 100):
        return {"success": False, "message": "需满足 0 ≤ 红色预警线 ≤ 黄色预警线 ≤ 100"}
    if continue_rule < 1:
        return {"success": False, "message": "连续低效期数至少为 1 期"}

    enabled = 1 if str(payload.get("enabled_flag", 1)) in ("1", "true", "True") else 0
    conn = open_conn()
    try:
        if rule_id:
            conn.execute(
                "UPDATE pe_warn_rule SET dim_key=?, warn_score=?, danger_score=?, "
                "continue_periods=?, enabled_flag=?, updated_at=? WHERE rule_id=?",
                (dim_key, warn_score, danger_score, continue_rule, enabled,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rule_id),
            )
            message = "预警规则已更新"
        else:
            exist = conn.execute(
                "SELECT rule_id FROM pe_warn_rule WHERE dim_key=?", (dim_key,)
            ).fetchone()
            if exist is not None:
                return {"success": False, "message": "该预警维度已配置规则，请直接修改"}
            conn.execute(
                "INSERT INTO pe_warn_rule (dim_key, warn_score, danger_score, "
                "continue_periods, enabled_flag) VALUES (?,?,?,?,?)",
                (dim_key, warn_score, danger_score, continue_rule, enabled),
            )
            message = "预警规则已新增"
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": message}


def remove_warn_rule(rule_id):
    """删除一条预警规则。"""
    conn = open_conn()
    try:
        conn.execute("DELETE FROM pe_warn_rule WHERE rule_id=?", (rule_id,))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "预警规则已删除"}


def seed_demo_warn_rules():
    """首次启动时灌入默认预警规则，已存在则不覆盖。"""
    conn = open_conn()
    try:
        if conn.execute("SELECT COUNT(1) FROM pe_warn_rule").fetchone()[0] > 0:
            return 0
        for dim_key, warn_score, danger_score, continue_rule in DEFAULT_WARN_RULES:
            conn.execute(
                "INSERT INTO pe_warn_rule (dim_key, warn_score, danger_score, "
                "continue_periods, enabled_flag) VALUES (?,?,?,?,1)",
                (dim_key, warn_score, danger_score, continue_rule),
            )
        conn.commit()
    finally:
        conn.close()
    return len(DEFAULT_WARN_RULES)


# ==================================================================
# 低效扫描
# ==================================================================
def scan_low_efficiency(period_tag=""):
    """按预警规则扫描指定周期的设备绩效，生成或更新预警记录。

    同一设备、同一周期、同一维度的预警只保留一条：重复扫描时更新等级、
    取值与连续低效期数，新增次数累加，避免预警记录刷屏。
    """
    period_tag = (period_tag or "").strip() or latest_score_period()
    rules = [rule for rule in fetch_warn_rules() if rule["enabled_flag"] == 1]
    if not rules:
        return {"success": False, "message": "未配置启用状态的预警规则", "period_tag": period_tag}

    rows = fetch_period_rows(period_tag)
    if not rows:
        return {"success": False, "message": "考核周期 %s 暂无绩效评分数据" % period_tag,
                "period_tag": period_tag}

    # 预取历史各期评分，用于计算连续低效期数
    period_rows_map = {period_tag: rows}
    for tag in previous_period_tags(period_tag, 4):
        period_rows_map[tag] = fetch_period_rows(tag)

    conn = open_conn()
    created = 0
    updated = 0
    try:
        for rule in rules:
            dim_key = rule["dim_key"]
            field_name = WARN_DIMENSION_MAP.get(dim_key, ("", "total_score"))[1]
            dim_label = WARN_DIMENSION_MAP.get(dim_key, (dim_key, ""))[0]
            for row in rows:
                value = round2(row.get(field_name) or 0)
                if value >= float(rule["warn_score"]):
                    continue
                continue_amount = count_continue_periods(
                    row["device_code"], dim_key, period_tag,
                    rule["warn_score"], period_rows_map) or 1
                level_code = judge_warn_level(
                    value, rule["warn_score"], rule["danger_score"],
                    continue_amount, rule["continue_periods"])
                trigger_text = "%s %.2f 分，低于预警线 %.2f 分" % (
                    dim_label, value, float(rule["warn_score"]))
                exist = conn.execute(
                    "SELECT record_id, trigger_amount FROM pe_warn_record "
                    "WHERE period_tag=? AND device_code=? AND dim_key=?",
                    (period_tag, row["device_code"], dim_key),
                ).fetchone()
                if exist is None:
                    code_amount = conn.execute(
                        "SELECT COUNT(1) FROM pe_warn_record WHERE period_tag=?",
                        (period_tag,)).fetchone()[0]
                    warn_code = "%s-%s-%04d" % (
                        WARN_CODE_PREFIX, period_code(period_tag), code_amount + 1)
                    conn.execute(
                        "INSERT INTO pe_warn_record (warn_code, period_tag, "
                        "device_code, device_name, dept_name, dim_key, dim_label, "
                        "metric_value, warn_score, warn_level, trigger_text, "
                        "continue_periods, handle_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')",
                        (warn_code, period_tag, row["device_code"], row["device_name"],
                         row["dept_name"], dim_key, dim_label, value,
                         round2(rule["warn_score"]), level_code, trigger_text,
                         continue_amount),
                    )
                    created += 1
                else:
                    conn.execute(
                        "UPDATE pe_warn_record SET metric_value=?, warn_level=?, "
                        "trigger_text=?, continue_periods=?, "
                        "trigger_amount=trigger_amount+1 WHERE record_id=?",
                        (value, level_code, trigger_text, continue_amount, exist["record_id"]),
                    )
                    updated += 1
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "period_tag": period_tag,
            "message": "扫描完成：新增预警 %d 条，更新 %d 条" % (created, updated),
            "created": created, "updated": updated}


# ==================================================================
# 预警查询与处置
# ==================================================================
def build_warn_condition(period_tag="", dept_name="", warn_level="", dim_key="",
                         handle_state="", keyword=""):
    """拼装预警记录筛选条件。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag = ?")
        params.append(period_tag)
    if dept_name:
        clauses.append("dept_name = ?")
        params.append(dept_name)
    if warn_level:
        clauses.append("warn_level = ?")
        params.append(warn_level)
    if dim_key:
        clauses.append("dim_key = ?")
        params.append(dim_key)
    if handle_state:
        clauses.append("handle_state = ?")
        params.append(handle_state)
    if keyword:
        clauses.append("(warn_code LIKE ? OR device_code LIKE ? OR device_name LIKE ?)")
        like_text = "%%%s%%" % keyword
        params.extend([like_text, like_text, like_text])
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def fetch_warn_records(period_tag="", dept_name="", warn_level="", dim_key="",
                       handle_state="", keyword="", page=1, size=WARN_PAGE_SIZE):
    """分页查询预警记录，附加等级名称、状态名称与整改建议。"""
    try:
        page_no = max(int(page), 1)
    except (TypeError, ValueError):
        page_no = 1
    try:
        page_size = max(int(size), 1)
    except (TypeError, ValueError):
        page_size = WARN_PAGE_SIZE
    offset = (page_no - 1) * page_size

    where_sql, params = build_warn_condition(
        period_tag, dept_name, warn_level, dim_key, handle_state, keyword)
    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql, params).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM pe_warn_record" + where_sql +
            " ORDER BY CASE warn_level WHEN 'RED' THEN 0 ELSE 1 END, "
            "continue_periods DESC, metric_value ASC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    finally:
        conn.close()

    records = []
    for item in dict_rows(rows):
        item["level_label"] = WARN_LEVEL_DICT.get(item["warn_level"], "未分级")
        item["state_label"] = WARN_STATE_DICT.get(item["handle_state"], "未知")
        item["advice_text"] = build_single_advice(item)
        item["long_term_flag"] = (item["continue_periods"] or 0) >= LONG_TERM_PERIODS
        records.append(item)
    total_page = (total + page_size - 1) // page_size if page_size else 1
    return records, total, total_page, page_no


def build_single_advice(record):
    """针对单条预警生成整改建议：按预警维度给出差异化的处置指引。"""
    dim_key = record.get("dim_key")
    value = round2(record.get("metric_value"))
    periods = record.get("continue_periods") or 1
    base_map = {
        "total": "综合得分偏低，建议列为院级重点帮扶设备，组织专项评估",
        "usage": "使用效率偏低，建议核查开机时段排班并推进院内共享共用",
        "income": "经济效益偏低，建议复核收费项目与检查量，评估外送或调剂",
        "maintain": "维保水平偏低，建议缩短保养周期并核查故障停机原因",
        "quality": "质量安全偏低，建议立即安排质控校准并复核操作规范",
        "service": "服务效能偏低，建议扩大适用检查项目范围并加强宣传推介",
    }
    advice = base_map.get(dim_key, "建议组织专项评估")
    if periods >= LONG_TERM_PERIODS:
        advice += "；该指标已连续 %d 期低于预警线，建议纳入设备更新或报废评估" % periods
    return advice


def handle_warn_record(record_id, state_code, handler_name, handle_note=""):
    """预警处置：确认或整改关闭，关闭后不再重复提醒。"""
    if state_code not in ("CONFIRM", "CLOSED"):
        return {"success": False, "message": "处置状态编码不合法"}
    handler_name = (handler_name or "").strip()
    if not handler_name:
        return {"success": False, "message": "处置人不能为空"}

    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_warn_record WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            return {"success": False, "message": "预警记录不存在"}
        conn.execute(
            "UPDATE pe_warn_record SET handle_state=?, handler_name=?, "
            "handle_note=?, handle_time=? WHERE record_id=?",
            (state_code, handler_name, (handle_note or "").strip(),
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), record_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "预警状态更新为「%s」" % WARN_STATE_DICT[state_code]}


# ==================================================================
# 统计与图表
# ==================================================================
def build_warn_summary(period_tag=""):
    """统计当期预警总量、红色预警量、待处理量、涉及设备与科室数。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag=?")
        params.append(period_tag)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql, params).fetchone()[0]
        red_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql +
            ((" AND" if where_sql else " WHERE") + " warn_level='RED'"),
            params).fetchone()[0]
        open_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql +
            ((" AND" if where_sql else " WHERE") + " handle_state='OPEN'"),
            params).fetchone()[0]
        closed_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql +
            ((" AND" if where_sql else " WHERE") + " handle_state='CLOSED'"),
            params).fetchone()[0]
        device_amount = conn.execute(
            "SELECT COUNT(DISTINCT device_code) FROM pe_warn_record" + where_sql,
            params).fetchone()[0]
        dept_amount = conn.execute(
            "SELECT COUNT(DISTINCT dept_name) FROM pe_warn_record" + where_sql,
            params).fetchone()[0]
        long_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_warn_record" + where_sql +
            ((" AND" if where_sql else " WHERE") + " continue_periods>=%d" % LONG_TERM_PERIODS),
            params).fetchone()[0]
    finally:
        conn.close()
    return {
        "total": total,
        "red_amount": red_amount,
        "yellow_amount": total - red_amount,
        "open_amount": open_amount,
        "closed_amount": closed_amount,
        "device_amount": device_amount,
        "dept_amount": dept_amount,
        "long_term_amount": long_amount,
        "close_rate": round2(closed_amount * 100.0 / total) if total else 0.0,
    }


def build_warn_charts(period_tag=""):
    """构建预警等级、维度、科室、连续期数分布与低效设备排行五类图表数据。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag=?")
        params.append(period_tag)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = open_conn()
    try:
        level_rows = conn.execute(
            "SELECT warn_level, COUNT(1) AS amount FROM pe_warn_record" + where_sql +
            " GROUP BY warn_level", params).fetchall()
        dim_rows = conn.execute(
            "SELECT dim_key, COUNT(1) AS amount FROM pe_warn_record" + where_sql +
            " GROUP BY dim_key", params).fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS amount, "
            "SUM(CASE WHEN warn_level='RED' THEN 1 ELSE 0 END) AS red_amount "
            "FROM pe_warn_record" + where_sql + " GROUP BY dept_name "
            "ORDER BY amount DESC LIMIT 8", params).fetchall()
        period_rows = conn.execute(
            "SELECT continue_periods, COUNT(1) AS amount FROM pe_warn_record" +
            where_sql + " GROUP BY continue_periods ORDER BY continue_periods",
            params).fetchall()
        device_rows = conn.execute(
            "SELECT device_code, device_name, dept_name, "
            "SUM(continue_periods) AS total_periods, COUNT(1) AS warn_amount, "
            "SUM(CASE WHEN warn_level='RED' THEN 1 ELSE 0 END) AS red_amount "
            "FROM pe_warn_record" + where_sql + " GROUP BY device_code, device_name "
            "ORDER BY warn_amount DESC, total_periods DESC LIMIT 10", params).fetchall()
    finally:
        conn.close()

    level_distribution = [
        {"name": WARN_LEVEL_DICT.get(row["warn_level"], row["warn_level"]),
         "value": row["amount"]}
        for row in level_rows
    ]
    dim_distribution = [
        {"name": WARN_DIMENSION_MAP.get(row["dim_key"], (row["dim_key"], ""))[0],
         "value": row["amount"]}
        for row in dim_rows
    ]
    dept_distribution = [
        {"name": row["dept_name"], "value": row["amount"], "red_amount": row["red_amount"]}
        for row in dept_rows
    ]
    continue_distribution = [
        {"name": "连续 %d 期" % row["continue_periods"], "value": row["amount"]}
        for row in period_rows
    ]
    device_ranking = [
        {"device_code": row["device_code"], "device_name": row["device_name"],
         "dept_name": row["dept_name"], "warn_amount": row["warn_amount"],
         "red_amount": row["red_amount"], "total_periods": row["total_periods"]}
        for row in device_rows
    ]
    return {
        "level_distribution": level_distribution,
        "dim_distribution": dim_distribution,
        "dept_distribution": dept_distribution,
        "continue_distribution": continue_distribution,
        "device_ranking": device_ranking,
    }


def fetch_warn_periods():
    """读取已有绩效评分的考核周期清单，供页面周期下拉框使用。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_score_record "
            "ORDER BY period_tag DESC").fetchall()
    finally:
        conn.close()
    return [row["period_tag"] for row in rows]


def build_warn_payload(period_tag="", dept_name="", warn_level="", dim_key="",
                       handle_state="", keyword="", page=1):
    """组装预警页面所需的全部数据：规则、汇总、图表、分页明细。"""
    current_tag = current_period_tag()
    period_tag = period_tag or latest_score_period()
    records, total, total_page, page_no = fetch_warn_records(
        period_tag, dept_name, warn_level, dim_key, handle_state, keyword, page)
    return {
        "result": "OK",
        "period_tag": period_tag,
        "current_period": current_tag,
        "rules": fetch_warn_rules(),
        "summary": build_warn_summary(period_tag),
        "charts": build_warn_charts(period_tag),
        "records": records,
        "total": total,
        "total_page": total_page,
        "page": page_no,
        "options": {
            "level_dict": WARN_LEVEL_DICT,
            "state_dict": WARN_STATE_DICT,
            "dim_dict": {k: v[0] for k, v in WARN_DIMENSION_MAP.items()},
            "dept_list": fetch_department_names(),
            "period_list": fetch_warn_periods(),
        },
    }


def build_warn_brief():
    """低效预警大屏摘要，供绩效分析大屏嵌入。"""
    summary = build_warn_summary(latest_score_period())
    return {
        "total": summary["total"],
        "red_amount": summary["red_amount"],
        "open_amount": summary["open_amount"],
        "text": "红色 %d 条 / 待处理 %d 条" % (summary["red_amount"], summary["open_amount"]),
    }


def seed_demo_warn():
    """首次启动时按默认规则扫描当期，生成演示预警数据。"""
    seed_demo_warn_rules()
    conn = open_conn()
    try:
        if conn.execute("SELECT COUNT(1) FROM pe_warn_record").fetchone()[0] > 0:
            return 0
    finally:
        conn.close()
    result = scan_low_efficiency(latest_score_period())
    return result.get("created", 0)

# --------------------------------------------------------------------------
# 第 7 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY7_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY7_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 7 天新增：低效设备预警规则表
-- ==================================================================
-- 预警规则表：按「预警维度」配置黄色预警线与红色预警线，并设定连续低效
-- 期数阈值；设备在某维度得分低于黄色预警线即产生预警，低于红色预警线或
-- 连续低效期数达到阈值即升级为红色严重预警
CREATE TABLE IF NOT EXISTS pe_warn_rule (
    rule_id          INTEGER PRIMARY KEY AUTOINCREMENT, -- 规则主键
    dim_key          TEXT    NOT NULL UNIQUE,           -- 预警维度编码
    warn_score       REAL    DEFAULT 70,                -- 黄色预警线（分）
    danger_score     REAL    DEFAULT 60,                -- 红色预警线（分）
    continue_periods INTEGER DEFAULT 2,                 -- 升级红色所需连续低效期数
    enabled_flag     INTEGER DEFAULT 1,                 -- 是否启用：1 启用 / 0 停用
    updated_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 7 天新增：低效设备预警记录表
-- ==================================================================
-- 预警记录表：按「一台设备一个周期一个维度一条」保存低效预警，含预警等级、
-- 触发取值、预警线、连续低效期数、触发次数与处置状态，支撑预警确认、整改
-- 闭环与历史追溯，避免重复扫描造成预警刷屏
CREATE TABLE IF NOT EXISTS pe_warn_record (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 预警主键
    warn_code        TEXT    DEFAULT '',                -- 预警编号（WRN-周期-序号）
    period_tag       TEXT    NOT NULL,                  -- 考核周期
    device_code      TEXT    NOT NULL,                  -- 设备编号
    device_name      TEXT    DEFAULT '',                -- 设备名称
    dept_name        TEXT    DEFAULT '',                -- 所属科室
    dim_key          TEXT    NOT NULL,                  -- 预警维度编码
    dim_label        TEXT    DEFAULT '',                -- 预警维度名称
    metric_value     REAL    DEFAULT 0,                 -- 触发时的维度取值
    warn_score       REAL    DEFAULT 0,                 -- 触发时套用的预警线
    warn_level       TEXT    DEFAULT 'YELLOW',          -- 预警等级（见 WARN_LEVEL_DICT）
    trigger_text     TEXT    DEFAULT '',                -- 预警描述文本
    continue_periods INTEGER DEFAULT 1,                 -- 连续低效期数
    trigger_amount   INTEGER DEFAULT 1,                 -- 累计触发次数
    handle_state     TEXT    DEFAULT 'OPEN',            -- 处置状态（见 WARN_STATE_DICT）
    handler_name     TEXT    DEFAULT '',                -- 处置人
    handle_note      TEXT    DEFAULT '',                -- 处置说明
    warn_time        TEXT    DEFAULT (datetime('now', 'localtime')),
    handle_time      TEXT    DEFAULT '',                -- 处置完成时间
    UNIQUE (period_tag, device_code, dim_key)
);

CREATE INDEX IF NOT EXISTS idx_warnrule_dim    ON pe_warn_rule (dim_key);
CREATE INDEX IF NOT EXISTS idx_warnrec_period  ON pe_warn_record (period_tag);
CREATE INDEX IF NOT EXISTS idx_warnrec_device  ON pe_warn_record (device_code);
CREATE INDEX IF NOT EXISTS idx_warnrec_level   ON pe_warn_record (warn_level);
CREATE INDEX IF NOT EXISTS idx_warnrec_state   ON pe_warn_record (handle_state);
CREATE INDEX IF NOT EXISTS idx_warnrec_dept    ON pe_warn_record (dept_name);
"""


def apply_day7_schema(conn):
    """把第 7 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY7_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 7 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-27（第 7 天 / 共 15 天）
#           绩效对比、可视化图表、效益报表与低效设备预警七类页面路由，
#           启动时完成建表、等级规则初始化、科室初始化与演示评分、上期
#           对照、效益明细、低效预警数据灌入，并提供评分试算、评分入库、
#           评分明细、权重元信息、榜单、科室横向对比、等级批量重判、
#           ECharts 图表 option、多维度排行筛选查询、效益报表核算归档与
#           低效预警规则维护、扫描重算、筛选查询、确认整改接口

from perf_warn import (
    build_warn_payload,
    build_warn_brief,
    scan_low_efficiency,
    handle_warn_record,
    fetch_warn_rules,
    save_warn_rule,
    seed_demo_warn,
    WARN_LEVEL_DICT,
    WARN_STATE_DICT,
    WARN_DIMENSION_MAP,
)

# 【函数内插入】def api_screen_data():
#    # 第 7 天新增：大屏叠加低效预警摘要，供预警提示条渲染
#    payload["warn_brief"] = build_warn_brief()

# ==================================================================
# 以下为第 7 天新增：低效设备预警页面与接口
# ==================================================================


@app.route("/pf/warn")
def page_warn():
    """低效设备预警页面：预警指标、五类图表、预警明细与整改闭环。"""
    return render_template(
        "page_warn.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/warn/panel")
def api_warn_panel():
    """低效预警数据接口：预警规则、汇总指标、五类图表与分页明细。"""
    return jsonify(build_warn_payload(
        period_tag=(request.args.get("period_tag") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        warn_level=(request.args.get("warn_level") or "").strip(),
        dim_key=(request.args.get("dim_key") or "").strip(),
        handle_state=(request.args.get("handle_state") or "").strip(),
        keyword=(request.args.get("keyword") or "").strip(),
        page=int(request.args.get("page", 1)),
    ))


@app.route("/api/pf/warn/rules", methods=["GET", "POST"])
def api_warn_rules():
    """预警规则接口：GET 读取全部规则，POST 新增或修改一条规则。"""
    if request.method == "GET":
        return jsonify({"result": "OK", "rules": fetch_warn_rules()})
    body = request.get_json(silent=True) or {}
    result = save_warn_rule(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/warn/scan", methods=["POST"])
def api_warn_scan():
    """按预警规则扫描指定考核周期，生成或更新低效预警记录。"""
    body = request.get_json(silent=True) or {}
    result = scan_low_efficiency((body.get("period_tag") or "").strip())
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/warn/handle", methods=["POST"])
def api_warn_handle():
    """预警处置接口：确认预警或整改关闭。"""
    body = request.get_json(silent=True) or {}
    result = handle_warn_record(
        body.get("record_id"),
        (body.get("state_code") or "").strip(),
        (body.get("handler_name") or "").strip(),
        (body.get("handle_note") or "").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)



# 【函数内插入】def api_health_check():
#    """启动引导：建表 → 初始化规则与科室 → 灌入演示评分、上期对照、效益明细与低效预警。"""
#    seed_demo_warn()

# 【函数内插入】if __name__ == "__main__":
#    print("低效预警：   http://127.0.0.1:%d/pf/warn" % PORT)

# --------------------------------------------------------------------------
# 第 7 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_warn.js              新增 343 行
#   [新增] templates/page_warn.html         新增 130 行
#   [修改] templates/screen_base.html       新增 1 行
