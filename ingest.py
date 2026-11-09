"""Parse uploaded Excel / JSON / YAML, match headers to Etched L6 fields, validate rows."""

import difflib
import io
import json
import re
from datetime import date, datetime, timedelta

import openpyxl
import yaml

from l6_schema import BY_KEY, types_for

SKIP_SHEETS = {"readme", "instructions", "instruction", "notes", "dictionary", "field_dictionary", "说明"}
RESULT_SYNONYMS = {
    "PASSED": "PASS", "P": "PASS", "OK": "PASS", "GOOD": "PASS", "Y": "PASS",
    "FAILED": "FAIL", "F": "FAIL", "NG": "FAIL", "NOK": "FAIL", "N": "FAIL",
    "ABORTED": "ABORT", "CANCELLED": "ABORT", "CANCELED": "ABORT",
}
DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S",
    "%Y%m%d-%H%M%S", "%Y%m%d %H%M%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S.%f",
)
D_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y")
PREVIEW_ROWS = 200


class IngestError(ValueError):
    pass


def norm(name):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name or "").strip().lower())
    return s.strip("_")


def _empty(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


# ---------------------------------------------------------------- reading

def read_payload(raw, fmt):
    """Return list of sheets: {"name", "header_row", "headers", "rows": [(row_no, [values])]}."""
    if fmt == "excel":
        return _read_excel(raw)
    if fmt in ("json", "yaml"):
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        try:
            obj = json.loads(text) if fmt == "json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            raise IngestError(f"Invalid {fmt.upper()}: {e}")
        return _read_obj(obj)
    raise IngestError(f"Unsupported format: {fmt}")


def payload_meta(raw, fmt):
    """Top-level metadata keys from a JSON/YAML body (submitted_by, data_type, ...)."""
    if fmt not in ("json", "yaml"):
        return {}
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    try:
        obj = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: obj[k] for k in ("data_type", "submitted_by", "source_system", "source_tz", "note")
            if isinstance(obj.get(k), str)}


def _read_excel(raw):
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        raise IngestError(f"Cannot open Excel file (.xlsx/.xlsm required): {e}")
    sheets = []
    for ws in wb.worksheets:
        if norm(ws.title) in SKIP_SHEETS:
            continue
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        sheets.append(_grid_to_sheet(ws.title, grid))
    wb.close()
    return [s for s in sheets if s["headers"]]


def _grid_to_sheet(name, grid):
    """Pick the header row (best field match in the first 15 rows) and return data rows."""
    known = set()
    for dt in BY_KEY.values():
        for f in dt.fields:
            known.add(f.name)
            known.update(f.all_aliases())
    best_i, best_score = None, -1
    for i, row in enumerate(grid[:15]):
        cells = [c for c in row if not _empty(c)]
        if len(cells) < 2 or not all(isinstance(c, str) for c in cells):
            continue
        score = sum(1 for c in cells if norm(c) in known)
        if score > best_score:
            best_i, best_score = i, score
    if best_i is None:
        return {"name": name, "header_row": None, "headers": [], "rows": []}
    raw_headers = grid[best_i]
    while raw_headers and _empty(raw_headers[-1]):
        raw_headers = raw_headers[:-1]
    headers = [str(h).strip() if not _empty(h) else f"column_{j + 1}" for j, h in enumerate(raw_headers)]
    rows = []
    for i in range(best_i + 1, len(grid)):
        vals = list(grid[i][:len(headers)]) + [None] * max(0, len(headers) - len(grid[i]))
        if all(_empty(v) for v in vals):
            continue
        rows.append((i + 1, vals))
    return {"name": name, "header_row": best_i + 1, "headers": headers, "rows": rows}


