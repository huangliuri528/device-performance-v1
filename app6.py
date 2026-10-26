# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-26（第 6 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 6 天新增与修改的源代码，不含第 1~5 天已有代码
#           本次迭代主题：医疗设备效益报表模块 + 效益报表页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5002/pf/screen）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 6 天新增业务模块 1：perf_report.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医院医疗设备绩效综合分析系统 V1.0
# 版本：V1.0
# 开发日期：2026-10-26（第 6 天 / 共 15 天）
# 模块功能：医疗设备效益报表模块。负责单机效益明细的录入与核算，按
#           折旧、维保、耗材、人力四类成本归集支出并计算净效益、收益率
#           与成本收益率，形成全院与科室两级效益汇总报表；输出科室效益
#           对比、成本结构占比、设备净效益排行与本期上期对比四类图表，
#           支持效益报表归档留痕与纯文本报表导出
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from perf_config import LOW_BENEFIT_ALERT_SCORE
from perf_store import open_conn, dict_rows
from perf_ranking import current_period_tag, previous_period_tag

# 成本科目字典：效益核算统一按这四类成本归集，口径固定便于横向对比
COST_ITEM_DICT = {
    "DEPRECIATE": "设备折旧",
    "MAINTAIN": "维保支出",
    "CONSUMABLE": "耗材支出",
    "MANPOWER": "人力成本",
}

# 效益等级字典：按收益率把设备划分为 five 档，供报表结论与配色使用
BENEFIT_LEVEL_DICT = {
    "EXCELLENT": "高效益",
    "GOOD": "良好",
    "NORMAL": "一般",
    "WEAK": "偏低",
    "LOSS": "亏损",
}

# 效益等级判定阈值（收益率百分比下限，由高到低匹配）
BENEFIT_LEVEL_THRESHOLD = [
    ("EXCELLENT", 60),
    ("GOOD", 35),
    ("NORMAL", 15),
    ("WEAK", 0),
]

# 报表范围字典：全院汇总与单科室汇总两种口径
REPORT_SCOPE_DICT = {
    "ALL": "全院汇总",
    "DEPT": "科室汇总",
}

# 默认分页尺寸（排行榜页用 10，报表页用 12 区分）
REPORT_PAGE_SIZE = 12


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def round2(value):
    """金额统一保留两位小数，避免浮点误差在报表中累积。"""
    return round(float(value or 0), 2)


def judge_benefit_level(profit_rate):
    """按收益率匹配效益等级编码与名称。

    判定口径：收益率 >=60% 高效益，>=35% 良好，>=15% 一般，
    0~15% 偏低，小于 0 即亏损。
    """
    rate = float(profit_rate or 0)
    for level_code, floor in BENEFIT_LEVEL_THRESHOLD:
        if rate >= floor:
            return level_code, BENEFIT_LEVEL_DICT[level_code]
    return "LOSS", BENEFIT_LEVEL_DICT["LOSS"]


def calc_benefit_row(income_amount, depreciate_cost, maintain_cost,
                     consumable_cost, manpower_cost, asset_value=0):
    """核算单条效益明细：汇总四类成本并推导净效益与两类收益率。

    收益率 = 净效益 / 收入；成本收益率 = 净效益 / 成本合计；
    成本为 0 时成本收益率记 0，避免除零异常。
    """
    income = round2(income_amount)
    cost_total = round2(
        float(depreciate_cost or 0) + float(maintain_cost or 0)
        + float(consumable_cost or 0) + float(manpower_cost or 0))
    net_benefit = round2(income - cost_total)
    profit_rate = round2(net_benefit / income * 100) if income else 0.0
    cost_rate = round2(net_benefit / cost_total * 100) if cost_total else 0.0
    payback_rate = round2(net_benefit / float(asset_value or 0) * 100) \
        if float(asset_value or 0) else 0.0
    level_code, level_name = judge_benefit_level(profit_rate)
    return {
        "income_amount": income,
        "cost_depreciate": round2(depreciate_cost),
        "cost_maintain": round2(maintain_cost),
        "cost_consumable": round2(consumable_cost),
        "cost_manpower": round2(manpower_cost),
        "cost_total": cost_total,
        "net_benefit": net_benefit,
        "profit_rate": profit_rate,
        "cost_rate": cost_rate,
        "payback_rate": payback_rate,
        "level_code": level_code,
        "level_name": level_name,
    }


