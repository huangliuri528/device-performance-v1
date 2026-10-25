# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-25（第 5 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 5 天新增与修改的源代码，不含第 1~4 天已有代码
#           本次迭代主题：绩效排行榜与多条件筛选查询模块 + 排行榜页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 5 天新增业务模块 1：perf_ranking.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-25（第 5 天 / 共 15 天）
# 模块功能：绩效排行榜与多条件筛选查询模块。实现按综合得分与使用效率、
#           经济效益、维保水平、质量安全、服务效能六个维度的 Top 排行，
#           支持科室/等级/周期/分数区间/低效益标记的组合筛选与分页查询，
#           通过与上一考核周期对比计算排名升降变动，并可将当期排行结果
#           归档为排行快照，输出排行条形图、分数区间分布与排名变动分布
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime
from perf_config import LOW_BENEFIT_ALERT_SCORE
from perf_store import open_conn, dict_rows, fetch_grade_rules

# 排行维度字典：键为前端传入的维度编码，值为（维度名称，评分表字段名）
RANK_DIMENSION_MAP = {
    "total": ("综合得分", "total_score"),
    "usage": ("使用效率", "usage_rate"),
    "income": ("经济效益", "income_rate"),
    "maintain": ("维保水平", "maintain_rate"),
    "quality": ("质量安全", "quality_rate"),
    "service": ("服务效能", "service_rate"),
}

# 排序方向字典：desc 为分值由高到低，asc 为分值由低到高（用于定位短板设备）
RANK_ORDER_DICT = {
    "desc": "由高到低",
    "asc": "由低到高",
}

# 分数区间字典：用于统计各分数段的设备分布
SCORE_SECTION_LIST = [
    ("优秀段", 90, 1000),
    ("良好段", 80, 90),
    ("合格段", 70, 80),
    ("低效段", 0, 70),
]

# 排名变动字典：与上期排名对比后的变动方向
RANK_MOVE_DICT = {
    "UP": "上升",
    "FLAT": "持平",
    "DOWN": "下降",
    "NEW": "新增",
}

# 默认分页尺寸
DEFAULT_PAGE_SIZE = 10


