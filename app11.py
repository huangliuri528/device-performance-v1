# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-31（第 11 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 11 天新增与修改的源代码，不含第 1~10 天已有代码
#           本次迭代主题：绩效目标管理与达标考核模块 + 目标考核页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 11 天新增业务模块 1：perf_target.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-31（第 11 天 / 共 15 天）
# 模块功能：绩效目标管理与达标考核模块。实现按考核周期制定科室级与设备级
#           绩效目标方案、一键生成或手工维护目标明细、按达标率自动核算达标
#           结论、目标复盘调整与达标情况的量化考核；达标率由「实际值 ÷ 目标
#           值」计算，达标结论按达标率与接近阈值判定为达标、接近达标与未达标，
#           并回写方案级的达标数量与平均达标率；输出达标结论分布、科室达标率
#           对比、目标与实际对比、差距排行与各期达标率趋势五类统计图表，形成
#           「定目标 → 下明细 → 算达标 → 查差距 → 调目标」的绩效目标闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）、random（Python 内置）

import datetime
import random

from perf_store import open_conn, dict_rows
from perf_ranking import fetch_period_options

# ------------------------------------------------------------------
# 目标对象类型字典：科室级用于整体考核，设备级用于单机跟踪
# ------------------------------------------------------------------
TARGET_SCOPE_DICT = {
    "DEPT": "科室整体",
    "DEVICE": "单台设备",
}

# 考核指标字典：与绩效评分表的五项原始指标及综合得分一一对应
TARGET_METRIC_DICT = {
    "total_score": "综合得分",
    "usage_rate": "使用效率",
    "income_rate": "经济效益",
    "maintain_rate": "维保水平",
    "quality_rate": "质量安全",
    "service_rate": "服务效能",
}

# 指标字段与评分表字段的映射，用于按指标键拼装实际值查询列
METRIC_FIELD_MAP = {
    "total_score": "total_score",
    "usage_rate": "usage_rate",
    "income_rate": "income_rate",
    "maintain_rate": "maintain_rate",
    "quality_rate": "quality_rate",
    "service_rate": "service_rate",
}

# 方案状态字典：随明细生成与达标核算逐级流转
TARGET_STATE_DICT = {
    "DRAFT": "草稿",
    "RUN": "执行中",
    "DONE": "已核算",
    "VOID": "已作废",
}

# 达标结论字典：按达标率与接近阈值自动判定
REACH_FLAG_DICT = {
    "REACH": "达标",
    "NEAR": "接近达标",
    "UNREACH": "未达标",
}

# 达标判定阈值：达标率达到该值判定达标，达到接近阈值判定接近达标
REACH_RATE_LINE = 100.0
NEAR_RATE_LINE = 95.0

# 目标值取值区间：与绩效评分同为百分制
TARGET_MIN_VALUE = 50.0
TARGET_MAX_VALUE = 100.0

# 目标明细分页尺寸与一次性生成明细的对象上限
TARGET_PAGE_SIZE = 10
MAX_ITEM_AMOUNT = 24

# 演示数据随机种子，保证每次启动生成的目标值一致
DEMO_RANDOM_SEED = 20261031


def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def now_text():
    """返回当前时间文本 YYYY-MM-DD HH:MM:SS。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_plan_code(seq_no):
    """生成目标方案编号：TP-年月-序号。"""
    return "TP-%s-%03d" % (datetime.date.today().strftime("%Y%m"), seq_no)


def build_item_code(seq_no):
    """生成目标明细编号：TI-年月日-序号。"""
    return "TI-%s-%04d" % (datetime.date.today().strftime("%Y%m%d"), seq_no)


def build_result_code(seq_no):
    """生成达标核算编号：TR-年月日-序号。"""
    return "TR-%s-%04d" % (datetime.date.today().strftime("%Y%m%d"), seq_no)


def next_seq_no(table_name):
    """读取指定业务表记录数并加一，用于拼装业务编码。"""
    conn = open_conn()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def clamp_target(value):
    """把目标值裁剪到百分制区间并保留两位小数。"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number < TARGET_MIN_VALUE:
        number = TARGET_MIN_VALUE
    if number > TARGET_MAX_VALUE:
        number = TARGET_MAX_VALUE
    return round(number, 2)


def judge_reach_flag(reach_rate):
    """按达标率判定达标结论：满分以上达标，接近线以上接近达标，其余未达标。"""
    rate = reach_rate or 0
    if rate >= REACH_RATE_LINE:
        return "REACH"
    if rate >= NEAR_RATE_LINE:
        return "NEAR"
    return "UNREACH"


