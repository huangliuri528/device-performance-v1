# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-01（第 12 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 12 天新增与修改的源代码，不含第 1~11 天已有代码
#           本次迭代主题：绩效指标库与权重方案管理模块 + 指标权重页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 12 天新增业务模块 1：perf_metric.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-11-01（第 12 天 / 共 15 天）
# 模块功能：绩效指标库与权重方案管理模块。实现绩效指标的统一建档（指标类型、
#           计量单位、计算口径、数据来源、权重上限与参考标准值）、多套权重
#           方案的编制与维护、单项权重的调整与一键归一化、方案的平衡校验与
#           发布启用、以及按不同权重方案对当期评分进行试算对比；试算按各方案
#           权重重算全院平均综合得分，量化不同权重取向对评价结果的影响；输出
#           指标类型分布、方案状态分布、各方案权重对比、指标平均权重排行与
#           方案试算得分对比五类统计图表，形成「建指标 → 配权重 → 校验平衡 →
#           发布启用 → 试算对比」的权重治理闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from perf_store import open_conn, dict_rows
from perf_config import SCORE_WEIGHT_MAP
from perf_ranking import fetch_period_options

# ------------------------------------------------------------------
# 指标类型字典：效益型越高越好，成本型越低越好
# ------------------------------------------------------------------
METRIC_TYPE_DICT = {
    "BENEFIT": "效益型",
    "COST": "成本型",
}

# 指标字段键与中文名的映射，与评分表五项一级指标一一对应
METRIC_KEY_LABEL = {
    "usage_rate": "使用效率",
    "income_rate": "经济效益",
    "maintain_rate": "维保水平",
    "quality_rate": "质量安全",
    "service_rate": "服务效能",
}

# 指标默认口径：指标键 -> （指标名称，指标类型，计量单位，计算口径，数据来源）
DEFAULT_METRIC_LIST = (
    ("usage_rate", "使用效率", "BENEFIT", "%",
     "开机时长 ÷ 应开机时长 × 100%", "设备物联网实时管控平台"),
    ("income_rate", "经济效益", "BENEFIT", "万元",
     "单机收入 ÷ 收入基准值 × 100 分", "医院 HRP 收入核算系统"),
    ("maintain_rate", "维保水平", "COST", "小时",
     "（1 − 故障停机时长 ÷ 停机基准）× 100 分", "医疗器械全生命周期运维管理平台"),
    ("quality_rate", "质量安全", "BENEFIT", "%",
     "质控合格项 ÷ 应检项 × 100%", "医学装备质控管理系统"),
    ("service_rate", "服务效能", "BENEFIT", "人次",
     "年检查人次 ÷ 人次基准值 × 100 分", "HIS 检查登记系统"),
)

# 权重方案状态字典：随校验、发布、停用逐级流转
SCHEME_STATE_DICT = {
    "DRAFT": "草稿",
    "CHECK": "待校验",
    "PUBLISH": "已发布",
    "STOP": "已停用",
}

# 权重平衡判定容差：合计与 100 的偏差在该范围内视为平衡
BALANCE_TOLERANCE = 0.01

# 权重取值区间（百分比）
WEIGHT_MIN_VALUE = 0.0
WEIGHT_MAX_VALUE = 100.0

# 分页尺寸：方案与指标各自独立分页
SCHEME_PAGE_SIZE = 8
METRIC_PAGE_SIZE = 12

# 演示权重方案清单，用于首日灌入演示数据
# （方案名称，指标键 -> 权重百分比）
DEMO_SCHEME_LIST = (
    ("2026 年度均衡导向权重方案", "PUBLISH", {
        "usage_rate": 30.0, "income_rate": 28.0, "maintain_rate": 16.0,
        "quality_rate": 14.0, "service_rate": 12.0}),
    ("2026 年度效益优先权重方案", "PUBLISH", {
        "usage_rate": 24.0, "income_rate": 40.0, "maintain_rate": 12.0,
        "quality_rate": 12.0, "service_rate": 12.0}),
    ("2026 年度质量优先权重方案", "CHECK", {
        "usage_rate": 24.0, "income_rate": 18.0, "maintain_rate": 16.0,
        "quality_rate": 30.0, "service_rate": 12.0}),
    ("2026 年度安全运维导向方案", "DRAFT", {
        "usage_rate": 22.0, "income_rate": 18.0, "maintain_rate": 32.0,
        "quality_rate": 18.0, "service_rate": 10.0}),
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


def next_seq_no(table_name):
    """读取指定业务表记录数并加一，用于拼装业务编码。"""
    conn = open_conn()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def build_metric_code(seq_no):
    """生成指标编号：MC-0001。"""
    return "MC-%04d" % seq_no


def build_scheme_code(seq_no):
    """生成权重方案编号：WS-年月-序号。"""
    return "WS-%s-%03d" % (datetime.date.today().strftime("%Y%m"), seq_no)


def build_item_code(seq_no):
    """生成权重明细编号：WI-年月日-序号。"""
    return "WI-%s-%04d" % (datetime.date.today().strftime("%Y%m%d"), seq_no)


def clamp_weight(value):
    """把权重裁剪到 0-100 区间并保留两位小数。"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number < WEIGHT_MIN_VALUE:
        number = WEIGHT_MIN_VALUE
    if number > WEIGHT_MAX_VALUE:
        number = WEIGHT_MAX_VALUE
    return round(number, 2)


def default_weight_of(metric_key):
    """读取指标在系统默认权重体系中的权重百分比。"""
    return round2(float(SCORE_WEIGHT_MAP.get(metric_key, 0)) * 100)


def current_period_tag():
    """返回评分表中最新一期考核周期，无数据时回退到当前季度。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT period_tag FROM pe_score_record ORDER BY period_tag DESC "
            "LIMIT 1").fetchone()
    finally:
        conn.close()
    if row and row["period_tag"]:
        return row["period_tag"]
    now = datetime.date.today()
    return "%d-Q%d" % (now.year, (now.month - 1) // 3 + 1)


# ------------------------------------------------------------------
# 指标库维护
# ------------------------------------------------------------------
def ensure_metric_lib():
    """初始化绩效指标库：按默认指标清单补齐缺失指标，返回新增条数。"""
    conn = open_conn()
    try:
        amount = 0
        for index, (metric_key, name, metric_type, unit, formula,
                    source) in enumerate(DEFAULT_METRIC_LIST):
            exist = conn.execute(
                "SELECT metric_id FROM pe_metric_lib WHERE metric_key=?",
                (metric_key,)).fetchone()
            if exist:
                continue
            conn.execute(
                "INSERT INTO pe_metric_lib (metric_code, metric_key, "
                "metric_name, metric_type, metric_unit, calc_formula, "
                "data_source, weight_limit, std_value, sort_no, use_flag, "
                "remark_text, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (build_metric_code(next_seq_no("pe_metric_lib") + amount),
                 metric_key, name, metric_type, unit, formula, source,
                 40.0, 85.0, index + 1,
                 "系统预置绩效指标", "绩效办-管理员"))
            amount += 1
        conn.commit()
    finally:
        conn.close()
    return amount


