# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-29（第 9 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 9 天新增与修改的源代码，不含第 1~8 天已有代码
#           本次迭代主题：绩效趋势预测模块 + 趋势预测页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 9 天新增业务模块 1：perf_forecast.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-29（第 9 天 / 共 15 天）
# 模块功能：绩效趋势预测模块。负责预测模型的参数维护、设备级与科室级绩效
#           指标的多算法批量预测、预测结果的置信区间与风险判定，以及预测
#           批次留痕与历史序列回溯；支持移动平均、线性趋势外推与一次指数
#           平滑三类算法，按预测指标、预测范围、趋势方向与风险等级组合筛选
#           查询，输出趋势方向分布、风险等级分布、科室预测均分对比、全院
#           实际与预测走势、预测变动排行五类统计图表，形成「建模型 → 取
#           历史序列 → 批量预测 → 风险预警 → 批次回溯」的绩效预测闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）、statistics（Python 内置）

import datetime
import random
import statistics

from perf_store import open_conn, dict_rows
from perf_ranking import judge_grade_level, fetch_period_options

# ------------------------------------------------------------------
# 可预测指标字典：与绩效评分表的五项原始指标及综合得分一一对应
# ------------------------------------------------------------------
METRIC_FIELD_MAP = {
    "total_score": "综合得分",
    "usage_rate": "使用效率",
    "income_rate": "经济效益",
    "maintain_rate": "维保水平",
    "quality_rate": "质量安全",
    "service_rate": "服务效能",
}

# 预测算法字典：三类算法共用同一套历史序列输入，便于横向对比
FORECAST_ALGO_DICT = {
    "MOVING_AVG": "移动平均",
    "LINEAR": "线性趋势外推",
    "EXP_SMOOTH": "一次指数平滑",
}

# 预测对象类型字典：设备级用于单机改进跟踪，科室级用于整体趋势研判
FORECAST_SCOPE_DICT = {
    "DEVICE": "单台设备",
    "DEPT": "科室整体",
}

# 趋势方向字典：由预测值与上期实际值的差判定
FORECAST_TREND_DICT = {
    "UP": "预测上升",
    "FLAT": "基本持平",
    "DOWN": "预测下滑",
}

# 风险等级字典：下滑且低于风险阈值记为高风险，波动偏大记为需关注
FORECAST_RISK_DICT = {
    "HIGH": "高风险",
    "MID": "需关注",
    "LOW": "运行平稳",
}

# 趋势判定阈值：环比变动绝对值超过该分值才判定为上升或下滑
TREND_CHANGE_THRESHOLD = 0.5

# 波动率关注阈值：历史标准差超过该分值记为「需关注」
VOLATILITY_WATCH = 8.0

# 置信区间系数：对应约八成置信水平
CONFIDENCE_Z = 1.28

# 指标取值区间：绩效得分统一为百分制
METRIC_MIN_VALUE = 0.0
METRIC_MAX_VALUE = 100.0

# 内置预测模型：（模型编码、名称、指标、算法、窗口期、平滑系数、风险阈值、展望期数）
BUILTIN_MODEL_LIST = [
    ("FC-001", "综合得分移动平均预测", "total_score", "MOVING_AVG", 4, 0.6, 70, 1),
    ("FC-002", "综合得分线性趋势预测", "total_score", "LINEAR", 4, 0.6, 70, 1),
    ("FC-003", "使用效率指数平滑预测", "usage_rate", "EXP_SMOOTH", 4, 0.7, 65, 1),
]

# 预测结果分页尺寸
FORECAST_PAGE_SIZE = 10

# 历史序列演示补灌期数：首日演示时为预测模型补足历史样本
DEMO_HISTORY_PERIODS = 3

# 演示数据随机种子，保证每次启动生成的历史序列一致
DEMO_RANDOM_SEED = 20261029


