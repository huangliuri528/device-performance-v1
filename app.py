# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-21（第 1 天 / 共 15 天）
# 模块功能：Flask 主程序。注册绩效分析大屏路由与统计接口，启动时完成
#           建表、等级规则初始化、科室初始化与演示评分数据灌入
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）

from flask import Flask, render_template, jsonify, redirect, url_for
from perf_config import HOST, PORT, DEBUG, SOFTWARE_FULL_NAME, SOFTWARE_VERSION
from perf_store import setup_database, seed_demo_scores, build_screen_payload, fetch_grade_rules

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