def save_metric(metric_code, metric_key, metric_name, metric_type="BENEFIT",
                metric_unit="", calc_formula="", data_source="",
                weight_limit=40.0, std_value=85.0, sort_no=0,
                remark_text="", operator="admin"):
    """新建或编辑一条绩效指标，指标键必须在五项一级指标范围内。"""
    metric_name = (metric_name or "").strip()
    if not metric_name:
        return {"success": False, "message": "请填写指标名称"}
    metric_key = (metric_key or "").strip()
    if metric_key not in METRIC_KEY_LABEL:
        return {"success": False, "message": "指标字段键 %s 不在支持范围内" % metric_key}
    if metric_type not in METRIC_TYPE_DICT:
        return {"success": False, "message": "指标类型 %s 不合法" % metric_type}
    try:
        limit_value = clamp_weight(weight_limit)
    except (TypeError, ValueError):
        limit_value = 40.0
    conn = open_conn()
    try:
        exist = None
        if (metric_code or "").strip():
            exist = conn.execute(
                "SELECT metric_code FROM pe_metric_lib WHERE metric_code=?",
                (metric_code.strip(),)).fetchone()
        if exist is None:
            code = build_metric_code(next_seq_no("pe_metric_lib"))
            conn.execute(
                "INSERT INTO pe_metric_lib (metric_code, metric_key, "
                "metric_name, metric_type, metric_unit, calc_formula, "
                "data_source, weight_limit, std_value, sort_no, use_flag, "
                "remark_text, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (code, metric_key, metric_name, metric_type,
                 (metric_unit or "").strip(), (calc_formula or "").strip(),
                 (data_source or "").strip(), limit_value,
                 round2(std_value), int(sort_no or 0),
                 (remark_text or "").strip(), (operator or "admin").strip()))
        else:
            code = metric_code.strip()
            conn.execute(
                "UPDATE pe_metric_lib SET metric_key=?, metric_name=?, "
                "metric_type=?, metric_unit=?, calc_formula=?, data_source=?, "
                "weight_limit=?, std_value=?, sort_no=?, remark_text=?, "
                "operator_name=? WHERE metric_code=?",
                (metric_key, metric_name, metric_type,
                 (metric_unit or "").strip(), (calc_formula or "").strip(),
                 (data_source or "").strip(), limit_value, round2(std_value),
                 int(sort_no or 0), (remark_text or "").strip(),
                 (operator or "admin").strip(), code))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "绩效指标 %s 已保存" % code,
            "metric_code": code}


def toggle_metric(metric_code, use_flag=1, operator="admin"):
    """启用或停用一条绩效指标，停用后的指标不再进入新建权重方案。"""
    metric_code = (metric_code or "").strip()
    if not metric_code:
        return {"success": False, "message": "请先选择绩效指标"}
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT metric_id FROM pe_metric_lib WHERE metric_code=?",
            (metric_code,)).fetchone()
        if row is None:
            return {"success": False, "message": "绩效指标不存在"}
        conn.execute(
            "UPDATE pe_metric_lib SET use_flag=? WHERE metric_code=?",
            (1 if int(use_flag or 0) else 0, metric_code))
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "绩效指标 %s 已%s" % (metric_code, "启用" if use_flag else "停用")}


