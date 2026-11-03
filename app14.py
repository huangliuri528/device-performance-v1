# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-03（第 14 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 14 天新增与修改的源代码，不含第 1~13 天已有代码
#           本次迭代主题：新增绩效奖金核算与分配管理模块（奖金池建档、科室分配展开、单项调节、平衡配平、复核发放）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 14 天新增业务模块 1：perf_bonus.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-03（第 14 天 / 共 15 天）
# 模块功能：绩效奖金核算与分配管理模块。实现按考核周期建立奖金池（奖金总额、
#           核算方式、参与科室）、按当期科室绩效评分自动展开科室分配明细并计算
#           核算权重与基准金额、单项调节系数调整、分配差额配平与平衡校验、提交
#           复核、复核裁决通过或驳回、奖金发放归档与奖金池作废全过程管理；核算
#           权重支持绩效得分加权、等级系数加权、设备规模加权与得分规模复合四种
#           口径，分配金额超出奖金总额时不允许提交复核，形成「奖金池建档 →
#           科室分配展开 → 单项调节 → 平衡校验 → 提交复核 → 复核发放 → 归档」
#           的绩效奖金核算闭环；输出核算方式分布、奖金池状态分布、科室奖金金额
#           排行、绩效等级平均奖金对比与各周期奖金发放趋势五类统计图表
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from perf_store import open_conn, dict_rows, fetch_department_names
from perf_ranking import fetch_period_options

# ------------------------------------------------------------------
# 核算方式字典：四种奖金分配权重口径
# ------------------------------------------------------------------
CALC_MODE_DICT = {
    "SCORE": "绩效得分加权",
    "GRADE": "等级系数加权",
    "SCALE": "设备规模加权",
    "HYBRID": "得分规模复合",
}

# 奖金池状态字典：随核算、复核、发放逐级流转
POOL_STATE_DICT = {
    "DRAFT": "草稿",
    "CALC": "核算中",
    "REVIEW": "待复核",
    "PAID": "已发放",
    "VOID": "已作废",
}

# 分配明细状态字典
DETAIL_STATE_DICT = {
    "NORMAL": "正常核算",
    "ADJUST": "已调节",
}

# 绩效等级系数：A/B/C/D 四级差异化分配权重
GRADE_COEF_MAP = {
    "A": 1.30,
    "B": 1.10,
    "C": 0.90,
    "D": 0.70,
}

# 平衡容差：分配合计与奖金总额差额在容差内视为平衡（万元）
BALANCE_TOLERANCE = 0.05

# 未归档状态集合：单项调节与配平只处理这些状态的奖金池
OPEN_STATE_LIST = ("DRAFT", "CALC")

# 分页尺寸：奖金池独立分页
POOL_PAGE_SIZE = 8

# 核算负责人候选
OWNER_LIST = ("绩效办-秦主任", "财务处-陶处长", "院办-邱主任")

# 演示奖金池清单
# （奖金池名称，考核周期序号，奖金总额万元，核算方式，目标状态，说明）
DEMO_POOL_LIST = (
    ("2025 年四季度设备绩效奖金", 3, 86.0, "SCORE", "PAID",
     "按当期绩效得分加权核算，已完成复核发放"),
    ("2026 年一季度设备绩效奖金", 2, 92.5, "GRADE", "PAID",
     "按绩效等级系数加权核算，突出 A 级科室激励"),
    ("2026 年二季度设备绩效奖金", 1, 98.0, "HYBRID", "REVIEW",
     "得分规模复合口径，已提交复核等待裁决"),
    ("2026 年三季度设备绩效奖金", 0, 105.0, "SCALE", "CALC",
     "按设备规模加权，单项调节与配平演示"),
)


