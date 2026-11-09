import io
import json

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from l6_schema import BY_KEY, ETCHED_TO_IEC, IEC_TO_ETCHED, types_for

REQ_FILL = PatternFill("solid", fgColor="FDE2E1")
OPT_FILL = PatternFill("solid", fgColor="E8EEF7")
HEAD_FILL = PatternFill("solid", fgColor="1F2937")


def _types(key, direction):
    if key == "all":
        return types_for(direction)
    return [BY_KEY[key]]


def excel_template(key, direction=IEC_TO_ETCHED):
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    src = "INVENTEC_MES" if direction == IEC_TO_ETCHED else "ETCHED"
    readme.append(["Etched L6 MES upload template", f"source: {src}"])
    readme["A1"].font = Font(bold=True, size=14)
    readme.append(["One sheet per data type. Sheet name = data type key. Row 1 = field names, rows below = data."])
    readme.append(["Red header = required. Times: YYYY-MM-DD HH:MM:SS. Extra columns are accepted and reported as unmatched."])
    readme.append([])
    readme.append(["data_type", "field", "type", "required", "allowed values", "aliases accepted", "description", "example"])
    for c in readme[5]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
    for dt in _types(key, direction):
        for f in dt.fields:
            readme.append([dt.key, f.name, f.type, "Y" if f.required else "", ", ".join(f.enum),
                           ", ".join(f.all_aliases()), f.desc, "" if f.example is None else f.example])
    for col, w in zip("ABCDEFGH", (22, 22, 10, 9, 36, 40, 50, 30)):
        readme.column_dimensions[col].width = w

    for dt in _types(key, direction):
        ws = wb.create_sheet(dt.key[:31])
        ws.append([f.name for f in dt.fields])
        ws.append([f.example for f in dt.fields])
        for f, c in zip(dt.fields, ws[1]):
            c.font = Font(bold=True)
            c.fill = REQ_FILL if f.required else OPT_FILL
            c.alignment = Alignment(horizontal="center")
            note = f"{f.type}{' | REQUIRED' if f.required else ''}"
            if f.enum:
                note += "\n" + " / ".join(f.enum)
            if f.desc:
                note += "\n" + f.desc
            c.comment = Comment(note, "Etched")
            ws.column_dimensions[c.column_letter].width = max(14, len(f.name) + 4)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def json_template(key, direction=IEC_TO_ETCHED):
    src = "INVENTEC_MES" if direction == IEC_TO_ETCHED else "ETCHED"
    types = _types(key, direction)
    head = {"source_system": src, "submitted_by": "", "source_tz": "Asia/Bangkok", "note": ""}
    if len(types) == 1:
        body = dict(head, data_type=types[0].key, records=[types[0].example_row()])
    else:
        body = dict(head, data={dt.key: [dt.example_row()] for dt in types})
    return json.dumps(body, indent=2, ensure_ascii=False)


def direction_of(key):
    if key == "all":
        return None
    return BY_KEY[key].direction


__all__ = ["excel_template", "json_template", "direction_of", "ETCHED_TO_IEC", "IEC_TO_ETCHED"]
