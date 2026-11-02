# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-02（第 13 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 13 天新增与修改的源代码，不含第 1~12 天已有代码
#           本次迭代主题：新增绩效数据采集与质控管理模块（批次编制、逐项填报、质控扫描、问题整改、批次归档）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 13 天新增业务模块 1：perf_gather.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-02（第 13 天 / 共 15 天）
# 模块功能：绩效数据采集与质控管理模块。实现采集批次编制（采集方式、考核
#           周期、采集范围与计划区间）、按科室与绩效指标自动展开采集明细、
#           逐项填报采集数值、按参考标准值自动判定数据质量标记（缺失、偏低、
#           正常、超阈值）、质控问题扫描登记与整改处置、批次数据质量得分回算
#           与批次提交归档全过程管理；存在未处置高等级质控问题的批次不允许
#           提交，形成「批次编制 → 明细展开 → 逐项填报 → 质控扫描 → 问题
#           整改 → 批次归档」的数据治理闭环；输出采集方式分布、批次状态分布、
#           数据质量标记分布、科室采集完成率对比与质控问题类型分布五类统计
#           图表
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from perf_store import open_conn, dict_rows, fetch_department_names
from perf_ranking import fetch_period_options

# ------------------------------------------------------------------
# 采集方式字典：区分系统直连、手工填报与混合采集三种取数途径
# ------------------------------------------------------------------
GATHER_TYPE_DICT = {
    "AUTO": "系统对接采集",
    "MANUAL": "科室手工填报",
    "MIXED": "对接加补录",
}

# 批次状态字典：随填报、质控、提交逐级流转
BATCH_STATE_DICT = {
    "DRAFT": "草稿",
    "RUN": "采集中",
    "CHECK": "质控中",
    "DONE": "已归档",
    "VOID": "已作废",
}

# 数据质量标记字典：按采集值与参考标准值的偏离程度自动判定
VALUE_FLAG_DICT = {
    "MISS": "缺失",
    "LOW": "偏低",
    "NORMAL": "正常",
    "OVER": "超阈值",
}

# 质控问题类型字典
ISSUE_TYPE_DICT = {
    "MISS": "数据缺失",
    "LOW": "数值偏低",
    "OVER": "超阈值",
    "CONFLICT": "逻辑冲突",
    "DELAY": "填报超期",
}

# 质控问题等级字典
ISSUE_LEVEL_DICT = {
    "HIGH": "严重",
    "MID": "一般",
    "LOW": "提示",
}

# 质控问题处置状态字典
ISSUE_STATE_DICT = {
    "OPEN": "待处置",
    "DOING": "处置中",
    "CLOSED": "已关闭",
}

# 各指标默认参考标准值，用于判定采集数值的偏离程度
DEFAULT_STD_MAP = {
    "usage_rate": 85.0,
    "income_rate": 80.0,
    "maintain_rate": 90.0,
    "quality_rate": 95.0,
    "service_rate": 80.0,
}

# 参与采集的绩效指标清单：（指标键，指标名称，计量单位）
GATHER_METRIC_LIST = (
    ("usage_rate", "使用效率", "%"),
    ("income_rate", "经济效益", "万元"),
    ("maintain_rate", "维保水平", "小时"),
    ("quality_rate", "质量安全", "%"),
    ("service_rate", "服务效能", "人次"),
)

# 判定阈值：低于参考值的该比例判为偏低，高于该倍数判为超阈值
LOW_RATIO = 0.8
OVER_RATIO = 1.5

# 数据质量权重：正常项 100 分、偏低 70 分、超阈值 50 分、缺失 0 分
FLAG_SCORE_MAP = {
    "NORMAL": 100.0,
    "LOW": 70.0,
    "OVER": 50.0,
    "MISS": 0.0,
}

# 未归档批次状态集合：质控扫描与填报只处理这些状态的批次
BATCH_OPEN_STATE_LIST = ("DRAFT", "RUN", "CHECK")

# 分页尺寸：批次独立分页
BATCH_PAGE_SIZE = 8

# 演示批次清单
# （批次名称，采集方式，起始日期偏移，采集天数，目标状态，覆盖范围科室数，说明）
DEMO_BATCH_LIST = (
    ("2026 年三季度绩效数据采集", "AUTO", -120, 10, "DONE", 5,
     "系统对接 HIS 与物联网平台自动取数"),
    ("2026 年四季度绩效数据采集", "MIXED", -46, 12, "DONE", 5,
     "系统取数后由科室补录缺口数据"),
    ("2026 年十月份绩效补采批次", "MANUAL", -14, 8, "CHECK", 4,
     "科室手工填报，质控整改中"),
    ("2026 年十一月份绩效数据采集", "AUTO", 3, 10, "RUN", 5,
     "本季度在采批次，进度推进中"),
)