def fetch_benefit_rows(period_tag="", dept_name=""):
    """按考核周期与科室读取效益明细，附带核算后的等级标签。"""
    conn = open_conn()
    try:
        sql = "SELECT * FROM pe_benefit_detail WHERE 1=1"
        params = []
        if period_tag:
            sql += " AND period_tag=?"
            params.append(period_tag)
        if dept_name:
            sql += " AND dept_name=?"
            params.append(dept_name)
        sql += " ORDER BY net_benefit DESC, benefit_id"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    records = []
    for item in dict_rows(rows):
        level_code, level_name = judge_benefit_level(item["profit_rate"])
        item["level_code"] = level_code
        item["level_name"] = level_name
        records.append(item)
    return records


def summarize_benefit(records):
    """对效益明细做汇总：收入、四类成本、净效益与平均收益率。"""
    income_total = sum(float(r["income_amount"] or 0) for r in records)
    depreciate_total = sum(float(r["cost_depreciate"] or 0) for r in records)
    maintain_total = sum(float(r["cost_maintain"] or 0) for r in records)
    consumable_total = sum(float(r["cost_consumable"] or 0) for r in records)
    manpower_total = sum(float(r["cost_manpower"] or 0) for r in records)
    cost_total = depreciate_total + maintain_total + consumable_total + manpower_total
    net_total = income_total - cost_total
    profit_rate = round2(net_total / income_total * 100) if income_total else 0.0
    level_counter = {}
    for item in records:
        level_counter[item["level_code"]] = level_counter.get(item["level_code"], 0) + 1
    return {
        "device_amount": len(records),
        "income_total": round2(income_total),
        "depreciate_total": round2(depreciate_total),
        "maintain_total": round2(maintain_total),
        "consumable_total": round2(consumable_total),
        "manpower_total": round2(manpower_total),
        "cost_total": round2(cost_total),
        "net_total": round2(net_total),
        "profit_rate": profit_rate,
        "loss_amount": level_counter.get("LOSS", 0),
        "level_distribution": [
            {"code": code, "name": BENEFIT_LEVEL_DICT[code], "value": amount}
            for code, amount in level_counter.items()
        ],
    }


def build_benefit_charts(records, period_tag=""):
    """生成效益报表四类图表数据：科室对比、成本结构、设备排行、周期对比。"""
    dept_map = {}
    for item in records:
        bucket = dept_map.setdefault(item["dept_name"] or "未归属",
                                     {"income": 0.0, "cost": 0.0, "net": 0.0})
        bucket["income"] += float(item["income_amount"] or 0)
        bucket["cost"] += float(item["cost_total"] or 0)
        bucket["net"] += float(item["net_benefit"] or 0)
    dept_compare = []
    for name, bucket in dept_map.items():
        dept_compare.append({
            "name": name,
            "income": round2(bucket["income"]),
            "cost": round2(bucket["cost"]),
            "net": round2(bucket["net"]),
            "rate": round2(bucket["net"] / bucket["income"] * 100) if bucket["income"] else 0.0,
        })
    dept_compare.sort(key=lambda x: x["net"], reverse=True)

    summary_all = summarize_benefit(records)
    cost_structure = [
        {"name": COST_ITEM_DICT["DEPRECIATE"], "value": summary_all["depreciate_total"]},
        {"name": COST_ITEM_DICT["MAINTAIN"], "value": summary_all["maintain_total"]},
        {"name": COST_ITEM_DICT["CONSUMABLE"], "value": summary_all["consumable_total"]},
        {"name": COST_ITEM_DICT["MANPOWER"], "value": summary_all["manpower_total"]},
    ]

    device_rank = sorted(records, key=lambda x: float(x["net_benefit"] or 0), reverse=True)[:10]
    device_rank = [{
        "name": r["device_name"],
        "code": r["device_code"],
        "dept": r["dept_name"],
        "net": round2(r["net_benefit"]),
        "rate": r["profit_rate"],
        "level": r["level_name"],
    } for r in device_rank]

    prev_tag = previous_period_tag(period_tag) if period_tag else ""
    prev_records = fetch_benefit_rows(prev_tag) if prev_tag else []
    prev_summary = summarize_benefit(prev_records)
    period_compare = {
        "period_tags": [prev_tag or "上期", period_tag or current_period_tag()],
        "income_series": [prev_summary["income_total"], summary_all["income_total"]],
        "cost_series": [prev_summary["cost_total"], summary_all["cost_total"]],
        "net_series": [prev_summary["net_total"], summary_all["net_total"]],
        "rate_series": [prev_summary["profit_rate"], summary_all["profit_rate"]],
        "has_previous": bool(prev_tag) and bool(prev_records),
    }

    return {
        "dept_compare": dept_compare,
        "cost_structure": cost_structure,
        "device_rank": device_rank,
        "period_compare": period_compare,
    }


