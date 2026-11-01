"""添付ファイルの読み込み（F09）。

CSV・テキスト・Excel（.xlsx）から文字を取り出し、課題分析のAIに渡す。
文字として取り出すことで、AIが添付ファイルを根拠にしたときも、
業務情報や回答と同じように引用を照合できる（output_check.verify_evidences）。

- 上限（ファイル数・合計文字数・大きさ）を超えたら、黙って切り捨てずにエラーで知らせる
- 1つでも読めないファイルがあれば、すべての誤りをまとめて返し、どれも採用しない
- 読み込んだ内容は画面のセッションの中だけで使い、保存しない（S07）
"""

from io import BytesIO
from pathlib import Path

from ai_bic.config import (
    ATTACHMENT_EXTENSIONS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_CHARS,
    MAX_ATTACHMENTS,
)
from ai_bic.models import Attachment

# CSV・テキストの文字コード。Excelで保存したCSVは Shift_JIS（cp932）のことが多い
ENCODINGS = ("utf-8-sig", "cp932")


class AttachmentError(Exception):
    """添付ファイルを読み込めなかった理由（利用者向けの日本語メッセージ）。"""


def read_attachment(name: str, data: bytes) -> Attachment:
    """1つのファイルを読み込み、文字を取り出す。読めなければ AttachmentError。"""
    ext = Path(name).suffix.lower()
    if ext not in ATTACHMENT_EXTENSIONS:
        allowed = "・".join(e.lstrip(".").upper() for e in ATTACHMENT_EXTENSIONS)
        raise AttachmentError(f"「{name}」は対応していない形式です（対応形式：{allowed}）")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(f"「{name}」が大きすぎます（上限：{MAX_ATTACHMENT_BYTES // 1024 // 1024}MB）")

    text = _read_xlsx(name, data) if ext == ".xlsx" else _read_text(name, data)
    if not text.strip():
        raise AttachmentError(f"「{name}」に文字が含まれていません")
    return Attachment(name=name, text=text.strip())


def load_attachments(files: list[tuple[str, bytes]]) -> tuple[list[Attachment], list[str]]:
    """複数のファイルを読み込む。

    戻り値：(読み込んだファイル, エラーメッセージの一覧)
    エラーが1つでもあれば、読み込んだファイルは空で返す（一部だけ使うと利用者が混乱するため）。
    """
    if len(files) > MAX_ATTACHMENTS:
        return [], [f"添付できるファイルは{MAX_ATTACHMENTS}つまでです（現在{len(files)}つ）"]

    attachments, errors = [], []
    for name, data in files:
        try:
            attachments.append(read_attachment(name, data))
        except AttachmentError as e:
            errors.append(str(e))

    total = sum(len(a.text) for a in attachments)
    if not errors and total > MAX_ATTACHMENT_CHARS:
        errors.append(
            f"添付ファイルの文字数が多すぎます（合計{total:,}文字／上限{MAX_ATTACHMENT_CHARS:,}文字）。"
            "必要な部分だけを残したファイルにしてください"
        )

    return ([], errors) if errors else (attachments, [])


def _read_text(name: str, data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttachmentError(f"「{name}」の文字コードを読み取れません（UTF-8 または Shift_JIS で保存してください）")


def _read_xlsx(name: str, data: bytes) -> str:
    """Excelの全シートを「【シート名】」の見出しと、タブ区切りの行に変換する。"""
    from openpyxl import load_workbook  # Excelを添付したときだけ読み込む

    try:
        # data_only=True：計算式ではなく、計算結果の値を読む
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise AttachmentError(f"「{name}」をExcelファイルとして開けません") from e

    lines = []
    try:
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):  # 空の行は飛ばす
                    rows.append("\t".join(cells).rstrip("\t"))
            if rows:  # 空のシートは見出しも出さない
                lines += [f"【{sheet.title}】", *rows]
    finally:
        workbook.close()
    return "\n".join(lines)
