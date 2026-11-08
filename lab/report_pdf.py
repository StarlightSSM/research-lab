"""논문 분석 결과 -> 한글 PDF 리포트 (reportlab)."""
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

INK = colors.HexColor("#1b1f2a")
MUTED = colors.HexColor("#5b6272")
ACCENT = colors.HexColor("#2a78d6")
TINT = colors.HexColor("#eef4fc")
LINE = colors.HexColor("#d9dee8")

_FONT = None


def _fonts():
    """한글 폰트 등록: Windows 맑은고딕 -> 리눅스 나눔고딕 -> 내장 CID 폰트"""
    global _FONT
    if _FONT:
        return _FONT
    candidates = [
        (r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf"),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    ]
    for reg, bold in candidates:
        if os.path.exists(reg):
            pdfmetrics.registerFont(TTFont("KR", reg))
            pdfmetrics.registerFont(TTFont("KR-Bold", bold if os.path.exists(bold) else reg))
            _FONT = ("KR", "KR-Bold")
            return _FONT
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
    _FONT = ("HYGothic-Medium", "HYGothic-Medium")
    return _FONT


def _styles():
    reg, bold = _fonts()
    base = dict(fontName=reg, textColor=INK, alignment=TA_LEFT)
    return {
        "label": ParagraphStyle("label", **{**base, "textColor": ACCENT}, fontSize=8.5, leading=12),
        "title": ParagraphStyle("title", **{**base, "fontName": bold}, fontSize=17, leading=23, spaceAfter=4),
        "meta": ParagraphStyle("meta", **{**base, "textColor": MUTED}, fontSize=9, leading=13),
        "h": ParagraphStyle("h", **{**base, "fontName": bold}, fontSize=12.5, leading=17, spaceBefore=11, spaceAfter=5),
        "body": ParagraphStyle("body", **base, fontSize=9.6, leading=15),
        "small": ParagraphStyle("small", **{**base, "textColor": MUTED}, fontSize=8.5, leading=12, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", **base, fontSize=9.2, leading=13.5, wordWrap="CJK"),
        "cellb": ParagraphStyle("cellb", **{**base, "fontName": bold}, fontSize=9.2, leading=13.5, wordWrap="CJK"),
        "tldr": ParagraphStyle("tldr", **{**base, "fontName": bold}, fontSize=11, leading=16.5, wordWrap="CJK"),
    }


def _p(text, style):
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)


def _bullets(items, st):
    items = [i for i in items or [] if str(i).strip()]
    if not items:
        return [_p("논문에서 확인되지 않음", st["small"])]
    return [ListFlowable([ListItem(_p(i, st["body"]), leftIndent=12, value="•") for i in items],
                         bulletType="bullet", start="•", leftIndent=12, bulletFontSize=8, bulletColor=ACCENT)]


def _kv_table(rows, st, widths=(34 * mm, None)):
    data = [[_p(k, st["cellb"]), _p(v or "-", st["cell"])] for k, v in rows]
    w = A4[0] - 36 * mm
    t = Table(data, colWidths=[widths[0], w - widths[0]])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), TINT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def build_report(a, out_path, lab_name="My Little Lab"):
    st = _styles()
    reg, bold = _fonts()
    meta = a.get("meta", {})
    story = []

    story.append(_p(f"PAPER BRIEF  ·  {lab_name}", st["label"]))
    story.append(Spacer(1, 3))
    story.append(_p(meta.get("title") or "제목 미확인", st["title"]))
    bits = [meta.get("authors"), meta.get("venue"), meta.get("year"),
            f"{meta.get('pages')}쪽" if meta.get("pages") else None,
            f"피인용 {meta['cited_by']}회 (OpenAlex)" if meta.get("cited_by") is not None else None]
    story.append(_p("  |  ".join(str(b) for b in bits if b), st["meta"]))
    if meta.get("doi"):
        story.append(_p(meta["doi"], st["small"]))
    story.append(Spacer(1, 8))

    # 한 줄 요약 박스
    box = Table([[_p("한 줄 요약", st["label"])], [_p(a.get("tldr") or "-", st["tldr"])]],
                colWidths=[A4[0] - 36 * mm])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT),
                             ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 4),
                             ("BOTTOMPADDING", (0, -1), (-1, -1), 9)]))
    story.append(box)
    kws = a.get("keywords") or a.get("author_keywords") or []
    if kws:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f'<font name="{bold}" color="#2a78d6">키워드</font>  ' +
                               escape("  ·  ".join(kws)), st["body"]))

    def section(num, title, flow):
        head = _p(f"{num}. {title}", st["h"])
        flow = flow if isinstance(flow, list) else [flow]
        story.append(CondPageBreak(35 * mm))  # 제목만 페이지 끝에 남지 않도록
        if isinstance(flow[0], Table):
            story.append(head)
            story.extend(flow)
        else:
            story.append(KeepTogether([head] + flow[:1]))
            story.extend(flow[1:])

    n = iter(range(1, 50))
    section(next(n), "핵심 요약", _p(a.get("summary"), st["body"]))

    toc = a.get("toc") or []
    if toc:
        rows = [[_p(("    " * (t.get("level", 1) - 1)) + t.get("title", ""), st["cell"]),
                 _p(str(t.get("page", "")), st["small"])] for t in toc[:45]]
        tt = Table(rows, colWidths=[A4[0] - 36 * mm - 16 * mm, 16 * mm])
        tt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
        section(next(n), "목차 (자동 추출)", tt)

    section(next(n), "연구 질문 · 가설", _bullets(a.get("research_questions"), st))

    m = a.get("methodology", {})
    tech = ", ".join(m.get("techniques") or [])
    det = ", ".join(a.get("detected_methods") or [])
    section(next(n), "연구방법론", _kv_table([
        ("연구 설계", m.get("research_design")), ("데이터", m.get("data")),
        ("분석 기법", tech), ("분석 절차", m.get("analysis_procedure")),
        ("본문 탐지 기법", det or "-"),
    ], st))

    section(next(n), "주요 결과", _bullets(a.get("key_findings"), st))
    c = a.get("contributions", {})
    section(next(n), "연구의 기여 · 시사점", _kv_table([("이론적 기여", c.get("theoretical")),
                                                     ("실무적 시사점", c.get("practical"))], st))
    section(next(n), "쟁점 · 비판적으로 읽기", _bullets(a.get("issues"), st))
    section(next(n), "한계점", _bullets(a.get("limitations"), st))
    section(next(n), "후속 연구 아이디어", _bullets(a.get("future_research"), st))
    section(next(n), "내 연구와의 연결", _p(a.get("my_research_link"), st["body"]))

    rel_flow = []
    if a.get("related_concepts"):
        rel_flow.append(Paragraph(f'<font name="{bold}">관련 개념·이론</font>  ' +
                                  escape("  ·  ".join(a["related_concepts"])), st["body"]))
        rel_flow.append(Spacer(1, 5))
    for r in a.get("related_papers") or []:
        line = escape(r.get("title", ""))
        sub = "  |  ".join(str(x) for x in (r.get("authors"), r.get("venue"), r.get("year"),
                                             f"피인용 {r.get('cited_by')}") if x)
        url = r.get("url") or ""
        rel_flow.append(Paragraph(f'<link href="{escape(url)}" color="#2a78d6">{line}</link>', st["body"]))
        rel_flow.append(_p(sub, st["small"]))
        rel_flow.append(Spacer(1, 3))
    if rel_flow:
        section(next(n), "관련 개념 · 함께 읽을 논문 (OpenAlex)", rel_flow)

    section(next(n), "세미나 토론 질문", _bullets(a.get("discussion_questions"), st))
    if a.get("glossary"):
        section(next(n), "용어 정리", _kv_table([(g.get("term"), g.get("explanation")) for g in a["glossary"]], st,
                                             widths=(42 * mm, None)))
    if a.get("_note"):
        story.append(Spacer(1, 8))
        story.append(_p("※ " + a["_note"], st["small"]))

    footer_txt = f"{lab_name}  ·  분석 엔진: {a.get('engine', '')}  ·  {a.get('generated_at', '')}"

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, footer_txt)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page}")
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, A4[1] - 10 * mm, 40 * mm, A4[1] - 10 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=17 * mm,
                            title=f"[Paper Brief] {meta.get('title', '')}", author=lab_name)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out_path