# ------------------------------------------------------------------
# 周期与编码工具
# ------------------------------------------------------------------
def today_text():
    """返回今天日期文本 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def next_period_tag(period_tag):
    """由当前考核周期推算下一周期，跨年时进入下一年第一季度。"""
    if not period_tag or "-Q" not in period_tag:
        return ""
    try:
        year_text, quarter_text = period_tag.split("-Q")
        year, quarter = int(year_text), int(quarter_text)
    except (ValueError, AttributeError):
        return ""
    if quarter < 4:
        return "%d-Q%d" % (year, quarter + 1)
    return "%d-Q1" % (year + 1)


def shift_period_tag(period_tag, steps=1):
    """把考核周期按季度向前推 steps 期，steps 为负数时向后回退。"""
    tag = period_tag
    for _ in range(abs(steps)):
        tag = next_period_tag(tag) if steps > 0 else previous_period(tag)
        if not tag:
            return ""
    return tag


def previous_period(period_tag):
    """由当前考核周期推算上一周期。"""
    if not period_tag or "-Q" not in period_tag:
        return ""
    try:
        year_text, quarter_text = period_tag.split("-Q")
        year, quarter = int(year_text), int(quarter_text)
    except (ValueError, AttributeError):
        return ""
    if quarter > 1:
        return "%d-Q%d" % (year, quarter - 1)
    return "%d-Q4" % (year - 1)


def latest_period_tag():
    """读取库中已有评分数据的最新考核周期，无数据时返回空串。"""
    options = fetch_period_options()
    return options[0] if options else ""


def build_batch_code(seq_no):
    """生成预测批次编号：FB-年月-序号。"""
    return "FB-%s-%03d" % (datetime.date.today().strftime("%Y%m"), seq_no)


def build_record_code(seq_no):
    """生成预测记录编号：FR-年月日-序号。"""
    return "FR-%s-%04d" % (datetime.date.today().strftime("%Y%m%d"), seq_no)


def next_seq_no(table_name):
    """读取指定业务表记录数并加一，用于拼装业务编码。"""
    conn = open_conn()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def clamp_metric(value):
    """把预测结果裁剪到百分制区间内。"""
    return round(min(METRIC_MAX_VALUE, max(METRIC_MIN_VALUE, value)), 2)


# ------------------------------------------------------------------
# 预测模型维护
# ------------------------------------------------------------------
def ensure_forecast_models():
    """首次启动时写入内置预测模型。"""
    conn = open_conn()
    try:
        for (model_code, model_name, metric_key, algo_code, window_size,
             alpha_value, risk_threshold, periods) in BUILTIN_MODEL_LIST:
            exist = conn.execute(
                "SELECT model_id FROM pe_forecast_model WHERE model_code=?",
                (model_code,)).fetchone()
            if exist is not None:
                continue
            conn.execute(
                "INSERT INTO pe_forecast_model "
                "(model_code, model_name, metric_key, algo_code, window_size, "
                "alpha_value, risk_threshold, forecast_periods, enabled_flag) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                (model_code, model_name, metric_key, algo_code, window_size,
                 alpha_value, risk_threshold, periods))
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_forecast_models(only_enabled=False):
    """读取预测模型清单。"""
    sql = "SELECT * FROM pe_forecast_model"
    if only_enabled:
        sql += " WHERE enabled_flag=1"
    sql += " ORDER BY model_code"
    conn = open_conn()
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows:
        row = dict(item)
        row["metric_label"] = METRIC_FIELD_MAP.get(row["metric_key"], row["metric_key"])
        row["algo_label"] = FORECAST_ALGO_DICT.get(row["algo_code"], row["algo_code"])
        result.append(row)
    return result


def fetch_model_row(model_code):
    """按模型编码读取模型配置，未找到返回 None。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_forecast_model WHERE model_code=?",
            (model_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def save_forecast_model(model_code, model_name, metric_key="total_score",
                        algo_code="MOVING_AVG", window_size=4, alpha_value=0.6,
                        risk_threshold=70, forecast_periods=1, enabled_flag=1):
    """新增或修改一套预测模型配置。

    :return: 含 success 与 message 的执行结果字典
    """
    model_name = (model_name or "").strip()
    if not model_name:
        return {"success": False, "message": "模型名称不能为空"}
    if metric_key not in METRIC_FIELD_MAP:
        return {"success": False, "message": "预测指标 %s 不合法" % metric_key}
    if algo_code not in FORECAST_ALGO_DICT:
        return {"success": False, "message": "预测算法 %s 不合法" % algo_code}
    try:
        window = int(window_size)
        periods = int(forecast_periods)
        alpha = float(alpha_value)
        threshold = float(risk_threshold)
    except (TypeError, ValueError):
        return {"success": False, "message": "模型参数必须为数字"}
    if window < 2 or window > 12:
        return {"success": False, "message": "历史窗口期数应在 2 ~ 12 之间"}
    if periods < 1 or periods > 4:
        return {"success": False, "message": "展望期数应在 1 ~ 4 之间"}
    if not 0 < alpha <= 1:
        return {"success": False, "message": "平滑系数应在 0 ~ 1 之间"}
    if not 0 <= threshold <= 100:
        return {"success": False, "message": "风险阈值应在 0 ~ 100 之间"}

    model_code = (model_code or "").strip()
    conn = open_conn()
    try:
        if model_code:
            exist = conn.execute(
                "SELECT model_id FROM pe_forecast_model WHERE model_code=?",
                (model_code,)).fetchone()
        else:
            exist = None
            model_code = "FC-%03d" % next_seq_no("pe_forecast_model")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if exist is None:
            conn.execute(
                "INSERT INTO pe_forecast_model "
                "(model_code, model_name, metric_key, algo_code, window_size, "
                "alpha_value, risk_threshold, forecast_periods, enabled_flag, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (model_code, model_name, metric_key, algo_code, window,
                 alpha, threshold, periods, 1 if enabled_flag else 0, stamp))
            action = "新建"
        else:
            conn.execute(
                "UPDATE pe_forecast_model SET model_name=?, metric_key=?, "
                "algo_code=?, window_size=?, alpha_value=?, risk_threshold=?, "
                "forecast_periods=?, enabled_flag=?, updated_at=? "
                "WHERE model_code=?",
                (model_name, metric_key, algo_code, window, alpha, threshold,
                 periods, 1 if enabled_flag else 0, stamp, model_code))
            action = "更新"
        conn.commit()
    finally:
        conn.close()
    return {"success": True,
            "message": "预测模型%s成功，编码 %s" % (action, model_code),
            "model_code": model_code}


