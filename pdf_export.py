import html
import io
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.platypus import Image
from PIL import Image as PILImage, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
FONT_PATH = ROOT / "assets" / "fonts" / "NotoSerif.ttf"
SERIF_FONT = "LiteraNotoSerif"
PIL_FONT = FONT_PATH


def register_book_font():
    if not FONT_PATH.exists():
        raise RuntimeError("Не найден локальный шрифт PDF: assets/fonts/NotoSerif.ttf")
    if SERIF_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(SERIF_FONT, str(FONT_PATH)))
        pdfmetrics.registerFontFamily(
            SERIF_FONT,
            normal=SERIF_FONT,
            bold=SERIF_FONT,
            italic=SERIF_FONT,
            boldItalic=SERIF_FONT,
        )


class TocTitle(Paragraph):
    def __init__(self, text, style, chapter_index):
        super().__init__(text, style)
        self.chapter_index = chapter_index


class BookDocument(BaseDocTemplate):
    def __init__(self, buffer, **kwargs):
        super().__init__(buffer, **kwargs)

    def afterFlowable(self, flowable):
        if isinstance(flowable, TocTitle):
            key = f"chapter-{flowable.chapter_index}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, 0, False)
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page, key))


class DecorativeRule(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 70 * mm
        self.height = 8 * mm
        self.hAlign = "CENTER"

    def draw(self):
        self.canv.saveState()
        self.canv.setStrokeColor(colors.HexColor("#B89A66"))
        self.canv.setLineWidth(0.7)
        self.canv.line(0, self.height / 2, self.width, self.height / 2)
        self.canv.restoreState()


def _draw_page(canvas, document):
    canvas.saveState()
    if document.page > 1:
        canvas.setStrokeColor(colors.HexColor("#D8D0C2"))
        canvas.setLineWidth(0.5)
        canvas.line(28 * mm, 18 * mm, A4[0] - 22 * mm, 18 * mm)
        canvas.setFont(SERIF_FONT, 9)
        canvas.setFillColor(colors.HexColor("#77716A"))
        canvas.drawCentredString(A4[0] / 2, 11 * mm, str(document.page))
    canvas.restoreState()


def _paragraphs_for_text(text, style):
    blocks = re.split(r"\n\s*\n", str(text or "").strip())
    return [
        Paragraph(html.escape(block).replace("\n", "<br/>"), style)
        for block in blocks
        if block.strip()
    ]


def make_cover_image(title, author, genre, language):
    width, height = 900, 1273
    genre_key = str(genre).lower()
    palettes = {
        "романтика": ("#5A2740", "#CDA8A4", "#F4C7B8"),
        "фэнтези": ("#18334A", "#557A95", "#D8C6A0"),
        "мистика и хоррор": ("#171925", "#4C2437", "#D7B36A"),
        "детектив": ("#102A32", "#1F5A63", "#D2B36A"),
        "приключения": ("#18382F", "#4D6B58", "#E1C17B"),
        "драма": ("#302B38", "#685A73", "#D8BDA6"),
    }
    base, mid, gold = palettes.get(genre_key, ("#1B2638", "#37546F", "#D4AF37"))
    image = PILImage.new("RGB", (width, height), base)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            glow = max(0, 1 - ((x - width * .5) ** 2 + (y - height * .25) ** 2) ** .5 / 900)
            r = int(int(base[1:3], 16) * (1 - glow * .18) + int(mid[1:3], 16) * glow * .18)
            g = int(int(base[3:5], 16) * (1 - glow * .18) + int(mid[3:5], 16) * glow * .18)
            b = int(int(base[5:7], 16) * (1 - glow * .18) + int(mid[5:7], 16) * glow * .18)
            pixels[x, y] = (min(r, 255), min(g, 255), min(b, 255))

    draw = ImageDraw.Draw(image)
    gold_rgb = tuple(int(gold[i:i + 2], 16) for i in (1, 3, 5))
    for x in range(90, width - 90, 42):
        draw.line((x, 110, x, height - 110), fill=gold_rgb, width=1)
    for y in range(180, height - 170, 54):
        draw.line((90, y, width - 90, y), fill=gold_rgb, width=1)

    title_font = ImageFont.truetype(str(PIL_FONT), 74)
    author_font = ImageFont.truetype(str(PIL_FONT), 34)
    genre_font = ImageFont.truetype(str(PIL_FONT), 24)

    def wrapped_lines(text, font, max_width):
        words = str(text).split()
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    genre_label = str(genre).upper() + ("" if language == "en" else "")
    draw.text((90, 165), genre_label, font=genre_font, fill=gold_rgb)
    title_lines = wrapped_lines(title, title_font, width - 180)[:4]
    title_y = 420
    for line in title_lines:
        box = draw.textbbox((0, 0), line, font=title_font)
        draw.text(((width - (box[2] - box[0])) / 2, title_y), line, font=title_font, fill=(248, 246, 239))
        title_y += 92
    draw.line((300, title_y + 30, width - 300, title_y + 30), fill=gold_rgb, width=3)
    draw.text(((width - draw.textbbox((0, 0), author, font=author_font)[2]) / 2, title_y + 90),
              author, font=author_font, fill=gold_rgb)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_book_pdf(book):
    register_book_font()
    title = str(book.get("title") or "Без названия")[:200]
    author = str(book.get("author") or "Автор")[:120]
    contents_title = "Contents" if book.get("language") == "en" else "Содержание"
    chapters = book.get("chapters")
    if not isinstance(chapters, list):
        raise ValueError("В книге нет списка глав.")
    chapters = [
        chapter for chapter in chapters
        if isinstance(chapter, dict) and str(chapter.get("text", "")).strip()
    ]
    if not chapters:
        raise ValueError("Сначала напишите хотя бы одну главу.")
    if len(chapters) > 40:
        raise ValueError("В одном PDF можно экспортировать не более 40 глав.")

    page_width, page_height = A4
    buffer = io.BytesIO()
    doc = BookDocument(
        buffer,
        pagesize=A4,
        leftMargin=28 * mm,
        rightMargin=22 * mm,
        topMargin=23 * mm,
        bottomMargin=25 * mm,
        title=title,
        author=author,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        page_width - doc.leftMargin - doc.rightMargin,
        page_height - doc.topMargin - doc.bottomMargin,
        id="book-body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(id="book", frames=[frame], onPage=_draw_page)])

    title_style = ParagraphStyle(
        "BookTitle", fontName=SERIF_FONT, fontSize=28, leading=36,
        alignment=TA_CENTER, textColor=colors.HexColor("#302A24"), spaceAfter=18,
    )
    author_style = ParagraphStyle(
        "BookAuthor", fontName=SERIF_FONT, fontSize=14, leading=20,
        alignment=TA_CENTER, textColor=colors.HexColor("#716657"),
    )
    section_style = ParagraphStyle(
        "SectionTitle", fontName=SERIF_FONT, fontSize=20, leading=27,
        alignment=TA_CENTER, textColor=colors.HexColor("#302A24"), spaceAfter=18,
    )
    chapter_style = ParagraphStyle(
        "ChapterTitle", fontName=SERIF_FONT, fontSize=19, leading=26,
        alignment=TA_CENTER, textColor=colors.HexColor("#302A24"),
        spaceAfter=24, keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "BookBody", fontName=SERIF_FONT, fontSize=11, leading=16,
        alignment=TA_JUSTIFY, firstLineIndent=7 * mm, spaceAfter=7,
        textColor=colors.HexColor("#25221E"), allowWidows=0, allowOrphans=0,
    )
    toc_style = ParagraphStyle(
        "TOCChapter", fontName=SERIF_FONT, fontSize=11, leading=18,
        leftIndent=0, firstLineIndent=0, textColor=colors.HexColor("#302A24"),
    )
    toc = TableOfContents()
    toc.levelStyles = [toc_style]
    toc.dotsMinLevel = 0

    cover_bytes = make_cover_image(title, author, str(book.get("genre") or "Другое"),
                                    str(book.get("language") or "ru"))
    cover = Image(io.BytesIO(cover_bytes), width=150 * mm, height=211 * mm)
    story = [
        Spacer(1, 2 * mm),
        cover,
        PageBreak(),
        Paragraph(contents_title, section_style),
        Spacer(1, 6 * mm),
        toc,
    ]
    story.append(PageBreak())

    for index, chapter in enumerate(chapters, start=1):
        chapter_title = str(chapter.get("title") or f"Глава {index}")[:200]
        story.append(TocTitle(html.escape(chapter_title), chapter_style, index))
        story.extend(_paragraphs_for_text(chapter.get("text"), body_style))
        if index < len(chapters):
            story.append(PageBreak())

    doc.multiBuild(story)
    return buffer.getvalue()