def current_period_tag():
    """按当前日期推算默认考核周期，格式 YYYY-Qn。"""
    today = datetime.date.today()
    return "%d-Q%d" % (today.year, (today.month - 1) // 3 + 1)


def previous_period_tag(period_tag):
    """由当前考核周期推算上一周期，跨年时回退到上一年第四季度。"""
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


def judge_grade_level(total_score):
    """按等级规则表把综合得分映射为等级编码与等级名称。"""
    for rule in fetch_grade_rules():
        if float(rule["min_score"]) <= float(total_score) < float(rule["max_score"]):
            return rule["grade_level"], rule["grade_name"], rule["color_tag"]
    return "", "未分级", "#22d3ee"


def fetch_period_rows(period_tag):
    """读取指定考核周期的全部评分记录。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT score_id, device_code, device_name, dept_name, period_tag, "
            "usage_rate, income_rate, maintain_rate, quality_rate, service_rate, "
            "total_score, grade_level, grade_comment, is_low_benefit "
            "FROM pe_score_record WHERE period_tag=? ORDER BY total_score DESC",
            (period_tag,),
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def sort_rows_by_dimension(rows, dim_key, order="desc"):
    """按指定维度排序，返回新列表，不改变入参。"""
    field = RANK_DIMENSION_MAP.get(dim_key, RANK_DIMENSION_MAP["total"])[1]
    reverse = (order or "desc").lower() != "asc"
    return sorted(rows, key=lambda x: float(x.get(field) or 0), reverse=reverse)


def attach_rank_number(rows, dim_key):
    """为排序后的记录补上名次与维度取值，便于表格与图表直接使用。

    名次从 1 开始递增；维度取值单独放在 metric_value 字段，避免前端按
    不同维度切换时重复解析字段名。
    """
    field = RANK_DIMENSION_MAP.get(dim_key, RANK_DIMENSION_MAP["total"])[1]
    result = []
    for index, row in enumerate(rows, 1):
        item = dict(row)
        item["rank_no"] = index
        item["metric_value"] = round(float(row.get(field) or 0), 2)
        item["dim_key"] = dim_key
        item["dim_label"] = RANK_DIMENSION_MAP.get(dim_key, ("综合得分", ""))[0]
        result.append(item)
    return result


def build_previous_rank_map(period_tag, dim_key):
    """构建上一周期同维度的名次映射，用于计算排名升降变动。"""
    prev_tag = previous_period_tag(period_tag)
    if not prev_tag:
        return {}
    prev_rows = attach_rank_number(
        sort_rows_by_dimension(fetch_period_rows(prev_tag), dim_key), dim_key)
    return {row["device_code"]: row["rank_no"] for row in prev_rows}


def attach_rank_move(rows, period_tag, dim_key):
    """为当期排行补上排名变动：上升/下降/持平/新增。"""
    prev_map = build_previous_rank_map(period_tag, dim_key)
    if not prev_map:
        for row in rows:
            row["prev_rank"] = 0
            row["rank_delta"] = 0
            row["move_code"] = "NEW"
            row["move_label"] = RANK_MOVE_DICT["NEW"]
        return rows
    for row in rows:
        prev_rank = prev_map.get(row["device_code"], 0)
        row["prev_rank"] = prev_rank
        if not prev_rank:
            row["rank_delta"] = 0
            row["move_code"] = "NEW"
        else:
            row["rank_delta"] = prev_rank - row["rank_no"]
            if row["rank_delta"] > 0:
                row["move_code"] = "UP"
            elif row["rank_delta"] < 0:
                row["move_code"] = "DOWN"
            else:
                row["move_code"] = "FLAT"
        row["move_label"] = RANK_MOVE_DICT[row["move_code"]]
    return rows


def filter_rank_rows(rows, keyword="", dept_name="", grade_level="",
                     min_score=None, max_score=None, low_flag=""):
    """按关键字、科室、等级、分数区间与低效益标记组合筛选排行记录。"""
    result = rows
    if keyword:
        kw = keyword.strip().lower()
        result = [r for r in result
                  if kw in str(r["device_name"]).lower()
                  or kw in str(r["device_code"]).lower()]
    if dept_name:
        result = [r for r in result if r["dept_name"] == dept_name]
    if grade_level:
        result = [r for r in result if r["grade_level"] == grade_level]
    if min_score is not None:
        result = [r for r in result if float(r["total_score"]) >= float(min_score)]
    if max_score is not None:
        result = [r for r in result if float(r["total_score"]) <= float(max_score)]
    if low_flag in ("1", "0"):
        result = [r for r in result if int(r["is_low_benefit"]) == int(low_flag)]
    return result


def paginate_rows(rows, page=1, size=DEFAULT_PAGE_SIZE):
    """对筛选结果分页，返回 (当前页记录, 总条数, 总页数, 当前页号)。"""
    size = int(size) if int(size) > 0 else DEFAULT_PAGE_SIZE
    total = len(rows)
    total_page = max(1, (total + size - 1) // size)
    page = min(max(1, int(page)), total_page)
    start = (page - 1) * size
    return rows[start:start + size], total, total_page, page


def summarize_rank(rows, filtered_rows):
    """汇总排行统计：参评台数、平均分、最高最低、优秀率与预警台数。"""
    if not rows:
        return {
            "device_total": 0, "avg_score": 0.0, "max_score": 0.0, "min_score": 0.0,
            "excellent_rate": 0.0, "alert_amount": 0, "filter_amount": 0,
            "best_device": "--", "worst_device": "--",
        }
    scores = [float(r["total_score"]) for r in rows]
    excellent = sum(1 for s in scores if s >= 90)
    return {
        "device_total": len(rows),
        "avg_score": round(sum(scores) / len(scores), 2),
        "max_score": round(max(scores), 2),
        "min_score": round(min(scores), 2),
        "excellent_rate": round(excellent / len(rows) * 100, 2),
        "alert_amount": sum(1 for r in rows if int(r["is_low_benefit"]) == 1),
        "filter_amount": len(filtered_rows),
        "best_device": rows[0]["device_name"],
        "worst_device": rows[-1]["device_name"],
        "alert_line": LOW_BENEFIT_ALERT_SCORE,
    }


def build_rank_charts(rows, dim_key):
    """组织排行榜图表：维度 Top10 条形、分数区间分布、排名变动分布。"""
    dim_label = RANK_DIMENSION_MAP.get(dim_key, ("综合得分", ""))[0]
    top_rows = attach_rank_number(
        sort_rows_by_dimension(rows, dim_key), dim_key)[:10]

    section_rows = []
    for name, low, high in SCORE_SECTION_LIST:
        section_rows.append({
            "name": name,
            "value": sum(1 for r in rows if low <= float(r["total_score"]) < high),
        })

    move_rows = []
    for code, label in RANK_MOVE_DICT.items():
        move_rows.append({
            "name": label,
            "code": code,
            "value": sum(1 for r in rows if r.get("move_code") == code),
        })

    dept_bucket = {}
    for row in rows:
        item = dept_bucket.setdefault(row["dept_name"], {
            "dept_name": row["dept_name"], "amount": 0, "score_sum": 0.0})
        item["amount"] += 1
        item["score_sum"] += float(row["total_score"])
    dept_rows = [{
        "dept_name": item["dept_name"],
        "avg_score": round(item["score_sum"] / item["amount"], 2),
        "amount": item["amount"],
    } for item in dept_bucket.values()]
    dept_rows.sort(key=lambda x: x["avg_score"], reverse=True)

    return {
        "dim_label": dim_label,
        "top_rows": [{
            "device_name": r["device_name"],
            "dept_name": r["dept_name"],
            "metric_value": r["metric_value"],
            "grade_level": r["grade_level"],
        } for r in top_rows],
        "score_section": section_rows,
        "move_distribution": move_rows,
        "dept_top": dept_rows[:6],
    }


def build_ranking_payload(period_tag="", dim_key="total", order="desc",
                          keyword="", dept_name="", grade_level="",
                          min_score=None, max_score=None, low_flag="",
                          page=1, size=DEFAULT_PAGE_SIZE):
    """组装排行榜页面完整数据：汇总、图表、分页明细与全部下拉选项。"""
    period = period_tag or current_period_tag()
    base_rows = fetch_period_rows(period)
    if not base_rows:
        periods = fetch_period_options()
        period = periods[0] if periods else period
        base_rows = fetch_period_rows(period)

    sorted_rows = attach_rank_number(sort_rows_by_dimension(base_rows, dim_key, order), dim_key)
    sorted_rows = attach_rank_move(sorted_rows, period, dim_key)
    filtered = filter_rank_rows(sorted_rows, keyword, dept_name, grade_level,
                                min_score, max_score, low_flag)
    page_rows, total, total_page, page = paginate_rows(filtered, page, size)

    return {
        "period_tag": period,
        "dim_key": dim_key if dim_key in RANK_DIMENSION_MAP else "total",
        "order": order if order in RANK_ORDER_DICT else "desc",
        "summary": summarize_rank(sorted_rows, filtered),
        "charts": build_rank_charts(sorted_rows,
                                    dim_key if dim_key in RANK_DIMENSION_MAP else "total"),
        "rows": page_rows,
        "row_amount": total,
        "total_page": total_page,
        "page": page,
        "dim_options": [{"code": k, "label": v[0]} for k, v in RANK_DIMENSION_MAP.items()],
        "order_options": [{"code": k, "label": v} for k, v in RANK_ORDER_DICT.items()],
        "period_options": fetch_period_options(),
        "dept_options": sorted({r["dept_name"] for r in sorted_rows}),
        "grade_options": [{"code": r["grade_level"], "label": r["grade_name"],
                           "color": r["color_tag"]} for r in fetch_grade_rules()],
        "alert_line": LOW_BENEFIT_ALERT_SCORE,
    }


def fetch_period_options():
    """读取库中已有的考核周期清单，按周期倒序返回。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_score_record ORDER BY period_tag DESC"
        ).fetchall()
    finally:
        conn.close()
    return [row["period_tag"] for row in rows if row["period_tag"]]