def _read_obj(obj):
    """Accepted shapes:
       [ {...}, ... ]                                   (needs data_type)
       {"data_type": "unit", "records": [ ... ]}
       {"data": {"unit": [...], "genealogy": [...]}}
       {"unit": [...], "genealogy": [...]}
    """
    groups = []
    if isinstance(obj, list):
        groups.append((None, obj))
    elif isinstance(obj, dict):
        if isinstance(obj.get("records"), list):
            groups.append((obj.get("data_type"), obj["records"]))
        elif isinstance(obj.get("data"), dict):
            groups += [(k, v) for k, v in obj["data"].items() if isinstance(v, list)]
        elif isinstance(obj.get("data"), list):
            groups.append((obj.get("data_type"), obj["data"]))
        else:
            groups += [(k, v) for k, v in obj.items() if isinstance(v, list)]
    if not groups:
        raise IngestError("No records found. Use {\"data_type\": ..., \"records\": [...]} "
                          "or {\"data\": {\"<data_type>\": [...]}}.")
    sheets = []
    for name, records in groups:
        headers = []
        for r in records:
            if isinstance(r, dict):
                for k in r:
                    if k not in headers:
                        headers.append(k)
        rows = []
        for i, r in enumerate(records):
            if not isinstance(r, dict):
                continue
            vals = [r.get(h) for h in headers]
            vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in vals]
            rows.append((i + 1, vals))
        sheets.append({"name": name or "records", "header_row": None, "headers": headers, "rows": rows})
    return sheets


# ---------------------------------------------------------------- matching

def _match_type_by_name(sheet_name, candidates):
    n = norm(sheet_name)
    for dt in candidates:
        if n == dt.key or n == norm(dt.label) or n in dt.aliases:
            return dt
    return None


def _field_index(dt):
    idx = {}
    for f in dt.fields:
        idx.setdefault(f.name, (f.name, "EXACT"))
    for f in dt.fields:
        for a in f.all_aliases():
            idx.setdefault(norm(a), (f.name, "ALIAS"))
    return idx


def map_headers(headers, dt):
    idx = _field_index(dt)
    used = set()
    mapping = []
    vocab = list(idx.keys())
    for h in headers:
        n = norm(h)
        hit = idx.get(n)
        if hit and hit[0] not in used:
            used.add(hit[0])
            mapping.append({"source": h, "target": hit[0], "match": hit[1], "suggestion": None})
        elif hit:
            mapping.append({"source": h, "target": None, "match": "DUPLICATE",
                            "suggestion": f"already mapped to {hit[0]}"})
        else:
            close = difflib.get_close_matches(n, vocab, n=1, cutoff=0.6)
            mapping.append({"source": h, "target": None, "match": "UNMATCHED",
                            "suggestion": idx[close[0]][0] if close else None})
    return mapping


def _detect_type(sheet, candidates):
    by_name = _match_type_by_name(sheet["name"], candidates)
    if by_name:
        return by_name, "sheet name"
    best, best_score = None, 0.0
    for dt in candidates:
        m = map_headers(sheet["headers"], dt)
        hit = {x["target"] for x in m if x["target"]}
        req = [f.name for f in dt.fields if f.required]
        req_hit = sum(1 for r in req if r in hit)
        score = len(hit) + 2 * req_hit - 3 * (len(req) - req_hit)
        if hit and score > best_score:
            best, best_score = dt, score
    return best, "column headers" if best else None


# ---------------------------------------------------------------- values

def _to_datetime(v):
    if isinstance(v, datetime):
        return v.replace(microsecond=0)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < v < 80000:
            return (datetime(1899, 12, 30) + timedelta(days=float(v))).replace(microsecond=0)
        s = str(int(v)) if float(v).is_integer() else str(v)
    else:
        s = str(v).strip()
    for fmt in DT_FORMATS + D_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"not a date/time: {s!r} (use YYYY-MM-DD HH:MM:SS)")