# ------------------------------------------------------------------
# 历史序列与预测算法
# ------------------------------------------------------------------
def fetch_metric_series(scope_type, scope_code, metric_key="total_score",
                        window_size=4):
    """读取某个预测对象的历史指标序列。

    :param scope_type: DEVICE 按设备编号取数，DEPT 按科室取各期均值
    :param scope_code: 设备编号或科室名称
    :param metric_key: 指标字段名，取值见 METRIC_FIELD_MAP
    :param window_size: 取最近多少期
    :return: [(period_tag, value), ...] 按周期升序
    """
    if metric_key not in METRIC_FIELD_MAP:
        return []
    conn = open_conn()
    try:
        if scope_type == "DEPT":
            rows = conn.execute(
                "SELECT period_tag, AVG(%s) AS metric_value FROM pe_score_record "
                "WHERE dept_name=? GROUP BY period_tag ORDER BY period_tag ASC"
                % metric_key, (scope_code,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT period_tag, %s AS metric_value FROM pe_score_record "
                "WHERE device_code=? ORDER BY period_tag ASC" % metric_key,
                (scope_code,)).fetchall()
    finally:
        conn.close()
    series = [(row["period_tag"], round(row["metric_value"] or 0, 2))
              for row in rows if row["period_tag"]]
    if window_size and len(series) > window_size:
        series = series[-window_size:]
    return series


def calc_moving_average(values, periods=1):
    """移动平均算法：取历史窗口内均值作为预测值。"""
    return sum(values) / float(len(values))


def calc_linear_trend(values, periods=1):
    """线性趋势外推：最小二乘拟合直线并向前外推 periods 期。"""
    size = len(values)
    if size < 2:
        return values[-1] if values else 0.0
    mean_x = (size - 1) / 2.0
    mean_y = sum(values) / float(size)
    numerator = 0.0
    denominator = 0.0
    for index, value in enumerate(values):
        numerator += (index - mean_x) * (value - mean_y)
        denominator += (index - mean_x) ** 2
    slope = numerator / denominator if denominator else 0.0
    return mean_y + slope * ((size - 1) / 2.0 + periods)


def calc_exponential_smoothing(values, alpha=0.6, periods=1):
    """一次指数平滑：由首期实际值起递推平滑值，平滑值即为预测值。"""
    if not values:
        return 0.0
    smooth = values[0]
    for value in values[1:]:
        smooth = alpha * value + (1 - alpha) * smooth
    return smooth


def calc_forecast_value(values, algo_code="MOVING_AVG", alpha=0.6, periods=1):
    """按指定算法计算预测值。"""
    if not values:
        return 0.0
    if algo_code == "LINEAR":
        return calc_linear_trend(values, periods)
    if algo_code == "EXP_SMOOTH":
        return calc_exponential_smoothing(values, alpha, periods)
    return calc_moving_average(values, periods)


def calc_volatility(values):
    """计算历史序列波动率（总体标准差），样本不足时返回 0。"""
    if len(values) < 2:
        return 0.0
    return round(statistics.pstdev(values), 2)


def judge_trend_code(change_value):
    """按环比变动值判定趋势方向编码。"""
    if change_value > TREND_CHANGE_THRESHOLD:
        return "UP"
    if change_value < -TREND_CHANGE_THRESHOLD:
        return "DOWN"
    return "FLAT"


def judge_risk_code(trend_code, forecast_value, volatility, risk_threshold):
    """按趋势、预测值与波动率判定风险等级编码。"""
    if trend_code == "DOWN" and forecast_value < risk_threshold:
        return "HIGH"
    if trend_code == "DOWN" or volatility >= VOLATILITY_WATCH:
        return "MID"
    return "LOW"


# ------------------------------------------------------------------
# 批量预测
# ------------------------------------------------------------------
def fetch_forecast_targets(scope_type, base_period):
    """读取某基准周期下的预测对象清单。"""
    conn = open_conn()
    try:
        if scope_type == "DEPT":
            rows = conn.execute(
                "SELECT dept_name AS scope_code, dept_name AS scope_name, "
                "dept_name FROM pe_score_record WHERE period_tag=? "
                "GROUP BY dept_name ORDER BY dept_name", (base_period,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_code AS scope_code, device_name AS scope_name, "
                "dept_name FROM pe_score_record WHERE period_tag=? "
                "ORDER BY device_code", (base_period,)).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def build_single_forecast(model, scope_type, target, base_period, target_period,
                          record_code):
    """为一个预测对象计算一条预测结果字典。

    :return: 预测结果字典，历史样本不足时返回 None
    """
    metric_key = model["metric_key"]
    series = fetch_metric_series(scope_type, target["scope_code"], metric_key,
                                 model["window_size"])
    if not series:
        return None
    values = [value for _, value in series]
    forecast_value = clamp_metric(
        calc_forecast_value(values, model["algo_code"], model["alpha_value"],
                            model["forecast_periods"]))
    last_value = values[-1]
    volatility = calc_volatility(values)
    change_value = round(forecast_value - last_value, 2)
    change_rate = round(change_value * 100.0 / last_value, 2) if last_value else 0.0
    trend_code = judge_trend_code(change_value)
    risk_code = judge_risk_code(trend_code, forecast_value, volatility,
                                model["risk_threshold"])
    grade_forecast = ""
    if metric_key == "total_score":
        # judge_grade_level 返回（等级编码、等级名称、配色标签）三元组
        grade_forecast = judge_grade_level(forecast_value)[0]
    return {
        "record_code": record_code,
        "scope_type": scope_type,
        "scope_code": target["scope_code"],
        "scope_name": target["scope_name"],
        "dept_name": target["dept_name"],
        "metric_key": metric_key,
        "base_period": series[-1][0],
        "target_period": target_period,
        "model_code": model["model_code"],
        "algo_code": model["algo_code"],
        "history_text": ",".join("%.2f" % value for value in values),
        "history_amount": len(values),
        "history_avg": round(sum(values) / float(len(values)), 2),
        "last_value": round(last_value, 2),
        "forecast_value": forecast_value,
        "forecast_low": clamp_metric(forecast_value - CONFIDENCE_Z * volatility),
        "forecast_high": clamp_metric(forecast_value + CONFIDENCE_Z * volatility),
        "change_value": change_value,
        "change_rate": change_rate,
        "trend_code": trend_code,
        "volatility": volatility,
        "risk_code": risk_code,
        "grade_forecast": grade_forecast,
        "trend_label": FORECAST_TREND_DICT[trend_code],
        "risk_label": FORECAST_RISK_DICT[risk_code],
        "metric_label": METRIC_FIELD_MAP.get(metric_key, metric_key),
        "algo_label": FORECAST_ALGO_DICT.get(model["algo_code"], model["algo_code"]),
        "scope_label": FORECAST_SCOPE_DICT.get(scope_type, scope_type),
        "periods": [tag for tag, _ in series],
        "values": values,
    }


def generate_forecast_batch(model_code="", metric_key="", scope_type="DEVICE",
                            target_period="", operator="admin"):
    """按模型对全部预测对象执行一次批量预测并登记批次留痕。

    :param model_code: 模型编码，留空时取首个启用模型
    :param metric_key: 覆盖模型指标（留空则使用模型自带指标）
    :param scope_type: DEVICE 设备级 / DEPT 科室级
    :param target_period: 预测目标周期，留空时按展望期数自动推算
    :return: 含 success、message 与批次统计的执行结果字典
    """
    if scope_type not in FORECAST_SCOPE_DICT:
        return {"success": False, "message": "预测对象类型 %s 不合法" % scope_type}
    model = fetch_model_row((model_code or "").strip())
    if model is None:
        enabled = fetch_forecast_models(only_enabled=True)
        if not enabled:
            return {"success": False, "message": "没有可用的预测模型"}
        model = enabled[0]
    if metric_key and metric_key in METRIC_FIELD_MAP:
        model = dict(model)
        model["metric_key"] = metric_key
    base_period = latest_period_tag()
    if not base_period:
        return {"success": False, "message": "评分库中暂无考核周期数据"}
    if not (target_period or "").strip():
        target_period = shift_period_tag(base_period, model["forecast_periods"])
    if not target_period:
        return {"success": False, "message": "预测目标周期推算失败"}

    targets = fetch_forecast_targets(scope_type, base_period)
    if not targets:
        return {"success": False, "message": "基准周期 %s 没有可预测的对象" % base_period}

    seq_no = next_seq_no("pe_forecast_record")
    records = []
    for index, target in enumerate(targets):
        row = build_single_forecast(model, scope_type, target, base_period,
                                    target_period, build_record_code(seq_no + index))
        if row is not None:
            records.append(row)
    if not records:
        return {"success": False, "message": "预测对象历史样本不足，请先补齐历史评分数据"}

    batch_code = build_batch_code(next_seq_no("pe_forecast_batch") + 1)
    up_amount = len([r for r in records if r["trend_code"] == "UP"])
    down_amount = len([r for r in records if r["trend_code"] == "DOWN"])
    risk_amount = len([r for r in records if r["risk_code"] == "HIGH"])
    avg_forecast = round(sum(r["forecast_value"] for r in records) / len(records), 2)
    avg_change = round(sum(r["change_value"] for r in records) / len(records), 2)

    conn = open_conn()
    try:
        conn.execute(
            "INSERT INTO pe_forecast_batch "
            "(batch_code, base_period, target_period, model_code, model_name, "
            "metric_key, scope_type, record_amount, up_amount, down_amount, "
            "risk_amount, avg_forecast, avg_change, operator_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_code, base_period, target_period, model["model_code"],
             model["model_name"], model["metric_key"], scope_type, len(records),
             up_amount, down_amount, risk_amount, avg_forecast, avg_change,
             (operator or "admin").strip()))
        for row in records:
            conn.execute(
                "INSERT INTO pe_forecast_record "
                "(record_code, batch_code, scope_type, scope_code, scope_name, "
                "dept_name, metric_key, base_period, target_period, model_code, "
                "algo_code, history_text, history_amount, history_avg, "
                "last_value, forecast_value, forecast_low, forecast_high, "
                "change_value, change_rate, trend_code, volatility, risk_code, "
                "grade_forecast) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["record_code"], batch_code, row["scope_type"],
                 row["scope_code"], row["scope_name"], row["dept_name"],
                 row["metric_key"], row["base_period"], row["target_period"],
                 row["model_code"], row["algo_code"], row["history_text"],
                 row["history_amount"], row["history_avg"], row["last_value"],
                 row["forecast_value"], row["forecast_low"], row["forecast_high"],
                 row["change_value"], row["change_rate"], row["trend_code"],
                 row["volatility"], row["risk_code"], row["grade_forecast"]))
        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "message": "预测批次 %s 生成完成：%s %s 共 %d 条，上升 %d 条、下滑 %d 条"
                   % (batch_code, target_period,
                      FORECAST_SCOPE_DICT[scope_type], len(records),
                      up_amount, down_amount),
        "batch_code": batch_code,
        "record_amount": len(records),
        "up_amount": up_amount,
        "down_amount": down_amount,
        "risk_amount": risk_amount,
        "avg_forecast": avg_forecast,
    }


