# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-30（第 10 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 10 天新增与修改的源代码，不含第 1~9 天已有代码
#           本次迭代主题：绩效改进建议与整改跟踪模块 + 改进建议页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 10 天新增业务模块 1：perf_improve.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-30（第 10 天 / 共 15 天）
# 模块功能：绩效改进建议与整改跟踪模块。负责按短板维度自动生成改进建议、
#           建议的分级采纳与下发、整改任务的进度跟踪与闭环关闭，以及整改
#           前后绩效得分的复核对比；支持按考核周期、科室、维度、优先级与
#           建议状态组合筛选查询，输出维度短板分布、优先级分布、任务状态
#           分布、科室整改完成率对比、整改成效均分提升五类统计图表，形成
#           「短板识别 → 生成建议 → 采纳下发 → 整改跟踪 → 复核评价」的
#           绩效改进闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime
import random

from perf_store import open_conn, dict_rows, fetch_department_names
from perf_ranking import current_period_tag, judge_grade_level
from perf_warn import latest_score_period, WARN_DIMENSION_MAP

# ------------------------------------------------------------------
# 改进维度字典：键为维度编码，值为（维度名称，评分字段名，达标参考值）
# 达标参考值取自院级绩效目标线，用于计算建议中的差距值
# ------------------------------------------------------------------
IMPROVE_DIM_DICT = {
    "total": ("综合得分", "total_score", 80.0),
    "usage": ("使用效率", "usage_rate", 75.0),
    "income": ("经济效益", "income_rate", 75.0),
    "maintain": ("维保水平", "maintain_rate", 80.0),
    "quality": ("质量安全", "quality_rate", 85.0),
    "service": ("服务效能", "service_rate", 75.0),
}

# 建议优先级字典：由差距值大小判定
ADVICE_PRIORITY_DICT = {
    "HIGH": "高优先级",
    "MID": "中优先级",
    "LOW": "低优先级",
}

# 建议来源字典：区分自动生成与人工登记，便于追溯建议依据
ADVICE_SOURCE_DICT = {
    "WARN": "低效预警",
    "FORECAST": "趋势预测",
    "SCORE": "评分短板",
    "MANUAL": "人工登记",
}

# 建议状态字典：待采纳 → 已采纳 → 已关闭；已忽略用于不需要整改的情形
ADVICE_STATE_DICT = {
    "PENDING": "待采纳",
    "ADOPTED": "已采纳",
    "CLOSED": "已关闭",
    "IGNORED": "已忽略",
}

# 整改任务状态字典：待整改 → 整改中 → 已完成 → 已复核关闭
IMPROVE_TASK_DICT = {
    "TODO": "待整改",
    "DOING": "整改中",
    "DONE": "已完成",
    "CLOSED": "已复核关闭",
}

# 复核结论字典：由整改前后得分变化判定
REVIEW_RESULT_DICT = {
    "PASS": "整改达标",
    "IMPROVE": "有提升未达标",
    "FAIL": "未见改善",
}

# 优先级判定阈值：差距值大于等于该值记为高优先级
HIGH_GAP_THRESHOLD = 12.0

# 中优先级判定阈值：差距值大于等于该值记为中优先级
MID_GAP_THRESHOLD = 5.0

# 复核达标判定阈值：整改后得分提升达到该分值视为整改达标
REVIEW_PASS_CHANGE = 3.0

# 复核有提升判定阈值：提升幅度达到该分值记为有提升未达标
REVIEW_IMPROVE_CHANGE = 0.5

# 单页条数
IMPROVE_PAGE_SIZE = 10

# 建议编号前缀，编号形如 IA-2026Q4-0001
ADVICE_CODE_PREFIX = "IA"

# 任务编号前缀，编号形如 IT-2026Q4-0001
TASK_CODE_PREFIX = "IT"

# 复核编号前缀，编号形如 IR-2026Q4-0001
REVIEW_CODE_PREFIX = "IR"

# 单台设备单次生成的建议条数上限，避免短板过多时刷屏
MAX_ADVICE_PER_DEVICE = 2

# 演示数据随机种子，保证每次启动生成的整改数据一致
DEMO_RANDOM_SEED = 20261030


# ------------------------------------------------------------------
# 通用工具
# ------------------------------------------------------------------
def round2(value):
    """数值保留两位小数，空值与非数值按 0 处理。"""
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def period_code(period_tag):
    """把周期标签转为编号片段，如 2026-Q4 → 2026Q4。"""
    return (period_tag or "").replace("-", "")


def now_text():
    """返回当前时间文本 YYYY-MM-DD HH:MM:SS。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def next_serial(table_name, code_column, prefix):
    """查询某编号前缀下的下一个流水序号，无历史记录时从 1 开始。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT %s FROM %s WHERE %s LIKE ? ORDER BY %s DESC LIMIT 1"
            % (code_column, table_name, code_column, code_column),
            (prefix + "%",)).fetchone()
    finally:
        conn.close()
    if not row:
        return 1
    try:
        return int(row[code_column].rsplit("-", 1)[-1]) + 1
    except (ValueError, IndexError, AttributeError, TypeError):
        return 1


def build_advice_code(period_tag, offset=0):
    """按考核周期生成建议编号，形如 IA-2026Q4-0007。

    offset 用于同一事务内连续插入时的本地序号递增，避免未提交数据不可见
    导致的编号重复。
    """
    prefix = "%s-%s-" % (ADVICE_CODE_PREFIX, period_code(period_tag))
    return "%s%04d" % (prefix, next_serial(
        "pe_improve_advice", "advice_code", prefix) + offset)


def build_task_code(period_tag, offset=0):
    """按考核周期生成整改任务编号，形如 IT-2026Q4-0003。"""
    prefix = "%s-%s-" % (TASK_CODE_PREFIX, period_code(period_tag))
    return "%s%04d" % (prefix, next_serial(
        "pe_improve_task", "task_code", prefix) + offset)