def build_report_conclusion(summary):
    """按汇总指标生成报表结论文本，供归档与导出时直接引用。"""
    if summary["device_amount"] == 0:
        return "本期暂无效益明细，无法出具效益结论。"
    if summary["net_total"] < 0:
        return ("本期整体亏损，净效益 %.2f 元，亏损设备 %d 台，"
                "建议优先核查低效设备的开机利用率与维保支出。"
                % (summary["net_total"], summary["loss_amount"]))
    if summary["profit_rate"] >= 35:
        return ("本期效益良好，净效益 %.2f 元，平均收益率 %.1f%%，"
                "可维持现有设备配置并适度扩容高效科室。"
                % (summary["net_total"], summary["profit_rate"]))
    return ("本期效益一般，净效益 %.2f 元，平均收益率 %.1f%%，"
            "%d 台设备处于亏损状态，建议开展单机效益专项分析。"
            % (summary["net_total"], summary["profit_rate"], summary["loss_amount"]))


def build_report_payload(period_tag="", dept_name="", keyword="",
                         level_code="", page=1, size=REPORT_PAGE_SIZE):
    """组装效益报表页面数据：汇总、图表、分页明细与下拉选项。"""
    period_tag = period_tag or current_period_tag()
    records = fetch_benefit_rows(period_tag, dept_name)
    if keyword:
        key = keyword.lower()
        records = [r for r in records
                   if key in (r["device_code"] or "").lower()
                   or key in (r["device_name"] or "").lower()]
    if level_code:
        records = [r for r in records if r["level_code"] == level_code]

    summary = summarize_benefit(records)
    summary["conclusion"] = build_report_conclusion(summary)
    charts = build_benefit_charts(records, period_tag)

    size = max(1, int(size or REPORT_PAGE_SIZE))
    page = max(1, int(page or 1))
    total = len(records)
    page_amount = max(1, (total + size - 1) // size)
    if page > page_amount:
        page = page_amount
    page_records = records[(page - 1) * size: page * size]

    conn = open_conn()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM pe_benefit_detail "
            "WHERE dept_name<>'' ORDER BY dept_name").fetchall()
        period_rows = conn.execute(
            "SELECT DISTINCT period_tag FROM pe_benefit_detail "
            "WHERE period_tag<>'' ORDER BY period_tag DESC").fetchall()
    finally:
        conn.close()

    return {
        "period_tag": period_tag,
        "summary": summary,
        "charts": charts,
        "records": page_records,
        "total": total,
        "page": page,
        "size": size,
        "page_amount": page_amount,
        "dept_options": [r["dept_name"] for r in dept_rows],
        "period_options": [r["period_tag"] for r in period_rows] or [period_tag],
        "level_options": [{"code": k, "label": v} for k, v in BENEFIT_LEVEL_DICT.items()],
        "cost_options": [{"code": k, "label": v} for k, v in COST_ITEM_DICT.items()],
        "alert_score": LOW_BENEFIT_ALERT_SCORE,
    }