def fetch_metric_rows(keyword="", metric_type="", use_flag="", page=1):
    """按条件分页查询绩效指标库。"""
    sql = "SELECT * FROM pe_metric_lib WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (metric_code LIKE ? OR metric_name LIKE ? OR metric_key "
                "LIKE ? OR data_source LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if metric_type:
        sql += "AND metric_type=? "
        args.append(metric_type)
    if use_flag not in ("", None):
        sql += "AND use_flag=? "
        args.append(1 if str(use_flag) in ("1", "true", "True") else 0)
    conn = open_conn()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + METRIC_PAGE_SIZE - 1) // METRIC_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY sort_no ASC, metric_id ASC LIMIT ? OFFSET ?",
            args + [METRIC_PAGE_SIZE, (page_no - 1) * METRIC_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in dict_rows(rows):
        item["type_label"] = METRIC_TYPE_DICT.get(item["metric_type"], item["metric_type"])
        item["use_label"] = "启用" if item["use_flag"] else "停用"
        records.append(item)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_metric_options(only_used=True):
    """读取指标候选，供权重明细编辑与方案生成使用。"""
    sql = "SELECT * FROM pe_metric_lib "
    args = []
    if only_used:
        sql += "WHERE use_flag=1 "
    sql += "ORDER BY sort_no ASC, metric_id ASC"
    conn = open_conn()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


# ------------------------------------------------------------------
# 权重方案编制
# ------------------------------------------------------------------
def create_weight_scheme(scheme_name, period_tag="", scheme_state="DRAFT",
                         remark_text="", operator="admin"):
    """新建一套权重方案，并按启用指标自动生成默认权重明细。

    权重明细的初始值取系统默认权重体系，生成后自动回算权重合计与平衡
    标记，并对当期评分做一次试算，便于新建后立刻看到方案效果。

    :return: 含 success、message 与 scheme_code 的执行结果字典
    """
    scheme_name = (scheme_name or "").strip()
    if not scheme_name:
        return {"success": False, "message": "请填写权重方案名称"}
    if scheme_state not in SCHEME_STATE_DICT:
        return {"success": False, "message": "方案状态 %s 不合法" % scheme_state}
    ensure_metric_lib()
    metrics = fetch_metric_options()
    if not metrics:
        return {"success": False, "message": "绩效指标库为空，请先维护指标"}
    period = (period_tag or "").strip() or current_period_tag()
    conn = open_conn()
    try:
        scheme_code = build_scheme_code(next_seq_no("pe_weight_scheme"))
        weight_sum = 0.0
        item_seq = next_seq_no("pe_weight_item")
        conn.execute(
            "INSERT INTO pe_weight_scheme (scheme_code, scheme_name, "
            "period_tag, metric_amount, weight_sum, balance_flag, "
            "trial_score, scheme_state, use_flag, remark_text, "
            "operator_name) VALUES (?,?,?,?,?,?,0,?,0,?,?)",
            (scheme_code, scheme_name, period, len(metrics), 0.0, 0,
             scheme_state, (remark_text or "").strip(),
             (operator or "admin").strip()))
        for index, metric in enumerate(metrics):
            weight = default_weight_of(metric["metric_key"])
            weight_sum += weight
            conn.execute(
                "INSERT INTO pe_weight_item (item_code, scheme_code, "
                "metric_code, metric_key, metric_name, weight_value, "
                "std_value, base_weight, remark_text) VALUES (?,?,?,?,?,?,?,?,?)",
                (build_item_code(item_seq + index), scheme_code,
                 metric["metric_code"], metric["metric_key"],
                 metric["metric_name"], weight, metric["std_value"], weight,
                 "沿用系统默认权重"))
        conn.execute(
            "UPDATE pe_weight_scheme SET weight_sum=? WHERE scheme_code=?",
            (round2(weight_sum), scheme_code))
        conn.commit()
    finally:
        conn.close()
    refresh_scheme_state(scheme_code)
    trial_scheme(scheme_code, operator)
    return {"success": True, "message": "权重方案 %s 已生成" % scheme_code,
            "scheme_code": scheme_code}


def save_weight_item(item_code, weight_value, std_value=None,
                     remark_text="", operator="admin"):
    """调整单条权重明细的权重值，并回算所属方案的权重合计与平衡标记。

    权重超过所属指标的权重上限时给出提示但仍允许保存，便于演示权重治理
    中的越限发现与归一化处理。
    """
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_weight_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "权重明细不存在"}
        weight = clamp_weight(weight_value)
        std = row["std_value"] if std_value in (None, "") else round2(std_value)
        conn.execute(
            "UPDATE pe_weight_item SET weight_value=?, std_value=?, "
            "remark_text=? WHERE item_code=?",
            (weight, std, (remark_text or "").strip(), row["item_code"]))
        total = conn.execute(
            "SELECT COALESCE(SUM(weight_value),0) AS s FROM pe_weight_item "
            "WHERE scheme_code=?", (row["scheme_code"],)).fetchone()["s"]
        conn.execute(
            "UPDATE pe_weight_scheme SET weight_sum=? WHERE scheme_code=?",
            (round2(total), row["scheme_code"]))
        conn.commit()
        limit_row = conn.execute(
            "SELECT weight_limit FROM pe_metric_lib WHERE metric_code=?",
            (row["metric_code"],)).fetchone()
    finally:
        conn.close()
    refresh_scheme_state(row["scheme_code"])
    message = "权重明细 %s 已调整为 %.2f%%" % (row["item_code"], weight)
    if limit_row and weight > float(limit_row["weight_limit"] or 0):
        message += "（已超过指标权重上限 %.2f%%）" % float(limit_row["weight_limit"] or 0)
    return {"success": True, "message": message}


