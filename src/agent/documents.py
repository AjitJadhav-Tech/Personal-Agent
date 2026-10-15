"""Renders the tailored resume and cover letter to PDF with Typst.

Text is passed to the templates as JSON data, never spliced into markup, so characters in
job descriptions or model output can't break or inject into the document.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

TEMPLATES = Path(__file__).parent / "templates"


def render(template: str, data: dict[str, Any]) -> bytes:
    import typst

    return typst.compile(
        str(TEMPLATES / template),
        root=str(TEMPLATES),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        ignore_system_fonts=True,
    )


def resume_pdf(resume: dict[str, Any]) -> bytes:
    return render("resume.typ", {k: v for k, v in resume.items() if k != "checks"})


def cover_letter_pdf(letter: dict[str, Any]) -> bytes:
    data = {k: v for k, v in letter.items() if k != "checks"}
    data["date"] = date.today().strftime("%d %B %Y")
    return render("cover_letter.typ", data)