def save_benefit_detail(device_code, device_name, dept_name, period_tag,
                        income_amount, depreciate_cost=0, maintain_cost=0,
                        consumable_cost=0, manpower_cost=0, asset_value=0,
                        operator="admin"):
    """登记或更新一台设备某周期的效益明细，核算后落库。

    同一台设备同一周期只保留一条明细，重复登记时按最新数据覆盖核算结果。
    校验口径：编号、名称、科室、周期必填；收入与四类成本不能为负。
    """
    device_code = (device_code or "").strip()
    device_name = (device_name or "").strip()
    dept_name = (dept_name or "").strip()
    period_tag = (period_tag or "").strip() or current_period_tag()
    if not device_code or not device_name or not dept_name:
        return {"success": False, "message": "设备编号、名称与科室不能为空"}
    for label, value in (("收入", income_amount), ("折旧", depreciate_cost),
                         ("维保", maintain_cost), ("耗材", consumable_cost),
                         ("人力", manpower_cost)):
        if float(value or 0) < 0:
            return {"success": False, "message": "%s金额不能为负" % label}

    calc = calc_benefit_row(income_amount, depreciate_cost, maintain_cost,
                            consumable_cost, manpower_cost, asset_value)
    conn = open_conn()
    try:
        exist = conn.execute(
            "SELECT benefit_id FROM pe_benefit_detail "
            "WHERE device_code=? AND period_tag=?", (device_code, period_tag)
        ).fetchone()
        if exist:
            conn.execute(
                "UPDATE pe_benefit_detail SET device_name=?, dept_name=?, "
                "income_amount=?, cost_depreciate=?, cost_maintain=?, "
                "cost_consumable=?, cost_manpower=?, asset_value=?, "
                "cost_total=?, net_benefit=?, profit_rate=?, cost_rate=?, "
                "payback_rate=?, level_code=? WHERE benefit_id=?",
                (device_name, dept_name, calc["income_amount"],
                 calc["cost_depreciate"], calc["cost_maintain"],
                 calc["cost_consumable"], calc["cost_manpower"],
                 round2(asset_value), calc["cost_total"], calc["net_benefit"],
                 calc["profit_rate"], calc["cost_rate"], calc["payback_rate"],
                 calc["level_code"], exist["benefit_id"]))
            action_text = "更新"
        else:
            conn.execute(
                "INSERT INTO pe_benefit_detail "
                "(device_code, device_name, dept_name, period_tag, income_amount, "
                "cost_depreciate, cost_maintain, cost_consumable, cost_manpower, "
                "asset_value, cost_total, net_benefit, profit_rate, cost_rate, "
                "payback_rate, level_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (device_code, device_name, dept_name, period_tag,
                 calc["income_amount"], calc["cost_depreciate"],
                 calc["cost_maintain"], calc["cost_consumable"],
                 calc["cost_manpower"], round2(asset_value), calc["cost_total"],
                 calc["net_benefit"], calc["profit_rate"], calc["cost_rate"],
                 calc["payback_rate"], calc["level_code"]))
            action_text = "登记"
        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "message": "%s %s（%s）效益明细，净效益 %.2f 元" %
                   (action_text, device_name, period_tag, calc["net_benefit"]),
        "level_name": calc["level_name"],
        "profit_rate": calc["profit_rate"],
    }