# ------------------------------------------------------------------
# 查询与统计
# ------------------------------------------------------------------
def fetch_forecast_records(batch_code="", scope_type="", dept_name="",
                           trend_code="", risk_code="", keyword="", page=1):
    """按组合条件分页查询预测结果明细。"""
    where = []
    params = []
    if batch_code:
        where.append("batch_code=?")
        params.append(batch_code)
    if scope_type:
        where.append("scope_type=?")
        params.append(scope_type)
    if dept_name:
        where.append("dept_name=?")
        params.append(dept_name)
    if trend_code:
        where.append("trend_code=?")
        params.append(trend_code)
    if risk_code:
        where.append("risk_code=?")
        params.append(risk_code)
    if keyword:
        where.append("(scope_code LIKE ? OR scope_name LIKE ? OR dept_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * FORECAST_PAGE_SIZE

    conn = open_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_record" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM pe_forecast_record" + clause +
            " ORDER BY change_value ASC, record_id DESC LIMIT ? OFFSET ?",
            params + [FORECAST_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["trend_label"] = FORECAST_TREND_DICT.get(row["trend_code"], row["trend_code"])
        row["risk_label"] = FORECAST_RISK_DICT.get(row["risk_code"], row["risk_code"])
        row["metric_label"] = METRIC_FIELD_MAP.get(row["metric_key"], row["metric_key"])
        row["algo_label"] = FORECAST_ALGO_DICT.get(row["algo_code"], row["algo_code"])
        row["scope_label"] = FORECAST_SCOPE_DICT.get(row["scope_type"], row["scope_type"])
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": FORECAST_PAGE_SIZE,
        "page_amount": max(1, (total + FORECAST_PAGE_SIZE - 1) // FORECAST_PAGE_SIZE),
    }


def fetch_forecast_batches(limit=10):
    """读取最近的预测批次留痕。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM pe_forecast_batch ORDER BY batch_id DESC LIMIT ?",
            (limit,)).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows:
        row = dict(item)
        row["scope_label"] = FORECAST_SCOPE_DICT.get(row["scope_type"], row["scope_type"])
        row["metric_label"] = METRIC_FIELD_MAP.get(row["metric_key"], row["metric_key"])
        result.append(row)
    return result


def fetch_forecast_series(scope_code="", metric_key="total_score",
                          batch_code="", window_size=6):
    """读取某预测对象的历史序列与预测结果，供趋势曲线渲染。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_forecast_record WHERE scope_code=? "
            "ORDER BY record_id DESC", (scope_code,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"success": False, "message": "未找到该对象的预测记录"}
    forecast = dict(row)
    metric_key = forecast["metric_key"]
    # 设备级预测按设备编号取历史序列，科室级预测按科室取各期均值
    conn = open_conn()
    try:
        if forecast["scope_type"] == "DEPT":
            period_rows = conn.execute(
                "SELECT period_tag, AVG(%s) AS metric_value FROM pe_score_record "
                "WHERE dept_name=? GROUP BY period_tag ORDER BY period_tag ASC"
                % metric_key, (forecast["scope_name"],)).fetchall()
        else:
            period_rows = conn.execute(
                "SELECT period_tag, %s AS metric_value FROM pe_score_record "
                "WHERE device_code=? ORDER BY period_tag ASC" % metric_key,
                (scope_code,)).fetchall()
    finally:
        conn.close()
    history = [(item["period_tag"], round(item["metric_value"] or 0, 2))
               for item in period_rows if item["period_tag"]]
    if len(history) > window_size:
        history = history[-window_size:]
    return {
        "success": True,
        "scope_code": scope_code,
        "scope_name": forecast["scope_name"],
        "metric_key": forecast["metric_key"],
        "metric_label": METRIC_FIELD_MAP.get(forecast["metric_key"], forecast["metric_key"]),
        "periods": [tag for tag, _ in history] + [forecast["target_period"]],
        "history": [value for _, value in history] + [None],
        "forecast": [None] * (len(history)) + [forecast["forecast_value"]],
        "forecast_low": forecast["forecast_low"],
        "forecast_high": forecast["forecast_high"],
        "change_value": forecast["change_value"],
        "trend_label": FORECAST_TREND_DICT.get(forecast["trend_code"], ""),
        "risk_label": FORECAST_RISK_DICT.get(forecast["risk_code"], ""),
    }


def build_forecast_summary():
    """汇总绩效趋势预测核心指标：模型量、批次数、预测对象数与风险分布。"""
    conn = open_conn()
    try:
        model_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_model WHERE enabled_flag=1"
        ).fetchone()["c"]
        batch_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_batch").fetchone()["c"]
        record_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_record").fetchone()["c"]
        up_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_record WHERE trend_code='UP'"
        ).fetchone()["c"]
        down_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_record WHERE trend_code='DOWN'"
        ).fetchone()["c"]
        risk_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_record WHERE risk_code='HIGH'"
        ).fetchone()["c"]
        avg_row = conn.execute(
            "SELECT COALESCE(AVG(forecast_value),0) AS a, "
            "COALESCE(AVG(change_value),0) AS c FROM pe_forecast_record").fetchone()
        last_batch = conn.execute(
            "SELECT * FROM pe_forecast_batch ORDER BY batch_id DESC LIMIT 1"
        ).fetchone()
        period_rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_score_record "
            "ORDER BY period_tag ASC").fetchall()
    finally:
        conn.close()
    batch_row = dict(last_batch) if last_batch is not None else {}
    return {
        "model_amount": model_amount,
        "batch_amount": batch_amount,
        "record_amount": record_amount,
        "up_amount": up_amount,
        "down_amount": down_amount,
        "risk_amount": risk_amount,
        "avg_forecast": round(avg_row["a"], 2),
        "avg_change": round(avg_row["c"], 2),
        "last_batch_code": batch_row.get("batch_code", "--"),
        "last_target_period": batch_row.get("target_period", "--"),
        "base_period": latest_period_tag() or "--",
        "history_periods": len(period_rows),
    }