def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def now_text():
    """返回当前时间文本 YYYY-MM-DD HH:MM:SS。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def round2(value):
    """把数值保留两位小数，空值按 0 处理。"""
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def round1(value):
    """把数值保留一位小数，空值按 0 处理。"""
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def date_offset_text(days):
    """返回相对今天偏移指定天数的日期文本。"""
    return (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def next_seq_no(table_name):
    """读取指定业务表记录数并加一，用于拼装业务编码。"""
    conn = open_conn()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def build_batch_code(seq_no):
    """生成采集批次编号：GB-0001。"""
    return "GB-%04d" % seq_no


def build_item_code(seq_no):
    """生成采集明细编号：GI-000001。"""
    return "GI-%06d" % seq_no


def build_issue_code(seq_no):
    """生成质控问题编号：GQ-000001。"""
    return "GQ-%06d" % seq_no


def judge_value_flag(item_value, std_value):
    """按采集数值与参考标准值判定数据质量标记。

    数值小于 0 或参照值缺失时按缺失处理；低于参考值 80% 判为偏低，高于
    参考值 150% 判为超阈值，其余判为正常。
    """
    try:
        value = float(item_value)
    except (TypeError, ValueError):
        return "MISS"
    std = float(std_value or 0)
    if value <= 0 or std <= 0:
        return "MISS"
    if value < std * LOW_RATIO:
        return "LOW"
    if value > std * OVER_RATIO:
        return "OVER"
    return "NORMAL"


def judge_issue_level(value_flag):
    """按数据质量标记推算质控问题等级：缺失为严重，超阈值为一般。"""
    if value_flag == "MISS":
        return "HIGH"
    if value_flag == "OVER":
        return "MID"
    return "LOW"


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_batch_row(batch_code):
    """按批次编号读取一条采集批次，不存在返回 None。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_gather_batch WHERE batch_code=?",
            ((batch_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_batch_items(batch_code):
    """读取某个采集批次下的全部采集明细。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_gather_item WHERE batch_code=? "
            "ORDER BY item_id ASC", ((batch_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


# ------------------------------------------------------------------
# 采集批次编制
# ------------------------------------------------------------------
def create_gather_batch(batch_name, period_tag="", gather_type="AUTO",
                        scope_dept="", plan_start="", plan_end="",
                        operator_name="", remark_text="", dept_limit=0):
    """新建一个采集批次，并按科室与绩效指标自动展开采集明细。

    明细按「一个科室一项指标一条」生成，参考标准值取各指标默认值，采集
    数值初始为空、质量标记为缺失；科室范围为空时覆盖全院科室。

    :return: 含 success、message、batch_code 与 amount 的执行结果字典
    """
    batch_name = (batch_name or "").strip()
    if not batch_name:
        return {"success": False, "message": "请填写采集批次名称"}
    if gather_type not in GATHER_TYPE_DICT:
        return {"success": False, "message": "采集方式 %s 不合法" % gather_type}

    scope_dept = (scope_dept or "").strip()
    if scope_dept:
        dept_list = [scope_dept]
    else:
        dept_list = fetch_department_names()
        if dept_limit and dept_limit > 0:
            dept_list = dept_list[:dept_limit]
    if not dept_list:
        return {"success": False, "message": "采集范围内没有可用科室"}

    conn = open_conn()
    try:
        code = build_batch_code(next_seq_no("pe_gather_batch"))
        item_seq = next_seq_no("pe_gather_item")
        item_amount = 0
        for dept_row in dept_list:
            dept_name = (dept_row["dept_name"] if isinstance(dept_row, dict)
                         else str(dept_row))
            for metric_key, metric_name, metric_unit in GATHER_METRIC_LIST:
                conn.execute(
                    "INSERT INTO pe_gather_item (item_code, batch_code, "
                    "target_name, metric_key, metric_name, metric_unit, "
                    "std_value, item_value, value_flag, gather_way, "
                    "checker_name) VALUES (?,?,?,?,?,?,?,0,'MISS',?,?)",
                    (build_item_code(item_seq), code, dept_name, metric_key,
                     metric_name, metric_unit, DEFAULT_STD_MAP.get(metric_key, 80.0),
                     gather_type, (operator_name or "").strip()))
                item_seq += 1
                item_amount += 1
        conn.execute(
            "INSERT INTO pe_gather_batch (batch_code, batch_name, period_tag, "
            "gather_type, scope_dept, plan_start, plan_end, item_total, "
            "batch_state, operator_name, remark_text) "
            "VALUES (?,?,?,?,?,?,?,?,'RUN',?,?)",
            (code, batch_name, (period_tag or "").strip(), gather_type,
             scope_dept, (plan_start or "").strip(), (plan_end or "").strip(),
             item_amount, (operator_name or "").strip(),
             (remark_text or "").strip()))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "采集批次 %s 已生成，展开 %d 条采集明细" % (code, item_amount),
            "batch_code": code, "amount": item_amount}


def recalc_batch_stat(batch_code):
    """重算某批次的已采集量、问题量、完成率与数据质量得分。"""
    conn = open_conn()
    try:
        stat = conn.execute(
            "SELECT SUM(CASE WHEN value_flag='NORMAL' THEN 1 ELSE 0 END) AS normal_c, "
            "SUM(CASE WHEN value_flag='LOW' THEN 1 ELSE 0 END) AS low_c, "
            "SUM(CASE WHEN value_flag='OVER' THEN 1 ELSE 0 END) AS over_c, "
            "SUM(CASE WHEN value_flag='MISS' THEN 1 ELSE 0 END) AS miss_c, "
            "COUNT(1) AS total_c FROM pe_gather_item WHERE batch_code=?",
            (batch_code,)).fetchone()
        total = int(stat["total_c"] or 0)
        miss = int(stat["miss_c"] or 0)
        done = total - miss
        score = 0.0
        if total:
            score = round2(
                (int(stat["normal_c"] or 0) * FLAG_SCORE_MAP["NORMAL"] +
                 int(stat["low_c"] or 0) * FLAG_SCORE_MAP["LOW"] +
                 int(stat["over_c"] or 0) * FLAG_SCORE_MAP["OVER"]) / total)
        conn.execute(
            "UPDATE pe_gather_batch SET item_done=?, item_issue=?, "
            "quality_score=? WHERE batch_code=?",
            (done, miss + int(stat["over_c"] or 0) + int(stat["low_c"] or 0),
             score, batch_code))
        conn.commit()
    finally:
        conn.close()
    return {"total": total, "done": done, "score": score,
            "done_rate": round1(done * 100.0 / total) if total else 0.0}


# ------------------------------------------------------------------
# 采集数值填报
# ------------------------------------------------------------------
def fill_gather_item(item_code, item_value=0, gather_way="AUTO",
                     checker_name="", operator="admin"):
    """填报单条采集明细的数值，并自动判定数据质量标记与回算批次统计。

    :return: 含 success、message、value_flag 与 flag_label 的执行结果字典
    """
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_gather_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "采集明细不存在"}
        batch = conn.execute(
            "SELECT batch_state FROM pe_gather_batch WHERE batch_code=?",
            (row["batch_code"],)).fetchone()
        if batch is not None and batch["batch_state"] in ("DONE", "VOID"):
            return {"success": False, "message": "已归档或作废的批次不可再填报"}
        flag = judge_value_flag(item_value, row["std_value"])
        conn.execute(
            "UPDATE pe_gather_item SET item_value=?, value_flag=?, "
            "gather_way=?, gather_time=?, checker_name=? WHERE item_code=?",
            (float(item_value or 0), flag, (gather_way or "").strip() or "AUTO",
             now_text(), (checker_name or "").strip(), row["item_code"]))
        conn.commit()
    finally:
        conn.close()
    recalc_batch_stat(row["batch_code"])
    return {"success": True, "message": "采集明细 %s 已填报" % row["item_code"],
            "value_flag": flag, "flag_label": VALUE_FLAG_DICT[flag]}


# ------------------------------------------------------------------
# 质控扫描与整改
# ------------------------------------------------------------------
def scan_gather_issue(batch_code, operator="admin"):
    """扫描某批次的采集明细，把非正常数据登记为质控问题。

    同一采集明细只登记一条未处置问题，已关闭的问题允许再次扫描登记；
    扫描后回算批次的问题量。

    :return: 含 success、message 与 amount 的执行结果字典
    """
    batch = fetch_batch_row(batch_code)
    if batch is None:
        return {"success": False, "message": "采集批次不存在"}
    if batch["batch_state"] in ("DONE", "VOID"):
        return {"success": False, "message": "已归档或作废的批次不可再质控扫描"}

    items = fetch_batch_items(batch["batch_code"])
    conn = open_conn()
    try:
        open_rows = conn.execute(
            "SELECT item_code FROM pe_quality_issue WHERE batch_code=? "
            "AND handle_state<>'CLOSED'", (batch["batch_code"],)).fetchall()
        open_codes = {row["item_code"] for row in open_rows}
        issue_seq = next_seq_no("pe_quality_issue")
        amount = 0
        for item in items:
            if item["value_flag"] == "NORMAL":
                continue
            if item["item_code"] in open_codes:
                continue
            issue_type = item["value_flag"]
            level = judge_issue_level(issue_type)
            if issue_type == "MISS":
                desc = "%s 的「%s」数据缺失，未取到有效数值" % (
                    item["target_name"], item["metric_name"])
            elif issue_type == "LOW":
                desc = "%s 的「%s」采集值 %.2f 低于参考值 %.2f 的 80%%" % (
                    item["target_name"], item["metric_name"],
                    float(item["item_value"] or 0), float(item["std_value"] or 0))
            else:
                desc = "%s 的「%s」采集值 %.2f 高于参考值 %.2f 的 150%%" % (
                    item["target_name"], item["metric_name"],
                    float(item["item_value"] or 0), float(item["std_value"] or 0))
            conn.execute(
                "INSERT INTO pe_quality_issue (issue_code, batch_code, "
                "item_code, target_name, metric_name, issue_type, issue_level, "
                "issue_desc, handle_state) VALUES (?,?,?,?,?,?,?,?,'OPEN')",
                (build_issue_code(issue_seq), batch["batch_code"],
                 item["item_code"], item["target_name"], item["metric_name"],
                 issue_type, level, desc))
            issue_seq += 1
            amount += 1
        if batch["batch_state"] == "RUN":
            conn.execute(
                "UPDATE pe_gather_batch SET batch_state='CHECK' WHERE batch_code=?",
                (batch["batch_code"],))
        conn.commit()
    finally:
        conn.close()
    recalc_batch_stat(batch["batch_code"])
    return {"success": True,
            "message": "质控扫描完成，登记 %d 条质控问题" % amount,
            "amount": amount}


def handle_quality_issue(issue_code, handle_note="", handler_name="",
                         handle_state="CLOSED", operator="admin"):
    """处置一条质控问题：填写整改说明并推进处置状态。

    :return: 含 success 与 message 的执行结果字典
    """
    if handle_state not in ISSUE_STATE_DICT:
        return {"success": False, "message": "处置状态 %s 不合法" % handle_state}
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_quality_issue WHERE issue_code=?",
            ((issue_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "质控问题不存在"}
        conn.execute(
            "UPDATE pe_quality_issue SET handle_state=?, handle_note=?, "
            "handler_name=?, handle_time=? WHERE issue_code=?",
            (handle_state, (handle_note or "").strip(),
             (handler_name or "").strip(), now_text(), row["issue_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "质控问题 %s 已标记为%s" % (
                row["issue_code"], ISSUE_STATE_DICT[handle_state])}


def close_gather_batch(batch_code, operator="admin"):
    """归档一个采集批次：存在未处置的严重质控问题时不允许提交。

    :return: 含 success、message 与 quality_score 的执行结果字典
    """
    batch = fetch_batch_row(batch_code)
    if batch is None:
        return {"success": False, "message": "采集批次不存在"}
    if batch["batch_state"] == "DONE":
        return {"success": False, "message": "该采集批次已归档"}
    if batch["batch_state"] == "VOID":
        return {"success": False, "message": "已作废的采集批次不可归档"}

    conn = open_conn()
    try:
        high_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_quality_issue WHERE batch_code=? "
            "AND issue_level='HIGH' AND handle_state<>'CLOSED'",
            (batch["batch_code"],)).fetchone()["c"]
    finally:
        conn.close()
    if high_amount:
        return {"success": False,
                "message": "仍有 %d 条严重质控问题未处置，不可归档" % high_amount}

    stat = recalc_batch_stat(batch["batch_code"])
    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_gather_batch SET batch_state='DONE', submit_time=?, "
            "operator_name=? WHERE batch_code=?",
            (now_text(), (operator or "admin").strip(), batch["batch_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "采集批次 %s 已归档，数据质量得分 %s" % (
                batch["batch_code"], stat["score"]),
            "quality_score": stat["score"]}


def cancel_gather_batch(batch_code, reason_text="", operator="admin"):
    """作废一个采集批次：已归档的批次不可作废。"""
    batch = fetch_batch_row(batch_code)
    if batch is None:
        return {"success": False, "message": "采集批次不存在"}
    if batch["batch_state"] == "DONE":
        return {"success": False, "message": "已归档的采集批次不可作废"}
    if batch["batch_state"] == "VOID":
        return {"success": False, "message": "该采集批次已作废"}
    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_gather_batch SET batch_state='VOID', remark_text=? "
            "WHERE batch_code=?",
            ((reason_text or "").strip(), batch["batch_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "采集批次 %s 已作废" % batch["batch_code"]}


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_gather_batches(keyword="", gather_type="", batch_state="", page=1):
    """按条件分页查询采集批次，返回分页结果与记录列表。"""
    sql = "SELECT * FROM pe_gather_batch WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (batch_code LIKE ? OR batch_name LIKE ? OR period_tag LIKE ? "
                "OR scope_dept LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if gather_type:
        sql += "AND gather_type=? "
        args.append(gather_type)
    if batch_state:
        sql += "AND batch_state=? "
        args.append(batch_state)
    conn = open_conn()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + BATCH_PAGE_SIZE - 1) // BATCH_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY batch_id DESC LIMIT ? OFFSET ?",
            args + [BATCH_PAGE_SIZE, (page_no - 1) * BATCH_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = GATHER_TYPE_DICT.get(item["gather_type"],
                                                    item["gather_type"])
        record["state_label"] = BATCH_STATE_DICT.get(item["batch_state"],
                                                     item["batch_state"])
        record["done_rate"] = round1(
            item["item_done"] * 100.0 / item["item_total"]) if item["item_total"] else 0.0
        record["scope_text"] = (item["scope_dept"] or "").strip() or "全院科室"
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_gather_items(batch_code="", value_flag="", keyword="", limit=40):
    """按条件查询采集明细，支持按批次、质量标记与关键字过滤。"""
    sql = "SELECT * FROM pe_gather_item WHERE 1=1 "
    args = []
    if batch_code:
        sql += "AND batch_code=? "
        args.append(batch_code)
    if value_flag:
        sql += "AND value_flag=? "
        args.append(value_flag)
    keyword = (keyword or "").strip()
    if keyword:
        sql += "AND (item_code LIKE ? OR target_name LIKE ? OR metric_name LIKE ?) "
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY item_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["flag_label"] = VALUE_FLAG_DICT.get(item["value_flag"],
                                                   item["value_flag"])
        records.append(record)
    return records


def fetch_quality_issues(batch_code="", issue_type="", handle_state="",
                         keyword="", limit=40):
    """按条件查询质控问题，支持按批次、类型、处置状态与关键字过滤。"""
    sql = "SELECT * FROM pe_quality_issue WHERE 1=1 "
    args = []
    if batch_code:
        sql += "AND batch_code=? "
        args.append(batch_code)
    if issue_type:
        sql += "AND issue_type=? "
        args.append(issue_type)
    if handle_state:
        sql += "AND handle_state=? "
        args.append(handle_state)
    keyword = (keyword or "").strip()
    if keyword:
        sql += "AND (issue_code LIKE ? OR target_name LIKE ? OR metric_name LIKE ? "
        sql += "OR issue_desc LIKE ?) "
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY issue_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = ISSUE_TYPE_DICT.get(item["issue_type"],
                                                   item["issue_type"])
        record["level_label"] = ISSUE_LEVEL_DICT.get(item["issue_level"],
                                                     item["issue_level"])
        record["state_label"] = ISSUE_STATE_DICT.get(item["handle_state"],
                                                     item["handle_state"])
        records.append(record)
    return records


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_gather_summary():
    """汇采集核心指标：批次量、明细量、完成率、问题量、处置量与平均质量分。"""
    conn = open_conn()
    try:
        batch_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_gather_batch").fetchone()["c"]
        state_rows = conn.execute(
            "SELECT batch_state, COUNT(1) AS c FROM pe_gather_batch "
            "GROUP BY batch_state").fetchall()
        item_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_gather_item").fetchone()["c"]
        flag_rows = conn.execute(
            "SELECT value_flag, COUNT(1) AS c FROM pe_gather_item "
            "GROUP BY value_flag").fetchall()
        issue_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_quality_issue").fetchone()["c"]
        open_issue = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_quality_issue "
            "WHERE handle_state='OPEN'").fetchone()["c"]
        high_issue = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_quality_issue "
            "WHERE issue_level='HIGH'").fetchone()["c"]
        closed_issue = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_quality_issue "
            "WHERE handle_state='CLOSED'").fetchone()["c"]
        score_row = conn.execute(
            "SELECT COALESCE(AVG(quality_score),0) AS a FROM pe_gather_batch "
            "WHERE item_total>0").fetchone()
    finally:
        conn.close()

    state_counter = {item["batch_state"]: item["c"] for item in state_rows}
    flag_counter = {item["value_flag"]: item["c"] for item in flag_rows}
    normal_amount = flag_counter.get("NORMAL", 0)
    miss_amount = flag_counter.get("MISS", 0)
    return {
        "batch_total": batch_total,
        "run_batch": state_counter.get("RUN", 0),
        "check_batch": state_counter.get("CHECK", 0),
        "done_batch": state_counter.get("DONE", 0),
        "item_total": item_total,
        "normal_amount": normal_amount,
        "miss_amount": miss_amount,
        "issue_total": issue_total,
        "open_issue": open_issue,
        "high_issue": high_issue,
        "closed_issue": closed_issue,
        "normal_rate": round1(normal_amount * 100.0 / item_total) if item_total else 0.0,
        "close_rate": round1(closed_issue * 100.0 / issue_total) if issue_total else 0.0,
        "avg_score": round1(score_row["a"]),
    }


def build_gather_charts():
    """构建五类图表数据：采集方式分布、批次状态分布、数据质量标记分布、
    科室采集完成率对比与质控问题类型分布。"""
    conn = open_conn()
    try:
        batch_rows = conn.execute(
            "SELECT batch_code, gather_type, batch_state FROM pe_gather_batch"
        ).fetchall()
        flag_rows = conn.execute(
            "SELECT value_flag, COUNT(1) AS c FROM pe_gather_item "
            "GROUP BY value_flag").fetchall()
        dept_rows = conn.execute(
            "SELECT target_name, "
            "SUM(CASE WHEN value_flag<>'MISS' THEN 1 ELSE 0 END) AS done_c, "
            "COUNT(1) AS total_c FROM pe_gather_item GROUP BY target_name"
        ).fetchall()
        type_rows = conn.execute(
            "SELECT issue_type, COUNT(1) AS c FROM pe_quality_issue "
            "GROUP BY issue_type").fetchall()
    finally:
        conn.close()

    type_counter = {}
    for item in batch_rows:
        type_counter[item["gather_type"]] = type_counter.get(item["gather_type"], 0) + 1
    gather_distribution = [
        {"name": GATHER_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in batch_rows:
        state_counter[item["batch_state"]] = state_counter.get(item["batch_state"], 0) + 1
    state_distribution = [
        {"name": BATCH_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("DRAFT", "RUN", "CHECK", "DONE", "VOID")
    ]

    flag_counter = {item["value_flag"]: item["c"] for item in flag_rows}
    flag_distribution = [
        {"name": VALUE_FLAG_DICT.get(code, code), "value": flag_counter.get(code, 0)}
        for code in ("NORMAL", "LOW", "OVER", "MISS")
    ]

    dept_rate = []
    for item in dept_rows:
        total = int(item["total_c"] or 0)
        dept_rate.append({
            "name": item["target_name"],
            "value": round1(item["done_c"] * 100.0 / total) if total else 0.0,
            "total": total,
        })
    dept_rate.sort(key=lambda x: x["value"], reverse=True)

    type_counter2 = {item["issue_type"]: item["c"] for item in type_rows}
    issue_distribution = [
        {"name": ISSUE_TYPE_DICT.get(code, code), "value": type_counter2.get(code, 0)}
        for code in ("MISS", "LOW", "OVER", "CONFLICT", "DELAY")
    ]

    return {
        "gather_distribution": gather_distribution,
        "state_distribution": state_distribution,
        "flag_distribution": flag_distribution,
        "dept_rate": dept_rate[:8],
        "issue_distribution": issue_distribution,
    }


def fetch_gather_options():
    """采集页面下拉选项：各类字典项、科室候选、周期候选与批次候选。"""
    conn = open_conn()
    try:
        batch_rows = conn.execute(
            "SELECT batch_code, batch_name FROM pe_gather_batch "
            "ORDER BY batch_id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {
        "dept_list": [(row["dept_name"] if isinstance(row, dict) else str(row))
                      for row in fetch_department_names()],
        "period_list": fetch_period_options(),
        "batch_list": [dict(row) for row in batch_rows],
        "type_options": [{"code": k, "label": v} for k, v in GATHER_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in BATCH_STATE_DICT.items()],
        "flag_options": [{"code": k, "label": v} for k, v in VALUE_FLAG_DICT.items()],
        "issue_options": [{"code": k, "label": v} for k, v in ISSUE_TYPE_DICT.items()],
        "issue_state_options": [{"code": k, "label": v}
                                for k, v in ISSUE_STATE_DICT.items()],
    }


def build_gather_payload(keyword="", gather_type="", batch_state="",
                         item_batch="", value_flag="", item_keyword="",
                         issue_batch="", issue_type="", handle_state="",
                         issue_keyword="", page=1):
    """组装绩效数据采集与质控管理页面所需的全部数据。"""
    pager = fetch_gather_batches(keyword, gather_type, batch_state, page)
    return {
        "summary": build_gather_summary(),
        "charts": build_gather_charts(),
        "batches": pager["records"],
        "batch_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "items": fetch_gather_items(item_batch, value_flag, item_keyword),
        "issues": fetch_quality_issues(issue_batch, issue_type, handle_state,
                                       issue_keyword),
        "options": fetch_gather_options(),
        "result": "OK",
    }


def build_gather_brief():
    """数据采集看板摘要：批次量、完成率、问题量与平均质量得分。"""
    summary = build_gather_summary()
    return {
        "batch_total": summary["batch_total"],
        "done_batch": summary["done_batch"],
        "item_total": summary["item_total"],
        "open_issue": summary["open_issue"],
        "normal_rate": summary["normal_rate"],
        "avg_score": summary["avg_score"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_gather():
    """灌入绩效数据采集演示数据：批次编制、明细展开、逐项填报、质控扫描、
    问题整改与批次归档。

    :return: 生成的采集明细条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_gather_batch").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    period_options = fetch_period_options()
    period_text = ""
    if period_options:
        first = period_options[0]
        period_text = first.get("value", first) if isinstance(first, dict) else str(first)

    amount = 0
    for index, (batch_name, gather_type, start_offset, span_days,
                target_state, dept_limit, remark) in enumerate(DEMO_BATCH_LIST):
        created = create_gather_batch(
            batch_name, period_text, gather_type, "",
            date_offset_text(start_offset),
            date_offset_text(start_offset + span_days),
            "绩效办-秦主任", remark, dept_limit)
        if not created.get("success"):
            continue
        batch_code = created["batch_code"]
        amount += int(created.get("amount") or 0)

        items = fetch_batch_items(batch_code)
        # 在采批次只填报前六成数据，用于演示进度与缺失质控
        fill_limit = len(items) if target_state in ("DONE", "CHECK") \
            else int(len(items) * 0.6)
        for item_index, item in enumerate(items):
            if item_index >= fill_limit:
                continue
            std_value = float(item["std_value"] or 80.0)
            if item_index % 11 == 3:
                value = 0.0                      # 故意留空，形成缺失质控问题
            elif item_index % 9 == 5:
                value = round2(std_value * 1.62)  # 超阈值样本
            elif item_index % 7 == 4:
                value = round2(std_value * 0.68)  # 偏低样本
            else:
                value = round2(std_value * (0.92 + (item_index % 5) * 0.03))
            fill_gather_item(item["item_code"], value, gather_type,
                             "绩效办-秦主任", "绩效办-秦主任")

        # 在采批次保留「采集中」状态不做质控扫描，用于演示在采进度
        if target_state == "RUN":
            continue
        scan_gather_issue(batch_code, "绩效办-秦主任")

        if target_state == "CHECK":
            # 质控中的批次只关闭一半问题，保留待处置样本
            conn = open_conn()
            try:
                rows = conn.execute(
                    "SELECT issue_code FROM pe_quality_issue WHERE batch_code=? "
                    "AND handle_state='OPEN' ORDER BY issue_id ASC",
                    (batch_code,)).fetchall()
            finally:
                conn.close()
            for position, row in enumerate(rows):
                if position % 2:
                    continue
                handle_quality_issue(row["issue_code"], "已联系科室补录并复核数据",
                                     "绩效办-秦主任", "CLOSED", "绩效办-秦主任")
            continue
        if target_state != "DONE":
            continue

        conn = open_conn()
        try:
            rows = conn.execute(
                "SELECT issue_code FROM pe_quality_issue WHERE batch_code=? "
                "AND handle_state='OPEN' ORDER BY issue_id ASC", (batch_code,)).fetchall()
        finally:
            conn.close()
        for row in rows:
            handle_quality_issue(row["issue_code"], "数据已补齐并完成复核确认",
                                 "绩效办-秦主任", "CLOSED", "绩效办-秦主任")
        close_gather_batch(batch_code, "绩效办-秦主任")
    return amount

# --------------------------------------------------------------------------
# 第 13 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY13_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY13_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 13 天新增：绩效数据采集与质控三表
-- ==================================================================
-- 采集批次表：按「一个考核周期一次采集一批」登记数据来源、采集方式、
-- 计划区间与批次进度，支撑采集任务下发与批次进度跟踪
CREATE TABLE IF NOT EXISTS pe_gather_batch (
    batch_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 采集批次主键
    batch_code      TEXT    NOT NULL UNIQUE,           -- 批次编号（GB-序号）
    batch_name      TEXT    DEFAULT '',                -- 批次名称
    period_tag      TEXT    DEFAULT '',                -- 考核周期标签
    gather_type     TEXT    DEFAULT 'AUTO',            -- 采集方式（见 GATHER_TYPE_DICT）
    scope_dept      TEXT    DEFAULT '',                -- 采集范围科室（空表示全院）
    plan_start      TEXT    DEFAULT '',                -- 计划开始日期
    plan_end        TEXT    DEFAULT '',                -- 计划结束日期
    item_total      INTEGER DEFAULT 0,                 -- 应采集数据项数
    item_done       INTEGER DEFAULT 0,                 -- 已采集数据项数
    item_issue      INTEGER DEFAULT 0,                 -- 质控问题项数
    quality_score   REAL    DEFAULT 0,                 -- 批次数据质量得分
    batch_state     TEXT    DEFAULT 'DRAFT',           -- 批次状态（见 BATCH_STATE_DICT）
    submit_time     TEXT    DEFAULT '',                -- 批次提交时间
    operator_name   TEXT    DEFAULT '',                -- 批次负责人
    remark_text     TEXT    DEFAULT '',                -- 批次说明
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 采集明细表：按「一个考核对象一项指标一条」记录采集数值与质量标记
CREATE TABLE IF NOT EXISTS pe_gather_item (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 采集明细主键
    item_code       TEXT    NOT NULL UNIQUE,           -- 明细编号（GI-序号）
    batch_code      TEXT    DEFAULT '',                -- 所属批次编号
    target_name     TEXT    DEFAULT '',                -- 考核对象（科室或设备）
    metric_key      TEXT    DEFAULT '',                -- 指标字段键
    metric_name     TEXT    DEFAULT '',                -- 指标名称快照
    metric_unit     TEXT    DEFAULT '',                -- 计量单位快照
    std_value       REAL    DEFAULT 0,                 -- 该指标参考标准值
    item_value      REAL    DEFAULT 0,                 -- 采集数值
    value_flag      TEXT    DEFAULT 'MISS',            -- 质量标记（见 VALUE_FLAG_DICT）
    gather_way      TEXT    DEFAULT 'AUTO',            -- 数据获取途径
    gather_time     TEXT    DEFAULT '',                -- 采集时间
    checker_name    TEXT    DEFAULT '',                -- 填报人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 质控问题表：登记采集数据的缺失、超阈值、逻辑冲突与整改处置情况
CREATE TABLE IF NOT EXISTS pe_quality_issue (
    issue_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 质控问题主键
    issue_code      TEXT    NOT NULL UNIQUE,           -- 问题编号（GQ-序号）
    batch_code      TEXT    DEFAULT '',                -- 所属批次编号
    item_code       TEXT    DEFAULT '',                -- 关联采集明细编号
    target_name     TEXT    DEFAULT '',                -- 考核对象快照
    metric_name     TEXT    DEFAULT '',                -- 指标名称快照
    issue_type      TEXT    DEFAULT 'MISS',            -- 问题类型（见 ISSUE_TYPE_DICT）
    issue_level     TEXT    DEFAULT 'MID',             -- 问题等级（见 ISSUE_LEVEL_DICT）
    issue_desc      TEXT    DEFAULT '',                -- 问题描述
    handle_state    TEXT    DEFAULT 'OPEN',            -- 处置状态（见 ISSUE_STATE_DICT）
    handle_note     TEXT    DEFAULT '',                -- 整改说明
    handler_name    TEXT    DEFAULT '',                -- 处置人
    handle_time     TEXT    DEFAULT '',                -- 处置时间
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_gbatch_code       ON pe_gather_batch (batch_code);
CREATE INDEX IF NOT EXISTS idx_gbatch_period     ON pe_gather_batch (period_tag);
CREATE INDEX IF NOT EXISTS idx_gbatch_state      ON pe_gather_batch (batch_state);
CREATE INDEX IF NOT EXISTS idx_gitem_code        ON pe_gather_item (item_code);
CREATE INDEX IF NOT EXISTS idx_gitem_batch       ON pe_gather_item (batch_code);
CREATE INDEX IF NOT EXISTS idx_gitem_flag        ON pe_gather_item (value_flag);
CREATE INDEX IF NOT EXISTS idx_gissue_code       ON pe_quality_issue (issue_code);
CREATE INDEX IF NOT EXISTS idx_gissue_batch      ON pe_quality_issue (batch_code);
CREATE INDEX IF NOT EXISTS idx_gissue_state      ON pe_quality_issue (handle_state);
CREATE INDEX IF NOT EXISTS idx_gissue_type       ON pe_quality_issue (issue_type);
"""


def apply_day13_schema(conn):
    """把第 13 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY13_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 13 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from perf_gather import (
    build_gather_payload,
    build_gather_brief,
    create_gather_batch,
    fill_gather_item,
    scan_gather_issue,
    handle_quality_issue,
    close_gather_batch,
    cancel_gather_batch,
    seed_demo_gather,
    GATHER_TYPE_DICT,
    BATCH_STATE_DICT,
    VALUE_FLAG_DICT,
    ISSUE_TYPE_DICT,
    ISSUE_LEVEL_DICT,
    ISSUE_STATE_DICT,
)

@app.route("/pf/gather")
def page_gather():
    """绩效数据采集与质控管理页面：批次编制、逐项填报与质控整改（第 13 天）。"""
    return render_template(
        "page_gather.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/gather/panel")
def api_gather_panel():
    """采集面板数据接口：概览指标、五类图表、批次、采集明细与质控问题。"""
    payload = build_gather_payload(
        keyword=request.args.get("keyword", ""),
        gather_type=request.args.get("gather_type", ""),
        batch_state=request.args.get("batch_state", ""),
        item_batch=request.args.get("item_batch", ""),
        value_flag=request.args.get("value_flag", ""),
        item_keyword=request.args.get("item_keyword", ""),
        issue_batch=request.args.get("issue_batch", ""),
        issue_type=request.args.get("issue_type", ""),
        handle_state=request.args.get("handle_state", ""),
        issue_keyword=request.args.get("issue_keyword", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/pf/gather/batch/create", methods=["POST"])
def api_gather_batch_create():
    """采集批次新建接口：按科室与指标自动展开采集明细。"""
    body = request.get_json(silent=True) or {}
    result = create_gather_batch(
        body.get("batch_name") or "",
        body.get("period_tag") or "",
        body.get("gather_type") or "AUTO",
        body.get("scope_dept") or "",
        body.get("plan_start") or "",
        body.get("plan_end") or "",
        (body.get("operator_name") or "admin").strip(),
        body.get("remark_text") or "",
        int(body.get("dept_limit") or 0),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/item/fill", methods=["POST"])
def api_gather_item_fill():
    """采集数值填报接口：自动判定质量标记并回算批次统计。"""
    body = request.get_json(silent=True) or {}
    result = fill_gather_item(
        body.get("item_code") or "",
        body.get("item_value") or 0,
        body.get("gather_way") or "AUTO",
        body.get("checker_name") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/scan", methods=["POST"])
def api_gather_scan():
    """质控扫描接口：把非正常采集数据登记为质控问题。"""
    body = request.get_json(silent=True) or {}
    result = scan_gather_issue(
        body.get("batch_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/issue/handle", methods=["POST"])
def api_gather_issue_handle():
    """质控问题处置接口：填写整改说明并推进处置状态。"""
    body = request.get_json(silent=True) or {}
    result = handle_quality_issue(
        body.get("issue_code") or "",
        body.get("handle_note") or "",
        body.get("handler_name") or "",
        body.get("handle_state") or "CLOSED",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/close", methods=["POST"])
def api_gather_close():
    """采集批次归档接口：存在未处置严重问题时不允许提交。"""
    body = request.get_json(silent=True) or {}
    result = close_gather_batch(
        body.get("batch_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/cancel", methods=["POST"])
def api_gather_cancel():
    """采集批次作废接口：已归档的批次不可作废。"""
    body = request.get_json(silent=True) or {}
    result = cancel_gather_batch(
        body.get("batch_code") or "",
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/gather/options")
def api_gather_options():
    """采集下拉选项接口：采集方式、批次状态、质量标记与问题类型字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in GATHER_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in BATCH_STATE_DICT.items()],
        "flag_options": [{"code": k, "label": v} for k, v in VALUE_FLAG_DICT.items()],
        "issue_options": [{"code": k, "label": v} for k, v in ISSUE_TYPE_DICT.items()],
        "level_options": [{"code": k, "label": v} for k, v in ISSUE_LEVEL_DICT.items()],
        "issue_state_options": [{"code": k, "label": v}
                                for k, v in ISSUE_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_gather()

# 【函数内插入】if __name__ == "__main__":
#    print("采集质控：   http://127.0.0.1:%d/pf/gather" % PORT)

# --------------------------------------------------------------------------
# 第 13 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_gather.js            新增 516 行
#   [新增] templates/page_gather.html       新增 297 行
#   [修改] templates/screen_base.html       新增 1 行