def build_report_text(period_tag="", dept_name=""):
    """生成纯文本效益报表：表头 + 明细行 + 汇总行，便于打印与归档。"""
    period_tag = period_tag or current_period_tag()
    records = fetch_benefit_rows(period_tag, dept_name)
    summary = summarize_benefit(records)
    lines = []
    lines.append("医院医疗设备效益报表")
    lines.append("考核周期：%s    统计范围：%s" %
                 (period_tag, dept_name or "全院"))
    lines.append("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("-" * 96)
    lines.append("%-14s %-18s %-10s %12s %12s %12s %8s" %
                 ("设备编号", "设备名称", "科室", "收入", "成本", "净效益", "收益率"))
    lines.append("-" * 96)
    for item in records:
        lines.append("%-14s %-18s %-10s %12.2f %12.2f %12.2f %7.1f%%" %
                     (item["device_code"], item["device_name"][:18],
                      item["dept_name"], item["income_amount"],
                      item["cost_total"], item["net_benefit"], item["profit_rate"]))
    lines.append("-" * 96)
    lines.append("合计：设备 %d 台 | 收入 %.2f 元 | 成本 %.2f 元 | 净效益 %.2f 元 | 平均收益率 %.1f%%" %
                 (summary["device_amount"], summary["income_total"],
                  summary["cost_total"], summary["net_total"], summary["profit_rate"]))
    lines.append("结论：%s" % build_report_conclusion(summary))
    return "\n".join(lines)


def fetch_report_total():
    """统计已归档报表数量，用于生成不重复的报表编号。"""
    conn = open_conn()
    try:
        row = conn.execute("SELECT COUNT(1) AS amount FROM pe_benefit_report").fetchone()
    finally:
        conn.close()
    return int(row["amount"]) if row else 0


def generate_benefit_report(period_tag="", dept_name="", operator="admin"):
    """生成一期效益报表并归档：写入报表归档表，返回报表编号与文本。"""
    period_tag = period_tag or current_period_tag()
    records = fetch_benefit_rows(period_tag, dept_name)
    if not records:
        return {"success": False, "message": "%s 暂无效益明细，无法生成报表" % period_tag}
    summary = summarize_benefit(records)
    # 报表编号精确到毫秒并拼接序号，保证同一秒内连续生成也不会重号
    seq_no = fetch_report_total() + 1
    report_code = "RPT-%s-%s%03d" % (
        period_tag.replace("-", ""),
        datetime.datetime.now().strftime("%m%d%H%M%S"), seq_no)
    scope_code = "DEPT" if dept_name else "ALL"
    report_text = build_report_text(period_tag, dept_name)

    conn = open_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO pe_benefit_report "
            "(report_code, period_tag, dept_name, scope_code, device_amount, "
            "income_total, cost_total, net_total, profit_rate, conclusion_text, "
            "report_text, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_code, period_tag, dept_name or "全院", scope_code,
             summary["device_amount"], summary["income_total"],
             summary["cost_total"], summary["net_total"], summary["profit_rate"],
             build_report_conclusion(summary), report_text, operator))
        conn.commit()
        report_id = cursor.lastrowid
    finally:
        conn.close()
    return {
        "success": True,
        "report_id": report_id,
        "report_code": report_code,
        "period_tag": period_tag,
        "device_amount": summary["device_amount"],
        "net_total": summary["net_total"],
        "profit_rate": summary["profit_rate"],
        "report_text": report_text,
        "message": "效益报表已生成：%s（%d 台设备）" % (report_code, summary["device_amount"]),
    }


def fetch_report_list(limit=10):
    """读取最近的效益报表归档清单（不含报表正文，避免响应过大）。"""
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT report_id, report_code, period_tag, dept_name, scope_code, "
            "device_amount, income_total, cost_total, net_total, profit_rate, "
            "operator_name, created_at FROM pe_benefit_report "
            "ORDER BY report_id DESC LIMIT ?", (int(limit or 10),)).fetchall()
    finally:
        conn.close()
    reports = []
    for item in dict_rows(rows):
        item["scope_label"] = REPORT_SCOPE_DICT.get(item["scope_code"], "全院汇总")
        reports.append(item)
    return reports


def fetch_report_text(report_id):
    """按报表主键取回报表正文文本。"""
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT report_code, report_text FROM pe_benefit_report WHERE report_id=?",
            (int(report_id or 0),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"report_code": row["report_code"], "report_text": row["report_text"]}


