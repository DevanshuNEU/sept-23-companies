"""Builds small valid xlsx workbooks with zipfile, for the tabular reader tests (#31 Task 3)."""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class RichText:
    """A shared string written as rich-text runs (`<r><t>..</t></r>`), optionally with a phonetic run."""

    def __init__(self, *parts: str, phonetic: str = ""):
        self.parts = parts
        self.phonetic = phonetic


def col_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _t(text: str) -> str:
    return f'<t xml:space="preserve">{escape(text)}</t>'


def _blank(value) -> bool:
    return value is None or value == ""


def write_xlsx(path, header, rows, *, inline=False, sparse=False, sheet_xml=None) -> Path:
    """Write `header` then `rows` to one sheet. str is a shared string (an inline string with `inline=True`),
    int and float are numeric `<v>` cells without `t`, RichText is a rich shared string, None or '' is an empty
    cell. `sparse=True` omits empty cells the way Excel does. `sheet_xml` replaces the generated sheet (for
    hand-made damage)."""
    path = Path(path)
    shared: list[str] = []
    index: dict = {}

    def shared_index(value) -> int:
        if isinstance(value, RichText):
            runs = "".join(f"<r>{_t(p)}</r>" for p in value.parts)
            if value.phonetic:
                runs += f"<rPh sb=\"0\" eb=\"1\">{_t(value.phonetic)}</rPh>"
            key = ("rich", value.parts, value.phonetic)
            xml = f"<si>{runs}</si>"
        else:
            key = ("plain", value)
            xml = f"<si>{_t(value)}</si>"
        if key not in index:
            index[key] = len(shared)
            shared.append(xml)
        return index[key]

    def cell(ref: str, value) -> str:
        if _blank(value):
            return f'<c r="{ref}"/>'
        if isinstance(value, bool):
            return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value!r}</v></c>'
        if inline:
            text = "".join(value.parts) if isinstance(value, RichText) else value
            return f'<c r="{ref}" t="inlineStr"><is>{_t(text)}</is></c>'
        return f'<c r="{ref}" t="s"><v>{shared_index(value)}</v></c>'

    xml_rows = []
    for number, values in enumerate([list(header)] + [list(r) for r in rows], start=1):
        cells = [cell(f"{col_letters(i)}{number}", v) for i, v in enumerate(values)
                 if not (sparse and _blank(v))]
        xml_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    sheet = sheet_xml or (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                          f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{MAIN}" xmlns:r="{REL}">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            '</Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/sharedStrings.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{MAIN}" count="{len(shared)}" '
            f'uniqueCount="{len(shared)}">{"".join(shared)}</sst>'),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            zf.writestr(name, body)
    return path