def balance_scheme_weight(scheme_code, operator="admin"):
    """把方案内各指标权重按当前比例归一化为合计 100%。

    :return: 含 success、message 与 weight_sum 的执行结果字典
    """
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_weight_item WHERE scheme_code=? "
            "ORDER BY item_id ASC", ((scheme_code or "").strip(),)).fetchall()
        if not rows:
            return {"success": False, "message": "权重方案不存在或没有明细"}
        total = sum(float(row["weight_value"] or 0) for row in rows)
        if total <= 0:
            return {"success": False, "message": "权重合计为 0，无法归一化"}
        left = 100.0
        for index, row in enumerate(rows):
            weight = float(row["weight_value"] or 0)
            if index == len(rows) - 1:
                value = round(left, 2)
            else:
                value = round(weight * 100.0 / total, 2)
                left = round(left - value, 2)
            conn.execute(
                "UPDATE pe_weight_item SET weight_value=?, remark_text=? "
                "WHERE item_code=?",
                (value, "已按 %s%% 比例归一化" % round2(weight * 100.0 / total),
                 row["item_code"]))
        conn.execute(
            "UPDATE pe_weight_scheme SET weight_sum=100.0 WHERE scheme_code=?",
            ((scheme_code or "").strip(),))
        conn.commit()
    finally:
        conn.close()
    refresh_scheme_state(scheme_code)
    trial_scheme(scheme_code, operator)
    return {"success": True, "message": "方案 %s 权重已归一化为 100%%" % scheme_code,
            "weight_sum": 100.0}