def archive_rank_snapshot(period_tag, dim_key="total"):
    """把当期某维度的排行结果归档为快照，支撑历史排名追溯。"""
    rows = attach_rank_number(
        sort_rows_by_dimension(fetch_period_rows(period_tag), dim_key), dim_key)
    if not rows:
        return {"success": False, "message": "该考核周期没有可归档的评分记录"}
    conn = open_conn()
    try:
        conn.execute("DELETE FROM pe_rank_snapshot WHERE period_tag=? AND dim_key=?",
                     (period_tag, dim_key))
        conn.executemany(
            "INSERT INTO pe_rank_snapshot (period_tag, dim_key, dim_label, device_code, "
            "device_name, dept_name, rank_no, metric_value, total_score, grade_level) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(period_tag, dim_key, RANK_DIMENSION_MAP.get(dim_key, ("综合得分", ""))[0],
              r["device_code"], r["device_name"], r["dept_name"], r["rank_no"],
              r["metric_value"], r["total_score"], r["grade_level"]) for r in rows],
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "message": "已归档 %s 的%s排行快照 %d 条"
                   % (period_tag, RANK_DIMENSION_MAP.get(dim_key, ("综合得分", ""))[0], len(rows)),
        "amount": len(rows),
    }


def fetch_rank_snapshots(limit=10):
    """查询最近归档的排行快照记录。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT period_tag, dim_key, dim_label, device_code, device_name, dept_name, "
            "rank_no, metric_value, total_score, grade_level, snapshot_time "
            "FROM pe_rank_snapshot ORDER BY rank_id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def seed_demo_previous_period():
    """灌入上一考核周期的演示评分数据，用于排名升降变动对比。

    以上一周期为基准，对当期记录做小幅扰动生成历史分数，使排行榜同时
    存在上升、下降与持平三种变动样本。
    """
    periods = fetch_period_options()
    if not periods:
        return 0
    current_tag = periods[0]
    prev_tag = previous_period_tag(current_tag)
    if not prev_tag:
        return 0
    rows = fetch_period_rows(current_tag)
    if not rows:
        return 0
    conn = open_conn()
    try:
        exist = conn.execute(
            "SELECT COUNT(1) AS cnt FROM pe_score_record WHERE period_tag=?",
            (prev_tag,),
        ).fetchone()["cnt"]
        if exist > 0:
            return 0
        demo_rows = []
        for index, row in enumerate(rows):
            delta = 4.5 if index % 3 == 0 else (-3.8 if index % 3 == 1 else 0.0)
            total = max(min(round(float(row["total_score"]) + delta, 2), 100.0), 0.0)
            level, name, _ = judge_grade_level(total)
            dim_scale = 1 + (delta / 100.0)
            demo_rows.append((
                row["device_code"], row["device_name"], row["dept_name"], prev_tag,
                round(float(row["usage_rate"]) * dim_scale, 2),
                round(float(row["income_rate"]) * dim_scale, 2),
                round(float(row["maintain_rate"]) * dim_scale, 2),
                round(float(row["quality_rate"]) * dim_scale, 2),
                round(float(row["service_rate"]) * dim_scale, 2),
                total, level, name,
                1 if total < LOW_BENEFIT_ALERT_SCORE else 0,
            ))
        conn.executemany(
            "INSERT INTO pe_score_record (device_code, device_name, dept_name, period_tag, "
            "usage_rate, income_rate, maintain_rate, quality_rate, service_rate, "
            "total_score, grade_level, grade_comment, is_low_benefit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", demo_rows)
        conn.commit()
        amount = len(demo_rows)
    finally:
        conn.close()
    return amount

# --------------------------------------------------------------------------
# 第 5 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY5_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY5_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 5 天新增：绩效排行快照表
-- ==================================================================
-- 排行快照表：归档每个考核周期在各排行维度下的名次结果，使管理员可以
-- 回溯「某设备在某一期、某一维度上的历史排名」，避免重算带来的口径漂移
CREATE TABLE IF NOT EXISTS pe_rank_snapshot (
    rank_id       INTEGER PRIMARY KEY AUTOINCREMENT,   -- 快照主键
    period_tag    TEXT    NOT NULL,                    -- 考核周期
    dim_key       TEXT    NOT NULL,                    -- 排行维度编码
    dim_label     TEXT    DEFAULT '',                  -- 排行维度名称
    device_code   TEXT    NOT NULL,                    -- 设备编号
    device_name   TEXT    DEFAULT '',                  -- 设备名称
    dept_name     TEXT    DEFAULT '',                  -- 所属科室
    rank_no       INTEGER DEFAULT 0,                   -- 当期名次
    metric_value  REAL    DEFAULT 0,                   -- 该维度取值
    total_score   REAL    DEFAULT 0,                   -- 综合得分
    grade_level   TEXT    DEFAULT '',                  -- 绩效等级
    snapshot_time TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_rank_period ON pe_rank_snapshot (period_tag);
CREATE INDEX IF NOT EXISTS idx_rank_dim    ON pe_rank_snapshot (dim_key);
CREATE INDEX IF NOT EXISTS idx_rank_device ON pe_rank_snapshot (device_code);
"""