def calc_reach_rate(actual_value, target_value):
    """计算达标率：实际值 ÷ 目标值 × 100，目标值为零时返回 0。"""
    target = target_value or 0
    if target <= 0:
        return 0.0
    return round((actual_value or 0) * 100.0 / target, 2)


def fetch_plan_row(plan_code):
    """按方案编号读取目标方案，未找到返回 None。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_target_plan WHERE plan_code=?",
            (plan_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


# ------------------------------------------------------------------
# 目标方案维护
# ------------------------------------------------------------------
def save_target_plan(plan_code, plan_name, period_tag, scope_type="DEPT",
                     metric_key="total_score", target_value=80,
                     remark_text="", operator="admin"):
    """登记或更新一套绩效目标方案。

    传入已存在的方案编号时执行更新，否则按新编号建档；考核周期留空时自动
    取库中已有评分数据的最新周期，保证目标与评分数据口径一致。

    :return: 含 success、message 与 plan_code 的执行结果字典
    """
    if scope_type not in TARGET_SCOPE_DICT:
        return {"success": False, "message": "目标对象类型 %s 不合法" % scope_type}
    if metric_key not in TARGET_METRIC_DICT:
        return {"success": False, "message": "考核指标 %s 不合法" % metric_key}
    period = (period_tag or "").strip()
    if not period:
        options = fetch_period_options()
        period = options[0] if options else ""
    if not period:
        return {"success": False, "message": "尚无评分数据，无法制定目标方案"}

    target = clamp_target(target_value)
    conn = open_conn()
    try:
        exist = None
        if (plan_code or "").strip():
            exist = conn.execute(
                "SELECT plan_code FROM pe_target_plan WHERE plan_code=?",
                (plan_code.strip(),)).fetchone()
        if exist is not None:
            code = exist["plan_code"]
            conn.execute(
                "UPDATE pe_target_plan SET plan_name=?, period_tag=?, "
                "scope_type=?, metric_key=?, target_value=?, remark_text=?, "
                "operator_name=? WHERE plan_code=?",
                ((plan_name or "").strip(), period, scope_type, metric_key,
                 target, (remark_text or "").strip(),
                 (operator or "admin").strip(), code))
        else:
            code = build_plan_code(next_seq_no("pe_target_plan"))
            conn.execute(
                "INSERT INTO pe_target_plan (plan_code, plan_name, period_tag, "
                "scope_type, metric_key, target_value, plan_state, remark_text, "
                "operator_name) VALUES (?,?,?,?,?,?,'DRAFT',?,?)",
                (code, (plan_name or "").strip(), period, scope_type,
                 metric_key, target, (remark_text or "").strip(),
                 (operator or "admin").strip()))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "目标方案 %s 已保存" % code,
            "plan_code": code}


def fetch_scope_targets(scope_type, period_tag):
    """读取指定周期内可作为目标对象的科室或设备清单。

    :return: 含 code 与 name 的候选列表
    """
    conn = open_conn()
    try:
        if scope_type == "DEVICE":
            rows = conn.execute(
                "SELECT device_code AS code, device_name AS name FROM "
                "pe_score_record WHERE period_tag=? GROUP BY device_code "
                "ORDER BY total_score DESC LIMIT ?",
                (period_tag, MAX_ITEM_AMOUNT)).fetchall()
        else:
            rows = conn.execute(
                "SELECT dept_name AS code, dept_name AS name FROM pe_score_record "
                "WHERE period_tag=? GROUP BY dept_name ORDER BY "
                "AVG(total_score) DESC", (period_tag,)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def generate_target_items(plan_code, operator="admin"):
    """按方案的目标对象类型一键生成目标明细。

    目标值在方案默认目标值基础上按对象做小幅差异化微调，使同一周期不同
    对象的指标具有区分度；已存在明细时不重复生成。

    :return: 含 success、message 与 amount 的执行结果字典
    """
    plan = fetch_plan_row((plan_code or "").strip())
    if plan is None:
        return {"success": False, "message": "目标方案不存在"}
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_target_item WHERE plan_code=?",
            (plan["plan_code"],)).fetchone()["c"]
        if already:
            return {"success": False, "message": "该方案已存在目标明细，无需重复生成"}
    finally:
        conn.close()

    targets = fetch_scope_targets(plan["scope_type"], plan["period_tag"])
    if not targets:
        return {"success": False, "message": "该考核周期内没有可用的目标对象"}
    randomizer = random.Random(DEMO_RANDOM_SEED + len(plan["plan_code"]))
    # 编号基准在开启写事务前取一次，循环内按序号递增，避免同事务内撞号
    serial = next_seq_no("pe_target_item")
    conn = open_conn()
    try:
        for index, target in enumerate(targets):
            # 差异化微调：在默认目标值上下浮动，形成不同的达成难度
            delta = round(randomizer.uniform(-4.0, 4.0), 2)
            value = clamp_target(plan["target_value"] + delta)
            conn.execute(
                "INSERT INTO pe_target_item (item_code, plan_code, period_tag, "
                "scope_type, scope_code, scope_name, metric_key, target_value, "
                "target_note, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (build_item_code(serial + index),
                 plan["plan_code"], plan["period_tag"], plan["scope_type"],
                 target["code"], target["name"], plan["metric_key"], value,
                 "按周期内实际水平下达的差异化目标", (operator or "admin").strip()))
        conn.execute(
            "UPDATE pe_target_plan SET item_amount=?, plan_state='RUN' "
            "WHERE plan_code=?", (len(targets), plan["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "已生成目标明细 %d 条" % len(targets),
            "amount": len(targets)}


def save_target_item(item_code, plan_code, scope_code, target_value,
                     scope_name="", target_note="", operator="admin"):
    """登记或更新一条目标明细，用于手工调整单个对象的指标值。

    :return: 含 success 与 message 的执行结果字典
    """
    plan = fetch_plan_row((plan_code or "").strip())
    if plan is None:
        return {"success": False, "message": "目标方案不存在"}
    value = clamp_target(target_value)
    conn = open_conn()
    try:
        exist = None
        if (item_code or "").strip():
            exist = conn.execute(
                "SELECT item_code FROM pe_target_item WHERE item_code=?",
                (item_code.strip(),)).fetchone()
        # 编辑已有明细时只需调整目标值，对象编码无需重复传入
        if exist is None and not (scope_code or "").strip():
            return {"success": False, "message": "请选择目标对象"}
        if exist is not None:
            conn.execute(
                "UPDATE pe_target_item SET target_value=?, target_note=?, "
                "operator_name=? WHERE item_code=?",
                (value, (target_note or "").strip(),
                 (operator or "admin").strip(), exist["item_code"]))
            code = exist["item_code"]
        else:
            code = build_item_code(next_seq_no("pe_target_item"))
            conn.execute(
                "INSERT INTO pe_target_item (item_code, plan_code, period_tag, "
                "scope_type, scope_code, scope_name, metric_key, target_value, "
                "target_note, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (code, plan["plan_code"], plan["period_tag"], plan["scope_type"],
                 (scope_code or "").strip(), (scope_name or "").strip() or
                 (scope_code or "").strip(), plan["metric_key"], value,
                 (target_note or "").strip(), (operator or "admin").strip()))
            amount = conn.execute(
                "SELECT COUNT(1) AS c FROM pe_target_item WHERE plan_code=?",
                (plan["plan_code"],)).fetchone()["c"]
            conn.execute(
                "UPDATE pe_target_plan SET item_amount=? WHERE plan_code=?",
                (amount, plan["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "目标明细已保存", "item_code": code}


# ------------------------------------------------------------------
# 达标核算
# ------------------------------------------------------------------
def fetch_actual_value(scope_type, scope_code, period_tag, metric_key):
    """读取目标对象在指定周期内的实际指标值。

    科室级取科室下属设备该指标的平均值，设备级直接取该设备的指标值。
    """
    field = METRIC_FIELD_MAP.get(metric_key, "total_score")
    conn = open_conn()
    try:
        if scope_type == "DEVICE":
            row = conn.execute(
                "SELECT AVG(%s) AS v FROM pe_score_record WHERE device_code=? "
                "AND period_tag=?" % field, (scope_code, period_tag)).fetchone()
        else:
            row = conn.execute(
                "SELECT AVG(%s) AS v FROM pe_score_record WHERE dept_name=? "
                "AND period_tag=?" % field, (scope_code, period_tag)).fetchone()
    finally:
        conn.close()
    return round(row["v"] or 0, 2) if row else 0.0


def calc_target_result(plan_code, operator="admin"):
    """核算一套目标方案的达标情况，逐条明细写入核算结果。

    核算时先清理该方案的历史结果，再按明细读取当期实际值，计算差距与达标
    率并判定达标结论；核算完成后回写方案的达标数量与平均达标率。

    :return: 含 success、message 与 amount 的执行结果字典
    """
    plan = fetch_plan_row((plan_code or "").strip())
    if plan is None:
        return {"success": False, "message": "目标方案不存在"}
    conn = open_conn()
    try:
        items = conn.execute(
            "SELECT * FROM pe_target_item WHERE plan_code=? ORDER BY item_id",
            (plan["plan_code"],)).fetchall()
        if not items:
            return {"success": False, "message": "该方案尚未生成目标明细"}
        conn.execute(
            "DELETE FROM pe_target_result WHERE plan_code=?",
            (plan["plan_code"],))
        judge_time = now_text()
        # 编号基准在开启写事务前取一次，循环内按序号递增，避免同事务内撞号
        serial = next_seq_no("pe_target_result")
        counter = {"REACH": 0, "NEAR": 0, "UNREACH": 0}
        rate_sum = 0.0
        for index, item in enumerate(items):
            actual = fetch_actual_value(item["scope_type"], item["scope_code"],
                                        item["period_tag"], item["metric_key"])
            rate = calc_reach_rate(actual, item["target_value"])
            flag = judge_reach_flag(rate)
            counter[flag] += 1
            rate_sum += rate
            conn.execute(
                "INSERT INTO pe_target_result (result_code, plan_code, "
                "item_code, period_tag, scope_type, scope_code, scope_name, "
                "metric_key, target_value, actual_value, gap_value, reach_rate, "
                "reach_flag, judge_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (build_result_code(serial + index),
                 plan["plan_code"], item["item_code"], item["period_tag"],
                 item["scope_type"], item["scope_code"], item["scope_name"],
                 item["metric_key"], item["target_value"], actual,
                 round(actual - item["target_value"], 2), rate, flag, judge_time))
        avg_rate = round(rate_sum / len(items), 2) if items else 0.0
        conn.execute(
            "UPDATE pe_target_plan SET plan_state='DONE', reach_amount=?, "
            "near_amount=?, unreach_amount=?, avg_reach_rate=?, judge_time=? "
            "WHERE plan_code=?",
            (counter["REACH"], counter["NEAR"], counter["UNREACH"], avg_rate,
             judge_time, plan["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "达标核算完成，达标 %d 条、接近 %d 条、未达标 %d 条" %
                       (counter["REACH"], counter["NEAR"], counter["UNREACH"]),
            "amount": len(items)}


# ------------------------------------------------------------------
# 查询
# ------------------------------------------------------------------
def fetch_target_plans(limit=20):
    """读取目标方案清单，并附带状态标签与达标统计。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_target_plan ORDER BY period_tag DESC, "
            "plan_id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["scope_label"] = TARGET_SCOPE_DICT.get(row["scope_type"], row["scope_type"])
        row["metric_label"] = TARGET_METRIC_DICT.get(row["metric_key"], row["metric_key"])
        row["state_label"] = TARGET_STATE_DICT.get(row["plan_state"], row["plan_state"])
        records.append(row)
    return records