def build_review_code(period_tag, offset=0):
    """按考核周期生成复核编号，形如 IR-2026Q4-0002。"""
    prefix = "%s-%s-" % (REVIEW_CODE_PREFIX, period_code(period_tag))
    return "%s%04d" % (prefix, next_serial(
        "pe_improve_review", "review_code", prefix) + offset)


def judge_priority(gap_value):
    """按差距值判定建议优先级：差距越大优先级越高。"""
    gap = abs(round2(gap_value))
    if gap >= HIGH_GAP_THRESHOLD:
        return "HIGH"
    if gap >= MID_GAP_THRESHOLD:
        return "MID"
    return "LOW"


def judge_review_result(before_score, after_score):
    """按整改前后得分变化判定复核结论。"""
    change = round2(after_score) - round2(before_score)
    if change >= REVIEW_PASS_CHANGE:
        return "PASS"
    if change >= REVIEW_IMPROVE_CHANGE:
        return "IMPROVE"
    return "FAIL"


# ==================================================================
# 建议生成
# ==================================================================
def fetch_period_score_rows(period_tag):
    """读取指定周期的绩效评分明细，供短板扫描使用。"""
    if not period_tag:
        return []
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT device_code, device_name, dept_name, usage_rate, income_rate, "
            "maintain_rate, quality_rate, service_rate, total_score "
            "FROM pe_score_record WHERE period_tag=? ORDER BY total_score ASC",
            (period_tag,)).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def build_advice_text(dim_key, metric_value, target_value, gap_value):
    """按短板维度生成差异化的改进建议文本。"""
    base_map = {
        "total": "综合得分未达院级目标线，建议列为重点帮扶设备并组织专项评估",
        "usage": "使用效率偏低，建议核查开机时段排班、推进院内共享共用机制",
        "income": "经济效益偏低，建议复核收费项目与检查量，评估调剂或外送方案",
        "maintain": "维保水平偏低，建议缩短保养周期、核查故障停机的根本原因",
        "quality": "质量安全偏低，建议立即安排质控校准并复核操作规程执行情况",
        "service": "服务效能偏低，建议拓展适用检查项目并加强临床科室宣传推介",
    }
    text = base_map.get(dim_key, "建议组织专项绩效评估")
    text += "；当前 %.2f 分，目标 %.2f 分，尚差 %.2f 分" % (
        round2(metric_value), round2(target_value), abs(round2(gap_value)))
    return text


def scan_weak_dimensions(period_tag=""):
    """扫描指定周期的评分明细，按短板维度生成改进建议。

    每台设备取差距最大的若干维度生成建议，已存在的建议会被跳过，
    返回本次新增的建议条数。
    """
    period_tag = period_tag or latest_score_period()
    score_rows = fetch_period_score_rows(period_tag)
    if not score_rows:
        return 0

    conn = open_conn()
    try:
        exist_map = {}
        for row in conn.execute(
                "SELECT device_code, dim_key FROM pe_improve_advice "
                "WHERE period_tag=?", (period_tag,)).fetchall():
            exist_map[(row["device_code"], row["dim_key"])] = True

        amount = 0
        for score_row in score_rows:
            gaps = []
            for dim_key, (dim_label, field_name, target_value) \
                    in IMPROVE_DIM_DICT.items():
                metric_value = round2(score_row.get(field_name))
                gap_value = round2(metric_value - target_value)
                if gap_value >= 0:
                    continue
                gaps.append((dim_key, dim_label, metric_value,
                             target_value, gap_value))
            if not gaps:
                continue
            # 差距越大越优先，只取差距最明显的若干维度
            gaps.sort(key=lambda item: item[4])
            for dim_key, dim_label, metric_value, target_value, gap_value \
                    in gaps[:MAX_ADVICE_PER_DEVICE]:
                if (score_row["device_code"], dim_key) in exist_map:
                    continue
                advice_code = build_advice_code(period_tag, amount)
                conn.execute(
                    "INSERT INTO pe_improve_advice "
                    "(advice_code, period_tag, device_code, device_name, dept_name, "
                    " dim_key, dim_label, metric_value, target_value, gap_value, "
                    " priority_code, source_code, advice_text, state_code, "
                    " operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (advice_code, period_tag, score_row["device_code"],
                     score_row["device_name"], score_row["dept_name"], dim_key,
                     dim_label, metric_value, target_value, gap_value,
                     judge_priority(gap_value), "SCORE",
                     build_advice_text(dim_key, metric_value, target_value,
                                       gap_value),
                     "PENDING", "绩效改进助手"))
                exist_map[(score_row["device_code"], dim_key)] = True
                amount += 1
        conn.commit()
    finally:
        conn.close()
    return amount


