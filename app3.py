# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-23（第 3 天 / 共 15 天）
# 模块功能：本文件为该软件的完整源代码单文件版，按依赖顺序依次内联配置模块、建表脚本、数据仓储层、各业务模块与 Flask 主程序入口，可独立阅读与归档，用于计算机软件著作权申报
# 依赖包：Flask==3.0.3
# 启动命令：python 医院医疗设备绩效综合分析系统V1.0.py （默认 http://127.0.0.1:5002/pf/screen）
# 说明：运行需与本工程 templates/、static/ 目录同级放置

import os
import sqlite3
import math
from flask import Flask, render_template, jsonify, redirect, url_for, request

# --------------------------------------------------------------------------
# 模块 1：perf_config.py
# 模块功能：绩效系统参数配置。定义评分指标的权重体系、绩效等级划分阈值、 服务端口与数据文件路径，供评分算法与大屏统计模块共同引用
# --------------------------------------------------------------------------


# ------------------------------------------------------------------
# 作者信息预留位（软著申报时可按需修改）
# ------------------------------------------------------------------
AUTHOR_NAME = "【作者姓名预留】"
AUTHOR_ORG = "【作者单位预留】"
SOFTWARE_FULL_NAME = "医院医疗设备绩效综合分析系统"
SOFTWARE_VERSION = "V1.0"
SOFTWARE_CODE = "HME-PERF-ANALYSIS"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "perf_analysis.db")
SCHEMA_FILE = os.path.join(BASE_DIR, "perf_schema.sql")

HOST = "0.0.0.0"
PORT = 5002
DEBUG = True

# ------------------------------------------------------------------
# 绩效评分指标权重体系（第 2 天评分算法的核心输入）
# 五项一级指标权重合计为 1.0
# ------------------------------------------------------------------
SCORE_WEIGHT_MAP = {
    "usage_rate": 0.30,      # 使用效率：开机利用率
    "income_rate": 0.28,     # 经济效益：单机收入贡献
    "maintain_rate": 0.16,   # 维保水平：故障停机与维保达标情况
    "quality_rate": 0.14,    # 质量安全：质控合格率
    "service_rate": 0.12,    # 服务效能：检查人次与服务覆盖
}

# ------------------------------------------------------------------
# 绩效等级判定阈值（第 3 天等级判定模块直接复用）
# ------------------------------------------------------------------
GRADE_RULE_LIST = [
    ("A", 90, 100, "优秀"),
    ("B", 80, 90, "良好"),
    ("C", 70, 80, "合格"),
    ("D", 0, 70, "低效"),
]

# 低效益预警触发线（第 9 天预警模块使用）
LOW_BENEFIT_ALERT_SCORE = 70.0

# 参与考核的科室清单（演示数据）
DEPARTMENT_SEED = [
    "放射科", "检验科", "超声科", "内镜中心", "手术室", "急诊科", "心内科", "骨科"
]


def ensure_data_dir():
    """确保数据目录存在。"""
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR)
    return DATA_DIR