def coerce(f, v):
    """Return normalized value or raise ValueError."""
    if f.type == "INT":
        if isinstance(v, bool):
            raise ValueError("boolean is not an integer")
        x = float(str(v).strip())
        if not x.is_integer():
            raise ValueError(f"not an integer: {v!r}")
        return int(x)
    if f.type == "REAL":
        if isinstance(v, bool):
            raise ValueError("boolean is not a number")
        try:
            return float(str(v).strip())
        except ValueError:
            raise ValueError(f"not a number: {v!r}")
    if f.type == "DATETIME":
        d = _to_datetime(v)
        return d.isoformat(sep=" ") if d.tzinfo else d.strftime("%Y-%m-%d %H:%M:%S")
    if f.type == "DATE":
        return _to_datetime(v).strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    elif isinstance(v, (datetime, date)):
        s = v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    else:
        s = str(v).strip()
    if f.enum:
        u = s.upper().replace(" ", "_").replace("-", "_")
        if "PASS" in f.enum and u in RESULT_SYNONYMS:
            u = RESULT_SYNONYMS[u]
        if u not in f.enum:
            raise ValueError(f"{s!r} not in {list(f.enum)}")
        return u
    return s


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def analyze(sheets, direction, data_type=None, sheet_map=None):
    """Map + validate every sheet. data_type forces one type; sheet_map = {sheet: type|'skip'}."""
    candidates = types_for(direction)
    allowed = {dt.key for dt in candidates}
    sheet_map = sheet_map or {}
    out = []
    for sh in sheets:
        forced = sheet_map.get(sh["name"]) or (data_type if data_type and data_type != "auto" else None)
        if forced == "skip":
            out.append(_skipped(sh, "skipped by user"))
            continue
        if forced:
            if forced not in allowed:
                raise IngestError(f"data_type {forced!r} is not valid for {direction}. "
                                  f"Valid: {sorted(allowed)}")
            dt, how = BY_KEY[forced], "selected"
        else:
            dt, how = _detect_type(sh, candidates)
        if dt is None:
            out.append(_skipped(sh, "could not detect data type; choose one"))
            continue
        out.append(_analyze_sheet(sh, dt, how))
    return out


def _skipped(sh, reason):
    return {"sheet": sh["name"], "data_type": None, "detected_by": None, "skip_reason": reason,
            "header_row": sh["header_row"], "headers": sh["headers"], "mapping": [],
            "missing_required": [], "unknown_columns": [], "total_rows": len(sh["rows"]),
            "ok_rows": 0, "error_rows": 0, "rows": [], "preview": [], "fields": []}


def _analyze_sheet(sh, dt, how):
    mapping = map_headers(sh["headers"], dt)
    mapped = {m["target"] for m in mapping if m["target"]}
    missing = [f.name for f in dt.fields if f.required and f.name not in mapped]
    unknown = [m["source"] for m in mapping if not m["target"]]
    rows = []
    for row_no, vals in sh["rows"]:
        values, extras, errors = {}, {}, []
        for m, v in zip(mapping, vals):
            if not m["target"]:
                if not _empty(v):
                    extras[m["source"]] = _jsonable(v)
                continue
            f = dt.field(m["target"])
            if _empty(v):
                continue
            try:
                values[f.name] = coerce(f, v)
            except ValueError as e:
                values[f.name] = str(_jsonable(v))
                errors.append({"field": f.name, "message": str(e)})
        for f in dt.fields:
            if f.required and f.name not in values:
                errors.append({"field": f.name, "message": "required field is empty or column missing"})
        if dt.require_one_of and not any(k in values for k in dt.require_one_of):
            errors.append({"field": dt.require_one_of[0],
                           "message": f"one of {list(dt.require_one_of)} is required"})
        rows.append({"row_no": row_no, "values": values, "extras": extras, "errors": errors,
                     "status": "ERROR" if errors else "OK"})
    err = sum(1 for r in rows if r["errors"])
    return {
        "sheet": sh["name"], "data_type": dt.key, "data_type_label": dt.label, "detected_by": how,
        "skip_reason": None, "header_row": sh["header_row"], "headers": sh["headers"],
        "mapping": mapping, "missing_required": missing, "unknown_columns": unknown,
        "fields": [f.name for f in dt.fields],
        "total_rows": len(rows), "ok_rows": len(rows) - err, "error_rows": err,
        "rows": rows, "preview": rows[:PREVIEW_ROWS],
    }


def summarize(results):
    active = [r for r in results if r["data_type"]]
    return {
        "sheets": len(results),
        "sheets_mapped": len(active),
        "total_rows": sum(r["total_rows"] for r in active),
        "ok_rows": sum(r["ok_rows"] for r in active),
        "error_rows": sum(r["error_rows"] for r in active),
        "unknown_columns": sum(len(r["unknown_columns"]) for r in active),
        "missing_required": sum(len(r["missing_required"]) for r in active),
        "data_types": sorted({r["data_type"] for r in active}),
    }