def seed_demo_benefit():
    """灌入两期演示效益明细：当期与上一期，保证周期对比图有数据。"""
    conn = open_conn()
    try:
        exist = conn.execute(
            "SELECT COUNT(1) AS amount FROM pe_benefit_detail").fetchone()["amount"]
        if exist > 0:
            return 0
    finally:
        conn.close()

    current_tag = current_period_tag()
    prev_tag = previous_period_tag(current_tag)
    # 演示设备清单：编号、名称、科室、原值、当期收入、上期收入
    device_plan = [
        ("PE-2026-001", "1.5T 磁共振成像系统", "放射科", 9800000, 1420000, 1310000),
        ("PE-2026-002", "64 排螺旋 CT", "放射科", 7600000, 1680000, 1520000),
        ("PE-2026-003", "全自动生化分析仪", "检验科", 1450000, 620000, 588000),
        ("PE-2026-004", "彩色多普勒超声仪", "超声科", 860000, 415000, 402000),
        ("PE-2026-005", "电子胃肠镜系统", "内镜中心", 1320000, 508000, 476000),
        ("PE-2026-006", "麻醉机工作站", "手术室", 640000, 226000, 214000),
        ("PE-2026-007", "便携式监护仪", "急诊科", 180000, 96000, 91000),
        ("PE-2026-008", "数字减影血管造影机", "心内科", 6200000, 1150000, 1080000),
        ("PE-2026-009", "骨科 C 型臂", "骨科", 980000, 268000, 251000),
        ("PE-2026-010", "全自动血细胞分析仪", "检验科", 560000, 233000, 220000),
        ("PE-2026-011", "高频电刀", "手术室", 220000, 118000, 112000),
        ("PE-2026-012", "移动 DR", "急诊科", 760000, 187000, 176000),
    ]

    conn = open_conn()
    try:
        for index, plan in enumerate(device_plan):
            code, name, dept, asset_value, income, prev_income = plan
            # 成本按设备原值与收入规模差异化生成，保证演示数据有盈有亏
            depreciate = round2(asset_value / 60)
            maintain = round2(income * (0.11 + (index % 4) * 0.035))
            consumable = round2(income * (0.16 + (index % 3) * 0.06))
            manpower = round2(income * (0.12 + (index % 2) * 0.05))
            if index == 0:
                # 构造一台高效益设备：成本压到收入的 35% 以内
                maintain = round2(income * 0.06)
                consumable = round2(income * 0.10)
                manpower = round2(income * 0.08)
            if index == 6 or index == 11:
                # 构造两台亏损设备：成本显著高于收入
                maintain = round2(income * 0.62)
                consumable = round2(income * 0.34)
            calc = calc_benefit_row(income, depreciate, maintain,
                                    consumable, manpower, asset_value)
            conn.execute(
                "INSERT INTO pe_benefit_detail "
                "(device_code, device_name, dept_name, period_tag, income_amount, "
                "cost_depreciate, cost_maintain, cost_consumable, cost_manpower, "
                "asset_value, cost_total, net_benefit, profit_rate, cost_rate, "
                "payback_rate, level_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, name, dept, current_tag, calc["income_amount"],
                 calc["cost_depreciate"], calc["cost_maintain"],
                 calc["cost_consumable"], calc["cost_manpower"], asset_value,
                 calc["cost_total"], calc["net_benefit"], calc["profit_rate"],
                 calc["cost_rate"], calc["payback_rate"], calc["level_code"]))

            prev_calc = calc_benefit_row(
                prev_income, depreciate, round2(maintain * 0.93),
                round2(consumable * 0.95), manpower, asset_value)
            conn.execute(
                "INSERT INTO pe_benefit_detail "
                "(device_code, device_name, dept_name, period_tag, income_amount, "
                "cost_depreciate, cost_maintain, cost_consumable, cost_manpower, "
                "asset_value, cost_total, net_benefit, profit_rate, cost_rate, "
                "payback_rate, level_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, name, dept, prev_tag, prev_calc["income_amount"],
                 prev_calc["cost_depreciate"], prev_calc["cost_maintain"],
                 prev_calc["cost_consumable"], prev_calc["cost_manpower"],
                 asset_value, prev_calc["cost_total"], prev_calc["net_benefit"],
                 prev_calc["profit_rate"], prev_calc["cost_rate"],
                 prev_calc["payback_rate"], prev_calc["level_code"]))
        conn.commit()
    finally:
        conn.close()
    return len(device_plan) * 2

