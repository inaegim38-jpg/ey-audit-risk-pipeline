#!/usr/bin/env python3
"""artifacts/portfolio_demo/ 산출물로 Power BI 대시보드 '구성 미리보기' PNG를 만든다.

주의: 이 이미지는 Power BI Desktop 화면이 아니라 matplotlib로 그린 모의(preview)이며,
합성 데이터 기반이다. 실제 Power BI 보고서는 powerbi/PORTFOLIO_DASHBOARD_SPEC.md를 따라 별도로 만든다.
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

DIR = Path(__file__).resolve().parent.parent / "artifacts" / "portfolio_demo"
COLORS = {"CRITICAL": "#b3261e", "HIGH": "#e8710a", "MEDIUM": "#d4a017", "LOW": "#5f6368"}


def pick_font():
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    for c in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "AppleGothic"):
        if c in names:
            return c
    return None


def main(out: Path = DIR / "powerbi_preview.png") -> int:
    summary = json.loads((DIR / "dashboard_summary.json").read_text(encoding="utf-8"))
    with (DIR / "review_items.csv").open(encoding="utf-8-sig") as fh:
        items = list(csv.DictReader(fh))
    font = pick_font()
    if font:
        plt.rcParams["font.family"] = font
    ko = font is not None
    L = (lambda k, e: k) if ko else (lambda k, e: e)

    fig = plt.figure(figsize=(13, 7.2), dpi=110)
    fig.suptitle(L("감사·리스크 자료 정합성 및 검토 이슈 트래커 — 대시보드 구성 미리보기", "Audit data-integrity & review issue tracker - dashboard layout preview"), fontsize=13, fontweight="bold", x=0.02, ha="left")
    fig.text(0.02, 0.925, L("합성(가상) 데이터 · matplotlib 모의 이미지(Power BI 화면 아님) · 결과는 검토 후보이며 부정 확정이 아님",
                              "Synthetic data - matplotlib mock (not a Power BI screenshot) - candidates only, not conclusions"), fontsize=9, color="#5f6368")

    cards = [
        (L("탐지 후보 수", "Findings"), f"{summary['finding_count']}"),
        (L("노출 금액(원)", "Exposure (KRW)"), f"{summary['exposure_amount']:,}"),
        (L("Critical+High", "Critical+High"), f"{summary['critical_high_count']}"),
        (L("미결 검토", "Open reviews"), f"{summary['open_review_count']}"),
        (L("검토 완료율", "Completion %"), f"{summary['review_completion_pct']:.1f}%"),
    ]
    for i, (label, val) in enumerate(cards):
        ax = fig.add_axes([0.02 + i * 0.196, 0.78, 0.18, 0.11])
        ax.set_facecolor("#f1f3f4"); ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color("#dadce0")
        ax.text(0.5, 0.62, val, ha="center", va="center", fontsize=18, fontweight="bold")
        ax.text(0.5, 0.2, label, ha="center", va="center", fontsize=9, color="#5f6368")

    ax1 = fig.add_axes([0.05, 0.42, 0.27, 0.30])
    sev = summary["severity_counts"]
    ks = [k for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW") if sev.get(k)]
    ax1.bar(ks, [sev[k] for k in ks], color=[COLORS[k] for k in ks])
    ax1.set_title(L("심각도별 후보", "Findings by severity"), fontsize=10, loc="left")
    ax1.spines[["top", "right"]].set_visible(False)

    ax2 = fig.add_axes([0.40, 0.42, 0.27, 0.30])
    rc = Counter(i["rule_id"] for i in items)
    ax2.barh(list(rc.keys())[::-1], list(rc.values())[::-1], color="#1a73e8")
    ax2.set_title(L("룰별 후보", "Findings by rule"), fontsize=10, loc="left")
    ax2.spines[["top", "right"]].set_visible(False)

    ax3 = fig.add_axes([0.74, 0.42, 0.22, 0.30])
    st = Counter(i["review_status"] for i in items)
    ax3.pie(list(st.values()), labels=list(st.keys()), autopct="%1.0f%%", colors=["#9aa0a6", "#fbbc04", "#34a853", "#a142f4"][: len(st)])
    ax3.set_title(L("검토 상태", "Review status"), fontsize=10, loc="left")

    ax4 = fig.add_axes([0.02, 0.04, 0.96, 0.30]); ax4.axis("off")
    ax4.set_title(L("검토 큐 (우선순위 순)", "Review queue (by priority)"), fontsize=10, loc="left")
    rows = [[i["suggested_priority"], i["rule_id"], i["severity"], i["transaction_id"] or "-", i["document_id"] or "-", f"{int(i['amount']):,}", i["review_status"]] for i in items]
    t = ax4.table(cellText=rows, colLabels=["priority", "rule", "severity", "tx", "doc", "amount", "status"], loc="upper left", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1, 1.15)

    fig.savefig(out); plt.close(fig)
    print("wrote", out, f"(korean font: {font})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