def save_advice(payload):
    """人工登记一条改进建议，返回写入结果。"""
    period_tag = (payload.get("period_tag") or "").strip() or latest_score_period()
    device_code = (payload.get("device_code") or "").strip()
    if not device_code:
        return {"success": False, "message": "设备编号不能为空"}
    dim_key = (payload.get("dim_key") or "total").strip()
    if dim_key not in IMPROVE_DIM_DICT:
        return {"success": False, "message": "改进维度编码不合法"}
    dim_label, _field, default_target = IMPROVE_DIM_DICT[dim_key]
    metric_value = round2(payload.get("metric_value"))
    target_value = round2(payload.get("target_value") or default_target)
    gap_value = round2(metric_value - target_value)

    conn = open_conn()
    try:
        device_row = conn.execute(
            "SELECT device_name, dept_name FROM pe_score_record "
            "WHERE device_code=? ORDER BY score_id DESC LIMIT 1",
            (device_code,)).fetchone()
        advice_code = build_advice_code(period_tag)
        conn.execute(
            "INSERT INTO pe_improve_advice "
            "(advice_code, period_tag, device_code, device_name, dept_name, "
            " dim_key, dim_label, metric_value, target_value, gap_value, "
            " priority_code, source_code, advice_text, state_code, "
            " operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (advice_code, period_tag, device_code,
             (payload.get("device_name") or "").strip()
             or (device_row["device_name"] if device_row else ""),
             (payload.get("dept_name") or "").strip()
             or (device_row["dept_name"] if device_row else ""),
             dim_key, dim_label, metric_value, target_value, gap_value,
             judge_priority(gap_value),
             (payload.get("source_code") or "MANUAL").strip() or "MANUAL",
             (payload.get("advice_text") or "").strip()
             or build_advice_text(dim_key, metric_value, target_value, gap_value),
             "PENDING",
             (payload.get("operator_name") or "").strip() or "admin"))
        conn.commit()
    except Exception as exc:                      # 唯一约束冲突时给出提示
        return {"success": False, "message": "建议登记失败：%s" % exc}
    finally:
        conn.close()
    return {"success": True, "advice_code": advice_code,
            "message": "改进建议「%s」已登记" % advice_code}


def change_advice_state(advice_code, state_code, operator_name=""):
    """切换建议状态：采纳、忽略或关闭。"""
    if state_code not in ADVICE_STATE_DICT:
        return {"success": False, "message": "建议状态编码不合法"}
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_improve_advice WHERE advice_code=?",
            (advice_code,)).fetchone()
        if row is None:
            return {"success": False, "message": "改进建议不存在"}
        conn.execute(
            "UPDATE pe_improve_advice SET state_code=?, operator_name=? "
            "WHERE advice_code=?",
            (state_code, (operator_name or "").strip() or row["operator_name"],
             advice_code))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "建议状态更新为「%s」" % ADVICE_STATE_DICT[state_code]}


# ==================================================================
# 整改任务
# ==================================================================
def dispatch_improve_task(payload):
    """把一条改进建议下发为整改任务，返回任务编号。"""
    advice_code = (payload.get("advice_code") or "").strip()
    owner_name = (payload.get("owner_name") or "").strip()
    plan_date = (payload.get("plan_date") or "").strip()
    if not owner_name:
        return {"success": False, "message": "整改责任人不能为空"}
    if not plan_date:
        return {"success": False, "message": "计划完成日期不能为空"}
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_improve_advice WHERE advice_code=?",
            (advice_code,)).fetchone()
        if row is None:
            return {"success": False, "message": "改进建议不存在"}
        task_code = build_task_code(row["period_tag"])
        title = (payload.get("task_title") or "").strip() or (
            "%s短板整改：%s" % (row["dim_label"] or "绩效", row["device_name"]))
        progress = round2(payload.get("progress_rate"))
        task_state = (payload.get("task_state") or "TODO").strip()
        if task_state not in IMPROVE_TASK_DICT:
            task_state = "TODO"
        if task_state == "DOING" and progress <= 0:
            progress = 20.0
        conn.execute(
            "INSERT INTO pe_improve_task "
            "(task_code, advice_code, device_code, device_name, dept_name, dim_key, "
            " task_title, owner_name, plan_date, finish_date, progress_rate, "
            " task_state, effect_note, operator_name, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_code, advice_code, row["device_code"], row["device_name"],
             row["dept_name"], row["dim_key"], title, owner_name, plan_date,
             (payload.get("finish_date") or "").strip(), progress, task_state,
             (payload.get("effect_note") or "").strip(),
             (payload.get("operator_name") or "").strip() or "admin", now_text()))
        conn.execute(
            "UPDATE pe_improve_advice SET state_code='ADOPTED' WHERE advice_code=?",
            (advice_code,))
        conn.commit()
    except Exception as exc:
        return {"success": False, "message": "任务下发失败：%s" % exc}
    finally:
        conn.close()
    return {"success": True, "task_code": task_code,
            "message": "整改任务「%s」已下发至 %s" % (task_code, owner_name)}