def build_forecast_charts():
    """构建五类图表数据：趋势分布、风险分布、科室预测均分、全院走势与变动排行。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT scope_type, scope_name, dept_name, metric_key, target_period, "
            "forecast_value, change_value, trend_code, risk_code, volatility "
            "FROM pe_forecast_record").fetchall()
        period_rows = conn.execute(
            "SELECT period_tag, AVG(total_score) AS metric_value "
            "FROM pe_score_record GROUP BY period_tag ORDER BY period_tag ASC"
        ).fetchall()
        batch_row = conn.execute(
            "SELECT target_period FROM pe_forecast_batch "
            "ORDER BY batch_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    forecasts = dict_rows(rows)

    trend_counter = {}
    for code in FORECAST_TREND_DICT:
        trend_counter[code] = 0
    for item in forecasts:
        trend_counter[item["trend_code"]] = trend_counter.get(item["trend_code"], 0) + 1
    trend_distribution = [
        {"name": FORECAST_TREND_DICT[code], "value": trend_counter[code]}
        for code in ("UP", "FLAT", "DOWN")
    ]

    risk_counter = {}
    for code in FORECAST_RISK_DICT:
        risk_counter[code] = 0
    for item in forecasts:
        risk_counter[item["risk_code"]] = risk_counter.get(item["risk_code"], 0) + 1
    risk_distribution = [
        {"name": FORECAST_RISK_DICT[code], "value": risk_counter[code]}
        for code in ("HIGH", "MID", "LOW")
    ]

    dept_counter = {}
    dept_total = {}
    for item in forecasts:
        if item["scope_type"] != "DEVICE" or item["metric_key"] != "total_score":
            continue
        dept = item["dept_name"] or "未分配"
        dept_total[dept] = dept_total.get(dept, 0) + item["forecast_value"]
        dept_counter[dept] = dept_counter.get(dept, 0) + 1
    dept_forecast = [{"name": dept, "value": round(dept_total[dept] / amount, 2)}
                     for dept, amount in dept_counter.items()]
    dept_forecast.sort(key=lambda x: x["value"], reverse=True)

    history_periods = [row["period_tag"] for row in period_rows]
    history_values = [round(row["metric_value"] or 0, 2) for row in period_rows]
    device_forecasts = [item for item in forecasts
                        if item["scope_type"] == "DEVICE"
                        and item["metric_key"] == "total_score"]
    forecast_mark = round(sum(item["forecast_value"] for item in device_forecasts)
                          / len(device_forecasts), 2) if device_forecasts else None
    target_period = batch_row["target_period"] if batch_row is not None else "预测期"
    period_series = {
        "periods": history_periods + ([target_period] if forecast_mark else []),
        "actual": history_values + ([None] if forecast_mark else []),
        "forecast": [None] * len(history_periods) + ([forecast_mark] if forecast_mark else []),
    }

    change_rank = sorted(
        [{"name": item["scope_name"], "value": item["change_value"],
          "forecast": item["forecast_value"]}
         for item in device_forecasts],
        key=lambda x: x["value"])[:10]

    return {
        "trend_distribution": trend_distribution,
        "risk_distribution": risk_distribution,
        "dept_forecast": dept_forecast,
        "period_series": period_series,
        "change_rank": change_rank,
    }


def fetch_forecast_options():
    """趋势预测页下拉选项：模型清单、周期清单、科室清单与各类字典。"""
    conn = open_conn()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM pe_score_record "
            "WHERE dept_name<>'' ORDER BY dept_name").fetchall()
    finally:
        conn.close()
    return {
        "model_list": fetch_forecast_models(),
        "dept_list": [row["dept_name"] for row in dept_rows],
        "period_list": fetch_period_options(),
        "metric_options": [{"code": k, "label": v} for k, v in METRIC_FIELD_MAP.items()],
        "algo_options": [{"code": k, "label": v} for k, v in FORECAST_ALGO_DICT.items()],
        "scope_options": [{"code": k, "label": v} for k, v in FORECAST_SCOPE_DICT.items()],
        "trend_options": [{"code": k, "label": v} for k, v in FORECAST_TREND_DICT.items()],
        "risk_options": [{"code": k, "label": v} for k, v in FORECAST_RISK_DICT.items()],
    }


def build_forecast_payload(batch_code="", scope_type="", dept_name="",
                           trend_code="", risk_code="", keyword="", page=1):
    """组装绩效趋势预测页面所需的全部数据。"""
    ensure_forecast_models()
    pager = fetch_forecast_records(batch_code, scope_type, dept_name, trend_code,
                                   risk_code, keyword, page)
    return {
        "summary": build_forecast_summary(),
        "charts": build_forecast_charts(),
        "records": pager["records"],
        "total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "models": fetch_forecast_models(),
        "batches": fetch_forecast_batches(8),
        "options": fetch_forecast_options(),
        "result": "OK",
    }


def build_forecast_brief():
    """绩效趋势预测看板摘要，供绩效分析大屏嵌入。"""
    summary = build_forecast_summary()
    return {
        "last_batch_code": summary["last_batch_code"],
        "target_period": summary["last_target_period"],
        "record_amount": summary["record_amount"],
        "down_amount": summary["down_amount"],
        "risk_amount": summary["risk_amount"],
        "avg_forecast": summary["avg_forecast"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_history_scores():
    """为预测模型补足历史考核周期的评分样本。

    以最新考核周期的实际评分为终点，按设备编号派生稳定的随机偏移量，
    向前生成三个季度的历史评分，使历史序列既有上升样本也有下滑样本。

    :return: 新增的历史评分数记录条数
    """
    base_period = latest_period_tag()
    if not base_period:
        return 0
    conn = open_conn()
    try:
        exist_periods = conn.execute(
            "SELECT COUNT(DISTINCT period_tag) AS c FROM pe_score_record"
        ).fetchone()["c"]
        if exist_periods >= DEMO_HISTORY_PERIODS + 1:
            return 0
        rows = conn.execute(
            "SELECT device_code, device_name, dept_name, usage_rate, "
            "income_rate, maintain_rate, quality_rate, service_rate, "
            "total_score FROM pe_score_record WHERE period_tag=? "
            "ORDER BY device_code", (base_period,)).fetchall()
        base_rows = dict_rows(rows)
    finally:
        conn.close()
    if not base_rows:
        return 0

    generator = random.Random(DEMO_RANDOM_SEED)
    amount = 0
    for index, row in enumerate(base_rows):
        # 按设备序号分配趋势方向：约三成持续下滑、四成稳步上升、其余窄幅波动
        trend_kind = index % 10
        if trend_kind < 3:
            step = -generator.uniform(1.2, 3.5)
        elif trend_kind < 7:
            step = generator.uniform(0.8, 2.6)
        else:
            step = generator.uniform(-0.6, 0.6)
        for offset in range(DEMO_HISTORY_PERIODS, 0, -1):
            period_tag = shift_period_tag(base_period, -offset)
            if not period_tag:
                continue
            jitter = generator.uniform(-2.2, 2.2)
            delta = step * offset + jitter
            usage = min(100, max(35, row["usage_rate"] - delta * 0.9))
            income = min(100, max(35, row["income_rate"] - delta * 0.7))
            maintain = min(100, max(35, row["maintain_rate"] - delta * 0.5))
            quality = min(100, max(35, row["quality_rate"] - delta * 0.4))
            service = min(100, max(35, row["service_rate"] - delta * 0.8))
            total = min(100, max(35, row["total_score"] - delta))
            conn = open_conn()
            try:
                exist = conn.execute(
                    "SELECT score_id FROM pe_score_record WHERE device_code=? "
                    "AND period_tag=?", (row["device_code"], period_tag)).fetchone()
                if exist is not None:
                    continue
                conn.execute(
                    "INSERT INTO pe_score_record "
                    "(device_code, device_name, dept_name, period_tag, usage_rate, "
                    "income_rate, maintain_rate, quality_rate, service_rate, "
                    "total_score, grade_level, grade_comment, is_low_benefit) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,'',0)",
                    (row["device_code"], row["device_name"], row["dept_name"],
                     period_tag, round(usage, 2), round(income, 2),
                     round(maintain, 2), round(quality, 2), round(service, 2),
                     round(total, 2), judge_grade_level(total)[0]))
                conn.commit()
                amount += 1
            finally:
                conn.close()
    return amount


def seed_demo_forecast():
    """灌入绩效趋势预测演示数据：补历史样本、建模型并生成两批预测。"""
    ensure_forecast_models()
    seed_demo_history_scores()
    conn = open_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM pe_forecast_batch").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()
    generate_forecast_batch("FC-001", "total_score", "DEVICE", "", "绩效办-预测演示")
    generate_forecast_batch("FC-002", "total_score", "DEPT", "", "绩效办-预测演示")
    return 2

# --------------------------------------------------------------------------
# 第 9 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY9_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY9_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 9 天新增：绩效趋势预测模型表
-- ==================================================================
-- 预测模型表：按「一个预测口径一套参数」维护预测配置，含目标指标、预测
-- 算法、历史窗口期数、指数平滑系数、风险阈值与展望期数，模型参数可在线
-- 调整并即时作用于下一次预测批次，支撑绩效趋势的多算法对比
CREATE TABLE IF NOT EXISTS pe_forecast_model (
    model_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 模型主键
    model_code      TEXT    NOT NULL UNIQUE,            -- 模型编码（FC-序号）
    model_name      TEXT    NOT NULL,                   -- 模型名称
    metric_key      TEXT    DEFAULT 'total_score',      -- 预测指标（见 METRIC_FIELD_MAP）
    algo_code       TEXT    DEFAULT 'MOVING_AVG',       -- 预测算法（见 FORECAST_ALGO_DICT）
    window_size     INTEGER DEFAULT 4,                  -- 历史窗口期数
    alpha_value     REAL    DEFAULT 0.6,                -- 指数平滑系数（0-1，仅平滑算法使用）
    risk_threshold  REAL    DEFAULT 70,                 -- 风险判定阈值（低于该分值且下滑记为高风险）
    forecast_periods INTEGER DEFAULT 1,                 -- 展望期数（向前预测的周期个数）
    enabled_flag    INTEGER DEFAULT 1,                  -- 是否启用：1 启用 / 0 停用
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 9 天新增：绩效趋势预测结果表
-- ==================================================================
-- 预测结果表：按「一个预测对象一个周期一条」保存预测输出，含历史序列、
-- 历史均值、上期实际值、预测值、八成置信区间、环比变动、趋势方向、波动
-- 率、风险等级与预测绩效等级，支撑绩效下滑的提前干预与改进跟踪
CREATE TABLE IF NOT EXISTS pe_forecast_record (
    record_id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- 记录主键
    record_code    TEXT    DEFAULT '',                  -- 记录编号（FR-日期-序号）
    batch_code     TEXT    DEFAULT '',                  -- 所属预测批次编号
    scope_type     TEXT    DEFAULT 'DEVICE',            -- 预测对象类型（见 FORECAST_SCOPE_DICT）
    scope_code     TEXT    DEFAULT '',                  -- 预测对象编码（设备编号或科室名称）
    scope_name     TEXT    DEFAULT '',                  -- 预测对象名称
    dept_name      TEXT    DEFAULT '',                  -- 所属科室（科室级预测时与 scope_name 相同）
    metric_key     TEXT    DEFAULT 'total_score',       -- 预测指标
    base_period    TEXT    DEFAULT '',                  -- 基准考核周期（历史序列最后一期）
    target_period  TEXT    DEFAULT '',                  -- 预测目标周期
    model_code     TEXT    DEFAULT '',                  -- 使用模型编码
    algo_code      TEXT    DEFAULT 'MOVING_AVG',        -- 使用算法快照
    history_text   TEXT    DEFAULT '',                  -- 历史序列（逗号分隔数值文本）
    history_amount INTEGER DEFAULT 0,                   -- 历史样本期数
    history_avg    REAL    DEFAULT 0,                   -- 历史均值
    last_value     REAL    DEFAULT 0,                   -- 上期实际值
    forecast_value REAL    DEFAULT 0,                   -- 预测值
    forecast_low   REAL    DEFAULT 0,                   -- 置信区间下限
    forecast_high  REAL    DEFAULT 0,                   -- 置信区间上限
    change_value   REAL    DEFAULT 0,                   -- 环比变动值
    change_rate    REAL    DEFAULT 0,                   -- 环比变动率（%）
    trend_code     TEXT    DEFAULT 'FLAT',              -- 趋势方向（见 FORECAST_TREND_DICT）
    volatility     REAL    DEFAULT 0,                   -- 历史波动率（标准差）
    risk_code      TEXT    DEFAULT 'LOW',               -- 风险等级（见 FORECAST_RISK_DICT）
    grade_forecast TEXT    DEFAULT '',                  -- 预测绩效等级（综合得分指标专用）
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 9 天新增：绩效预测批次留痕表
-- ==================================================================
-- 预测批次表：按「一次批量预测一条」记录预测全过程，含基准周期、目标周期、
-- 使用模型、预测范围、生成条数、升降分布、预测均值与操作人，支撑预测结果
-- 的版本回溯与不同批次之间的横向对比
CREATE TABLE IF NOT EXISTS pe_forecast_batch (
    batch_id      INTEGER PRIMARY KEY AUTOINCREMENT,    -- 批次主键
    batch_code    TEXT    DEFAULT '',                   -- 批次编号（FB-年月-序号）
    base_period   TEXT    DEFAULT '',                   -- 基准考核周期
    target_period TEXT    DEFAULT '',                   -- 预测目标周期
    model_code    TEXT    DEFAULT '',                   -- 使用模型编码
    model_name    TEXT    DEFAULT '',                   -- 使用模型名称快照
    metric_key    TEXT    DEFAULT 'total_score',        -- 预测指标快照
    scope_type    TEXT    DEFAULT 'DEVICE',             -- 预测对象类型快照
    record_amount INTEGER DEFAULT 0,                    -- 本批预测对象条数
    up_amount     INTEGER DEFAULT 0,                    -- 预测上升条数
    down_amount   INTEGER DEFAULT 0,                    -- 预测下滑条数
    risk_amount   INTEGER DEFAULT 0,                    -- 高风险条数
    avg_forecast  REAL    DEFAULT 0,                    -- 预测均值
    avg_change    REAL    DEFAULT 0,                    -- 平均环比变动值
    operator_name TEXT    DEFAULT 'admin',              -- 预测操作人
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_fcmodel_code   ON pe_forecast_model (model_code);
CREATE INDEX IF NOT EXISTS idx_fcrecord_code  ON pe_forecast_record (record_code);
CREATE INDEX IF NOT EXISTS idx_fcrecord_batch ON pe_forecast_record (batch_code);
CREATE INDEX IF NOT EXISTS idx_fcrecord_scope ON pe_forecast_record (scope_code);
CREATE INDEX IF NOT EXISTS idx_fcrecord_trend ON pe_forecast_record (trend_code);
CREATE INDEX IF NOT EXISTS idx_fcrecord_risk  ON pe_forecast_record (risk_code);
CREATE INDEX IF NOT EXISTS idx_fcbatch_code   ON pe_forecast_batch (batch_code);
CREATE INDEX IF NOT EXISTS idx_fcbatch_period ON pe_forecast_batch (target_period);
"""


