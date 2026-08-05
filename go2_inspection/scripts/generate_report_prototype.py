"""生成可评审的整批巡检 Word 报告雏形。"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting import build_prototype_report, render_report_docx


def main() -> None:
    destination = PROJECT_ROOT / "docs" / "GO2智能巡检报告_雏形.docx"
    evidence = next(
        (PROJECT_ROOT / "dataset_inbox" / "visible").rglob("color_*.jpg"),
        None,
    )
    report = build_prototype_report(str(evidence) if evidence else None)
    destination.write_bytes(render_report_docx(report))
    print(destination)


if __name__ == "__main__":
    main()
