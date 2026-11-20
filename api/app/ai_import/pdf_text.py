"""PDF から文字と表を取り出す(pdfplumber)。

文字情報のある PDF(会計ソフトや Excel から出力したもの)は、ここで取り出した文字を AI に渡す。
スキャンした PDF は文字がほとんど取れないので、いまは「対応していない」として止める
(画像として AI に読ませる方法は次の段階で追加する)。
"""

import io
from dataclasses import dataclass

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError

# 文字の数がこれ未満なら、スキャンした PDF とみなす
MIN_TEXT_CHARS = 20


class PdfReadError(Exception):
    """PDF として読めない、または対応していない PDF。メッセージは画面にそのまま出す。"""


@dataclass(frozen=True)
class PdfText:
    pages: list[str]  # ページごとの文字(表は TSV にして後ろに付ける)
    page_count: int

    @property
    def char_count(self) -> int:
        return sum(len("".join(p.split())) for p in self.pages)

    def for_prompt(self) -> str:
        return "\n\n".join(f"=== {i}ページ ===\n{text}" for i, text in enumerate(self.pages, start=1))


def read_pdf(data: bytes, max_pages: int) -> PdfText:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except (PDFSyntaxError, ValueError, TypeError) as e:
        raise PdfReadError("PDFとして読み込めませんでした。ファイルが壊れていないか確認してください") from e
    with pdf:
        if getattr(pdf.doc, "is_extractable", True) is False:
            raise PdfReadError("文字の取り出しが禁止されたPDFのため読み込めません")
        count = len(pdf.pages)
        if count == 0:
            raise PdfReadError("ページのないPDFです")
        if count > max_pages:
            raise PdfReadError(f"ページ数が多すぎます({count}ページ)。{max_pages}ページまでのPDFにしてください")
        pages = [_page_text(page) for page in pdf.pages]

    text = PdfText(pages=pages, page_count=count)
    if text.char_count < MIN_TEXT_CHARS:
        raise PdfReadError("文字情報のないPDF(スキャンした画像など)には、まだ対応していません")
    return text


def _page_text(page) -> str:
    body = page.extract_text(layout=False) or ""
    tables = []
    for table in page.extract_tables() or []:
        rows = ["\t".join((cell or "").replace("\n", " ").strip() for cell in row) for row in table if row]
        if rows:
            tables.append("\n".join(rows))
    if tables:
        body += "\n\n[表]\n" + "\n\n[表]\n".join(tables)
    return body.strip()