def fetch_task_row(task_code):
    """读取单条整改任务明细。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_improve_task WHERE task_code=?", (task_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def update_task_progress(payload):
    """更新整改进度：进度到达 100% 时自动置为已完成并回填完成日期。"""
    task_code = (payload.get("task_code") or "").strip()
    row = fetch_task_row(task_code)
    if not row:
        return {"success": False, "message": "整改任务不存在"}
    progress = round2(payload.get("progress_rate"))
    if progress < 0 or progress > 100:
        return {"success": False, "message": "整改进度需在 0 至 100 之间"}
    task_state = (payload.get("task_state") or "").strip() or row["task_state"]
    if task_state not in IMPROVE_TASK_DICT:
        return {"success": False, "message": "任务状态编码不合法"}
    if progress >= 100 and task_state == "DOING":
        task_state = "DONE"
    if progress < 100 and task_state == "DONE":
        task_state = "DOING"
    finish_date = (payload.get("finish_date") or "").strip()
    if task_state in ("DONE", "CLOSED") and not finish_date:
        finish_date = row.get("finish_date") or today_text()

    conn = open_conn()
    try:
        conn.execute(
            "UPDATE pe_improve_task SET progress_rate=?, task_state=?, "
            "finish_date=?, effect_note=?, updated_at=? WHERE task_code=?",
            (progress, task_state, finish_date,
             (payload.get("effect_note") or "").strip() or row.get("effect_note", ""),
             now_text(), task_code))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "任务「%s」进度更新为 %.0f%%（%s）"
                       % (task_code, progress, IMPROVE_TASK_DICT[task_state])}


def review_improve_task(payload):
    """整改复核：取整改前后两期得分对比，判定复核结论并关闭任务。"""
    task_code = (payload.get("task_code") or "").strip()
    reviewer_name = (payload.get("reviewer_name") or "").strip()
    if not reviewer_name:
        return {"success": False, "message": "复核人不能为空"}
    row = fetch_task_row(task_code)
    if not row:
        return {"success": False, "message": "整改任务不存在"}

    conn = open_conn()
    try:
        period_rows = conn.execute(
            "SELECT period_tag, total_score FROM pe_score_record "
            "WHERE device_code=? ORDER BY substr(period_tag,1,4) DESC, "
            "substr(period_tag,-1) DESC LIMIT 2",
            (row["device_code"],)).fetchall()
        advice_row = conn.execute(
            "SELECT period_tag FROM pe_improve_advice WHERE advice_code=?",
            (row["advice_code"],)).fetchone()
    finally:
        conn.close()
    # 最新一期为整改后得分，上一期为整改前得分
    after_score = round2(period_rows[0]["total_score"]) if period_rows else 0.0
    before_score = round2(period_rows[1]["total_score"]) if len(period_rows) > 1 \
        else after_score
    result_code = judge_review_result(before_score, after_score)
    advice_period = advice_row["period_tag"] if advice_row else ""
    review_code = build_review_code(advice_period)

    conn = open_conn()
    try:
        conn.execute(
            "INSERT INTO pe_improve_review "
            "(review_code, task_code, device_code, device_name, dept_name, "
            " period_tag, before_score, after_score, score_change, review_result, "
            " reviewer_name, review_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (review_code, task_code, row["device_code"], row["device_name"],
             row["dept_name"],
             period_rows[0]["period_tag"] if period_rows else "",
             before_score, after_score, round2(after_score - before_score),
             result_code, reviewer_name, (payload.get("review_note") or "").strip()))
        conn.execute(
            "UPDATE pe_improve_task SET task_state='CLOSED', progress_rate=100, "
            "finish_date=?, updated_at=? WHERE task_code=?",
            (row.get("finish_date") or today_text(), now_text(), task_code))
        conn.execute(
            "UPDATE pe_improve_advice SET state_code='CLOSED' WHERE advice_code=?",
            (row["advice_code"],))
        conn.commit()
    except Exception as exc:
        return {"success": False, "message": "复核登记失败：%s" % exc}
    finally:
        conn.close()
    return {"success": True, "review_code": review_code,
            "result_code": result_code,
            "message": "复核完成：%s（得分 %.2f → %.2f）"
                       % (REVIEW_RESULT_DICT[result_code], before_score, after_score)}


# ==================================================================
# 查询与统计
# ==================================================================
def build_improve_condition(period_tag="", dept_name="", dim_key="",
                            priority_code="", state_code="", keyword=""):
    """按筛选条件拼装 SQL 片段，返回 WHERE 子句与参数列表。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag=?")
        params.append(period_tag)
    if dept_name:
        clauses.append("dept_name=?")
        params.append(dept_name)
    if dim_key:
        clauses.append("dim_key=?")
        params.append(dim_key)
    if priority_code:
        clauses.append("priority_code=?")
        params.append(priority_code)
    if state_code:
        clauses.append("state_code=?")
        params.append(state_code)
    keyword = (keyword or "").strip()
    if keyword:
        clauses.append("(device_code LIKE ? OR device_name LIKE ? "
                       "OR advice_text LIKE ?)")
        params.extend(["%%%s%%" % keyword] * 3)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def fetch_advice_rows(period_tag="", dept_name="", dim_key="", priority_code="",
                      state_code="", keyword="", page=1):
    """分页查询改进建议明细，高优先级与差距大的排在前面。"""
    where_sql, params = build_improve_condition(
        period_tag, dept_name, dim_key, priority_code, state_code, keyword)
    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql, params).fetchone()[0]
        offset_sql = " LIMIT ? OFFSET ?"
        size = IMPROVE_PAGE_SIZE
        try:
            page_no = max(1, int(page))
        except (TypeError, ValueError):
            page_no = 1
        total_page = (total + size - 1) // size if total else 1
        if page_no > total_page:
            page_no = total_page
        rows = conn.execute(
            "SELECT * FROM pe_improve_advice" + where_sql +
            " ORDER BY CASE priority_code WHEN 'HIGH' THEN 0 WHEN 'MID' THEN 1 "
            "ELSE 2 END, gap_value ASC, advice_id ASC" + offset_sql,
            params + [size, (page_no - 1) * size]).fetchall()
        advice_codes = [row["advice_code"] for row in rows]
        task_map = {}
        if advice_codes:
            holders = ",".join(["?"] * len(advice_codes))
            for item in conn.execute(
                    "SELECT advice_code, task_code, owner_name, progress_rate, "
                    "task_state FROM pe_improve_task WHERE advice_code IN (%s)"
                    % holders, advice_codes).fetchall():
                task_map[item["advice_code"]] = dict(item)
    finally:
        conn.close()

    result = []
    for row in rows:
        item = dict(row)
        item["priority_label"] = ADVICE_PRIORITY_DICT.get(
            row["priority_code"], row["priority_code"])
        item["state_label"] = ADVICE_STATE_DICT.get(
            row["state_code"], row["state_code"])
        item["source_label"] = ADVICE_SOURCE_DICT.get(
            row["source_code"], row["source_code"])
        task = task_map.get(row["advice_code"], {})
        item["task_code"] = task.get("task_code", "")
        item["owner_name"] = task.get("owner_name", "")
        item["progress_rate"] = round2(task.get("progress_rate"))
        item["task_state"] = task.get("task_state", "")
        item["task_state_label"] = IMPROVE_TASK_DICT.get(
            task.get("task_state", ""), "未下发")
        result.append(item)
    return result, total, total_page, page_no


