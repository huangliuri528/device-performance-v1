# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-22（第 2 天 / 共 15 天）
# 模块功能：Flask 主程序。注册绩效分析大屏、绩效排行榜与绩效评分页面路由，
#           启动时完成建表、等级规则初始化、科室初始化与演示评分数据灌入，
#           并提供评分试算、评分入库、评分明细、权重元信息与榜单接口
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）

from flask import Flask, render_template, jsonify, redirect, url_for, request
from perf_config import HOST, PORT, DEBUG, SOFTWARE_FULL_NAME, SOFTWARE_VERSION, SCORE_WEIGHT_MAP
from perf_store import (
    setup_database,
    seed_demo_scores,
    build_screen_payload,
    fetch_grade_rules,
    fetch_score_ranking,
    count_score_records,
)
from perf_score import (
    evaluate_single_device,
    batch_evaluate_devices,
    fetch_score_detail,
    RAW_METRIC_RULE,
)

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
    from perf_score import save_score_result
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
    payload["result"] = "OK"
    return jsonify(payload)


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
    print("=" * 62)
    app.run(host=HOST, port=PORT, debug=DEBUG)