def fetch_target_items(plan_code="", keyword="", page=1):
    """按方案分页查询目标明细，并附带指标标签。"""
    where = []
    params = []
    if plan_code:
        where.append("plan_code=?")
        params.append(plan_code)
    if keyword:
        where.append("(scope_code LIKE ? OR scope_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * TARGET_PAGE_SIZE

    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_target_item" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM pe_target_item" + clause +
            " ORDER BY item_id ASC LIMIT ? OFFSET ?",
            params + [TARGET_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["metric_label"] = TARGET_METRIC_DICT.get(row["metric_key"], row["metric_key"])
        row["scope_label"] = TARGET_SCOPE_DICT.get(row["scope_type"], row["scope_type"])
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": TARGET_PAGE_SIZE,
        "page_amount": max(1, (total + TARGET_PAGE_SIZE - 1) // TARGET_PAGE_SIZE),
    }


def fetch_target_results(plan_code="", reach_flag="", keyword="", page=1):
    """按方案与达标结论分页查询核算结果，并附带结论标签。"""
    where = []
    params = []
    if plan_code:
        where.append("plan_code=?")
        params.append(plan_code)
    if reach_flag:
        where.append("reach_flag=?")
        params.append(reach_flag)
    if keyword:
        where.append("(scope_code LIKE ? OR scope_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * TARGET_PAGE_SIZE

    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_target_result" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM pe_target_result" + clause +
            " ORDER BY reach_rate DESC, result_id ASC LIMIT ? OFFSET ?",
            params + [TARGET_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["flag_label"] = REACH_FLAG_DICT.get(row["reach_flag"], row["reach_flag"])
        row["metric_label"] = TARGET_METRIC_DICT.get(row["metric_key"], row["metric_key"])
        row["scope_label"] = TARGET_SCOPE_DICT.get(row["scope_type"], row["scope_type"])
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": TARGET_PAGE_SIZE,
        "page_amount": max(1, (total + TARGET_PAGE_SIZE - 1) // TARGET_PAGE_SIZE),
    }


# ------------------------------------------------------------------
# 统计
# ------------------------------------------------------------------
def build_target_summary():
    """汇总绩效目标核心指标：方案数、目标对象数、达标率、达标与未达标条数。"""
    conn = open_conn()
    try:
        plan_row = conn.execute(
            "SELECT COUNT(1) AS c, COALESCE(AVG(avg_reach_rate),0) AS r "
            "FROM pe_target_plan WHERE plan_state='DONE'").fetchone()
        item_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_target_item").fetchone()["c"]
        flag_rows = conn.execute(
            "SELECT reach_flag, COUNT(1) AS c FROM pe_target_result "
            "GROUP BY reach_flag").fetchall()
        gap_row = conn.execute(
            "SELECT COALESCE(MIN(gap_value),0) AS min_gap, "
            "COALESCE(AVG(gap_value),0) AS avg_gap FROM pe_target_result").fetchone()
        latest_plan = conn.execute(
            "SELECT plan_code, period_tag, avg_reach_rate FROM pe_target_plan "
            "WHERE plan_state='DONE' ORDER BY plan_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    flag_counter = {item["reach_flag"]: item["c"] for item in flag_rows}
    reach = flag_counter.get("REACH", 0)
    near = flag_counter.get("NEAR", 0)
    unreach = flag_counter.get("UNREACH", 0)
    judged = reach + near + unreach
    return {
        "plan_amount": plan_row["c"],
        "item_amount": item_amount,
        "reach_amount": reach,
        "near_amount": near,
        "unreach_amount": unreach,
        "reach_rate": round(reach * 100.0 / judged, 1) if judged else 0.0,
        "avg_reach_rate": round(plan_row["r"], 2),
        "avg_gap": round(gap_row["avg_gap"], 2),
        "min_gap": round(gap_row["min_gap"], 2),
        "latest_plan": dict(latest_plan) if latest_plan is not None else {},
    }


def build_target_charts():
    """构建五类图表数据：达标结论分布、科室达标率对比、目标与实际对比、
    差距排行与各期达标率趋势。"""
    conn = open_conn()
    try:
        result_rows = conn.execute(
            "SELECT scope_type, scope_name, target_value, actual_value, "
            "gap_value, reach_rate, reach_flag, period_tag "
            "FROM pe_target_result").fetchall()
        plan_rows = conn.execute(
            "SELECT period_tag, avg_reach_rate, reach_amount, near_amount, "
            "unreach_amount, item_amount FROM pe_target_plan "
            "WHERE plan_state='DONE' ORDER BY period_tag ASC").fetchall()
    finally:
        conn.close()

    flag_counter = {}
    for item in result_rows:
        flag_counter[item["reach_flag"]] = flag_counter.get(item["reach_flag"], 0) + 1
    reach_distribution = [
        {"name": REACH_FLAG_DICT.get(code, code), "value": flag_counter.get(code, 0)}
        for code in ("REACH", "NEAR", "UNREACH")
    ]

    dept_rows = [item for item in result_rows if item["scope_type"] == "DEPT"]
    dept_rate = sorted(
        [{"name": item["scope_name"], "value": item["reach_rate"]}
         for item in dept_rows],
        key=lambda x: x["value"], reverse=True)[:8]

    compare_rank = sorted(
        [{"name": item["scope_name"], "target": item["target_value"],
          "actual": item["actual_value"]} for item in result_rows],
        key=lambda x: x["target"], reverse=True)[:8]

    gap_rank = sorted(
        [{"name": item["scope_name"], "value": round(item["gap_value"], 2)}
         for item in result_rows],
        key=lambda x: x["value"])[:6]

    period_keys = []
    period_rate = {}
    period_reach = {}
    for item in plan_rows:
        tag = item["period_tag"]
        if tag not in period_rate:
            period_keys.append(tag)
            period_rate[tag] = round(item["avg_reach_rate"] or 0, 2)
            total = max(1, item["item_amount"] or 1)
            period_reach[tag] = round((item["reach_amount"] or 0) * 100.0 / total, 1)

    return {
        "reach_distribution": reach_distribution,
        "dept_rate": dept_rate,
        "compare_rank": compare_rank,
        "gap_rank": gap_rank,
        "period_trend": {
            "periods": period_keys,
            "rate_values": [period_rate[key] for key in period_keys],
            "reach_values": [period_reach[key] for key in period_keys],
        },
    }


def fetch_target_options():
    """绩效目标页下拉选项：各类字典项、考核周期与目标方案候选。"""
    return {
        "period_options": fetch_period_options(),
        "plan_options": [{"code": item["plan_code"],
                          "label": item["plan_code"] + " · " + item["plan_name"]}
                         for item in fetch_target_plans()],
        "scope_options": [{"code": k, "label": v} for k, v in TARGET_SCOPE_DICT.items()],
        "metric_options": [{"code": k, "label": v} for k, v in TARGET_METRIC_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in TARGET_STATE_DICT.items()],
        "flag_options": [{"code": k, "label": v} for k, v in REACH_FLAG_DICT.items()],
    }


def build_target_payload(plan_code="", item_plan="", item_keyword="",
                         result_plan="", reach_flag="", result_keyword="",
                         item_page=1, result_page=1):
    """组装绩效目标管理与达标考核页面所需的全部数据。"""
    plans = fetch_target_plans()
    active_plan = (plan_code or "").strip()
    if not active_plan and plans:
        active_plan = plans[0]["plan_code"]
    item_pager = fetch_target_items(item_plan or active_plan, item_keyword, item_page)
    result_pager = fetch_target_results(result_plan or active_plan, reach_flag,
                                        result_keyword, result_page)
    return {
        "summary": build_target_summary(),
        "charts": build_target_charts(),
        "plans": plans,
        "active_plan": active_plan,
        "items": item_pager["records"],
        "item_total": item_pager["total"],
        "item_page": item_pager["page"],
        "item_page_amount": item_pager["page_amount"],
        "results": result_pager["records"],
        "result_total": result_pager["total"],
        "result_page": result_pager["page"],
        "result_page_amount": result_pager["page_amount"],
        "options": fetch_target_options(),
        "result": "OK",
    }


def build_target_brief():
    """绩效目标看板摘要，供绩效分析大屏嵌入。"""
    summary = build_target_summary()
    return {
        "plan_amount": summary["plan_amount"],
        "item_amount": summary["item_amount"],
        "reach_amount": summary["reach_amount"],
        "unreach_amount": summary["unreach_amount"],
        "avg_reach_rate": summary["avg_reach_rate"],
        "avg_gap": summary["avg_gap"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_target():
    """灌入绩效目标演示数据：制定方案、生成明细并核算达标情况。

    演示覆盖科室综合得分、设备使用效率与上一周期科室综合得分三类方案，
    使达标结论分布与各期达标率趋势均有样本。

    :return: 生成的方案条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_target_plan").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()
    periods = fetch_period_options()
    if not periods:
        return 0
    current_period = periods[0]
    previous = periods[1] if len(periods) > 1 else current_period
    earlier = periods[2] if len(periods) > 2 else previous

    demo_plan_list = (
        ("%s 科室综合得分目标" % current_period, current_period, "DEPT",
         "total_score", 78.0),
        ("%s 设备使用效率目标" % current_period, current_period, "DEVICE",
         "usage_rate", 72.0),
        ("%s 科室综合得分目标" % previous, previous, "DEPT", "total_score", 76.0),
        ("%s 科室综合得分目标" % earlier, earlier, "DEPT", "total_score", 74.0),
    )
    amount = 0
    for plan_name, period_tag, scope_type, metric_key, target_value \
            in demo_plan_list:
        outcome = save_target_plan(
            "", plan_name, period_tag, scope_type, metric_key, target_value,
            "演示绩效目标方案", "绩效办-吴主任")
        if not outcome.get("success"):
            continue
        amount += 1
        code = outcome["plan_code"]
        generate_target_items(code, "绩效办-吴主任")
        calc_target_result(code, "绩效办-吴主任")
    return amount

# --------------------------------------------------------------------------
# 第 11 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY11_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY11_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 11 天新增：绩效目标方案表
-- ==================================================================
-- 绩效目标方案表：按「一个考核周期一套目标方案」记录目标对象范围、考核指标与
-- 默认目标值，方案状态随明细生成与达标核算逐级流转；核算完成后回写达标数量、
-- 接近数量、未达标数量与平均达标率，支撑全院目标完成情况的总体研判
CREATE TABLE IF NOT EXISTS pe_target_plan (
    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 方案主键
    plan_code      TEXT    NOT NULL UNIQUE,            -- 方案编号（TP-年月-序号）
    plan_name      TEXT    DEFAULT '',                 -- 方案名称
    period_tag     TEXT    DEFAULT '',                 -- 考核周期（如 2026-Q4）
    scope_type     TEXT    DEFAULT 'DEPT',             -- 目标对象类型（见 TARGET_SCOPE_DICT）
    metric_key     TEXT    DEFAULT 'total_score',      -- 考核指标键（见 TARGET_METRIC_DICT）
    target_value   REAL    DEFAULT 80,                 -- 默认目标值（百分制）
    item_amount    INTEGER DEFAULT 0,                  -- 目标明细条数
    reach_amount   INTEGER DEFAULT 0,                  -- 达标条数
    near_amount    INTEGER DEFAULT 0,                  -- 接近达标条数
    unreach_amount INTEGER DEFAULT 0,                  -- 未达标条数
    avg_reach_rate REAL    DEFAULT 0,                  -- 平均达标率（%）
    plan_state     TEXT    DEFAULT 'DRAFT',            -- 方案状态（见 TARGET_STATE_DICT）
    judge_time     TEXT    DEFAULT '',                 -- 最近核算时间
    remark_text    TEXT    DEFAULT '',                 -- 方案说明
    operator_name  TEXT    DEFAULT 'admin',            -- 方案制定人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 11 天新增：绩效目标明细表
-- ==================================================================
-- 绩效目标明细表：按「一个目标对象一条明细」记录科室或设备的个性化目标值与
-- 目标说明，明细由方案一键生成或手工维护，是达标核算的直接依据，支持同一
-- 周期不同对象差异化下达指标
CREATE TABLE IF NOT EXISTS pe_target_item (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 明细主键
    item_code      TEXT    NOT NULL UNIQUE,            -- 明细编号（TI-年月日-序号）
    plan_code      TEXT    NOT NULL,                   -- 关联方案编号
    period_tag     TEXT    DEFAULT '',                 -- 考核周期
    scope_type     TEXT    DEFAULT 'DEPT',             -- 目标对象类型
    scope_code     TEXT    DEFAULT '',                 -- 目标对象编码（科室名或设备编号）
    scope_name     TEXT    DEFAULT '',                 -- 目标对象名称
    metric_key     TEXT    DEFAULT 'total_score',      -- 考核指标键
    target_value   REAL    DEFAULT 80,                 -- 目标值
    target_note    TEXT    DEFAULT '',                 -- 目标说明
    operator_name  TEXT    DEFAULT 'admin',            -- 明细维护人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 11 天新增：绩效达标核算结果表
-- ==================================================================
-- 达标核算结果表：按「一条目标明细一条核算结果」记录目标值、实际值与差距，
-- 达标率由「实际值 ÷ 目标值」计算，达标结论按达标率与接近阈值自动判定，
-- 支撑目标完成情况的量化考核与差距原因分析
CREATE TABLE IF NOT EXISTS pe_target_result (
    result_id      INTEGER PRIMARY KEY AUTOINCREMENT,  -- 核算主键
    result_code    TEXT    NOT NULL UNIQUE,            -- 核算编号（TR-年月日-序号）
    plan_code      TEXT    NOT NULL,                   -- 关联方案编号
    item_code      TEXT    NOT NULL,                   -- 关联明细编号
    period_tag     TEXT    DEFAULT '',                 -- 考核周期
    scope_type     TEXT    DEFAULT 'DEPT',             -- 目标对象类型
    scope_code     TEXT    DEFAULT '',                 -- 目标对象编码
    scope_name     TEXT    DEFAULT '',                 -- 目标对象名称
    metric_key     TEXT    DEFAULT 'total_score',      -- 考核指标键
    target_value   REAL    DEFAULT 0,                  -- 目标值
    actual_value   REAL    DEFAULT 0,                  -- 实际值
    gap_value      REAL    DEFAULT 0,                  -- 差距值（实际 - 目标）
    reach_rate     REAL    DEFAULT 0,                  -- 达标率（%）
    reach_flag     TEXT    DEFAULT 'UNREACH',          -- 达标结论（见 REACH_FLAG_DICT）
    judge_time     TEXT    DEFAULT '',                 -- 核算时间
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_tplan_code      ON pe_target_plan (plan_code);
CREATE INDEX IF NOT EXISTS idx_tplan_period    ON pe_target_plan (period_tag);
CREATE INDEX IF NOT EXISTS idx_tplan_state     ON pe_target_plan (plan_state);
CREATE INDEX IF NOT EXISTS idx_titem_code      ON pe_target_item (item_code);
CREATE INDEX IF NOT EXISTS idx_titem_plan      ON pe_target_item (plan_code);
CREATE INDEX IF NOT EXISTS idx_titem_scope     ON pe_target_item (scope_code);
CREATE INDEX IF NOT EXISTS idx_tresult_code    ON pe_target_result (result_code);
CREATE INDEX IF NOT EXISTS idx_tresult_plan    ON pe_target_result (plan_code);
CREATE INDEX IF NOT EXISTS idx_tresult_flag    ON pe_target_result (reach_flag);
"""


def apply_day11_schema(conn):
    """把第 11 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY11_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 11 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-31（第 11 天 / 共 15 天）
#           绩效趋势预测、绩效改进建议与绩效目标考核十一类页面路由，启动时
#           完成建表、等级规则初始化、科室初始化与演示评分、上期对照、效益
#           明细、低效预警、导出模板、导出留痕、历史评分样本、趋势预测、
#           改进建议整改跟踪与目标方案达标核算数据灌入，并提供评分试算、评分
#           入库、评分明细、权重元信息、榜单、科室横向对比、等级批量重判、
#           ECharts 图表 option、多维度排行筛选查询、效益报表核算归档、低效
#           预警规则维护扫描整改、导出模板维护、报告生成落盘、文件预览下载与
#           留痕删除、预测模型维护、批量趋势预测、

from perf_target import (
    build_target_payload,
    build_target_brief,
    save_target_plan,
    generate_target_items,
    save_target_item,
    calc_target_result,
    fetch_target_plans,
    seed_demo_target,
    TARGET_SCOPE_DICT,
    TARGET_METRIC_DICT,
    TARGET_STATE_DICT,
    REACH_FLAG_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 10 天新增：大屏叠加改进建议摘要，供改进概况卡渲染
#    # 第 11 天新增：大屏叠加绩效目标摘要，供目标概况卡渲染
#    payload["target_brief"] = build_target_brief()

@app.route("/pf/target")
def page_target():
    """绩效目标管理与达标考核页面：目标方案、目标明细与达标核算结果。"""
    return render_template(
        "page_target.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/target/panel")
def api_target_panel():
    """绩效目标数据接口：汇总指标、五类图表、方案明细与双分页结果。"""
    return jsonify(build_target_payload(
        plan_code=(request.args.get("plan_code") or "").strip(),
        item_plan=(request.args.get("item_plan") or "").strip(),
        item_keyword=(request.args.get("item_keyword") or "").strip(),
        result_plan=(request.args.get("result_plan") or "").strip(),
        reach_flag=(request.args.get("reach_flag") or "").strip(),
        result_keyword=(request.args.get("result_keyword") or "").strip(),
        item_page=int(request.args.get("item_page", 1)),
        result_page=int(request.args.get("result_page", 1)),
    ))


@app.route("/api/pf/target/plan/save", methods=["POST"])
def api_target_plan_save():
    """目标方案保存接口：传入编号则更新，留空编号则按序号建档。"""
    body = request.get_json(silent=True) or {}
    result = save_target_plan(
        (body.get("plan_code") or "").strip(),
        (body.get("plan_name") or "").strip(),
        (body.get("period_tag") or "").strip(),
        (body.get("scope_type") or "DEPT").strip(),
        (body.get("metric_key") or "total_score").strip(),
        body.get("target_value") or 80,
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/target/items/generate", methods=["POST"])
def api_target_items_generate():
    """目标明细生成接口：按方案的目标对象类型一键生成差异化目标明细。"""
    body = request.get_json(silent=True) or {}
    result = generate_target_items(
        (body.get("plan_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/target/item/save", methods=["POST"])
def api_target_item_save():
    """目标明细保存接口：手工调整单个目标对象的指标值。"""
    body = request.get_json(silent=True) or {}
    result = save_target_item(
        (body.get("item_code") or "").strip(),
        (body.get("plan_code") or "").strip(),
        (body.get("scope_code") or "").strip(),
        body.get("target_value") or 80,
        body.get("scope_name") or "",
        body.get("target_note") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/target/calc", methods=["POST"])
def api_target_calc():
    """达标核算接口：逐条明细对比实际值并回写方案达标统计。"""
    body = request.get_json(silent=True) or {}
    result = calc_target_result(
        (body.get("plan_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/target/plans")
def api_target_plans():
    """目标方案清单接口：返回全部方案及其达标统计。"""
    return jsonify({
        "result": "OK",
        "plans": fetch_target_plans(),
    })


@app.route("/api/pf/target/options")
def api_target_options():
    """绩效目标下拉选项接口：目标对象类型、考核指标、方案状态与达标结论。"""
    return jsonify({
        "result": "OK",
        "scope_options": [{"code": k, "label": v} for k, v in TARGET_SCOPE_DICT.items()],
        "metric_options": [{"code": k, "label": v} for k, v in TARGET_METRIC_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in TARGET_STATE_DICT.items()],
        "flag_options": [{"code": k, "label": v} for k, v in REACH_FLAG_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_target()

# 【函数内插入】if __name__ == "__main__":
#    print("目标考核：   http://127.0.0.1:%d/pf/target" % PORT)

# --------------------------------------------------------------------------
# 第 11 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_target.js            新增 518 行
#   [新增] templates/page_target.html       新增 268 行
#   [修改] templates/screen_base.html       新增 1 行