def apply_day5_schema(conn):
    """把第 5 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY5_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 5 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-25（第 5 天 / 共 15 天）
#           入库、评分明细、权重元信息、榜单、科室横向对比、等级批量重判、
#           ECharts 图表 option 与多维度排行筛选查询接口

from perf_ranking import (
    build_ranking_payload,
    archive_rank_snapshot,
    fetch_rank_snapshots,
    seed_demo_previous_period,
    RANK_DIMENSION_MAP,
)

# 【函数内插入】def screen_performance():
#    """绩效排行榜页面（第 5 天接入多维度排行与组合筛选查询）。
#
#    页面支持按综合得分与五项一级指标切换排行维度，展示 Top10 条形图、
#    分数区间分布、排名升降变动与科室平均分 Top6，并可按科室、等级、
#    分数区间与低效益标记组合筛选后分页查看明细。
#    """

# ==================================================================
# 以下为第 5 天新增：绩效排行榜与多条件筛选查询接口
# ==================================================================


@app.route("/api/pf/ranking")
def api_perf_ranking():
    """排行榜数据接口：按维度排行并返回汇总、图表与分页明细。"""
    min_score = request.args.get("min_score") or ""
    max_score = request.args.get("max_score") or ""
    payload = build_ranking_payload(
        period_tag=(request.args.get("period_tag") or "").strip(),
        dim_key=(request.args.get("dim_key") or "total").strip(),
        order=(request.args.get("order") or "desc").strip(),
        keyword=(request.args.get("keyword") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        grade_level=(request.args.get("grade_level") or "").strip(),
        min_score=float(min_score) if min_score else None,
        max_score=float(max_score) if max_score else None,
        low_flag=(request.args.get("low_flag") or "").strip(),
        page=int(request.args.get("page", 1)),
        size=int(request.args.get("size", 10)),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/pf/ranking/archive", methods=["POST"])
def api_rank_archive():
    """排行快照归档接口：把当期某维度的名次结果写入快照表。"""
    body = request.get_json(silent=True) or {}
    outcome = archive_rank_snapshot(
        (body.get("period_tag") or "").strip(),
        (body.get("dim_key") or "total").strip(),
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/pf/ranking/snapshots")
def api_rank_snapshots():
    """排行快照查询接口：返回最近归档的名次记录。"""
    return jsonify({
        "result": "OK",
        "rows": fetch_rank_snapshots(int(request.args.get("limit", 10))),
        "dim_options": [{"code": k, "label": v[0]} for k, v in RANK_DIMENSION_MAP.items()],
    })



# 【函数内插入】def api_health_check():
#    """启动引导：建表 → 初始化规则与科室 → 灌入演示评分与上期对照数据。"""
#    seed_demo_previous_period()

# 【函数内插入】if __name__ == "__main__":
#    print("绩效排行： http://127.0.0.1:%d/pf/ranking" % PORT)

# --------------------------------------------------------------------------
# 第 5 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_ranking.js           新增 316 行
#   [修改] static/perf_screen.css           新增 41 行
#   [修改] templates/page_ranking.html      新增 108 行