def refresh_scheme_state(scheme_code):
    """按权重合计刷新方案的平衡标记，返回是否平衡。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(weight_value),0) AS s, COUNT(1) AS c "
            "FROM pe_weight_item WHERE scheme_code=?",
            ((scheme_code or "").strip(),)).fetchone()
        if row is None or not row["c"]:
            return False
        balanced = 1 if abs(float(row["s"]) - 100.0) <= BALANCE_TOLERANCE else 0
        conn.execute(
            "UPDATE pe_weight_scheme SET balance_flag=?, weight_sum=?, "
            "metric_amount=? WHERE scheme_code=?",
            (balanced, round2(row["s"]), int(row["c"]),
             (scheme_code or "").strip()))
        conn.commit()
    finally:
        conn.close()
    return bool(balanced)


def publish_weight_scheme(scheme_code, operator="admin"):
    """发布权重方案：权重必须平衡，发布后自动停用其他启用中的方案。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_weight_scheme WHERE scheme_code=?",
            ((scheme_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "权重方案不存在"}
        if not row["balance_flag"]:
            return {"success": False,
                    "message": "权重合计为 %.2f%%，请归一化后再发布" % row["weight_sum"]}
        conn.execute(
            "UPDATE pe_weight_scheme SET use_flag=0 WHERE scheme_code<>?",
            ((scheme_code or "").strip(),))
        conn.execute(
            "UPDATE pe_weight_scheme SET scheme_state='PUBLISH', use_flag=1 "
            "WHERE scheme_code=?", ((scheme_code or "").strip(),))
        conn.commit()
    finally:
        conn.close()
    trial_scheme(scheme_code, operator)
    return {"success": True, "message": "权重方案 %s 已发布并启用" % scheme_code}


def stop_weight_scheme(scheme_code, operator="admin"):
    """停用权重方案：取消启用标记并把状态置为已停用。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_weight_scheme WHERE scheme_code=?",
            ((scheme_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "权重方案不存在"}
        conn.execute(
            "UPDATE pe_weight_scheme SET scheme_state='STOP', use_flag=0 "
            "WHERE scheme_code=?", ((scheme_code or "").strip(),))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "权重方案 %s 已停用" % scheme_code}


# ------------------------------------------------------------------
# 权重试算对比
# ------------------------------------------------------------------
def trial_scheme(scheme_code, operator="admin"):
    """按指定方案的权重对当期评分重算全院平均综合得分并回写方案。

    试算不改变正式评分结果，仅用于量化不同权重取向对评价结果的影响。

    :return: 含 success、score 与 amount 的执行结果字典
    """
    conn = open_conn()
    try:
        scheme = conn.execute(
            "SELECT * FROM pe_weight_scheme WHERE scheme_code=?",
            ((scheme_code or "").strip(),)).fetchone()
        if scheme is None:
            return {"success": False, "message": "权重方案不存在"}
        items = conn.execute(
            "SELECT metric_key, weight_value FROM pe_weight_item "
            "WHERE scheme_code=?", ((scheme_code or "").strip(),)).fetchall()
        rows = conn.execute(
            "SELECT usage_rate, income_rate, maintain_rate, quality_rate, "
            "service_rate FROM pe_score_record WHERE period_tag=?",
            (scheme["period_tag"],)).fetchall()
        total = sum(float(row["weight_value"] or 0) for row in items)
        scale = total if total else 1.0
        score_sum = 0.0
        for row in rows:
            score = 0.0
            for item in items:
                key = item["metric_key"]
                field = key if key in (
                    "usage_rate", "income_rate", "maintain_rate",
                    "quality_rate", "service_rate") else ""
                if not field:
                    continue
                score += float(row[field] or 0) * float(item["weight_value"] or 0)
            score_sum += score / scale if scale else 0.0
        average = round2(score_sum / len(rows)) if rows else 0.0
        conn.execute(
            "UPDATE pe_weight_scheme SET trial_score=? WHERE scheme_code=?",
            (average, (scheme_code or "").strip()))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "score": average, "amount": len(rows)}


def build_scheme_trial(scheme_code):
    """按方案权重重算各科室平均综合得分，返回科室排行数据。"""
    conn = open_conn()
    try:
        scheme = conn.execute(
            "SELECT * FROM pe_weight_scheme WHERE scheme_code=?",
            ((scheme_code or "").strip(),)).fetchone()
        if scheme is None:
            return []
        items = conn.execute(
            "SELECT metric_key, weight_value FROM pe_weight_item "
            "WHERE scheme_code=?", ((scheme_code or "").strip(),)).fetchall()
        rows = conn.execute(
            "SELECT dept_name, usage_rate, income_rate, maintain_rate, "
            "quality_rate, service_rate FROM pe_score_record "
            "WHERE period_tag=?", (scheme["period_tag"],)).fetchall()
        # 权重按百分比存储，重算时统一除以权重合计换算回百分制得分
        total = sum(float(row["weight_value"] or 0) for row in items)
        score_map = {}
        count_map = {}
        for row in rows:
            score = 0.0
            for item in items:
                key = item["metric_key"]
                if key not in ("usage_rate", "income_rate", "maintain_rate",
                               "quality_rate", "service_rate"):
                    continue
                score += float(row[key] or 0) * float(item["weight_value"] or 0)
            score = score / total if total else 0.0
            dept = row["dept_name"] or "未分科"
            score_map[dept] = score_map.get(dept, 0.0) + score
            count_map[dept] = count_map.get(dept, 0) + 1
    finally:
        conn.close()
    result = [{"name": dept, "value": round2(score_map[dept] / count_map[dept])}
              for dept in score_map if count_map[dept]]
    result.sort(key=lambda x: x["value"], reverse=True)
    return result


# ------------------------------------------------------------------
# 查询、统计与图表
# ------------------------------------------------------------------
def fetch_scheme_rows(keyword="", scheme_state="", page=1):
    """按条件分页查询权重方案。"""
    sql = "SELECT * FROM pe_weight_scheme WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += "AND (scheme_code LIKE ? OR scheme_name LIKE ?) "
        like = "%%%s%%" % keyword
        args.extend([like, like])
    if scheme_state:
        sql += "AND scheme_state=? "
        args.append(scheme_state)
    conn = open_conn()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + SCHEME_PAGE_SIZE - 1) // SCHEME_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY use_flag DESC, scheme_id DESC LIMIT ? OFFSET ?",
            args + [SCHEME_PAGE_SIZE, (page_no - 1) * SCHEME_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in dict_rows(rows):
        item["state_label"] = SCHEME_STATE_DICT.get(item["scheme_state"],
                                                    item["scheme_state"])
        item["balance_label"] = "平衡" if item["balance_flag"] else "不平衡"
        item["use_label"] = "当前启用" if item["use_flag"] else "未启用"
        records.append(item)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_scheme_items(scheme_code="", limit=30):
    """读取方案的权重明细，按权重降序排列。"""
    sql = "SELECT * FROM pe_weight_item WHERE 1=1 "
    args = []
    if scheme_code:
        sql += "AND scheme_code=? "
        args.append(scheme_code)
    conn = open_conn()
    try:
        rows = conn.execute(
            sql + "ORDER BY weight_value DESC, item_id ASC LIMIT ?",
            args + [limit]).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def build_metric_summary():
    """汇总指标库与权重方案核心指标：指标量、启用量、方案量、启用方案与试算分。"""
    ensure_metric_lib()
    conn = open_conn()
    try:
        metric_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_metric_lib").fetchone()["c"]
        metric_used = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_metric_lib WHERE use_flag=1").fetchone()["c"]
        scheme_total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_weight_scheme").fetchone()["c"]
        publish_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_weight_scheme "
            "WHERE scheme_state='PUBLISH'").fetchone()["c"]
        balance_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_weight_scheme WHERE balance_flag=1"
        ).fetchone()["c"]
        current = conn.execute(
            "SELECT scheme_code, scheme_name, weight_sum, trial_score FROM "
            "pe_weight_scheme WHERE use_flag=1 ORDER BY scheme_id DESC "
            "LIMIT 1").fetchone()
        trial_rows = conn.execute(
            "SELECT scheme_name, trial_score FROM pe_weight_scheme "
            "WHERE trial_score>0 ORDER BY trial_score DESC").fetchall()
    finally:
        conn.close()
    trial_values = [row["trial_score"] for row in trial_rows]
    spread = round2(max(trial_values) - min(trial_values)) if len(trial_values) > 1 else 0.0
    return {
        "metric_total": metric_total,
        "metric_used": metric_used,
        "scheme_total": scheme_total,
        "publish_amount": publish_amount,
        "balance_amount": balance_amount,
        "current_scheme": current["scheme_name"] if current else "未启用",
        "current_weight": round2(current["weight_sum"]) if current else 0.0,
        "current_score": round2(current["trial_score"]) if current else 0.0,
        "trial_spread": spread,
        "period_tag": current_period_tag(),
    }


def build_metric_charts():
    """构建五类图表数据：指标类型分布、方案状态分布、各方案权重对比、
    指标平均权重排行与方案试算得分对比。"""
    conn = open_conn()
    try:
        metric_rows = conn.execute(
            "SELECT metric_type, COUNT(1) AS c FROM pe_metric_lib "
            "GROUP BY metric_type").fetchall()
        scheme_rows = conn.execute(
            "SELECT scheme_code, scheme_name, scheme_state, balance_flag, "
            "weight_sum, trial_score, use_flag FROM pe_weight_scheme "
            "ORDER BY scheme_id ASC").fetchall()
        item_rows = conn.execute(
            "SELECT scheme_code, metric_key, metric_name, weight_value "
            "FROM pe_weight_item").fetchall()
    finally:
        conn.close()

    type_counter = {row["metric_type"]: row["c"] for row in metric_rows}
    type_distribution = [
        {"name": METRIC_TYPE_DICT.get(code, code), "value": type_counter.get(code, 0)}
        for code in ("BENEFIT", "COST")
    ]

    state_counter = {}
    for row in scheme_rows:
        state_counter[row["scheme_state"]] = state_counter.get(row["scheme_state"], 0) + 1
    state_distribution = [
        {"name": SCHEME_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("DRAFT", "CHECK", "PUBLISH", "STOP")
    ]

    metric_keys = [row[0] for row in DEFAULT_METRIC_LIST]
    scheme_names = [row["scheme_name"] for row in scheme_rows]
    weight_matrix = {}
    for row in item_rows:
        bucket = weight_matrix.setdefault(row["scheme_code"], {})
        bucket[row["metric_key"]] = round2(row["weight_value"])
    scheme_compare = {
        "names": scheme_names,
        "metrics": [METRIC_KEY_LABEL.get(key, key) for key in metric_keys],
        "series": [
            {"name": METRIC_KEY_LABEL.get(key, key),
             "data": [weight_matrix.get(row["scheme_code"], {}).get(key, 0)
                      for row in scheme_rows]}
            for key in metric_keys
        ],
    }

    weight_rank = []
    for key in metric_keys:
        values = [weight_matrix.get(row["scheme_code"], {}).get(key, 0)
                  for row in scheme_rows]
        values = [value for value in values if value]
        weight_rank.append({
            "name": METRIC_KEY_LABEL.get(key, key),
            "value": round2(sum(values) / len(values)) if values else 0.0,
            "default": default_weight_of(key),
        })
    weight_rank.sort(key=lambda x: x["value"], reverse=True)

    trial_compare = [
        {"name": row["scheme_name"], "value": round2(row["trial_score"]),
         "balance": "平衡" if row["balance_flag"] else "不平衡"}
        for row in scheme_rows
    ]
    trial_compare.sort(key=lambda x: x["value"], reverse=True)

    return {
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "scheme_compare": scheme_compare,
        "weight_rank": weight_rank,
        "trial_compare": trial_compare,
    }


def fetch_metric_page_options():
    """指标与权重方案页面下拉选项：各类字典项、周期候选与指标候选。"""
    return {
        "metric_list": [{"code": row["metric_code"],
                         "label": row["metric_name"] + "（%s）" % row["metric_unit"]}
                        for row in fetch_metric_options(only_used=False)],
        "period_list": fetch_period_options(),
        "type_options": [{"code": k, "label": v} for k, v in METRIC_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in SCHEME_STATE_DICT.items()],
        "key_options": [{"code": row[0], "label": METRIC_KEY_LABEL.get(row[0], row[0])}
                        for row in DEFAULT_METRIC_LIST],
        "use_options": [{"code": "1", "label": "启用"}, {"code": "0", "label": "停用"}],
    }


def build_metric_payload(keyword="", metric_type="", metric_use="",
                         scheme_keyword="", scheme_state="",
                         item_scheme="", metric_page=1, scheme_page=1):
    """组装绩效指标库与权重方案管理页面所需的全部数据。"""
    metric_pager = fetch_metric_rows(keyword, metric_type, metric_use, metric_page)
    scheme_pager = fetch_scheme_rows(scheme_keyword, scheme_state, scheme_page)
    return {
        "summary": build_metric_summary(),
        "charts": build_metric_charts(),
        "metrics": metric_pager["records"],
        "metric_total": metric_pager["total"],
        "metric_page": metric_pager["page"],
        "metric_page_amount": metric_pager["page_amount"],
        "schemes": scheme_pager["records"],
        "scheme_total": scheme_pager["total"],
        "scheme_page": scheme_pager["page"],
        "scheme_page_amount": scheme_pager["page_amount"],
        "items": fetch_scheme_items(item_scheme),
        "trial": build_scheme_trial(item_scheme) if item_scheme else [],
        "options": fetch_metric_page_options(),
        "result": "OK",
    }


def build_metric_brief():
    """权重方案看板摘要，供绩效分析大屏嵌入。"""
    summary = build_metric_summary()
    return {
        "metric_total": summary["metric_total"],
        "scheme_total": summary["scheme_total"],
        "publish_amount": summary["publish_amount"],
        "current_scheme": summary["current_scheme"],
        "current_score": summary["current_score"],
        "trial_spread": summary["trial_spread"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_metric():
    """灌入绩效指标与权重方案演示数据：指标建档、四套权重方案与试算留痕。

    :return: 生成的权重方案条数
    """
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_weight_scheme").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()
    ensure_metric_lib()
    period = current_period_tag()
    amount = 0
    for scheme_name, scheme_state, weight_map in DEMO_SCHEME_LIST:
        outcome = create_weight_scheme(
            scheme_name, period, "DRAFT",
            "演示用权重方案，用于对比不同权重取向的评价结果", "绩效办-吴主任")
        if not outcome.get("success"):
            continue
        scheme_code = outcome["scheme_code"]
        items = fetch_scheme_items(scheme_code)
        for item in items:
            weight = weight_map.get(item["metric_key"])
            if weight is None:
                continue
            save_weight_item(item["item_code"], weight,
                             remark_text="按%s导向设定" % scheme_name[7:9],
                             operator="绩效办-吴主任")
        refresh_scheme_state(scheme_code)
        trial_scheme(scheme_code, "绩效办-吴主任")
        if scheme_state == "PUBLISH":
            publish_weight_scheme(scheme_code, "绩效办-吴主任")
        else:
            conn = open_conn()
            try:
                conn.execute(
                    "UPDATE pe_weight_scheme SET scheme_state=? "
                    "WHERE scheme_code=?", (scheme_state, scheme_code))
                conn.commit()
            finally:
                conn.close()
        amount += 1

    # 把最后一套方案留作不平衡样本，用于演示归一化与发布校验
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT scheme_code FROM pe_weight_scheme WHERE scheme_state='DRAFT' "
            "ORDER BY scheme_id DESC LIMIT 1").fetchone()
        last_item = None
        if row:
            last_item = conn.execute(
                "SELECT item_code FROM pe_weight_item WHERE scheme_code=? "
                "ORDER BY weight_value DESC LIMIT 1", (row["scheme_code"],)).fetchone()
    finally:
        conn.close()
    if last_item:
        save_weight_item(last_item["item_code"], 26.0,
                         remark_text="演示用不平衡权重", operator="绩效办-吴主任")
    return amount

# --------------------------------------------------------------------------
# 第 12 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY12_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY12_SCHEMA_SQL = r"""
-- ------------------------------------------------------------------
-- 第 12 天新增：绩效指标库与权重方案管理
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pe_metric_lib (
    metric_id      INTEGER PRIMARY KEY AUTOINCREMENT, -- 指标主键
    metric_code    TEXT    NOT NULL UNIQUE,           -- 指标编号
    metric_key     TEXT    DEFAULT '',               -- 指标字段键（对应评分表列）
    metric_name    TEXT    DEFAULT '',               -- 指标名称
    metric_type    TEXT    DEFAULT 'BENEFIT',        -- 指标类型（见 METRIC_TYPE_DICT）
    metric_unit    TEXT    DEFAULT '',               -- 计量单位
    calc_formula   TEXT    DEFAULT '',               -- 计算口径说明
    data_source    TEXT    DEFAULT '',               -- 数据来源系统
    weight_limit   REAL    DEFAULT 0,                -- 权重上限（%）
    std_value      REAL    DEFAULT 0,                -- 参考标准值
    sort_no        INTEGER DEFAULT 0,                -- 展示排序号
    use_flag       INTEGER DEFAULT 1,                -- 是否启用（1=启用）
    remark_text    TEXT    DEFAULT '',               -- 备注说明
    operator_name  TEXT    DEFAULT '',               -- 维护人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS pe_weight_scheme (
    scheme_id      INTEGER PRIMARY KEY AUTOINCREMENT, -- 方案主键
    scheme_code    TEXT    NOT NULL UNIQUE,           -- 权重方案编号
    scheme_name    TEXT    DEFAULT '',               -- 权重方案名称
    period_tag     TEXT    DEFAULT '',               -- 适用考核周期
    metric_amount  INTEGER DEFAULT 0,                -- 纳入指标数量
    weight_sum     REAL    DEFAULT 0,                -- 权重合计（%）
    balance_flag   INTEGER DEFAULT 0,                -- 权重是否平衡（100% 为 1）
    trial_score    REAL    DEFAULT 0,                -- 方案试算全院平均得分
    scheme_state   TEXT    DEFAULT 'DRAFT',          -- 方案状态（见 SCHEME_STATE_DICT）
    use_flag       INTEGER DEFAULT 0,                -- 是否当前启用方案（1=启用）
    remark_text    TEXT    DEFAULT '',               -- 方案说明
    operator_name  TEXT    DEFAULT '',               -- 编制人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS pe_weight_item (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 明细主键
    item_code      TEXT    NOT NULL UNIQUE,           -- 权重明细编号
    scheme_code    TEXT    DEFAULT '',               -- 所属方案编号
    metric_code    TEXT    DEFAULT '',               -- 指标编号
    metric_key     TEXT    DEFAULT '',               -- 指标字段键
    metric_name    TEXT    DEFAULT '',               -- 指标名称快照
    weight_value   REAL    DEFAULT 0,                -- 权重值（%）
    std_value      REAL    DEFAULT 0,                -- 该指标达标参考值
    base_weight    REAL    DEFAULT 0,                -- 系统默认权重（%）
    remark_text    TEXT    DEFAULT '',               -- 权重说明
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_metric_code      ON pe_metric_lib (metric_code);
CREATE INDEX IF NOT EXISTS idx_metric_key       ON pe_metric_lib (metric_key);
CREATE INDEX IF NOT EXISTS idx_metric_use       ON pe_metric_lib (use_flag);
CREATE INDEX IF NOT EXISTS idx_wscheme_code     ON pe_weight_scheme (scheme_code);
CREATE INDEX IF NOT EXISTS idx_wscheme_state    ON pe_weight_scheme (scheme_state);
CREATE INDEX IF NOT EXISTS idx_witem_code       ON pe_weight_item (item_code);
CREATE INDEX IF NOT EXISTS idx_witem_scheme     ON pe_weight_item (scheme_code);
CREATE INDEX IF NOT EXISTS idx_witem_metric     ON pe_weight_item (metric_code);
"""


def apply_day12_schema(conn):
    """把第 12 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY12_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 12 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-11-01（第 12 天 / 共 15 天）
#           绩效趋势预测、绩效改进建议、绩效目标考核与绩效指标权重十二类页面
#           路由，启动时完成建表、等级规则初始化、科室初始化与演示评分、上期
#           对照、效益明细、低效预警、导出模板、导出留痕、历史评分样本、趋势
#           预测、改进建议整改跟踪、目标方案达标核算与绩效指标权重方案数据
#           灌入，并提供评分试算、评分入库、评分明细、权重元信息、榜单、科室横向对比、等级批量重判、

from perf_metric import (
    build_metric_payload,
    build_metric_brief,
    save_metric,
    toggle_metric,
    create_weight_scheme,
    save_weight_item,
    balance_scheme_weight,
    publish_weight_scheme,
    stop_weight_scheme,
    trial_scheme,
    ensure_metric_lib,
    seed_demo_metric,
    METRIC_TYPE_DICT,
    SCHEME_STATE_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 12 天新增：大屏叠加指标权重摘要，供权重概况卡渲染
#    payload["metric_brief"] = build_metric_brief()

@app.route("/pf/metric")
def page_metric():
    """绩效指标库与权重方案管理页面：指标建档、权重方案与试算对比。"""
    return render_template(
        "page_metric.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/metric/panel")
def api_metric_panel():
    """指标与权重面板数据接口：概览、五类图表、方案、明细与指标库。"""
    return jsonify(build_metric_payload(
        keyword=request.args.get("keyword", ""),
        metric_type=request.args.get("metric_type", ""),
        metric_use=request.args.get("metric_use", ""),
        scheme_keyword=request.args.get("scheme_keyword", ""),
        scheme_state=request.args.get("scheme_state", ""),
        item_scheme=request.args.get("item_scheme", ""),
        metric_page=request.args.get("metric_page", 1),
        scheme_page=request.args.get("scheme_page", 1),
    ))


@app.route("/api/pf/metric/save", methods=["POST"])
def api_metric_save():
    """绩效指标保存接口：新建或编辑指标口径、权重上限与参考标准值。"""
    body = request.get_json(silent=True) or {}
    result = save_metric(
        (body.get("metric_code") or "").strip(),
        body.get("metric_key") or "",
        body.get("metric_name") or "",
        body.get("metric_type") or "BENEFIT",
        body.get("metric_unit") or "",
        body.get("calc_formula") or "",
        body.get("data_source") or "",
        body.get("weight_limit") or 40.0,
        body.get("std_value") or 85.0,
        body.get("sort_no") or 0,
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/toggle", methods=["POST"])
def api_metric_toggle():
    """绩效指标启停接口：停用后的指标不再进入新建权重方案。"""
    body = request.get_json(silent=True) or {}
    result = toggle_metric(
        (body.get("metric_code") or "").strip(),
        body.get("use_flag") or 0,
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/scheme/create", methods=["POST"])
def api_metric_scheme_create():
    """权重方案新建接口：按启用指标生成默认权重明细并试算。"""
    body = request.get_json(silent=True) or {}
    result = create_weight_scheme(
        body.get("scheme_name") or "",
        body.get("period_tag") or "",
        body.get("scheme_state") or "DRAFT",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/item/save", methods=["POST"])
def api_metric_item_save():
    """权重明细调整接口：修改单项权重并回算方案权重合计与平衡标记。"""
    body = request.get_json(silent=True) or {}
    result = save_weight_item(
        (body.get("item_code") or "").strip(),
        body.get("weight_value") or 0,
        body.get("std_value"),
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/scheme/balance", methods=["POST"])
def api_metric_scheme_balance():
    """权重归一化接口：把方案权重按当前比例归一化到合计 100%。"""
    body = request.get_json(silent=True) or {}
    result = balance_scheme_weight(
        (body.get("scheme_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/scheme/publish", methods=["POST"])
def api_metric_scheme_publish():
    """权重方案发布接口：权重平衡后发布并停用其他启用方案。"""
    body = request.get_json(silent=True) or {}
    result = publish_weight_scheme(
        (body.get("scheme_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/scheme/stop", methods=["POST"])
def api_metric_scheme_stop():
    """权重方案停用接口：取消启用标记并把状态置为已停用。"""
    body = request.get_json(silent=True) or {}
    result = stop_weight_scheme(
        (body.get("scheme_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/scheme/trial", methods=["POST"])
def api_metric_scheme_trial():
    """权重试算接口：按方案权重重算当期全院平均综合得分。"""
    body = request.get_json(silent=True) or {}
    result = trial_scheme(
        (body.get("scheme_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/metric/options")
def api_metric_options():
    """指标权重下拉选项接口：指标类型、方案状态与指标字段字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in METRIC_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in SCHEME_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    ensure_metric_lib()
#    seed_demo_metric()

# 【函数内插入】if __name__ == "__main__":
#    print("指标权重：   http://127.0.0.1:%d/pf/metric" % PORT)

# --------------------------------------------------------------------------
# 第 12 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_metric.js            新增 512 行
#   [新增] templates/page_metric.html       新增 299 行
#   [修改] templates/screen_base.html       新增 1 行