def fetch_task_rows(period_tag=""):
    """读取整改任务清单，供页面任务面板渲染。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_improve_task ORDER BY "
            "CASE task_state WHEN 'TODO' THEN 0 WHEN 'DOING' THEN 1 "
            "WHEN 'DONE' THEN 2 ELSE 3 END, plan_date ASC, task_id DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["task_state_label"] = IMPROVE_TASK_DICT.get(
            row["task_state"], row["task_state"])
        item["dim_label"] = IMPROVE_DIM_DICT.get(
            row["dim_key"], (row["dim_key"], "", 0))[0]
        result.append(item)
    return result


def fetch_review_rows():
    """读取整改复核记录，按复核时间倒序。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_improve_review ORDER BY review_id DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["result_label"] = REVIEW_RESULT_DICT.get(
            row["review_result"], row["review_result"])
        result.append(item)
    return result


def build_improve_summary(period_tag=""):
    """统计改进建议总量、高优先级量、已采纳量、已下发任务量与复核达标量。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag=?")
        params.append(period_tag)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql, params
        ).fetchone()[0]
        high_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql +
            ((" AND" if where_sql else " WHERE") + " priority_code='HIGH'"),
            params).fetchone()[0]
        pending_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql +
            ((" AND" if where_sql else " WHERE") + " state_code='PENDING'"),
            params).fetchone()[0]
        adopted_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql +
            ((" AND" if where_sql else " WHERE") + " state_code='ADOPTED'"),
            params).fetchone()[0]
        closed_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_advice" + where_sql +
            ((" AND" if where_sql else " WHERE") + " state_code='CLOSED'"),
            params).fetchone()[0]
        device_amount = conn.execute(
            "SELECT COUNT(DISTINCT device_code) FROM pe_improve_advice" + where_sql,
            params).fetchone()[0]
        dept_amount = conn.execute(
            "SELECT COUNT(DISTINCT dept_name) FROM pe_improve_advice" + where_sql,
            params).fetchone()[0]
        task_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_task").fetchone()[0]
        task_done = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_task WHERE task_state IN "
            "('DONE','CLOSED')").fetchone()[0]
        review_pass = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_review WHERE review_result='PASS'"
        ).fetchone()[0]
        review_amount = conn.execute(
            "SELECT COUNT(1) FROM pe_improve_review").fetchone()[0]
        avg_gap = conn.execute(
            "SELECT AVG(ABS(gap_value)) AS gap FROM pe_improve_advice" + where_sql,
            params).fetchone()["gap"]
    finally:
        conn.close()

    return {
        "total": total,
        "high_amount": high_amount,
        "pending_amount": pending_amount,
        "adopted_amount": adopted_amount,
        "closed_amount": closed_amount,
        "device_amount": device_amount,
        "dept_amount": dept_amount,
        "task_amount": task_amount,
        "task_done": task_done,
        "review_amount": review_amount,
        "review_pass": review_pass,
        "adopt_rate": round2(adopted_amount * 100.0 / total) if total else 0.0,
        "close_rate": round2(closed_amount * 100.0 / total) if total else 0.0,
        "task_done_rate": round2(task_done * 100.0 / task_amount) if task_amount else 0.0,
        "avg_gap": round2(avg_gap),
    }


def build_improve_charts(period_tag=""):
    """构建维度短板、优先级、建议状态、科室整改与复核成效五类图表数据。"""
    clauses = []
    params = []
    if period_tag:
        clauses.append("period_tag=?")
        params.append(period_tag)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = open_conn()
    try:
        dim_rows = conn.execute(
            "SELECT dim_key, COUNT(1) AS amount, AVG(ABS(gap_value)) AS avg_gap "
            "FROM pe_improve_advice" + where_sql +
            " GROUP BY dim_key ORDER BY amount DESC", params).fetchall()
        priority_rows = conn.execute(
            "SELECT priority_code, COUNT(1) AS amount FROM pe_improve_advice" +
            where_sql + " GROUP BY priority_code", params).fetchall()
        state_rows = conn.execute(
            "SELECT state_code, COUNT(1) AS amount FROM pe_improve_advice" +
            where_sql + " GROUP BY state_code", params).fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS total_amount, "
            "SUM(CASE WHEN state_code='CLOSED' THEN 1 ELSE 0 END) AS closed_amount "
            "FROM pe_improve_advice" + where_sql +
            " GROUP BY dept_name ORDER BY total_amount DESC LIMIT 8",
            params).fetchall()
        device_rows = conn.execute(
            "SELECT device_code, device_name, dept_name, COUNT(1) AS advice_amount, "
            "AVG(ABS(gap_value)) AS avg_gap FROM pe_improve_advice" + where_sql +
            " GROUP BY device_code, device_name ORDER BY avg_gap DESC LIMIT 10",
            params).fetchall()
        review_rows = conn.execute(
            "SELECT review_result, COUNT(1) AS amount, AVG(score_change) AS avg_change "
            "FROM pe_improve_review GROUP BY review_result").fetchall()
    finally:
        conn.close()

    dim_distribution = [
        {"name": IMPROVE_DIM_DICT.get(row["dim_key"], (row["dim_key"], "", 0))[0],
         "value": row["amount"], "avg_gap": round2(row["avg_gap"])}
        for row in dim_rows
    ]
    priority_distribution = [
        {"name": ADVICE_PRIORITY_DICT.get(row["priority_code"], row["priority_code"]),
         "value": row["amount"]}
        for row in priority_rows
    ]
    state_distribution = [
        {"name": ADVICE_STATE_DICT.get(row["state_code"], row["state_code"]),
         "value": row["amount"]}
        for row in state_rows
    ]
    dept_compare = [
        {"name": row["dept_name"], "total_amount": row["total_amount"],
         "closed_amount": row["closed_amount"],
         "close_rate": round2(row["closed_amount"] * 100.0 / row["total_amount"])
         if row["total_amount"] else 0.0}
        for row in dept_rows
    ]
    device_ranking = [
        {"device_code": row["device_code"], "device_name": row["device_name"],
         "dept_name": row["dept_name"], "advice_amount": row["advice_amount"],
         "avg_gap": round2(row["avg_gap"])}
        for row in device_rows
    ]
    review_distribution = [
        {"name": REVIEW_RESULT_DICT.get(row["review_result"], row["review_result"]),
         "value": row["amount"], "avg_change": round2(row["avg_change"])}
        for row in review_rows
    ]
    return {
        "dim_distribution": dim_distribution,
        "priority_distribution": priority_distribution,
        "state_distribution": state_distribution,
        "dept_compare": dept_compare,
        "device_ranking": device_ranking,
        "review_distribution": review_distribution,
    }


def fetch_improve_periods():
    """读取已有改进建议的考核周期清单，供页面周期下拉框使用。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_improve_advice "
            "ORDER BY period_tag DESC").fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT DISTINCT period_tag FROM pe_score_record "
                "ORDER BY period_tag DESC").fetchall()
    finally:
        conn.close()
    return [row["period_tag"] for row in rows]


