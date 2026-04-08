#!/usr/bin/env python3
"""
Goldman Terminal — Daily AI Portfolio Analysis
每天 15:00 自動執行
抓取所有持股的 fundamentals → 組裝成分析報告 → 交給 Goldman agent
"""
import sqlite3, urllib.request, json, sys, os, subprocess
from datetime import date, datetime

_BASE = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.getenv("DB_PATH", os.path.join(_BASE, "stock.db"))
API_BASE = "http://192.168.88.174:8000"

def fetch_json(path):
    url = f"{API_BASE}{path}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())

def main():
    today = date.today().isoformat()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Daily AI Analysis — {today}")

    # 1. 取得所有持股
    stocks = fetch_json("/api/stocks")
    holdings = fetch_json("/api/positions")
    nav_data = fetch_json("/api/nav")

    print(f"  持股: {[s['symbol'] for s in stocks]}")
    print(f"  NAV: NT${nav_data['cash']:,.0f} 現金 + NT${nav_data['total_cost']:,.0f} 成本")

    # 2. 對每檔持股抓完整 ai-export
    analyses = []
    for stock in stocks:
        try:
            data = fetch_json(f"/api/stocks/{stock['id']}/ai-export")
            analyses.append(data)
            print(f"  ✅ {stock['symbol']} {stock['name']}")
        except Exception as e:
            print(f"  ❌ {stock['symbol']}: {e}")

    # 3. 組裝完整報告文本
    total_cost = nav_data['total_cost']
    cash = nav_data['cash']

    report_lines = []
    report_lines.append(f"📊 **Goldman Terminal 每日分析** — {today}")
    report_lines.append("")
    report_lines.append(f"**帳戶概況**")
    report_lines.append(f"- 現金: NT${cash:,.0f}")
    report_lines.append(f"- 持股成本: NT${total_cost:,.0f}")
    report_lines.append("")

    # 產業配置
    alloc = fetch_json("/api/industry-allocation")
    report_lines.append("**產業配置**")
    for a in sorted(alloc, key=lambda x: x['pct'], reverse=True):
        report_lines.append(f"- {a['sector']}: {a['pct']}% (NT${a['cost']:,.0f}, {a['stock_count']}檔)")
    report_lines.append("")

    # 各股分析
    report_lines.append("**個股掃描**")
    for a in analyses:
        s = a['stock']
        v = a.get('valuation', {}) or {}
        inst = a.get('institutional') or []
        rev = a.get('revenue') or []

        lines = []
        lines.append(f"【{s['symbol']} {s['name']}】")
        if v.get('per'):
            lines.append(f"  P/E: {v['per']} | P/B: {v['pbr']} | 殖利率: {v['dividend_yield']}%")
            lines.append(f"  現價: NT${v.get('current_price','--')} | 52W: NT${v.get('price_52w_low','--')}~{v.get('price_52w_high','--')}")
        if inst:
            total_foreign = sum(r.get('foreign_net', 0) for r in inst)
            sign = '+' if total_foreign >= 0 else ''
            lines.append(f"  外資近5日: {sign}{total_foreign:,.0f}張")
        if rev:
            latest = rev[-1] if rev else None
            if latest:
                lines.append(f"  最新月營收: {latest.get('date','--')} NT${latest.get('revenue',0)/1e8:.2f}億 MoM {latest.get('mom',0):+.1f}%")
        report_lines.extend(lines)

    report_lines.append("")
    report_lines.append("_以上資料由 Goldman Terminal 自動生成 · 數據來源: FinMind_")

    full_text = "\n".join(report_lines)

    # 4. 寫入檔案供 Goldman agent 讀取
    report_path = "/home/openclaw/.openclaw/workspace-goldman-agent/reports/daily_analysis_latest.txt"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(full_text)
    print(f"\n  報告已寫入: {report_path}")
    print("\n  📡 等待 Goldman agent 分析...")

    # 5. 通知 Goldman agent（新檔案已就緒）
    trigger_path = "/home/openclaw/.openclaw/workspace-goldman-agent/reports/.trigger"
    with open(trigger_path, "w") as f:
        f.write(today)

    print("  ✅ 完成")

if __name__ == "__main__":
    main()