# --------------------------------------------------------------------------
# 内联建表脚本：perf_schema.sql（原 .sql 文件，此处转为字符串常量 PERF_SCHEMA_SQL）
# --------------------------------------------------------------------------
PERF_SCHEMA_SQL = r"""
-- ==================================================================
-- 软件全称：医院医疗设备绩效综合分析系统 V1.0
-- 版本：V1.0
-- 开发日期：2026-10-21（第 1 天 / 共 15 天）
-- 模块功能：绩效数据库初始化脚本，定义科室档案表、设备绩效评分记录表、
--           绩效等级规则表三张基础表，为第 2 天评分算法与第 3 天等级
--           判定模块提供数据底座
-- 依赖包：SQLite3（Python 内置）
-- ==================================================================

PRAGMA foreign_keys = ON;

-- 科室档案表：绩效考核的横向对比主体
CREATE TABLE IF NOT EXISTS pe_department_profile (
    dept_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- 科室主键
    dept_name     TEXT    NOT NULL UNIQUE,            -- 科室名称
    dept_type     TEXT    DEFAULT '临床',             -- 科室类型（临床/医技/门诊）
    director_name TEXT    DEFAULT '',                 -- 科室负责人
    device_amount INTEGER DEFAULT 0,                  -- 在册设备数量
    staff_amount  INTEGER DEFAULT 0,                  -- 科室在岗人数
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 设备绩效评分记录表：存放每台设备每个考核周期的五项指标与综合得分
CREATE TABLE IF NOT EXISTS pe_score_record (
    score_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 评分记录主键
    device_code     TEXT    NOT NULL,                 -- 设备编号
    device_name     TEXT    NOT NULL,                 -- 设备名称
    dept_name       TEXT    NOT NULL,                 -- 所属科室
    period_tag      TEXT    NOT NULL,                 -- 考核周期（如 2026-Q3）
    usage_rate      REAL    DEFAULT 0,                -- 使用效率得分（0-100）
    income_rate     REAL    DEFAULT 0,                -- 经济效益得分（0-100）
    maintain_rate   REAL    DEFAULT 0,                -- 维保水平得分（0-100）
    quality_rate    REAL    DEFAULT 0,                -- 质量安全得分（0-100）
    service_rate    REAL    DEFAULT 0,                -- 服务效能得分（0-100）
    total_score     REAL    DEFAULT 0,                -- 加权综合得分
    grade_level     TEXT    DEFAULT '',               -- 绩效等级 A/B/C/D
    grade_comment   TEXT    DEFAULT '',               -- 等级评语
    is_low_benefit  INTEGER DEFAULT 0,                -- 是否低效益预警（1=是）
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 绩效等级规则表：等级阈值可配置，第 10 天后台管理模块将支持界面维护
CREATE TABLE IF NOT EXISTS pe_grade_rule (
    rule_id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- 规则主键
    grade_level TEXT    NOT NULL UNIQUE,             -- 等级编码
    grade_name  TEXT    NOT NULL,                    -- 等级名称
    min_score   REAL    NOT NULL,                    -- 分数下限（含）
    max_score   REAL    NOT NULL,                    -- 分数上限（不含）
    color_tag   TEXT    DEFAULT '#22d3ee'            -- 图表配色标记
);

CREATE INDEX IF NOT EXISTS idx_score_period ON pe_score_record (period_tag);
CREATE INDEX IF NOT EXISTS idx_score_dept   ON pe_score_record (dept_name);
CREATE INDEX IF NOT EXISTS idx_score_total  ON pe_score_record (total_score);
"""


# --------------------------------------------------------------------------
# 模块 3：perf_store.py
# 模块功能：绩效数据仓库层。封装 SQLite 连接、建表执行、等级规则初始化、 科室档案初始化、大屏汇总统计函数。所有评分与排名业务后续均 基于本层提供的读写接口展开
# --------------------------------------------------------------------------



def open_conn():
    """打开数据库连接并返回可按列名访问的游标结果。"""
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def setup_database():
    """执行建表脚本并初始化等级规则与科室档案。"""
    ensure_data_dir()
    # 单文件版：建表脚本已内联为 PERF_SCHEMA_SQL 常量，无需外部 .sql 文件
    script = PERF_SCHEMA_SQL
    conn = open_conn()
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()
    init_grade_rules()
    init_department_profiles()
    return True


def init_grade_rules():
    """写入 A/B/C/D 四级绩效等级阈值规则，重复调用不会产生重复数据。"""
    color_map = {"A": "#22c55e", "B": "#38bdf8", "C": "#facc15", "D": "#f87171"}
    conn = open_conn()
    try:
        for level, low, high, name in GRADE_RULE_LIST:
            exist = conn.execute(
                "SELECT COUNT(1) AS cnt FROM pe_grade_rule WHERE grade_level=?", (level,)
            ).fetchone()
            if exist["cnt"] == 0:
                conn.execute(
                    "INSERT INTO pe_grade_rule (grade_level, grade_name, min_score, max_score, color_tag) "
                    "VALUES (?,?,?,?,?)",
                    (level, name, float(low), float(high), color_map.get(level, "#22d3ee")),
                )
        conn.commit()
    finally:
        conn.close()


def init_department_profiles():
    """初始化参与绩效考核的科室档案。"""
    conn = open_conn()
    try:
        for dept in DEPARTMENT_SEED:
            exist = conn.execute(
                "SELECT COUNT(1) AS cnt FROM pe_department_profile WHERE dept_name=?", (dept,)
            ).fetchone()
            if exist["cnt"] == 0:
                conn.execute(
                    "INSERT INTO pe_department_profile (dept_name, dept_type) VALUES (?,?)",
                    (dept, "医技" if dept in ("放射科", "检验科", "超声科", "内镜中心") else "临床"),
                )
        conn.commit()
    finally:
        conn.close()