def build_improve_payload(period_tag="", dept_name="", dim_key="",
                          priority_code="", state_code="", keyword="", page=1):
    """组装改进建议页面所需的全部数据：汇总、图表、建议明细与任务清单。"""
    period_list = fetch_improve_periods()
    if not period_tag and period_list:
        period_tag = period_list[0]
    advices, total, total_page, page_no = fetch_advice_rows(
        period_tag, dept_name, dim_key, priority_code, state_code, keyword, page)
    return {
        "result": "OK",
        "period_tag": period_tag,
        "current_period": current_period_tag(),
        "summary": build_improve_summary(period_tag),
        "charts": build_improve_charts(period_tag),
        "advices": advices,
        "tasks": fetch_task_rows(period_tag),
        "reviews": fetch_review_rows(),
        "total": total,
        "total_page": total_page,
        "page": page_no,
        "options": {
            "dim_dict": {key: value[0] for key, value in IMPROVE_DIM_DICT.items()},
            "dim_target": {key: value[2] for key, value in IMPROVE_DIM_DICT.items()},
            "priority_dict": ADVICE_PRIORITY_DICT,
            "source_dict": ADVICE_SOURCE_DICT,
            "state_dict": ADVICE_STATE_DICT,
            "task_dict": IMPROVE_TASK_DICT,
            "review_dict": REVIEW_RESULT_DICT,
            "dept_list": fetch_department_names(),
            "period_list": fetch_improve_periods(),
        },
    }


def build_improve_brief():
    """绩效改进大屏摘要，供绩效分析大屏嵌入。"""
    period_tag = fetch_improve_periods()[0] if fetch_improve_periods() else ""
    summary = build_improve_summary(period_tag)
    return {
        "total": summary["total"],
        "high_amount": summary["high_amount"],
        "pending_amount": summary["pending_amount"],
        "task_done": summary["task_done"],
        "text": "高优先级 %d 条 / 待采纳 %d 条" % (
            summary["high_amount"], summary["pending_amount"]),
    }


