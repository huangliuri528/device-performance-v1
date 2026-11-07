# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-24（第 4 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 4 天新增与修改的源代码，不含第 1~3 天已有代码
#           本次迭代主题：ECharts绩效可视化接口模块 + 绩效图表渲染
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 4 天新增业务模块 1：perf_chart.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-24（第 4 天 / 共 15 天）
# 模块功能：ECharts 绩效可视化接口模块。把绩效统计数据转换为 ECharts 可
#           直接消费的 option 结构，涵盖综合评分仪表盘、科室对比柱状图、
#           五维能力雷达图、等级分布环形图、周期趋势折线图、使用率-收益
#           散点图六类图表，供绩效大屏与可视化图表页统一渲染，避免在
#           前端页面中散落图表配置
# 依赖包：无（依赖 perf_store、perf_compare 提供统计结果）

from perf_config import SCORE_WEIGHT_MAP, LOW_BENEFIT_ALERT_SCORE
from perf_store import open_conn, dict_rows
from perf_compare import (
    build_dept_compare,
    build_grade_distribution,
    build_dimension_compare,
    fetch_period_options,
)

# 可视化主题配色：与绩效大屏深蓝霓虹风格保持一致
CHART_COLOR_MAIN = "#38bdf8"
CHART_COLOR_WARN = "#f59e0b"
CHART_COLOR_DANGER = "#f43f5e"
CHART_COLOR_OK = "#22d3ee"
CHART_AXIS_COLOR = "#7c8db5"
CHART_SPLIT_COLOR = "rgba(120,150,200,0.18)"

# 图表键名与中文标题映射，供前端按需取图
CHART_TITLE_MAP = {
    "gauge": "绩效综合评分仪表盘",
    "dept_bar": "科室绩效对比",
    "radar": "五维能力雷达",
    "grade_pie": "绩效等级分布",
    "trend": "考核周期趋势",
    "scatter": "使用率-收益分布",
}