def apply_day9_schema(conn):
    """把第 9 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY9_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 9 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-29（第 9 天 / 共 15 天）
#           绩效对比、可视化图表、效益报表、低效设备预警、绩效报告导出与
#           绩效趋势预测九类页面路由，启动时完成建表、等级规则初始化、科室
#           初始化与演示评分、上期对照、效益明细、低效预警、导出模板、导出
#           留痕、历史评分样本与趋势预测数据灌入，并提供评分试算、评分入库、
#           评分明细、权重元信息、榜单、科室横向对比、等级批量重判、ECharts
#           图表 option、多维度排行筛选查询、效益报表核算归档、低效预警规则
#           维护扫描整改、导出模板维护、报告生成落盘、文件预览下载与留痕删除，
#           以及预测模型维护、批量趋势预测、预测结果筛选查询与历史序列回溯
#           接口

from perf_forecast import (
    build_forecast_payload,
    build_forecast_brief,
    generate_forecast_batch,
    fetch_forecast_models,
    save_forecast_model,
    fetch_forecast_series,
    fetch_forecast_batches,
    seed_demo_forecast,
    FORECAST_ALGO_DICT,
    FORECAST_SCOPE_DICT,
)

# 【函数内插入】def api_screen_data():
#    # 第 9 天新增：大屏叠加趋势预测摘要，供预测概况卡渲染
#    payload["forecast_brief"] = build_forecast_brief()

# ==================================================================
# 以下为第 9 天新增：绩效趋势预测页面与接口
# ==================================================================


@app.route("/pf/forecast")
def page_forecast():
    """绩效趋势预测页面：预测指标、五类图表、模型清单、预测明细与批次留痕。"""
    return render_template(
        "page_forecast.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/forecast/panel")
def api_forecast_panel():
    """趋势预测数据接口：汇总指标、五类图表、模型清单、分页明细与批次留痕。"""
    return jsonify(build_forecast_payload(
        batch_code=(request.args.get("batch_code") or "").strip(),
        scope_type=(request.args.get("scope_type") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        trend_code=(request.args.get("trend_code") or "").strip(),
        risk_code=(request.args.get("risk_code") or "").strip(),
        keyword=(request.args.get("keyword") or "").strip(),
        page=int(request.args.get("page", 1)),
    ))


@app.route("/api/pf/forecast/generate", methods=["POST"])
def api_forecast_generate():
    """执行一次批量趋势预测并登记预测批次留痕。"""
    body = request.get_json(silent=True) or {}
    result = generate_forecast_batch(
        (body.get("model_code") or "").strip(),
        (body.get("metric_key") or "").strip(),
        (body.get("scope_type") or "DEVICE").strip(),
        (body.get("target_period") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/forecast/model", methods=["GET", "POST"])
def api_forecast_model():
    """预测模型接口：GET 读取模型清单，POST 新建或修改模型参数。"""
    if request.method == "GET":
        return jsonify({"result": "OK", "models": fetch_forecast_models()})
    body = request.get_json(silent=True) or {}
    result = save_forecast_model(
        (body.get("model_code") or "").strip(),
        (body.get("model_name") or "").strip(),
        (body.get("metric_key") or "total_score").strip(),
        (body.get("algo_code") or "MOVING_AVG").strip(),
        body.get("window_size", 4),
        body.get("alpha_value", 0.6),
        body.get("risk_threshold", 70),
        body.get("forecast_periods", 1),
        body.get("enabled_flag", 1),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/forecast/batches")
def api_forecast_batches():
    """预测批次留痕接口：返回最近若干次批量预测记录。"""
    return jsonify({
        "result": "OK",
        "batches": fetch_forecast_batches(int(request.args.get("limit", 10))),
    })


@app.route("/api/pf/forecast/series")
def api_forecast_series():
    """历史序列接口：返回某预测对象的历史实际序列与预测值，供曲线渲染。"""
    result = fetch_forecast_series(
        (request.args.get("scope_code") or "").strip(),
        (request.args.get("metric_key") or "total_score").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/pf/forecast/options")
def api_forecast_options():
    """趋势预测下拉选项接口：算法、预测范围与预测指标字典。"""
    return jsonify({
        "result": "OK",
        "algo_options": [{"code": k, "label": v}
                         for k, v in FORECAST_ALGO_DICT.items()],
        "scope_options": [{"code": k, "label": v}
                          for k, v in FORECAST_SCOPE_DICT.items()],
    })



# 【函数内插入】def bootstrap():
#    seed_demo_forecast()

# 【函数内插入】if __name__ == "__main__":
#    print("趋势预测：   http://127.0.0.1:%d/pf/forecast" % PORT)

# --------------------------------------------------------------------------
# 第 9 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_forecast.js          新增 479 行
#   [新增] templates/page_forecast.html     新增 273 行
#   [修改] templates/screen_base.html       新增 1 行