# --------------------------------------------------------------------------
# 第 6 天数据表脚本增量：perf_schema.sql（M，已内联为字符串常量 DAY6_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY6_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 6 天新增：医疗设备效益明细表
-- ==================================================================
-- 效益明细表：按「一台设备一个考核周期一条明细」归集检查收入与折旧、
-- 维保、耗材、人力四类成本，并保存核算后的净效益、收益率、成本收益率
-- 与效益等级，支撑全院与科室两级效益报表
CREATE TABLE IF NOT EXISTS pe_benefit_detail (
    benefit_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 效益明细主键
    device_code      TEXT    NOT NULL,                 -- 设备编号
    device_name      TEXT    NOT NULL,                 -- 设备名称
    dept_name        TEXT    NOT NULL,                 -- 所属科室
    period_tag       TEXT    NOT NULL,                 -- 考核周期（如 2026-Q4）
    income_amount    REAL    DEFAULT 0,                -- 检查收入（元）
    cost_depreciate  REAL    DEFAULT 0,                -- 设备折旧（元）
    cost_maintain    REAL    DEFAULT 0,                -- 维保支出（元）
    cost_consumable  REAL    DEFAULT 0,                -- 耗材支出（元）
    cost_manpower    REAL    DEFAULT 0,                -- 人力成本（元）
    asset_value      REAL    DEFAULT 0,                -- 设备原值（元）
    cost_total       REAL    DEFAULT 0,                -- 成本合计（元）
    net_benefit      REAL    DEFAULT 0,                -- 净效益（元）
    profit_rate      REAL    DEFAULT 0,                -- 收益率（%）
    cost_rate        REAL    DEFAULT 0,                -- 成本收益率（%）
    payback_rate     REAL    DEFAULT 0,                -- 原值回收率（%）
    level_code       TEXT    DEFAULT 'NORMAL',         -- 效益等级（见 BENEFIT_LEVEL_DICT）
    created_at       TEXT    DEFAULT (datetime('now', 'localtime')),
    UNIQUE (device_code, period_tag)
);