def fetch_period_trend():
    """按考核周期统计平均综合得分与参评台数，用于趋势折线图。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT period_tag, COUNT(1) AS device_amount, "
            "AVG(total_score) AS avg_score, MAX(total_score) AS max_score "
            "FROM pe_score_record GROUP BY period_tag ORDER BY period_tag"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in dict_rows(rows):
        result.append({
            "period_tag": item["period_tag"],
            "device_amount": item["device_amount"],
            "avg_score": round(float(item["avg_score"] or 0), 2),
            "max_score": round(float(item["max_score"] or 0), 2),
        })
    return result


def fetch_scatter_rows(period_tag=""):
    """取每台设备的使用效率得分、经济效益得分与综合得分，用于散点图。"""
    sql = ("SELECT device_name, dept_name, usage_rate, income_rate, "
           "total_score, grade_level FROM pe_score_record")
    params = []
    if period_tag:
        sql += " WHERE period_tag=?"
        params.append(period_tag)
    conn = open_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return dict_rows(rows)


def build_gauge_option(avg_score, dept_amount=0):
    """综合评分仪表盘：以全院平均分指代整体绩效水位。"""
    value = round(float(avg_score or 0), 2)
    return {
        "series": [{
            "type": "gauge",
            "min": 0,
            "max": 100,
            "startAngle": 210,
            "endAngle": -30,
            "radius": "92%",
            "center": ["50%", "58%"],
            "progress": {"show": True, "width": 12},
            "axisLine": {
                "lineStyle": {
                    "width": 12,
                    "color": [
                        [0.6, CHART_COLOR_DANGER],
                        [0.75, CHART_COLOR_WARN],
                        [1, CHART_COLOR_OK],
                    ],
                }
            },
            "pointer": {"width": 5, "length": "62%", "itemStyle": {"color": "#e2e8f0"}},
            "axisTick": {"distance": -14, "length": 5, "lineStyle": {"color": CHART_AXIS_COLOR}},
            "splitLine": {"distance": -16, "length": 10,
                          "lineStyle": {"color": CHART_AXIS_COLOR, "width": 1}},
            "axisLabel": {"distance": -8, "color": CHART_AXIS_COLOR, "fontSize": 10},
            "anchor": {"show": False},
            "title": {"show": True, "offsetCenter": [0, "72%"],
                      "color": "#94a3b8", "fontSize": 12},
            "detail": {
                "valueAnimation": True,
                "offsetCenter": [0, "38%"],
                "fontSize": 26,
                "fontWeight": "bold",
                "color": "#e2e8f0",
                "formatter": "{value}",
            },
            "data": [{"value": value, "name": "综合评分"}],
        }],
        "extra": {"dept_amount": dept_amount, "alert_score": LOW_BENEFIT_ALERT_SCORE},
    }


def build_dept_bar_option(dept_rows):
    """科室绩效对比柱状图：平均分柱 + 预警台数折线 + 全院均值标记线。"""
    names = [r["dept_name"] for r in dept_rows]
    scores = [r["avg_score"] for r in dept_rows]
    lows = [r["low_amount"] for r in dept_rows]
    avg_line = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"data": ["平均得分", "预警台数"], "textStyle": {"color": "#94a3b8"},
                   "top": 2, "itemWidth": 12, "itemHeight": 8},
        "grid": {"left": 46, "right": 42, "top": 30, "bottom": 52},
        "xAxis": {
            "type": "category",
            "data": names,
            "axisLabel": {"color": CHART_AXIS_COLOR, "interval": 0, "rotate": 26, "fontSize": 10},
            "axisLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}},
        },
        "yAxis": [
            {"type": "value", "name": "得分", "max": 100,
             "nameTextStyle": {"color": CHART_AXIS_COLOR},
             "axisLabel": {"color": CHART_AXIS_COLOR},
             "splitLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}}},
            {"type": "value", "name": "预警", "minInterval": 1,
             "nameTextStyle": {"color": CHART_AXIS_COLOR},
             "axisLabel": {"color": CHART_AXIS_COLOR},
             "splitLine": {"show": False}},
        ],
        "series": [
            {
                "name": "平均得分",
                "type": "bar",
                "barWidth": "46%",
                "data": scores,
                "itemStyle": {
                    "color": {
                        "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": "#38bdf8"},
                            {"offset": 1, "color": "#1d4ed8"},
                        ],
                    }
                },
                "markLine": {
                    "symbol": "none",
                    "data": [{"yAxis": avg_line, "label": {
                        "formatter": "全院均值 " + str(avg_line),
                        "color": "#fbbf24", "fontSize": 10}}],
                    "lineStyle": {"color": "#fbbf24", "type": "dashed"},
                },
            },
            {
                "name": "预警台数",
                "type": "line",
                "yAxisIndex": 1,
                "data": lows,
                "smooth": True,
                "symbolSize": 6,
                "lineStyle": {"color": CHART_COLOR_DANGER},
                "itemStyle": {"color": CHART_COLOR_DANGER},
            },
        ],
    }


def build_radar_option(dim_rows):
    """五维能力雷达图：展示全院在五个评分维度上的平均表现。"""
    indicator = [{"name": r["metric_label"], "max": 100,
                  "weight": r.get("weight", 0)} for r in dim_rows]
    values = [r["avg_score"] for r in dim_rows]
    return {
        "tooltip": {"trigger": "item"},
        "radar": {
            "indicator": indicator,
            "radius": "66%",
            "center": ["50%", "56%"],
            "axisName": {"color": "#cbd5e1", "fontSize": 11},
            "splitLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}},
            "splitArea": {"areaStyle": {"color": ["rgba(56,189,248,0.05)",
                                                  "rgba(29,78,216,0.05)"]}},
            "axisLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}},
        },
        "series": [{
            "type": "radar",
            "data": [{
                "value": values,
                "name": "全院五维均值",
                "areaStyle": {"color": "rgba(56,189,248,0.28)"},
                "lineStyle": {"color": CHART_COLOR_MAIN},
                "itemStyle": {"color": CHART_COLOR_MAIN},
            }],
        }],
    }


def build_grade_pie_option(distribution):
    """绩效等级分布环形图，配色取自等级规则表的 color_tag 字段。"""
    rows = distribution.get("rows", [])
    return {
        "tooltip": {"trigger": "item", "formatter": "{b}：{c} 台（{d}%）"},
        "legend": {"bottom": 2, "textStyle": {"color": "#94a3b8", "fontSize": 10},
                   "itemWidth": 10, "itemHeight": 8},
        "series": [{
            "type": "pie",
            "radius": ["46%", "70%"],
            "center": ["50%", "46%"],
            "avoidLabelOverlap": True,
            "itemStyle": {"borderColor": "#0b1730", "borderWidth": 2},
            "label": {"color": "#cbd5e1", "fontSize": 11,
                      "formatter": "{b}\n{c}台"},
            "data": [{"name": r["grade_name"], "value": r["amount"],
                      "itemStyle": {"color": r.get("color_tag", CHART_COLOR_OK)}}
                     for r in rows],
        }],
        "extra": {"total": distribution.get("total", 0)},
    }


def build_trend_option(trend_rows):
    """考核周期趋势折线图：平均分与最高分双线对比。"""
    periods = [r["period_tag"] for r in trend_rows]
    avg_scores = [r["avg_score"] for r in trend_rows]
    max_scores = [r["max_score"] for r in trend_rows]
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["平均得分", "最高得分"],
                   "textStyle": {"color": "#94a3b8"}, "top": 2,
                   "itemWidth": 12, "itemHeight": 8},
        "grid": {"left": 46, "right": 18, "top": 30, "bottom": 34},
        "xAxis": {"type": "category", "data": periods, "boundaryGap": False,
                  "axisLabel": {"color": CHART_AXIS_COLOR},
                  "axisLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}}},
        "yAxis": {"type": "value", "max": 100,
                  "axisLabel": {"color": CHART_AXIS_COLOR},
                  "splitLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}}},
        "series": [
            {"name": "平均得分", "type": "line", "data": avg_scores, "smooth": True,
             "areaStyle": {"color": "rgba(56,189,248,0.18)"},
             "lineStyle": {"color": CHART_COLOR_MAIN},
             "itemStyle": {"color": CHART_COLOR_MAIN}},
            {"name": "最高得分", "type": "line", "data": max_scores, "smooth": True,
             "lineStyle": {"color": CHART_COLOR_WARN},
             "itemStyle": {"color": CHART_COLOR_WARN}},
        ],
    }


def build_scatter_option(scatter_rows):
    """使用率-收益散点图：横轴使用效率得分、纵轴经济效益得分。

    低于预警分的设备以警示色标记，直观定位低效益设备。
    """
    normal_points = []
    alert_points = []
    for item in scatter_rows:
        # 散点以对象形式承载：value 为坐标，name 为设备名，便于 tooltip 取值
        point = {
            "name": item["device_name"],
            "value": [float(item["usage_rate"] or 0),
                      float(item["income_rate"] or 0)],
            "total_score": float(item["total_score"] or 0),
            "dept_name": item["dept_name"],
        }
        if float(item["total_score"] or 0) < LOW_BENEFIT_ALERT_SCORE:
            alert_points.append(point)
        else:
            normal_points.append(point)
    return {
        "tooltip": {"trigger": "item", "formatter": "{b}（{c}）"},
        "grid": {"left": 46, "right": 20, "top": 26, "bottom": 40},
        "xAxis": {"type": "value", "name": "使用效率", "max": 100,
                  "nameTextStyle": {"color": CHART_AXIS_COLOR},
                  "axisLabel": {"color": CHART_AXIS_COLOR},
                  "splitLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}}},
        "yAxis": {"type": "value", "name": "经济效益", "max": 100,
                  "nameTextStyle": {"color": CHART_AXIS_COLOR},
                  "axisLabel": {"color": CHART_AXIS_COLOR},
                  "splitLine": {"lineStyle": {"color": CHART_SPLIT_COLOR}}},
        "series": [
            {"name": "正常设备", "type": "scatter", "data": normal_points,
             "symbolSize": 10, "itemStyle": {"color": CHART_COLOR_MAIN}},
            {"name": "低效益设备", "type": "scatter", "data": alert_points,
             "symbolSize": 12, "itemStyle": {"color": CHART_COLOR_DANGER}},
        ],
    }


def calc_overall_average(period_tag=""):
    """计算全院综合平均分，作为仪表盘指针取值。"""
    sql = "SELECT AVG(total_score) AS avg_score FROM pe_score_record"
    params = []
    if period_tag:
        sql += " WHERE period_tag=?"
        params.append(period_tag)
    conn = open_conn()
    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
    return round(float(row["avg_score"] or 0), 2) if row else 0.0


def build_chart_payload(period_tag=""):
    """一次性组装六类图表的 option，供大屏与图表页统一取数渲染。"""
    dept_rows = build_dept_compare(period_tag)
    distribution = build_grade_distribution(period_tag)
    dim_rows = build_dimension_compare(period_tag)
    trend_rows = fetch_period_trend()
    scatter_rows = fetch_scatter_rows(period_tag)
    avg_score = calc_overall_average(period_tag)

    return {
        "period_tag": period_tag or "全部周期",
        "period_options": fetch_period_options(),
        "overall_score": avg_score,
        "alert_score": LOW_BENEFIT_ALERT_SCORE,
        "weight_map": SCORE_WEIGHT_MAP,
        "charts": {
            "gauge": build_gauge_option(avg_score, len(dept_rows)),
            "dept_bar": build_dept_bar_option(dept_rows),
            "radar": build_radar_option(dim_rows),
            "grade_pie": build_grade_pie_option(distribution),
            "trend": build_trend_option(trend_rows),
            "scatter": build_scatter_option(scatter_rows),
        },
        "chart_titles": CHART_TITLE_MAP,
        "chart_keys": list(CHART_TITLE_MAP.keys()),
    }


def fetch_single_chart(chart_key, period_tag=""):
    """按图表键名单独取一个图表的 option，便于前端按需刷新。"""
    if chart_key == "gauge":
        return build_gauge_option(calc_overall_average(period_tag))
    if chart_key == "dept_bar":
        return build_dept_bar_option(build_dept_compare(period_tag))
    if chart_key == "radar":
        return build_radar_option(build_dimension_compare(period_tag))
    if chart_key == "grade_pie":
        return build_grade_pie_option(build_grade_distribution(period_tag))
    if chart_key == "trend":
        return build_trend_option(fetch_period_trend())
    if chart_key == "scatter":
        return build_scatter_option(fetch_scatter_rows(period_tag))
    return {"result": "FAIL", "message": "不支持的图表类型：%s" % chart_key}

# --------------------------------------------------------------------------
# 第 4 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-24（第 4 天 / 共 15 天）
# 模块功能：Flask 主程序。注册绩效分析大屏、绩效排行榜、绩效评分、科室
#           绩效对比与可视化图表五类页面路由，启动时完成建表、等级规则
#           初始化、科室初始化与演示评分数据灌入，并提供评分试算、评分
#           入库、评分明细、权重元信息、榜单、科室横向对比、等级批量重判
#           与 ECharts 图表 option 接口

from perf_chart import (
    build_chart_payload,
    fetch_single_chart,
    CHART_TITLE_MAP,
)

# ==================================================================
# 以下为第 4 天新增：ECharts 绩效可视化接口
# ==================================================================


@app.route("/pf/charts")
def page_charts():
    """绩效可视化图表页面：集中渲染六类 ECharts 图表（第 4 天）。"""
    return render_template(
        "page_charts.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/charts")
def api_chart_payload():
    """图表 option 总接口：一次返回全部六类图表的 ECharts 配置。"""
    payload = build_chart_payload((request.args.get("period_tag") or "").strip())
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/pf/charts/<chart_key>")
def api_single_chart(chart_key):
    """单图表 option 接口：按图表键名取单个图表配置，便于局部刷新。"""
    option = fetch_single_chart(chart_key,
                                (request.args.get("period_tag") or "").strip())
    failed = "message" in option
    payload = {
        "result": "FAIL" if failed else "OK",
        "chart_key": chart_key,
        "chart_title": CHART_TITLE_MAP.get(chart_key, chart_key),
        "option": {} if failed else option,
    }
    if failed:
        payload["message"] = option["message"]
    return jsonify(payload)



# 【函数内插入】if __name__ == "__main__":
#    print("可视化图表： http://127.0.0.1:%d/pf/charts" % PORT)

# --------------------------------------------------------------------------
# 第 4 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_chart.js             新增 73 行
#   [修改] static/perf_screen.css           新增 66 行
#   [新增] templates/page_charts.html       新增 54 行
#   [修改] templates/screen_base.html       新增 1 行
