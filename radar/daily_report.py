"""
Academic Radar 日报生成器
由 WorkBuddy 定时任务触发，运行雷达后生成格式化日报。
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RADAR_HOME = Path.home() / "academic-radar"
REPORT_DIR = Path("E:/workbody/2026-07-28-22-30-20") / ".workbuddy" / "reports"
DATA_DIR = RADAR_HOME / "data" / "radar"

def main():
    # 1. 运行雷达
    result = subprocess.run(
        [sys.executable, "radar_main.py"],
        cwd=RADAR_HOME,
        capture_output=True, text=True, timeout=600
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)

    # 2. 找最新存档
    json_files = sorted(DATA_DIR.glob("*.json"))
    if not json_files:
        print("No new results today.")
        return

    latest = json_files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    items = data.get("items", [])

    if not items:
        print("No relevant papers today.")
        return

    # 3. 生成日报
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"学术雷达日报_{today}.md"

    lines = []
    lines.append(f"# 学术雷达日报 — {today}")
    lines.append("")
    lines.append(f"**研究方向**: 胰腺炎 / 消化道出血 / 消化道早癌 / 消化内镜(ERCP/EUS)")
    lines.append(f"**命中论文**: {len(items)} 篇")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, item in enumerate(items, 1):
        score = item.get("score", "?")
        title = item.get("title", "无标题")
        authors = ", ".join(item.get("authors", [])[:5])
        journal = item.get("source_journal", "")
        year = item.get("publication_date", "")[:4] if item.get("publication_date") else ""
        doi = item.get("doi", "")
        summary = item.get("summary", "")
        status = item.get("status", "unknown")

        # 发表状态标识
        if status == "published":
            status_icon = "✅"
        elif status == "preprint":
            status_icon = "📄"
        else:
            status_icon = "❓"

        lines.append(f"### {idx}. {status_icon} {title}")
        lines.append("")
        lines.append(f"**相关性评分**: {score}/5")
        if authors:
            lines.append(f"**作者**: {authors}")
        if journal:
            lines.append(f"**期刊**: {journal}{' (' + year + ')' if year else ''}")
        if doi:
            lines.append(f"**DOI**: [{doi}](https://doi.org/{doi})")
        lines.append("")
        if summary:
            lines.append(f"**摘要**: {summary}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"_下次推送: 明天 08:00_")
    lines.append(f"_生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report saved: {report_path}")

if __name__ == "__main__":
    main()
