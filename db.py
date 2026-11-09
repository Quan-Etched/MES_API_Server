import json
import secrets
import sqlite3
from datetime import datetime, timezone

from l6_schema import BY_KEY, ETCHED_TO_IEC, IEC_TO_ETCHED, full_schema_sql


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path):
    conn = connect(path)
    try:
        conn.executescript(full_schema_sql())
    finally:
        conn.close()


def new_batch_id(direction):
    prefix = "IEC" if direction == IEC_TO_ETCHED else "ETC"
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2).upper()}"


def find_duplicate(conn, sha256, direction):
    row = conn.execute(
        "SELECT batch_id, received_ts FROM upload_batch WHERE file_sha256=? AND direction=? "
        "ORDER BY received_ts DESC LIMIT 1", (sha256, direction)).fetchone()
    return dict(row) if row else None


def save_batch(conn, meta, results, strict):
    """Write one upload. IEC rows (OK and ERROR) go to iec_<type>; Etched OK rows go to l6_<type>.

    Returns (batch_id, status, inserted_rows).
    """
    direction = meta["direction"]
    active = [r for r in results if r["data_type"]]
    total = sum(r["total_rows"] for r in active)
    errors = sum(r["error_rows"] for r in active)
    ts = now_utc()
    batch_id = meta.get("batch_id") or new_batch_id(direction)

    if direction == IEC_TO_ETCHED:
        status = "RECEIVED" if errors == 0 else "RECEIVED_WITH_ERRORS"
    elif errors and strict:
        status = "REJECTED"
    else:
        status = "COMMITTED" if errors == 0 else "PARTIAL"

    conn.execute(
        "INSERT INTO upload_batch (batch_id, direction, source_system, submitted_by, file_format, file_name,"
        " file_sha256, stored_path, source_tz, note, data_types, total_rows, ok_rows, error_rows, status,"
        " client_ip, received_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, direction, meta.get("source_system") or "", meta.get("submitted_by"), meta["file_format"],
         meta.get("file_name"), meta.get("file_sha256"), meta.get("stored_path"), meta.get("source_tz"),
         meta.get("note"), ",".join(sorted({r["data_type"] for r in active})), total, total - errors, errors,
         status, meta.get("client_ip"), ts))

    for r in results:
        for m in r["mapping"]:
            conn.execute(
                "INSERT INTO upload_field_mapping (batch_id, sheet_name, data_type, source_column, target_field,"
                " match_type, suggestion) VALUES (?,?,?,?,?,?,?)",
                (batch_id, r["sheet"], r["data_type"], m["source"], m["target"], m["match"], m["suggestion"]))

    inserted = 0
    if status != "REJECTED":
        for r in active:
            dt = BY_KEY[r["data_type"]]
            names = [f.name for f in dt.fields]
            if direction == IEC_TO_ETCHED:
                cols = ["batch_id", "sheet_name", "row_no", "row_status", "row_errors"] + names + ["extra_json", "received_ts"]
                sql = f"INSERT INTO {dt.iec_table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in r["rows"]:
                    vals = [None if row["values"].get(n) is None else str(row["values"][n]) for n in names]
                    conn.execute(sql, [batch_id, r["sheet"], row["row_no"], row["status"],
                                       json.dumps(row["errors"], ensure_ascii=False) if row["errors"] else None]
                                 + vals + [json.dumps(row["extras"], ensure_ascii=False) if row["extras"] else None, ts])
                    inserted += 1
            else:
                cols = names + ["batch_id", "loaded_ts"]
                sql = f"INSERT INTO {dt.core_table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in r["rows"]:
                    if row["errors"]:
                        continue
                    try:
                        conn.execute(sql, [row["values"].get(n) for n in names] + [batch_id, ts])
                        inserted += 1
                    except sqlite3.IntegrityError as e:
                        row["errors"].append({"field": None, "message": f"database rejected row: {e}"})
    return batch_id, status, inserted


def list_batches(conn, limit=200, direction=None):
    sql = "SELECT * FROM upload_batch"
    args = []
    if direction:
        sql += " WHERE direction=?"
        args.append(direction)
    sql += " ORDER BY received_ts DESC, batch_id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def get_batch(conn, batch_id):
    row = conn.execute("SELECT * FROM upload_batch WHERE batch_id=?", (batch_id,)).fetchone()
    return dict(row) if row else None


def batch_mapping(conn, batch_id):
    return [dict(r) for r in conn.execute(
        "SELECT sheet_name, data_type, source_column, target_field, match_type, suggestion "
        "FROM upload_field_mapping WHERE batch_id=? ORDER BY id", (batch_id,))]


def batch_rows(conn, batch, data_type, limit=500, offset=0):
    dt = BY_KEY[data_type]
    table = dt.iec_table if batch["direction"] == IEC_TO_ETCHED else dt.core_table
    rows = conn.execute(f"SELECT * FROM {table} WHERE batch_id=? ORDER BY id LIMIT ? OFFSET ?",
                        (batch["batch_id"], limit, offset)).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE batch_id=?", (batch["batch_id"],)).fetchone()[0]
    return table, [dict(r) for r in rows], total


def etched_records(conn, data_type, since_id=0, unit_sn=None, limit=500):
    dt = BY_KEY[data_type]
    if dt.direction != ETCHED_TO_IEC:
        raise KeyError(data_type)
    sql = f"SELECT * FROM {dt.core_table} WHERE id > ?"
    args = [since_id]
    if unit_sn:
        sql += " AND unit_sn = ?"
        args.append(unit_sn)
    sql += " ORDER BY id LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def table_counts(conn):
    out = {}
    for dt in BY_KEY.values():
        out[dt.core_table] = conn.execute(f"SELECT COUNT(*) FROM {dt.core_table}").fetchone()[0]
        if dt.direction == IEC_TO_ETCHED:
            out[dt.iec_table] = conn.execute(f"SELECT COUNT(*) FROM {dt.iec_table}").fetchone()[0]
    return out