# ==================================================================
# 演示数据
# ==================================================================
def seed_demo_improve():
    """灌入第 10 天演示数据：扫描生成建议、下发任务、回填进度与复核。"""
    randomizer = random.Random(DEMO_RANDOM_SEED)
    period_tag = latest_score_period()
    if not period_tag:
        return 0
    scan_weak_dimensions(period_tag)

    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT advice_code, device_code, dim_key, dim_label, device_name "
            "FROM pe_improve_advice WHERE period_tag=? "
            "ORDER BY CASE priority_code WHEN 'HIGH' THEN 0 WHEN 'MID' THEN 1 "
            "ELSE 2 END, gap_value ASC", (period_tag,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return 0

    owner_pool = ["设备科-刘工", "放射科-陈主任", "检验科-杨技师",
                  "超声科-赵医生", "手术室-孙护士长", "医工科-周工"]
    today = datetime.date.today()
    amount = 0
    for index, row in enumerate(rows):
        # 演示分布：前 6 条下发任务，其余保留待采纳或已忽略
        if index >= 6:
            break
        plan_day = today + datetime.timedelta(days=7 + (index % 4) * 5)
        outcome = dispatch_improve_task({
            "advice_code": row["advice_code"],
            "owner_name": owner_pool[index % len(owner_pool)],
            "plan_date": plan_day.strftime("%Y-%m-%d"),
            "task_title": "%s专项整改（%s）" % (
                row["dim_label"] or "绩效", row["device_code"]),
            "operator_name": "绩效改进助手",
        })
        if not outcome.get("success"):
            continue
        amount += 1
        task_code = outcome["task_code"]
        # 演示分布：前 4 条已整改完成并复核，第 5 条整改中，第 6 条待整改
        if index < 4:
            update_task_progress({
                "task_code": task_code,
                "progress_rate": 100,
                "task_state": "DONE",
                "effect_note": "已完成整改并复测绩效指标",
            })
            review_improve_task({
                "task_code": task_code,
                "reviewer_name": "绩效办-吴主任",
                "review_note": "整改后复测得分已归档，纳入下一周期跟踪",
            })
        elif index == 4:
            update_task_progress({
                "task_code": task_code,
                "progress_rate": randomizer.choice([45, 60, 70]),
                "task_state": "DOING",
                "effect_note": "整改进行中，按计划推进",
            })
    # 保留 1 条为已忽略、其余保留待采纳，展示建议状态的完整取值
    for row in rows[6:7]:
        change_advice_state(row["advice_code"], "IGNORED", "绩效办-吴主任")
    return amount

# --------------------------------------------------------------------------
# 第 10 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY10_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY10_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 10 天新增：绩效改进建议表
-- ==================================================================
-- 改进建议表：按「一台设备一个短板维度一条」汇总低效预警与趋势预测两条线索
-- 生成的绩效改进建议，记录短板取值、与达标线的差距、优先级、来源与建议文本，
-- 支持人工补充登记与采纳忽略决策，形成绩效改进的立项依据
CREATE TABLE IF NOT EXISTS pe_improve_advice (
    advice_id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- 建议主键
    advice_code   TEXT    NOT NULL UNIQUE,             -- 建议编号（IA-年月日-序号）
    period_tag    TEXT    NOT NULL,                    -- 考核周期
    device_code   TEXT    NOT NULL,                    -- 设备编号
    device_name   TEXT    DEFAULT '',                  -- 设备名称
    dept_name     TEXT    DEFAULT '',                  -- 所属科室
    dim_key       TEXT    DEFAULT 'total',             -- 短板维度（见 IMPROVE_DIM_DICT）
    dim_label     TEXT    DEFAULT '',                  -- 维度名称快照
    metric_value  REAL    DEFAULT 0,                   -- 短板指标当前取值
    target_value  REAL    DEFAULT 0,                   -- 达标参考值
    gap_value     REAL    DEFAULT 0,                   -- 与达标线的差距
    priority_code TEXT    DEFAULT 'MID',               -- 优先级（见 ADVICE_PRIORITY_DICT）
    source_code   TEXT    DEFAULT 'WARN',              -- 建议来源（见 ADVICE_SOURCE_DICT）
    advice_text   TEXT    DEFAULT '',                  -- 改进建议内容
    state_code    TEXT    DEFAULT 'PENDING',           -- 建议状态（见 ADVICE_STATE_DICT）
    operator_name TEXT    DEFAULT 'admin',             -- 建议登记人
    created_at    TEXT    DEFAULT (datetime('now', 'localtime')),
    UNIQUE (period_tag, device_code, dim_key)
);

-- ==================================================================
-- 第 10 天新增：绩效整改任务表
-- ==================================================================
-- 整改任务表：按「一条建议一个整改任务」下发整改工作要求，记录责任人与科室、
-- 计划完成日期、整改进度与整改成效说明，进度登记与状态流转同步更新，支撑
-- 改进建议落地过程的全过程跟踪
CREATE TABLE IF NOT EXISTS pe_improve_task (
    task_id       INTEGER PRIMARY KEY AUTOINCREMENT,   -- 任务主键
    task_code     TEXT    NOT NULL UNIQUE,             -- 任务编号（IT-年月日-序号）
    advice_code   TEXT    DEFAULT '',                  -- 关联建议编号
    device_code   TEXT    NOT NULL,                    -- 设备编号
    device_name   TEXT    DEFAULT '',                  -- 设备名称
    dept_name     TEXT    DEFAULT '',                  -- 责任科室
    dim_key       TEXT    DEFAULT 'total',             -- 整改维度快照
    task_title    TEXT    DEFAULT '',                  -- 整改任务标题
    owner_name    TEXT    DEFAULT '',                  -- 整改责任人
    plan_date     TEXT    DEFAULT '',                  -- 计划完成日期
    finish_date   TEXT    DEFAULT '',                  -- 实际完成日期
    progress_rate REAL    DEFAULT 0,                   -- 整改进度（%）
    task_state    TEXT    DEFAULT 'TODO',              -- 任务状态（见 IMPROVE_TASK_DICT）
    effect_note   TEXT    DEFAULT '',                  -- 整改成效说明
    operator_name TEXT    DEFAULT 'admin',             -- 任务下发人
    created_at    TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    DEFAULT ''
);

-- ==================================================================
-- 第 10 天新增：绩效整改复核表
-- ==================================================================
-- 整改复核表：按「一个整改任务一次复核」记录整改前后绩效得分对比，复核结论
-- 由整改后与整改前的综合得分差值判定，支撑改进成效的量化评价与闭环归档
CREATE TABLE IF NOT EXISTS pe_improve_review (
    review_id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- 复核主键
    review_code   TEXT    NOT NULL UNIQUE,             -- 复核编号（IR-年月日-序号）
    task_code     TEXT    NOT NULL,                    -- 关联整改任务编号
    device_code   TEXT    NOT NULL,                    -- 设备编号
    device_name   TEXT    DEFAULT '',                  -- 设备名称
    dept_name     TEXT    DEFAULT '',                  -- 所属科室
    period_tag    TEXT    DEFAULT '',                  -- 复核考核周期
    before_score  REAL    DEFAULT 0,                   -- 整改前综合得分
    after_score   REAL    DEFAULT 0,                   -- 整改后综合得分
    score_change  REAL    DEFAULT 0,                   -- 得分变化值
    review_result TEXT    DEFAULT 'PASS',              -- 复核结论（见 REVIEW_RESULT_DICT）
    reviewer_name TEXT    DEFAULT '',                  -- 复核人
    review_note   TEXT    DEFAULT '',                  -- 复核意见
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_advice_code    ON pe_improve_advice (advice_code);
CREATE INDEX IF NOT EXISTS idx_advice_period  ON pe_improve_advice (period_tag);
CREATE INDEX IF NOT EXISTS idx_advice_device  ON pe_improve_advice (device_code);
CREATE INDEX IF NOT EXISTS idx_advice_dept    ON pe_improve_advice (dept_name);
CREATE INDEX IF NOT EXISTS idx_advice_dim     ON pe_improve_advice (dim_key);
CREATE INDEX IF NOT EXISTS idx_advice_state   ON pe_improve_advice (state_code);
CREATE INDEX IF NOT EXISTS idx_advice_prior   ON pe_improve_advice (priority_code);
CREATE INDEX IF NOT EXISTS idx_task_code      ON pe_improve_task (task_code);
CREATE INDEX IF NOT EXISTS idx_task_advice    ON pe_improve_task (advice_code);
CREATE INDEX IF NOT EXISTS idx_task_device    ON pe_improve_task (device_code);
CREATE INDEX IF NOT EXISTS idx_task_state     ON pe_improve_task (task_state);
CREATE INDEX IF NOT EXISTS idx_review_code    ON pe_improve_review (review_code);
CREATE INDEX IF NOT EXISTS idx_review_task    ON pe_improve_review (task_code);
CREATE INDEX IF NOT EXISTS idx_review_device  ON pe_improve_review (device_code);
"""


def apply_day10_schema(conn):
    """把第 10 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY10_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 10 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-30（第 10 天 / 共 15 天）
#           绩效对比、可视化图表、效益报表、低效设备预警、绩效报告导出、
#           绩效趋势预测与绩效改进建议十类页面路由，启动时完成建表、等级
#           规则初始化、科室初始化与演示评分、上期对照、效益明细、低效预警、
#           导出模板、导出留痕、历史评分样本、趋势预测与改进建议整改跟踪
#           数据灌入，并提供评分试算、评分入库、评分明细、权重元信息、榜单、
#           科室横向对比、等级批量重判、ECharts 图表 option、多维度排行筛选
#           查询、效益报表核算归档、低效预警规则维护扫描整改、导出模板维护、
#           报告生成落盘、文件预览下载与留痕删除、预测模型维护、批量趋势预测、
#           预测结果筛选查询与历史序列回溯，以及改进建议扫描生成与登记、建议
#           状态切换、整改任务下发、整改进度回填与整改复核评分对比接口

from perf_improve import (
    build_improve_payload,
    build_improve_brief,
    scan_weak_dimensions,
    save_advice,
    change_advice_state,
    dispatch_improve_task,
    update_task_progress,
    review_improve_task,
    seed_demo_improve,
    IMPROVE_DIM_DICT,
    ADVICE_PRIORITY_DICT,
    ADVICE_SOURCE_DICT,
    ADVICE_STATE_DICT,
    IMPROVE_TASK_DICT,
    REVIEW_RESULT_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 10 天新增：大屏叠加改进建议摘要，供改进概况卡渲染
#    payload["improve_brief"] = build_improve_brief()

# ==================================================================
# 以下为第 10 天新增：绩效改进建议与整改跟踪页面与接口
# ==================================================================


@app.route("/pf/improve")
def page_improve():
    """绩效改进建议页面：改进指标、五类图表、建议明细、整改任务与复核记录。"""
    return render_template(
        "page_improve.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/improve/panel")
def api_improve_panel():
    """改进建议数据接口：汇总指标、五类图表、分页建议、整改任务与复核记录。"""
    return jsonify(build_improve_payload(
        period_tag=(request.args.get("period_tag") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        dim_key=(request.args.get("dim_key") or "").strip(),
        priority_code=(request.args.get("priority_code") or "").strip(),
        state_code=(request.args.get("state_code") or "").strip(),
        keyword=(request.args.get("keyword") or "").strip(),
        page=int(request.args.get("page", 1)),
    ))


@app.route("/api/pf/improve/scan", methods=["POST"])
def api_improve_scan():
    """按绩效短板扫描并生成改进建议，返回本次新增条数。"""
    body = request.get_json(silent=True) or {}
    amount = scan_weak_dimensions((body.get("period_tag") or "").strip())
    return jsonify({
        "result": "OK",
        "amount": amount,
        "message": "本次新增改进建议 %d 条" % amount,
    })


@app.route("/api/pf/improve/advice", methods=["POST"])
def api_improve_advice():
    """人工登记一条绩效改进建议。"""
    body = request.get_json(silent=True) or {}
    result = save_advice(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/improve/state", methods=["POST"])
def api_improve_state():
    """切换建议状态：采纳、忽略或关闭。"""
    body = request.get_json(silent=True) or {}
    result = change_advice_state(
        (body.get("advice_code") or "").strip(),
        (body.get("state_code") or "").strip(),
        (body.get("operator_name") or "").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/improve/task", methods=["POST"])
def api_improve_task():
    """把改进建议下发为整改任务，并把建议置为已采纳。"""
    body = request.get_json(silent=True) or {}
    result = dispatch_improve_task(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/improve/task/progress", methods=["POST"])
def api_improve_task_progress():
    """回填整改进度与整改成效说明。"""
    body = request.get_json(silent=True) or {}
    result = update_task_progress(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/improve/task/review", methods=["POST"])
def api_improve_task_review():
    """整改复核：对比整改前后综合得分，判定复核结论并关闭任务。"""
    body = request.get_json(silent=True) or {}
    result = review_improve_task(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/improve/options")
def api_improve_options():
    """改进建议下拉选项接口：整改维度、优先级、来源、建议状态与任务状态。"""
    return jsonify({
        "result": "OK",
        "dim_options": [{"code": k, "label": v[0], "target": v[2]}
                        for k, v in IMPROVE_DIM_DICT.items()],
        "priority_options": [{"code": k, "label": v}
                             for k, v in ADVICE_PRIORITY_DICT.items()],
        "source_options": [{"code": k, "label": v}
                           for k, v in ADVICE_SOURCE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in ADVICE_STATE_DICT.items()],
        "task_options": [{"code": k, "label": v}
                         for k, v in IMPROVE_TASK_DICT.items()],
        "review_options": [{"code": k, "label": v}
                           for k, v in REVIEW_RESULT_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_improve()

# 【函数内插入】if __name__ == "__main__":
#    print("改进建议：   http://127.0.0.1:%d/pf/improve" % PORT)

# --------------------------------------------------------------------------
# 第 10 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_improve.js           新增 658 行
#   [新增] templates/page_improve.html      新增 336 行
#   [修改] templates/screen_base.html       新增 1 行