def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def now_text():
    """返回当前时间文本 YYYY-MM-DD HH:MM:SS。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_offset_text(days):
    """返回相对今天偏移指定天数的日期文本。"""
    return (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


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


def build_pool_code(seq_no):
    """生成奖金池编号：BP-0001。"""
    return "BP-%04d" % seq_no


def build_detail_code(seq_no):
    """生成分配明细编号：BD-000001。"""
    return "BD-%06d" % seq_no


def build_audit_code(seq_no):
    """生成复核流转编号：BA-000001。"""
    return "BA-%06d" % seq_no


def calc_weight_value(calc_mode, avg_score, grade_level, device_amount):
    """按核算方式计算某科室的分配权重，权重恒为正数。

    SCORE 取平均绩效得分；GRADE 取绩效等级系数；SCALE 取在册设备规模；
    HYBRID 取「平均得分 × 设备规模平方根」的复合口径，兼顾绩效与工作量。
    """
    score = float(avg_score or 0)
    scale = max(1, int(device_amount or 0))
    coef = GRADE_COEF_MAP.get((grade_level or "").strip().upper(), 1.0)
    if calc_mode == "GRADE":
        return round2(coef * 100)
    if calc_mode == "SCALE":
        return round2(scale)
    if calc_mode == "HYBRID":
        return round2(max(score, 1) * (scale ** 0.5))
    return round2(max(score, 1))


def calc_grade_mode(grade_rows):
    """从科室评分等级计数中取出现次数最多的等级作为主导绩效等级。"""
    if not grade_rows:
        return "C"
    best_code, best_amount = "C", -1
    for row in grade_rows:
        code = (row["grade_level"] or "").strip().upper() or "C"
        amount = int(row["c"] or 0)
        if amount > best_amount:
            best_code, best_amount = code, amount
    return best_code if best_code in GRADE_COEF_MAP else "C"


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_pool_row(pool_code):
    """按奖金池编号读取一条奖金池，不存在返回 None。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_bonus_pool WHERE pool_code=?",
            ((pool_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_pool_details(pool_code):
    """读取某奖金池下的全部分配明细，按明细编号升序返回。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_bonus_detail WHERE pool_code=? "
            "ORDER BY detail_id ASC", ((pool_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def write_audit(pool_code, action_name, from_state, to_state, action_desc,
                operator, conn):
    """在给定连接的事务内写入一条复核流转留痕，调用方负责提交。"""
    row = conn.execute(
        "SELECT COALESCE(MAX(audit_id),0) AS m FROM pe_bonus_audit").fetchone()
    seq = int(row["m"] or 0) + 1
    conn.execute(
        "INSERT INTO pe_bonus_audit (audit_code, pool_code, action_name, "
        "from_state, to_state, action_desc, operator_name, action_time) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (build_audit_code(seq), pool_code, action_name, from_state, to_state,
         action_desc, (operator or "admin").strip(), now_text()))


# ------------------------------------------------------------------
# 奖金池建档与科室分配展开
# ------------------------------------------------------------------
def create_bonus_pool(pool_name, period_tag="", total_amount=0,
                      calc_mode="SCORE", operator_name="", remark_text="",
                      dept_limit=0):
    """新建一个奖金池，并按考核周期的科室绩效评分自动展开分配明细。

    明细按「一个科室一条」生成，逐科室汇总当期平均得分、主导等级与设备
    评分条数，结合科室档案的设备数与在岗人数计算核算权重；基准金额按
    权重占比分配奖金总额，实发金额初始等于基准金额。

    :return: 含 success、message、pool_code 与 amount 的执行结果字典
    """
    pool_name = (pool_name or "").strip()
    if not pool_name:
        return {"success": False, "message": "请填写奖金池名称"}
    if calc_mode not in CALC_MODE_DICT:
        return {"success": False, "message": "核算方式 %s 不合法" % calc_mode}
    try:
        total = float(total_amount or 0)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0:
        return {"success": False, "message": "奖金总额必须大于 0"}

    period_tag = (period_tag or "").strip()
    conn = open_conn()
    try:
        score_rows = conn.execute(
            "SELECT dept_name, AVG(total_score) AS avg_score, "
            "COUNT(1) AS score_amount FROM pe_score_record "
            "WHERE (?='' OR period_tag=?) GROUP BY dept_name "
            "ORDER BY avg_score DESC", (period_tag, period_tag)).fetchall()
    finally:
        conn.close()
    if not score_rows:
        return {"success": False, "message": "所选考核周期没有可用的科室绩效评分"}

    if dept_limit and dept_limit > 0:
        score_rows = list(score_rows)[:dept_limit]

    conn = open_conn()
    try:
        dept_rows = conn.execute(
            "SELECT dept_name, dept_type, device_amount, staff_amount "
            "FROM pe_department_profile").fetchall()
        dept_map = {row["dept_name"]: dict(row) for row in dept_rows}
        grade_rows = conn.execute(
            "SELECT dept_name, grade_level, COUNT(1) AS c FROM pe_score_record "
            "WHERE (?='' OR period_tag=?) GROUP BY dept_name, grade_level",
            (period_tag, period_tag)).fetchall()
        grade_map = {}
        for row in grade_rows:
            grade_map.setdefault(row["dept_name"], []).append(dict(row))

        dept_items = []
        for row in score_rows:
            dept_name = row["dept_name"]
            profile = dept_map.get(dept_name, {})
            device_amount = int(profile.get("device_amount") or 0)
            staff_amount = int(profile.get("staff_amount") or 0)
            avg_score = round2(row["avg_score"])
            grade_level = calc_grade_mode(grade_map.get(dept_name, []))
            weight = calc_weight_value(calc_mode, avg_score, grade_level,
                                       device_amount)
            dept_items.append({
                "dept_name": dept_name,
                "dept_type": profile.get("dept_type") or "临床",
                "device_amount": device_amount,
                "staff_amount": staff_amount,
                "score_amount": int(row["score_amount"] or 0),
                "avg_score": avg_score,
                "grade_level": grade_level,
                "weight_value": weight,
            })
        weight_sum = sum(item["weight_value"] for item in dept_items)
        if weight_sum <= 0:
            return {"success": False, "message": "核算权重合计为 0，无法分配奖金"}

        code = build_pool_code(next_seq_no("pe_bonus_pool"))
        detail_seq = next_seq_no("pe_bonus_detail")
        allocated = 0.0
        last_index = len(dept_items) - 1
        for index, item in enumerate(dept_items):
            # 逐条按权重占比分配，最后一条承接四舍五入残差，保证合计精确等于总额
            if index == last_index:
                base_amount = round2(total - allocated)
            else:
                base_amount = round2(total * item["weight_value"] / weight_sum)
            allocated += base_amount
            conn.execute(
                "INSERT INTO pe_bonus_detail (detail_code, pool_code, dept_name, "
                "dept_type, device_amount, staff_amount, score_amount, avg_score, "
                "grade_level, weight_value, base_amount, adjust_coef, "
                "final_amount, detail_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,'NORMAL')",
                (build_detail_code(detail_seq), code, item["dept_name"],
                 item["dept_type"], item["device_amount"], item["staff_amount"],
                 item["score_amount"], item["avg_score"], item["grade_level"],
                 item["weight_value"], base_amount, base_amount))
            detail_seq += 1
        conn.execute(
            "INSERT INTO pe_bonus_pool (pool_code, pool_name, period_tag, "
            "total_amount, calc_mode, dept_amount, allocated_amount, "
            "balance_gap, balance_flag, pool_state, operator_name, remark_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,'CALC',?,?)",
            (code, pool_name, period_tag, total, calc_mode, len(dept_items),
             round2(allocated), round2(allocated - total),
             1 if abs(allocated - total) > BALANCE_TOLERANCE else 0,
             (operator_name or "").strip(), (remark_text or "").strip()))
        write_audit(code, "奖金池建档", "", "CALC",
                    "按 %s 口径展开 %d 个科室分配明细，奖金总额 %.2f 万元" % (
                        CALC_MODE_DICT[calc_mode], len(dept_items), total),
                    (operator_name or "admin"), conn)
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "奖金池 %s 已生成，展开 %d 个科室分配明细" %
                       (code, len(dept_items)),
            "pool_code": code, "amount": len(dept_items)}


def recalc_pool_stat(pool_code):
    """重算某奖金池的分配合计、差额与平衡标记，并回写奖金池。

    :return: 含 allocated、gap、balanced 与 dept_amount 的统计字典
    """
    conn = open_conn()
    try:
        stat = conn.execute(
            "SELECT COALESCE(SUM(final_amount),0) AS s, COUNT(1) AS c "
            "FROM pe_bonus_detail WHERE pool_code=?", (pool_code,)).fetchone()
        pool = conn.execute(
            "SELECT total_amount FROM pe_bonus_pool WHERE pool_code=?",
            (pool_code,)).fetchone()
        allocated = round2(stat["s"])
        dept_amount = int(stat["c"] or 0)
        total = round2(pool["total_amount"]) if pool else 0.0
        gap = round2(allocated - total)
        balanced = abs(gap) <= BALANCE_TOLERANCE
        conn.execute(
            "UPDATE pe_bonus_pool SET allocated_amount=?, balance_gap=?, "
            "balance_flag=?, dept_amount=? WHERE pool_code=?",
            (allocated, gap, 0 if balanced else 1, dept_amount, pool_code))
        conn.commit()
    finally:
        conn.close()
    return {"allocated": allocated, "gap": gap, "balanced": balanced,
            "dept_amount": dept_amount, "total": total}


# ------------------------------------------------------------------
# 单项调节与平衡配平
# ------------------------------------------------------------------
def adjust_bonus_detail(detail_code, adjust_coef=1, adjust_note="",
                        operator="admin"):
    """调节单个科室的分配系数，按系数重算实发金额并回算奖金池统计。

    :return: 含 success、message 与 final_amount 的执行结果字典
    """
    try:
        coef = float(adjust_coef or 1)
    except (TypeError, ValueError):
        coef = 1.0
    if coef < 0.5 or coef > 1.5:
        return {"success": False, "message": "调节系数须在 0.5 ~ 1.5 之间"}
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_bonus_detail WHERE detail_code=?",
            ((detail_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "分配明细不存在"}
        pool = conn.execute(
            "SELECT pool_state FROM pe_bonus_pool WHERE pool_code=?",
            (row["pool_code"],)).fetchone()
        if pool is not None and pool["pool_state"] not in OPEN_STATE_LIST:
            return {"success": False,
                    "message": "仅草稿或核算中的奖金池可以调节分配系数"}
        final_amount = round2(float(row["base_amount"] or 0) * coef)
        state = "ADJUST" if abs(coef - 1.0) > 0.0001 else "NORMAL"
        conn.execute(
            "UPDATE pe_bonus_detail SET adjust_coef=?, final_amount=?, "
            "detail_state=?, adjust_note=? WHERE detail_code=?",
            (round2(coef), final_amount, state, (adjust_note or "").strip(),
             row["detail_code"]))
        conn.commit()
    finally:
        conn.close()
    stat = recalc_pool_stat(row["pool_code"])
    return {"success": True,
            "message": "科室 %s 调节系数已设为 %.2f，实发 %.2f 万元" % (
                row["dept_name"], coef, final_amount),
            "final_amount": final_amount, "gap": stat["gap"]}


def balance_bonus_pool(pool_code, operator="admin"):
    """对奖金池做平衡配平：把分配差额按权重占比摊回各科室基准金额。

    配平后各科室实发金额 = 回写基准金额 × 已设调节系数，保证分配合计与
    奖金总额一致；已发放或已作废的奖金池不允许配平。

    :return: 含 success、message 与 gap 的执行结果字典
    """
    pool = fetch_pool_row(pool_code)
    if pool is None:
        return {"success": False, "message": "奖金池不存在"}
    if pool["pool_state"] not in OPEN_STATE_LIST:
        return {"success": False, "message": "仅草稿或核算中的奖金池可以配平"}
    details = fetch_pool_details(pool["pool_code"])
    if not details:
        return {"success": False, "message": "该奖金池没有可配平的分配明细"}
    # 有效权重 = 核算权重 × 调节系数，按有效权重占比分摊奖金总额，
    # 保证「Σ(基准金额 × 调节系数) = 奖金总额」在有单项调节时依然成立
    effective_sum = sum(
        float(item["weight_value"] or 0) * float(item["adjust_coef"] or 1)
        for item in details)
    if effective_sum <= 0:
        return {"success": False, "message": "有效核算权重合计为 0，无法配平"}
    total = float(pool["total_amount"] or 0)
    conn = open_conn()
    try:
        allocated = 0.0
        last_index = len(details) - 1
        for index, item in enumerate(details):
            coef = float(item["adjust_coef"] or 1) or 1.0
            weight = float(item["weight_value"] or 0)
            if index == last_index:
                # 最后一条承接累计舍入残差，确保实发金额合计精确等于奖金总额
                final_amount = round2(total - allocated)
            else:
                final_amount = round2(total * weight * coef / effective_sum)
            allocated += final_amount
            base_amount = round2(final_amount / coef)
            conn.execute(
                "UPDATE pe_bonus_detail SET base_amount=?, final_amount=? "
                "WHERE detail_code=?",
                (base_amount, final_amount, item["detail_code"]))
        conn.commit()
    finally:
        conn.close()
    stat = recalc_pool_stat(pool["pool_code"])
    conn = open_conn()
    try:
        write_audit(pool["pool_code"], "分配配平", "CALC", "CALC",
                    "按核算权重重新配平 %d 个科室，分配差额 %.2f 万元" % (
                        len(details), stat["gap"]), operator, conn)
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "奖金池 %s 已配平，差额 %.2f 万元" %
                       (pool["pool_code"], stat["gap"]),
            "gap": stat["gap"], "balanced": stat["balanced"]}


def submit_bonus_pool(pool_code, operator="admin"):
    """提交复核：分配未平衡或明细不足时不允许提交。"""
    pool = fetch_pool_row(pool_code)
    if pool is None:
        return {"success": False, "message": "奖金池不存在"}
    if pool["pool_state"] not in OPEN_STATE_LIST:
        return {"success": False, "message": "仅草稿或核算中的奖金池可以提交复核"}
    stat = recalc_pool_stat(pool["pool_code"])
    if not stat["balanced"]:
        return {"success": False,
                "message": "分配差额 %.2f 万元超出容差，请先执行配平" % stat["gap"]}
    if stat["dept_amount"] < 2:
        return {"success": False, "message": "参与分配的科室不足 2 个，不可提交复核"}
    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_bonus_pool SET pool_state='REVIEW', submit_time=? "
            "WHERE pool_code=?", (now_text(), pool["pool_code"]))
        write_audit(pool["pool_code"], "提交复核", pool["pool_state"], "REVIEW",
                    "分配合计 %.2f 万元，差额 %.2f 万元，提交复核" % (
                        stat["allocated"], stat["gap"]), operator, conn)
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "奖金池 %s 已提交复核" % pool["pool_code"]}


def audit_bonus_pool(pool_code, result_code="PASS", audit_note="",
                     operator="admin"):
    """复核裁决：通过则发放归档，驳回退回核算中。

    :return: 含 success 与 message 的执行结果字典
    """
    if result_code not in ("PASS", "REJECT"):
        return {"success": False, "message": "复核结论 %s 不合法" % result_code}
    pool = fetch_pool_row(pool_code)
    if pool is None:
        return {"success": False, "message": "奖金池不存在"}
    if pool["pool_state"] != "REVIEW":
        return {"success": False, "message": "仅待复核状态的奖金池可以裁决"}

    target_state = "PAID" if result_code == "PASS" else "CALC"
    stamp = now_text()
    conn = open_conn()
    try:
        if result_code == "PASS":
            conn.execute(
                "UPDATE pe_bonus_pool SET pool_state='PAID', audit_time=?, "
                "pay_time=?, remark_text=? WHERE pool_code=?",
                (stamp, stamp,
                 (audit_note or "").strip() or pool["remark_text"],
                 pool["pool_code"]))
        else:
            conn.execute(
                "UPDATE pe_bonus_pool SET pool_state='CALC', audit_time=?, "
                "remark_text=? WHERE pool_code=?",
                (stamp, (audit_note or "").strip() or pool["remark_text"],
                 pool["pool_code"]))
        write_audit(pool["pool_code"], "复核裁决", "REVIEW", target_state,
                    "复核%s：%s" % (
                        "通过并发放" if result_code == "PASS" else "驳回退回核算",
                        (audit_note or "").strip() or "无补充意见"),
                    operator, conn)
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "奖金池 %s 复核%s" % (
                pool["pool_code"], "通过，已完成奖金发放归档" if
                result_code == "PASS" else "驳回，退回核算中")}


def cancel_bonus_pool(pool_code, reason_text="", operator="admin"):
    """作废奖金池：已发放归档的奖金池不可作废。"""
    pool = fetch_pool_row(pool_code)
    if pool is None:
        return {"success": False, "message": "奖金池不存在"}
    if pool["pool_state"] == "PAID":
        return {"success": False, "message": "已发放归档的奖金池不可作废"}
    if pool["pool_state"] == "VOID":
        return {"success": False, "message": "该奖金池已作废"}
    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_bonus_pool SET pool_state='VOID', remark_text=? "
            "WHERE pool_code=?",
            ((reason_text or "").strip(), pool["pool_code"]))
        write_audit(pool["pool_code"], "奖金池作废", pool["pool_state"], "VOID",
                    "作废奖金池：%s" % ((reason_text or "").strip() or
                                     "未填写原因"), operator, conn)
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "奖金池 %s 已作废" % pool["pool_code"]}


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_bonus_pools(keyword="", calc_mode="", pool_state="", page=1):
    """按条件分页查询奖金池，返回分页结果与记录列表。"""
    sql = "SELECT * FROM pe_bonus_pool WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (pool_code LIKE ? OR pool_name LIKE ? OR period_tag LIKE ? "
                "OR operator_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if calc_mode:
        sql += "AND calc_mode=? "
        args.append(calc_mode)
    if pool_state:
        sql += "AND pool_state=? "
        args.append(pool_state)
    conn = open_conn()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + POOL_PAGE_SIZE - 1) // POOL_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY pool_id DESC LIMIT ? OFFSET ?",
            args + [POOL_PAGE_SIZE, (page_no - 1) * POOL_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["mode_label"] = CALC_MODE_DICT.get(item["calc_mode"],
                                                  item["calc_mode"])
        record["state_label"] = POOL_STATE_DICT.get(item["pool_state"],
                                                    item["pool_state"])
        record["balance_label"] = "已平衡" if not item["balance_flag"] else "超差"
        record["alloc_rate"] = round1(
            item["allocated_amount"] * 100.0 / item["total_amount"]) \
            if item["total_amount"] else 0.0
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_bonus_details(pool_code="", detail_state="", keyword="", limit=40):
    """按条件查询分配明细，支持按奖金池、明细状态与关键字过滤。"""
    sql = "SELECT * FROM pe_bonus_detail WHERE 1=1 "
    args = []
    if pool_code:
        sql += "AND pool_code=? "
        args.append(pool_code)
    if detail_state:
        sql += "AND detail_state=? "
        args.append(detail_state)
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (detail_code LIKE ? OR dept_name LIKE ? OR "
                "grade_level LIKE ? OR adjust_note LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY detail_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["state_label"] = DETAIL_STATE_DICT.get(item["detail_state"],
                                                      item["detail_state"])
        record["grade_coef"] = GRADE_COEF_MAP.get(item["grade_level"], 1.0)
        records.append(record)
    return records


def fetch_bonus_audits(pool_code="", limit=40):
    """按条件查询复核流转记录，取最近若干条。"""
    sql = "SELECT * FROM pe_bonus_audit WHERE 1=1 "
    args = []
    if pool_code:
        sql += "AND pool_code=? "
        args.append(pool_code)
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY audit_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_bonus_summary():
    """汇总奖金核算核心指标：奖金池量、发放量、奖金总额、已发放金额、
    待复核量、超差池与参与科室数。"""
    conn = open_conn()
    try:
        pool_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_bonus_pool").fetchone()["c"]
        state_rows = conn.execute(
            "SELECT pool_state, COUNT(1) AS c FROM pe_bonus_pool "
            "GROUP BY pool_state").fetchall()
        amount_row = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) AS s FROM pe_bonus_pool "
            "WHERE pool_state<>'VOID'").fetchone()
        paid_row = conn.execute(
            "SELECT COALESCE(SUM(allocated_amount),0) AS s FROM pe_bonus_pool "
            "WHERE pool_state='PAID'").fetchone()
        balance_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_bonus_pool WHERE balance_flag=1 "
            "AND pool_state<>'VOID'").fetchone()["c"]
        detail_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_bonus_detail").fetchone()["c"]
        adjust_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_bonus_detail "
            "WHERE detail_state='ADJUST'").fetchone()["c"]
        dept_amount = conn.execute(
            "SELECT COUNT(DISTINCT dept_name) AS c FROM pe_bonus_detail"
        ).fetchone()["c"]
        avg_row = conn.execute(
            "SELECT COALESCE(AVG(avg_score),0) AS a FROM pe_bonus_detail"
        ).fetchone()
    finally:
        conn.close()
    state_counter = {item["pool_state"]: item["c"] for item in state_rows}
    total = round2(amount_row["s"])
    paid = round2(paid_row["s"])
    return {
        "pool_total": pool_total,
        "paid_pool": state_counter.get("PAID", 0),
        "review_pool": state_counter.get("REVIEW", 0),
        "calc_pool": state_counter.get("CALC", 0) + state_counter.get("DRAFT", 0),
        "void_pool": state_counter.get("VOID", 0),
        "total_amount": total,
        "paid_amount": paid,
        "pay_rate": round1(paid * 100.0 / total) if total else 0.0,
        "balance_amount": balance_amount,
        "detail_total": detail_total,
        "adjust_amount": adjust_amount,
        "dept_amount": dept_amount,
        "avg_score": round1(avg_row["a"]),
    }


def build_bonus_charts():
    """构建五类图表数据：核算方式分布、奖金池状态分布、科室奖金金额排行、
    绩效等级平均奖金对比与各考核周期奖金发放趋势。"""
    conn = open_conn()
    try:
        pool_rows = conn.execute(
            "SELECT pool_code, period_tag, calc_mode, pool_state, total_amount, "
            "allocated_amount FROM pe_bonus_pool").fetchall()
        grade_rows = conn.execute(
            "SELECT grade_level, AVG(final_amount) AS a, COUNT(1) AS c "
            "FROM pe_bonus_detail GROUP BY grade_level").fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, SUM(final_amount) AS s FROM pe_bonus_detail "
            "GROUP BY dept_name").fetchall()
    finally:
        conn.close()

    mode_counter = {}
    state_counter = {}
    for item in pool_rows:
        mode_counter[item["calc_mode"]] = mode_counter.get(item["calc_mode"], 0) + 1
        state_counter[item["pool_state"]] = state_counter.get(item["pool_state"], 0) + 1
    mode_distribution = [
        {"name": CALC_MODE_DICT.get(code, code), "value": amount}
        for code, amount in mode_counter.items()
    ]
    state_distribution = [
        {"name": POOL_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("DRAFT", "CALC", "REVIEW", "PAID", "VOID")
    ]

    dept_rank = [
        {"name": item["dept_name"], "value": round2(item["s"])}
        for item in dept_rows
    ]
    dept_rank.sort(key=lambda x: x["value"], reverse=True)

    grade_map = {item["grade_level"]: round2(item["a"]) for item in grade_rows}
    grade_compare = [
        {"name": code + " 级", "value": grade_map.get(code, 0),
         "coef": GRADE_COEF_MAP[code]}
        for code in ("A", "B", "C", "D")
    ]

    period_map = {}
    for item in pool_rows:
        key = (item["period_tag"] or "").strip() or "未指定周期"
        bucket = period_map.setdefault(key, {"total": 0.0, "allocated": 0.0})
        bucket["total"] += float(item["total_amount"] or 0)
        if item["pool_state"] == "PAID":
            bucket["allocated"] += float(item["allocated_amount"] or 0)
    period_keys = sorted(period_map.keys())
    period_trend = {
        "periods": period_keys,
        "total_values": [round2(period_map[key]["total"]) for key in period_keys],
        "paid_values": [round2(period_map[key]["allocated"]) for key in period_keys],
    }

    return {
        "mode_distribution": mode_distribution,
        "state_distribution": state_distribution,
        "dept_rank": dept_rank[:8],
        "grade_compare": grade_compare,
        "period_trend": period_trend,
    }


def fetch_bonus_options():
    """奖金页面下拉选项：各类字典项、科室候选、周期候选与奖金池候选。"""
    conn = open_conn()
    try:
        pool_rows = conn.execute(
            "SELECT pool_code, pool_name FROM pe_bonus_pool "
            "ORDER BY pool_id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    dept_list = [(row["dept_name"] if isinstance(row, dict) else str(row))
                 for row in fetch_department_names()]
    return {
        "dept_list": dept_list,
        "period_list": fetch_period_options(),
        "pool_list": [dict(row) for row in pool_rows],
        "owner_list": list(OWNER_LIST),
        "mode_options": [{"code": k, "label": v}
                         for k, v in CALC_MODE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in POOL_STATE_DICT.items()],
        "detail_state_options": [{"code": k, "label": v}
                                 for k, v in DETAIL_STATE_DICT.items()],
    }


def build_bonus_payload(keyword="", calc_mode="", pool_state="",
                        detail_pool="", detail_state="", detail_keyword="",
                        audit_pool="", page=1):
    """组装绩效奖金核算与分配管理页面所需的全部数据。"""
    pager = fetch_bonus_pools(keyword, calc_mode, pool_state, page)
    return {
        "summary": build_bonus_summary(),
        "charts": build_bonus_charts(),
        "pools": pager["records"],
        "pool_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "details": fetch_bonus_details(detail_pool, detail_state, detail_keyword),
        "audits": fetch_bonus_audits(audit_pool),
        "options": fetch_bonus_options(),
        "result": "OK",
    }


def build_bonus_brief():
    """奖金核算看板摘要：奖金池量、已发放金额、待复核量与平均绩效得分。"""
    summary = build_bonus_summary()
    return {
        "pool_total": summary["pool_total"],
        "paid_pool": summary["paid_pool"],
        "paid_amount": summary["paid_amount"],
        "review_pool": summary["review_pool"],
        "balance_amount": summary["balance_amount"],
        "avg_score": summary["avg_score"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_bonus():
    """灌入绩效奖金演示数据：奖金池建档、科室分配展开、单项调节与配平、
    提交复核、复核发放与作废，覆盖四类核算口径与全部奖金池状态。

    :return: 生成的奖金池条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_bonus_pool").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    period_options = fetch_period_options()
    amount = 0
    for index, (pool_name, period_index, total, calc_mode, target_state,
                remark) in enumerate(DEMO_POOL_LIST):
        period_tag = ""
        if period_index < len(period_options):
            first = period_options[period_index]
            period_tag = first.get("value", first) if isinstance(first, dict) \
                else str(first)
        created = create_bonus_pool(
            pool_name, period_tag, total, calc_mode,
            OWNER_LIST[index % len(OWNER_LIST)], remark)
        if not created.get("success"):
            continue
        pool_code = created["pool_code"]
        amount += 1

        details = fetch_pool_details(pool_code)
        # 第 4 个奖金池演示单项调节：前两条明细分别上调与下调系数
        if target_state == "CALC":
            if len(details) > 0:
                adjust_bonus_detail(details[0]["detail_code"], 1.15,
                                    "重点科室绩效突出，上调激励系数",
                                    OWNER_LIST[0])
            if len(details) > 1:
                adjust_bonus_detail(details[1]["detail_code"], 0.92,
                                    "设备利用率偏低，适度下调分配",
                                    OWNER_LIST[0])
            balance_bonus_pool(pool_code, OWNER_LIST[0])
            continue
        # 其余奖金池先配平，保证分配合计与奖金总额一致
        balance_bonus_pool(pool_code, OWNER_LIST[0])

        if target_state == "REVIEW":
            submit_bonus_pool(pool_code, OWNER_LIST[1])
            continue
        if target_state == "PAID":
            submit_bonus_pool(pool_code, OWNER_LIST[1])
            audit_bonus_pool(pool_code, "PASS", "复核无误，同意发放",
                             OWNER_LIST[1])

    # 追加一个作废奖金池，覆盖作废状态与复核流转留痕
    period_tag = period_options[-1] if period_options else ""
    if isinstance(period_tag, dict):
        period_tag = period_tag.get("value", "")
    voided = create_bonus_pool(
        "2025 年三季度设备绩效奖金（作废）", period_tag, 78.0, "SCORE",
        OWNER_LIST[2], "预算调整，本奖金池作废")
    if voided.get("success"):
        amount += 1
        cancel_bonus_pool(voided["pool_code"], "年度预算调整，取消本期奖金池",
                          OWNER_LIST[2])
    return amount

# --------------------------------------------------------------------------
# 第 14 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY14_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY14_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 14 天新增：绩效奖金核算与分配三表
-- ==================================================================
-- 奖金池表：按「一个考核周期一个奖金池」登记奖金总额、核算方式、分配进度
-- 与复核发放状态，支撑绩效奖金从预算建档到发放归档的全过程管理
CREATE TABLE IF NOT EXISTS pe_bonus_pool (
    pool_id          INTEGER PRIMARY KEY AUTOINCREMENT, -- 奖金池主键
    pool_code        TEXT    NOT NULL UNIQUE,           -- 奖金池编号（BP-序号）
    pool_name        TEXT    DEFAULT '',                -- 奖金池名称
    period_tag       TEXT    DEFAULT '',                -- 考核周期标签
    total_amount     REAL    DEFAULT 0,                 -- 奖金总额（万元）
    calc_mode        TEXT    DEFAULT 'SCORE',           -- 核算方式（见 CALC_MODE_DICT）
    dept_amount      INTEGER DEFAULT 0,                 -- 参与分配科室数
    allocated_amount REAL    DEFAULT 0,                 -- 已分配金额合计（万元）
    balance_gap      REAL    DEFAULT 0,                 -- 分配差额（万元）
    balance_flag     INTEGER DEFAULT 0,                 -- 是否平衡（0 平衡 / 1 超差）
    pool_state       TEXT    DEFAULT 'DRAFT',           -- 奖金池状态（见 POOL_STATE_DICT）
    operator_name    TEXT    DEFAULT '',                -- 核算负责人
    submit_time      TEXT    DEFAULT '',                -- 提交复核时间
    audit_time       TEXT    DEFAULT '',                -- 复核时间
    pay_time         TEXT    DEFAULT '',                -- 发放时间
    remark_text      TEXT    DEFAULT '',                -- 奖金池说明
    created_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 科室奖金分配明细表：一个奖金池按科室展开一条，记录绩效依据、核算权重、
-- 调节系数与实发金额，支撑单项调节与分配差异追溯
CREATE TABLE IF NOT EXISTS pe_bonus_detail (
    detail_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 分配明细主键
    detail_code     TEXT    NOT NULL UNIQUE,           -- 明细编号（BD-序号）
    pool_code       TEXT    DEFAULT '',                -- 所属奖金池编号
    dept_name       TEXT    DEFAULT '',                -- 科室名称
    dept_type       TEXT    DEFAULT '',                -- 科室类型
    device_amount   INTEGER DEFAULT 0,                 -- 在册设备数
    staff_amount    INTEGER DEFAULT 0,                 -- 在岗人数
    score_amount    INTEGER DEFAULT 0,                 -- 参与核算设备评分数
    avg_score       REAL    DEFAULT 0,                 -- 科室平均绩效得分
    grade_level     TEXT    DEFAULT '',                -- 主导绩效等级
    weight_value    REAL    DEFAULT 0,                 -- 核算权重
    base_amount     REAL    DEFAULT 0,                 -- 基准分配金额（万元）
    adjust_coef     REAL    DEFAULT 1,                 -- 调节系数
    final_amount    REAL    DEFAULT 0,                 -- 实发金额（万元）
    detail_state    TEXT    DEFAULT 'NORMAL',          -- 明细状态（见 DETAIL_STATE_DICT）
    adjust_note     TEXT    DEFAULT '',                -- 调节说明
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 奖金核算复核流转表：登记建档、核算、提交复核、复核裁决、发放与作废留痕
CREATE TABLE IF NOT EXISTS pe_bonus_audit (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 复核流转主键
    audit_code      TEXT    NOT NULL UNIQUE,           -- 流转编号（BA-序号）
    pool_code       TEXT    DEFAULT '',                -- 所属奖金池编号
    action_name     TEXT    DEFAULT '',                -- 动作名称
    from_state      TEXT    DEFAULT '',                -- 变更前状态
    to_state        TEXT    DEFAULT '',                -- 变更后状态
    action_desc     TEXT    DEFAULT '',                -- 动作说明
    operator_name   TEXT    DEFAULT '',                -- 操作人
    action_time     TEXT    DEFAULT '',                -- 动作时间
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_bpool_code       ON pe_bonus_pool (pool_code);
CREATE INDEX IF NOT EXISTS idx_bpool_period     ON pe_bonus_pool (period_tag);
CREATE INDEX IF NOT EXISTS idx_bpool_state      ON pe_bonus_pool (pool_state);
CREATE INDEX IF NOT EXISTS idx_bdetail_code     ON pe_bonus_detail (detail_code);
CREATE INDEX IF NOT EXISTS idx_bdetail_pool     ON pe_bonus_detail (pool_code);
CREATE INDEX IF NOT EXISTS idx_bdetail_dept     ON pe_bonus_detail (dept_name);
CREATE INDEX IF NOT EXISTS idx_baudit_code      ON pe_bonus_audit (audit_code);
CREATE INDEX IF NOT EXISTS idx_baudit_pool      ON pe_bonus_audit (pool_code);
"""


def apply_day14_schema(conn):
    """把第 14 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY14_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 14 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from perf_bonus import (
    build_bonus_payload,
    build_bonus_brief,
    create_bonus_pool,
    adjust_bonus_detail,
    balance_bonus_pool,
    submit_bonus_pool,
    audit_bonus_pool,
    cancel_bonus_pool,
    seed_demo_bonus,
    CALC_MODE_DICT,
    POOL_STATE_DICT,
    DETAIL_STATE_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 13 天新增：大屏叠加数据采集质控摘要，供采集概况卡渲染
#    payload["gather_brief"] = build_gather_brief()
#    # 第 14 天新增：大屏叠加绩效奖金核算摘要，供奖金概况卡渲染
#    payload["bonus_brief"] = build_bonus_brief()

@app.route("/pf/bonus")
def page_bonus():
    """绩效奖金核算与分配管理页面：奖金池建档、单项调节、复核发放（第 14 天）。"""
    return render_template(
        "page_bonus.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/bonus/panel")
def api_bonus_panel():
    """奖金面板数据接口：概览指标、五类图表、奖金池、分配明细与复核流转。"""
    payload = build_bonus_payload(
        keyword=request.args.get("keyword", ""),
        calc_mode=request.args.get("calc_mode", ""),
        pool_state=request.args.get("pool_state", ""),
        detail_pool=request.args.get("detail_pool", ""),
        detail_state=request.args.get("detail_state", ""),
        detail_keyword=request.args.get("detail_keyword", ""),
        audit_pool=request.args.get("audit_pool", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/pf/bonus/pool/create", methods=["POST"])
def api_bonus_pool_create():
    """奖金池建档接口：按考核周期与核算方式自动展开科室分配明细。"""
    body = request.get_json(silent=True) or {}
    result = create_bonus_pool(
        body.get("pool_name") or "",
        body.get("period_tag") or "",
        body.get("total_amount") or 0,
        body.get("calc_mode") or "SCORE",
        (body.get("operator_name") or "admin").strip(),
        body.get("remark_text") or "",
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/detail/adjust", methods=["POST"])
def api_bonus_detail_adjust():
    """单项调节接口：调整科室分配系数并回算奖金池统计。"""
    body = request.get_json(silent=True) or {}
    result = adjust_bonus_detail(
        body.get("detail_code") or "",
        body.get("adjust_coef") or 1,
        body.get("adjust_note") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/balance", methods=["POST"])
def api_bonus_balance():
    """平衡配平接口：按核算权重摊回差额，保证分配合计与奖金总额一致。"""
    body = request.get_json(silent=True) or {}
    result = balance_bonus_pool(
        body.get("pool_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/submit", methods=["POST"])
def api_bonus_submit():
    """提交复核接口：分配未平衡或科室不足时拒绝提交。"""
    body = request.get_json(silent=True) or {}
    result = submit_bonus_pool(
        body.get("pool_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/audit", methods=["POST"])
def api_bonus_audit():
    """复核裁决接口：通过则发放归档，驳回退回核算中。"""
    body = request.get_json(silent=True) or {}
    result = audit_bonus_pool(
        body.get("pool_code") or "",
        body.get("result_code") or "PASS",
        body.get("audit_note") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/cancel", methods=["POST"])
def api_bonus_cancel():
    """奖金池作废接口：已发放归档的奖金池不可作废。"""
    body = request.get_json(silent=True) or {}
    result = cancel_bonus_pool(
        body.get("pool_code") or "",
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/bonus/options")
def api_bonus_options():
    """奖金下拉选项接口：核算方式、奖金池状态与明细状态字典。"""
    return jsonify({
        "result": "OK",
        "mode_options": [{"code": k, "label": v}
                         for k, v in CALC_MODE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in POOL_STATE_DICT.items()],
        "detail_state_options": [{"code": k, "label": v}
                                 for k, v in DETAIL_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_bonus()

# 【函数内插入】if __name__ == "__main__":
#    print("奖金核算：   http://127.0.0.1:%d/pf/bonus" % PORT)

# --------------------------------------------------------------------------
# 第 14 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_bonus.js             新增 534 行
#   [新增] templates/page_bonus.html        新增 287 行
#   [修改] templates/screen_base.html       新增 1 行