def dict_rows(rows):
    """结果集转字典列表。"""
    return [dict(row) for row in rows]


def count_score_records():
    """统计绩效评分记录总数。"""
    conn = open_conn()
    try:
        row = conn.execute("SELECT COUNT(1) AS cnt FROM pe_score_record").fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def fetch_grade_rules():
    """读取全部绩效等级规则，按等级排序。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT grade_level, grade_name, min_score, max_score, color_tag "
            "FROM pe_grade_rule ORDER BY min_score DESC"
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def fetch_department_names():
    """读取参与考核的科室名称列表。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT dept_name, dept_type, device_amount FROM pe_department_profile ORDER BY dept_id"
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def fetch_dept_score_summary():
    """按科室汇总平均综合得分，用于大屏科室横向对比柱状图。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS device_count, "
            "ROUND(AVG(total_score), 2) AS avg_score, "
            "MAX(total_score) AS max_score, MIN(total_score) AS min_score "
            "FROM pe_score_record GROUP BY dept_name ORDER BY avg_score DESC"
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def fetch_score_ranking(limit=10):
    """读取绩效得分排行榜数据。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT device_code, device_name, dept_name, total_score, grade_level, period_tag "
            "FROM pe_score_record ORDER BY total_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def build_screen_payload():
    """组装绩效大屏首页所需的统计数据。

    第 1 天输出：科室清单、等级规则、平均基准分（暂取演示基准值）。
    第 2 天接入真实评分算法后，平均基准分改由加权计算得出。
    """
    dept_summary = fetch_dept_score_summary()
    if dept_summary:
        avg_all = round(sum(item["avg_score"] for item in dept_summary) / len(dept_summary), 2)
    else:
        avg_all = 82.60
    return {
        "record_total": count_score_records(),
        "dept_total": len(fetch_department_names()),
        "base_score": avg_all,
        "grade_rules": fetch_grade_rules(),
        "dept_summary": dept_summary,
        "ranking_list": fetch_score_ranking(10),
        "department_list": fetch_department_names(),
    }