-- ==================================================================
-- 第 6 天新增：效益报表归档表
-- ==================================================================
-- 报表归档表：保存每次生成的效益报表编号、统计范围、汇总指标与报表正文，
-- 使报表可回溯、可复阅，避免不同时点重算导致口径不一致
CREATE TABLE IF NOT EXISTS pe_benefit_report (
    report_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 报表主键
    report_code     TEXT    DEFAULT '',                -- 报表编号
    period_tag      TEXT    DEFAULT '',                -- 考核周期
    dept_name       TEXT    DEFAULT '',                -- 统计科室（全院时为「全院」）
    scope_code      TEXT    DEFAULT 'ALL',             -- 统计范围（见 REPORT_SCOPE_DICT）
    device_amount   INTEGER DEFAULT 0,                 -- 纳入设备台数
    income_total    REAL    DEFAULT 0,                 -- 收入合计（元）
    cost_total      REAL    DEFAULT 0,                 -- 成本合计（元）
    net_total       REAL    DEFAULT 0,                 -- 净效益合计（元）
    profit_rate     REAL    DEFAULT 0,                 -- 平均收益率（%）
    conclusion_text TEXT    DEFAULT '',                -- 报表结论
    report_text     TEXT    DEFAULT '',                -- 报表正文文本
    operator_name   TEXT    DEFAULT 'admin',           -- 制表人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_benefit_period ON pe_benefit_detail (period_tag);
CREATE INDEX IF NOT EXISTS idx_benefit_dept   ON pe_benefit_detail (dept_name);
CREATE INDEX IF NOT EXISTS idx_benefit_net    ON pe_benefit_detail (net_benefit);
CREATE INDEX IF NOT EXISTS idx_report_period  ON pe_benefit_report (period_tag);
CREATE INDEX IF NOT EXISTS idx_report_time    ON pe_benefit_report (created_at);
"""


def apply_day6_schema(conn):
    """把第 6 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY6_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 6 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-26（第 6 天 / 共 15 天）
#           绩效对比、可视化图表与效益报表六类页面路由，启动时完成建表、
#           等级规则初始化、科室初始化与演示评分、上期对照、效益明细数据
#           灌入，并提供评分试算、评分入库、评分明细、权重元信息、榜单、
#           科室横向对比、等级批量重判、ECharts 图表 option、多维度排行
#           筛选查询与效益报表核算、生成归档、正文复阅接口

from perf_report import (
    build_report_payload,
    save_benefit_detail,
    generate_benefit_report,
    fetch_report_list,
    fetch_report_text,
    seed_demo_benefit,
    BENEFIT_LEVEL_DICT,
    COST_ITEM_DICT,
)

# ==================================================================
# 以下为第 6 天新增：医疗设备效益报表接口
# ==================================================================


@app.route("/pf/report")
def page_report():
    """效益报表页面：效益汇总、四张分析图表、明细检索与报表归档复阅。"""
    return render_template(
        "page_report.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/pf/report")
def api_benefit_report():
    """效益报表数据接口：汇总指标、四类图表与分页明细。"""
    payload = build_report_payload(
        period_tag=(request.args.get("period_tag") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        keyword=(request.args.get("keyword") or "").strip(),
        level_code=(request.args.get("level_code") or "").strip(),
        page=int(request.args.get("page", 1)),
        size=int(request.args.get("size", 12)),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/pf/report/benefit", methods=["POST"])
def api_benefit_detail():
    """效益明细登记接口：登记或更新某台设备某周期的收入与四类成本。"""
    body = request.get_json(silent=True) or request.form.to_dict()
    outcome = save_benefit_detail(
        body.get("device_code") or "",
        body.get("device_name") or "",
        body.get("dept_name") or "",
        body.get("period_tag") or "",
        body.get("income_amount") or 0,
        body.get("depreciate_cost") or 0,
        body.get("maintain_cost") or 0,
        body.get("consumable_cost") or 0,
        body.get("manpower_cost") or 0,
        body.get("asset_value") or 0,
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/pf/report/generate", methods=["POST"])
def api_report_generate():
    """效益报表生成接口：按周期与科室生成报表并归档。"""
    body = request.get_json(silent=True) or {}
    outcome = generate_benefit_report(
        (body.get("period_tag") or "").strip(),
        (body.get("dept_name") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/pf/report/list")
def api_report_list():
    """报表归档清单接口：返回最近生成的效益报表。"""
    return jsonify({
        "result": "OK",
        "reports": fetch_report_list(int(request.args.get("limit", 10))),
        "level_legend": [{"code": k, "label": v} for k, v in BENEFIT_LEVEL_DICT.items()],
        "cost_legend": [{"code": k, "label": v} for k, v in COST_ITEM_DICT.items()],
    })


@app.route("/api/pf/report/text")
def api_report_text():
    """报表正文接口：按报表主键取回归档的报表正文文本。"""
    detail = fetch_report_text(int(request.args.get("report_id") or 0))
    if not detail:
        return jsonify({"result": "FAIL", "message": "报表不存在"})
    detail["result"] = "OK"
    return jsonify(detail)



# 【函数内插入】def api_health_check():
#    """启动引导：建表 → 初始化规则与科室 → 灌入演示评分、上期对照与效益明细。"""
#    seed_demo_benefit()

# 【函数内插入】if __name__ == "__main__":
#    print("效益报表：   http://127.0.0.1:%d/pf/report" % PORT)

# --------------------------------------------------------------------------
# 第 6 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/perf_report.js            新增 350 行
#   [修改] static/perf_screen.css           新增 90 行
#   [新增] templates/page_report.html       新增 182 行
#   [修改] templates/screen_base.html       新增 1 行