def seed_demo_scores():
    """写入演示评分记录，保证首日启动大屏有数据可展示。"""
    if count_score_records() > 0:
        return 0
    demo_rows = [
        ("PE-1001", "数字化X射线摄影系统", "放射科", "2026-Q3", 92, 88, 80, 95, 86, 88.9, "B", "良好", 0),
        ("PE-1002", "全自动生化分析仪", "检验科", "2026-Q3", 96, 91, 85, 93, 90, 92.0, "A", "优秀", 0),
        ("PE-1003", "彩色多普勒超声仪", "超声科", "2026-Q3", 89, 84, 78, 90, 88, 86.3, "B", "良好", 0),
        ("PE-1004", "电子胃镜系统", "内镜中心", "2026-Q3", 84, 80, 75, 88, 82, 82.1, "B", "良好", 0),
        ("PE-1005", "便携式监护仪", "急诊科", "2026-Q3", 76, 68, 70, 80, 74, 73.5, "C", "合格", 0),
        ("PE-1006", "高频电刀", "手术室", "2026-Q3", 62, 58, 55, 70, 60, 60.6, "D", "低效", 1),
        ("PE-1007", "动态心电记录仪", "心内科", "2026-Q3", 71, 66, 68, 76, 70, 69.8, "D", "低效", 1),
        ("PE-1008", "C型臂X射线机", "骨科", "2026-Q3", 88, 85, 82, 86, 84, 85.4, "B", "良好", 0),
    ]
    conn = open_conn()
    try:
        conn.executemany(
            "INSERT INTO pe_score_record "
            "(device_code, device_name, dept_name, period_tag, usage_rate, income_rate, "
            "maintain_rate, quality_rate, service_rate, total_score, grade_level, grade_comment, is_low_benefit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            demo_rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(demo_rows)


# --------------------------------------------------------------------------
# 模块 4：perf_score.py
# 模块功能：绩效评分核心算法模块。实现「原始采集值 → 五维指标得分 → 加权 综合得分 → 绩效等级判定 → 低效益标记」的完整计算链路，包含 正向指标、比率指标、逆向指标三类归一化策略，是本系统的核心自研算法
# --------------------------------------------------------------------------


# ------------------------------------------------------------------
# 原始指标采集规则表
#   mode = direct  ：正向指标，原始值本身就是百分制得分（如利用率、合格率）
#   mode = ratio   ：比率指标，原始值 / baseline * 100，超过 100 分按 100 封顶
#   mode = reverse ：逆向指标，原始值越大得分越低（如故障停机时长）
# ------------------------------------------------------------------
RAW_METRIC_RULE = {
    "usage_rate": {"label": "开机利用率", "unit": "%", "baseline": 100.0, "mode": "direct"},
    "income_amount": {"label": "单机收入", "unit": "万元", "baseline": 50.0, "mode": "ratio"},
    "stop_hours": {"label": "故障停机时长", "unit": "小时", "baseline": 40.0, "mode": "reverse"},
    "quality_pass": {"label": "质控合格率", "unit": "%", "baseline": 100.0, "mode": "direct"},
    "service_count": {"label": "年检查人次", "unit": "人次", "baseline": 3000.0, "mode": "ratio"},
}

# 原始指标与五项一级绩效指标的对应关系
RAW_TO_SCORE_FIELD = {
    "usage_rate": "usage_rate",
    "income_amount": "income_rate",
    "stop_hours": "maintain_rate",
    "quality_pass": "quality_rate",
    "service_count": "service_rate",
}


def clamp_score(value):
    """把得分限制在 0-100 区间内，并保留两位小数。"""
    if value < 0:
        value = 0.0
    if value > 100:
        value = 100.0
    return round(float(value), 2)


def normalize_raw_metric(metric_key, raw_value):
    """按规则把单个原始采集值归一化为 0-100 的百分制得分。"""
    rule = RAW_METRIC_RULE.get(metric_key)
    if not rule:
        return 0.0
    try:
        raw = float(raw_value)
    except (TypeError, ValueError):
        return 0.0

    mode = rule["mode"]
    baseline = rule["baseline"]
    if mode == "direct":
        score = raw
    elif mode == "ratio":
        score = 100.0 * raw / baseline if baseline else 0.0
    elif mode == "reverse":
        score = 100.0 - 100.0 * raw / baseline if baseline else 0.0
    else:
        score = 0.0
    return clamp_score(score)


def map_raw_metrics_to_scores(raw_metrics):
    """把一组原始采集值映射为五项一级指标得分字典。

    输入示例：{"usage_rate": 88, "income_amount": 42, "stop_hours": 6,
              "quality_pass": 95, "service_count": 2600}
    输出示例：{"usage_rate": 88.0, "income_rate": 84.0, "maintain_rate": 85.0, ...}
    """
    scores = {}
    for raw_key, score_key in RAW_TO_SCORE_FIELD.items():
        scores[score_key] = normalize_raw_metric(raw_key, raw_metrics.get(raw_key, 0))
    return scores


def calc_weighted_total(scores):
    """按权重体系计算加权综合得分（结果保留两位小数）。"""
    total = 0.0
    for field, weight in SCORE_WEIGHT_MAP.items():
        total += float(scores.get(field, 0.0)) * float(weight)
    return round(total, 2)


def fetch_grade_rule_rows():
    """从数据库读取等级规则（按分数下限降序）。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT grade_level, grade_name, min_score, max_score, color_tag "
            "FROM pe_grade_rule ORDER BY min_score DESC"
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def judge_grade_level(total_score, rule_rows=None):
    """依据等级规则判定绩效等级。

    返回 (等级编码, 等级名称, 等级评语)。若未匹配到规则则回退为 D 级。
    """
    if rule_rows is None:
        rule_rows = fetch_grade_rule_rows()
    for rule in rule_rows:
        if total_score >= rule["min_score"] and total_score < rule["max_score"]:
            return rule["grade_level"], rule["grade_name"], "综合得分 %.2f，评定为%s" % (
                total_score, rule["grade_name"])
    if total_score >= 100:
        top = rule_rows[0] if rule_rows else None
        if top:
            return top["grade_level"], top["grade_name"], "满分表现，评定为%s" % top["grade_name"]
    return "D", "低效", "综合得分 %.2f，低于最低等级线" % total_score


def mark_low_benefit_flag(total_score):
    """判断是否触发低效益预警：得分低于预警线返回 1。"""
    return 1 if total_score < LOW_BENEFIT_ALERT_SCORE else 0


def locate_weak_dimension(scores):
    """找出得分最低的一维指标，作为改进建议的依据。"""
    if not scores:
        return "", ""
    field = min(scores, key=lambda k: scores[k])
    label_map = {
        "usage_rate": "使用效率",
        "income_rate": "经济效益",
        "maintain_rate": "维保水平",
        "quality_rate": "质量安全",
        "service_rate": "服务效能",
    }
    return field, label_map.get(field, field)


def evaluate_single_device(device_code, device_name, dept_name, period_tag, raw_metrics):
    """单台设备绩效评估主流程（不落库，纯计算）。

    返回包含五维得分、综合得分、等级、预警标记与改进建议的结果字典。
    """
    scores = map_raw_metrics_to_scores(raw_metrics)
    total = calc_weighted_total(scores)
    level, level_name, comment = judge_grade_level(total)
    weak_field, weak_label = locate_weak_dimension(scores)
    return {
        "device_code": device_code,
        "device_name": device_name,
        "dept_name": dept_name,
        "period_tag": period_tag,
        "usage_rate": scores["usage_rate"],
        "income_rate": scores["income_rate"],
        "maintain_rate": scores["maintain_rate"],
        "quality_rate": scores["quality_rate"],
        "service_rate": scores["service_rate"],
        "total_score": total,
        "grade_level": level,
        "grade_comment": comment,
        "is_low_benefit": mark_low_benefit_flag(total),
        "weak_dimension": weak_label,
        "advice_text": "建议优先提升「%s」指标（当前 %.2f 分）" % (weak_label, scores.get(weak_field, 0)),
    }


def save_score_result(result):
    """把评估结果写入绩效评分记录表（同设备同周期先删后插，避免重复）。"""
    conn = open_conn()
    try:
        conn.execute(
            "DELETE FROM pe_score_record WHERE device_code=? AND period_tag=?",
            (result["device_code"], result["period_tag"]),
        )
        conn.execute(
            "INSERT INTO pe_score_record "
            "(device_code, device_name, dept_name, period_tag, usage_rate, income_rate, "
            "maintain_rate, quality_rate, service_rate, total_score, grade_level, grade_comment, is_low_benefit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result["device_code"], result["device_name"], result["dept_name"],
                result["period_tag"], result["usage_rate"], result["income_rate"],
                result["maintain_rate"], result["quality_rate"], result["service_rate"],
                result["total_score"], result["grade_level"], result["grade_comment"],
                result["is_low_benefit"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def batch_evaluate_devices(task_list):
    """批量评估并落库。

    task_list 元素结构：
        {"device_code":..., "device_name":..., "dept_name":..., "period_tag":..., "raw":{...}}
    返回评估结果列表。
    """
    rule_rows = fetch_grade_rule_rows()
    results = []
    for task in task_list:
        raw = task.get("raw", {})
        result = evaluate_single_device(
            task.get("device_code", ""),
            task.get("device_name", ""),
            task.get("dept_name", ""),
            task.get("period_tag", ""),
            raw,
        )
        result["grade_level"], result["grade_comment"] = judge_grade_level(
            result["total_score"], rule_rows)[0:2]
        save_score_result(result)
        results.append(result)
    return results


def fetch_score_detail(device_code, period_tag):
    """读取某台设备某周期的评分明细，用于第 7 天单台设备绩效详情页。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pe_score_record WHERE device_code=? AND period_tag=?",
            (device_code, period_tag),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# 模块 5：perf_compare.py
# 模块功能：科室绩效横向对比与绩效等级判定业务层。实现全库评分记录的等级 批量重判（等级、评语、低效益标记刷新）、科室维度横向对比 （平均分、极值、离散度、优秀率、预警台数、排名）、等级分布统计 与五维指标均值对比，输出科室对比柱状图、等级分布环图与 五维雷达图所需的图表数据
# --------------------------------------------------------------------------


# 五维指标的中文标签（与 perf_config 权重字典一一对应）
DIMENSION_LABEL_MAP = {
    "usage_rate": "使用效率",
    "income_rate": "经济效益",
    "maintain_rate": "维保水平",
    "quality_rate": "质量安全",
    "service_rate": "服务效能",
}


def fetch_period_options():
    """读取库中已有的考核周期，供页面周期筛选下拉使用。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT period_tag, COUNT(1) AS record_amount FROM pe_score_record "
            "GROUP BY period_tag ORDER BY period_tag DESC"
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def rejudge_all_grades(period_tag=""):
    """批量重判绩效等级。

    依据最新的 pe_grade_rule 逐条刷新 pe_score_record 的等级编码、等级评语
    与低效益标记，解决规则调整后历史评分等级未同步的问题。
    返回 (总条数, 实际变更条数)。
    """
    rule_rows = fetch_grade_rule_rows()
    conn = open_conn()
    try:
        if period_tag:
            rows = conn.execute(
                "SELECT score_id, total_score FROM pe_score_record WHERE period_tag=?",
                (period_tag,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT score_id, total_score FROM pe_score_record").fetchall()
        records = dict_rows(rows)
        changed = 0
        for item in records:
            level, name, comment = judge_grade_level(item["total_score"], rule_rows)
            low_flag = mark_low_benefit_flag(item["total_score"])
            cur = conn.execute(
                "UPDATE pe_score_record SET grade_level=?, grade_comment=?, is_low_benefit=? "
                "WHERE score_id=? AND (grade_level<>? OR is_low_benefit<>?)",
                (level, comment, low_flag, item["score_id"], level, low_flag),
            )
            changed += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    return len(records), changed


def calc_std_dev(values):
    """计算总体标准差，用于衡量科室内部设备绩效的离散程度。"""
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    return round(math.sqrt(variance), 2)


def build_dept_compare(period_tag=""):
    """按科室横向对比绩效表现。

    输出字段：设备台数、平均综合得分、最高分、最低分、标准差（均衡度）、
    A 级设备占比、低效益预警台数，并按平均分降序给出科室排名。
    """
    conn = open_conn()
    try:
        sql = ("SELECT dept_name, device_code, device_name, total_score, grade_level, "
               "usage_rate, income_rate, maintain_rate, quality_rate, service_rate "
               "FROM pe_score_record")
        params = []
        if period_tag:
            sql += " WHERE period_tag=?"
            params.append(period_tag)
        rows = conn.execute(sql, params).fetchall()
        records = dict_rows(rows)
    finally:
        conn.close()

    dept_map = {}
    for item in records:
        node = dept_map.setdefault(item["dept_name"], {"scores": [], "dims": {}, "low": 0, "grade_a": 0})
        node["scores"].append(item["total_score"])
        node["grade_list"] = node.get("grade_list", []) + [item["grade_level"]]
        if item["grade_level"] == "A":
            node["grade_a"] += 1
        if item["total_score"] < LOW_BENEFIT_ALERT_SCORE:
            node["low"] += 1
        for key in DIMENSION_LABEL_MAP:
            node["dims"][key] = node["dims"].get(key, 0) + float(item[key] or 0)

    result = []
    for dept_name, node in dept_map.items():
        scores = node["scores"]
        amount = len(scores)
        avg_score = round(sum(scores) / amount, 2) if amount else 0.0
        dim_avg = {k: round(v / amount, 2) for k, v in node["dims"].items()} if amount else {}
        result.append({
            "dept_name": dept_name,
            "device_amount": amount,
            "avg_score": avg_score,
            "max_score": round(max(scores), 2) if scores else 0.0,
            "min_score": round(min(scores), 2) if scores else 0.0,
            "std_dev": calc_std_dev(scores),
            "grade_a_rate": round(node["grade_a"] * 100.0 / amount, 1) if amount else 0.0,
            "low_amount": node["low"],
            "dimension_avg": dim_avg,
        })

    result.sort(key=lambda x: x["avg_score"], reverse=True)
    for idx, item in enumerate(result, start=1):
        item["rank_no"] = idx
    return result


def build_grade_distribution(period_tag=""):
    """统计 A/B/C/D 四个等级的设备数量与占比。"""
    rule_rows = fetch_grade_rules()
    color_map = {r["grade_level"]: r["color_tag"] for r in rule_rows}
    name_map = {r["grade_level"]: r["grade_name"] for r in rule_rows}

    conn = open_conn()
    try:
        sql = "SELECT grade_level, COUNT(1) AS amount FROM pe_score_record"
        params = []
        if period_tag:
            sql += " WHERE period_tag=?"
            params.append(period_tag)
        rows = conn.execute(sql + " GROUP BY grade_level", params).fetchall()
        stat = {r["grade_level"]: r["amount"] for r in dict_rows(rows)}
    finally:
        conn.close()

    total = sum(stat.values())
    result = []
    for level in ["A", "B", "C", "D"]:
        amount = stat.get(level, 0)
        result.append({
            "grade_level": level,
            "grade_name": name_map.get(level, level),
            "color_tag": color_map.get(level, "#22d3ee"),
            "amount": amount,
            "rate": round(amount * 100.0 / total, 1) if total else 0.0,
        })
    return {"total": total, "rows": result}


def build_dimension_compare(period_tag=""):
    """统计全院五维指标的平均得分，用于雷达图对比科室短板。"""
    dept_compare = build_dept_compare(period_tag)
    total_devices = sum(item["device_amount"] for item in dept_compare)
    dim_sum = {key: 0.0 for key in DIMENSION_LABEL_MAP}
    for item in dept_compare:
        for key in DIMENSION_LABEL_MAP:
            dim_sum[key] += (item["dimension_avg"].get(key, 0) * item["device_amount"])
    rows = []
    for key, label in DIMENSION_LABEL_MAP.items():
        value = round(dim_sum[key] / total_devices, 2) if total_devices else 0.0
        rows.append({
            "metric_key": key,
            "metric_label": label,
            "weight": SCORE_WEIGHT_MAP.get(key, 0),
            "avg_score": value,
        })
    return rows


def build_compare_payload(period_tag=""):
    """组装科室绩效对比页所需的全部图表与表格数据。"""
    dept_compare = build_dept_compare(period_tag)
    return {
        "period_tag": period_tag or "全部周期",
        "period_options": fetch_period_options(),
        "dept_compare": dept_compare,
        "grade_distribution": build_grade_distribution(period_tag),
        "dimension_compare": build_dimension_compare(period_tag),
        "department_list": fetch_department_names(),
        "alert_score": LOW_BENEFIT_ALERT_SCORE,
        "best_dept": dept_compare[0]["dept_name"] if dept_compare else "",
        "worst_dept": dept_compare[-1]["dept_name"] if dept_compare else "",
    }


# --------------------------------------------------------------------------
# 模块 6：app.py
# 模块功能：Flask 主程序。注册绩效分析大屏、绩效排行榜、绩效评分与科室 绩效对比四类页面路由，启动时完成建表、等级规则初始化、科室 初始化与演示评分数据灌入，并提供评分试算、评分入库、评分明细、 权重元信息、榜单、科室横向对比与等级批量重判接口
# --------------------------------------------------------------------------


app = Flask(__name__)
app.json.ensure_ascii = False


@app.route("/")
def home_entry():
    """根路径跳转到绩效分析大屏。"""
    return redirect(url_for("screen_performance"))


@app.route("/pf/screen")
def screen_performance():
    """绩效综合分析大屏页面（深色大屏风格，第 12 天补充图表联动）。"""
    return render_template(
        "screen_performance.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/pf/ranking")
def page_ranking():
    """绩效排行榜页面占位路由，第 5 天接入完整排行与筛选逻辑。"""
    return render_template(
        "page_ranking.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/pf/score")
def page_score():
    """绩效评分试算与结果展示页面（第 2 天新增）。

    页面提供原始指标录入表单，实时展示五维雷达图、加权得分与等级判定结果。
    """
    return render_template(
        "page_score.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/pf/compare")
def page_compare():
    """科室绩效横向对比页面（第 3 天新增）。

    页面展示科室平均分对比柱状图、等级分布环图、五维指标雷达图与科室对比明细表，
    并提供按考核周期筛选与等级批量重判入口。
    """
    return render_template(
        "page_compare.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


# ==================================================================
# 以下为第 2 天新增：绩效评分核心算法接口
# ==================================================================

@app.route("/api/pf/score/calc", methods=["POST"])
def api_score_calc():
    """单台设备评分试算接口（只算不存）。

    入参：device_code / device_name / dept_name / period_tag / 五项原始指标
    出参：五维得分、加权综合得分、等级、预警标记与改进建议
    """
    payload = request.get_json(silent=True) or request.form.to_dict()
    result = evaluate_single_device(
        payload.get("device_code", "") or "PE-DEMO",
        payload.get("device_name", "") or "试算设备",
        payload.get("dept_name", "") or "未指定科室",
        payload.get("period_tag", "") or "2026-Q3",
        payload,
    )
    result["result"] = "OK"
    return jsonify(result)


@app.route("/api/pf/score/commit", methods=["POST"])
def api_score_commit():
    """评分结果落库接口：支持单条或批量（batch 字段为任务数组）。"""
    payload = request.get_json(silent=True) or {}
    batch = payload.get("batch")
    if batch:
        results = batch_evaluate_devices(batch)
        return jsonify({"result": "OK", "amount": len(results), "rows": results})

    result = evaluate_single_device(
        payload.get("device_code", ""),
        payload.get("device_name", ""),
        payload.get("dept_name", ""),
        payload.get("period_tag", ""),
        payload,
    )
    save_score_result(result)
    return jsonify({"result": "OK", "amount": 1, "rows": [result]})


@app.route("/api/pf/score/detail")
def api_score_detail():
    """查询某台设备某周期的评分明细。"""
    device_code = request.args.get("device_code", "")
    period_tag = request.args.get("period_tag", "2026-Q3")
    detail = fetch_score_detail(device_code, period_tag)
    if not detail:
        return jsonify({"result": "FAIL", "message": "未找到评分记录"})
    return jsonify({"result": "OK", "detail": detail})


@app.route("/api/pf/score/meta")
def api_score_meta():
    """返回评分模型元信息：权重体系、原始指标规则、等级规则。"""
    return jsonify({
        "result": "OK",
        "weights": SCORE_WEIGHT_MAP,
        "raw_rules": RAW_METRIC_RULE,
        "grade_rules": fetch_grade_rules(),
        "record_total": count_score_records(),
    })


@app.route("/api/pf/score/top")
def api_score_top():
    """评分结果排行接口，供评分页右侧榜单组件使用。"""
    limit = int(request.args.get("limit", 10))
    return jsonify({"result": "OK", "rows": fetch_score_ranking(limit)})


@app.route("/api/pf/screen")
def api_screen_data():
    """大屏统计接口：返回评分记录数、科室数、基准分、科室对比与排行榜。"""
    payload = build_screen_payload()
    # 第 3 天新增：大屏叠加等级分布统计，供等级占比图渲染
    payload["grade_distribution"] = build_grade_distribution()
    payload["result"] = "OK"
    return jsonify(payload)


# ==================================================================
# 以下为第 3 天新增：科室绩效对比与等级判定接口
# ==================================================================

@app.route("/api/pf/compare")
def api_dept_compare():
    """科室绩效对比接口：按考核周期返回科室排名、等级分布与五维均值。"""
    payload = build_compare_payload(request.args.get("period_tag", ""))
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/pf/grade/rejudge", methods=["POST"])
def api_grade_rejudge():
    """等级批量重判接口：按最新等级规则刷新全部评分记录的等级与预警标记。"""
    body = request.get_json(silent=True) or {}
    total, changed = rejudge_all_grades((body.get("period_tag") or "").strip())
    return jsonify({
        "result": "OK",
        "success": True,
        "total": total,
        "changed": changed,
        "message": "等级重判完成：共 %d 条，更新 %d 条" % (total, changed),
    })


@app.route("/api/pf/grade-rules")
def api_grade_rules():
    """绩效等级规则接口，供前端图表配色与等级判定提示使用。"""
    return jsonify({"result": "OK", "rules": fetch_grade_rules()})


@app.route("/api/pf/health")
def api_health_check():
    """健康检查接口。"""
    return jsonify({
        "result": "OK",
        "software": SOFTWARE_FULL_NAME,
        "version": SOFTWARE_VERSION,
        "port": PORT,
    })


def bootstrap():
    """启动引导：建表 → 初始化规则与科室 → 灌入演示评分。"""
    setup_database()
    seed_demo_scores()


if __name__ == "__main__":
    bootstrap()
    print("=" * 62)
    print("%s %s 启动中..." % (SOFTWARE_FULL_NAME, SOFTWARE_VERSION))
    print("访问地址： http://127.0.0.1:%d/pf/screen" % PORT)
    print("科室对比： http://127.0.0.1:%d/pf/compare" % PORT)
    print("=" * 62)
    app.run(host=HOST, port=PORT, debug=DEBUG)
