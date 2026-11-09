"""Etched L6 MES integration sandbox (Inventec MES <-> Etched).

Run locally:   python app.py            (http://127.0.0.1:5050)
Production:    gunicorn -w 2 -b 127.0.0.1:5050 "app:create_app()"
"""

import hashlib
import json
import os
import platform
from pathlib import Path

from flask import (Flask, Response, abort, g, jsonify, make_response, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

import db
import ingest
from l6_schema import BY_KEY, DATA_TYPES, ETCHED_TO_IEC, IEC_TO_ETCHED, full_schema_sql, types_for
from templates_gen import excel_template, json_template

APP_VERSION = "0.1.0"
BASE = Path(__file__).resolve().parent
DIRECTIONS = {"iec": IEC_TO_ETCHED, "etched": ETCHED_TO_IEC}
EXT_FORMAT = {".xlsx": "excel", ".xlsm": "excel", ".json": "json", ".yaml": "yaml", ".yml": "yaml"}
OPEN_PATHS = ("/static/", "/login", "/api/v1/health")


def create_app():
    app = Flask(__name__)
    data_dir = Path(os.environ.get("MES_DATA_DIR", BASE / "data"))
    app.config.update(
        DB_PATH=os.environ.get("MES_DB_PATH", str(data_dir / "mes_l6.db")),
        UPLOAD_DIR=os.environ.get("MES_UPLOAD_DIR", str(data_dir / "uploads")),
        API_KEYS={k.strip() for k in os.environ.get("MES_API_KEYS", "").split(",") if k.strip()},
        MAX_CONTENT_LENGTH=int(os.environ.get("MES_MAX_MB", "50")) * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    Path(app.config["DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    db.init_db(app.config["DB_PATH"])

    def conn():
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    # ------------------------------------------------------------ auth (only when MES_API_KEYS is set)

    @app.before_request
    def _auth():
        keys = app.config["API_KEYS"]
        if not keys or request.path.startswith(OPEN_PATHS):
            return None
        supplied = request.headers.get("X-API-Key") or request.cookies.get("mes_key")
        if supplied in keys:
            return None
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="missing or invalid X-API-Key"), 401
        return redirect(url_for("login", next=request.path))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            key = request.form.get("key", "").strip()
            if key in app.config["API_KEYS"]:
                resp = make_response(redirect(request.args.get("next") or url_for("home")))
                resp.set_cookie("mes_key", key, httponly=True, samesite="Lax", max_age=30 * 24 * 3600)
                return resp
            error = "Invalid access key"
        return render_template("login.html", error=error)

    @app.context_processor
    def _globals():
        return {"app_version": APP_VERSION, "auth_enabled": bool(app.config["API_KEYS"])}

    # ------------------------------------------------------------ pages

    @app.get("/")
    def home():
        c = conn()
        return render_template("index.html", recent=db.list_batches(c, limit=8), counts=db.table_counts(c))

    def _type_meta(direction):
        return {dt.key: {"label": dt.label,
                         "required": [f.name for f in dt.fields if f.required],
                         "fields": [f.name for f in dt.fields]} for dt in types_for(direction)}

    @app.get("/import")
    def page_import():
        return render_template("upload.html", direction="iec", types=types_for(IEC_TO_ETCHED),
                               type_meta=_type_meta(IEC_TO_ETCHED),
                               title="Inventec MES Import",
                               subtitle="Inventec (IEC) sends WIP, genealogy and production-stage data to Etched. "
                                        "Everything received is stored in separate iec_* staging tables.")

    @app.get("/etched-upload")
    def page_etched():
        return render_template("upload.html", direction="etched", types=types_for(ETCHED_TO_IEC),
                               type_meta=_type_meta(ETCHED_TO_IEC),
                               title="Etched Test Results Upload",
                               subtitle="Etched publishes station results, measurements and identities. "
                                        "Inventec pulls them with GET /api/v1/etched/<data_type>.")

    @app.get("/history")
    def page_history():
        d = DIRECTIONS.get(request.args.get("direction", ""))
        return render_template("history.html", batches=db.list_batches(conn(), limit=500, direction=d),
                               direction=request.args.get("direction", ""))

    @app.get("/batch/<batch_id>")
    def page_batch(batch_id):
        c = conn()
        b = db.get_batch(c, batch_id) or abort(404)
        dtype = request.args.get("data_type") or (b["data_types"].split(",")[0] if b["data_types"] else None)
        table, rows, total = (None, [], 0)
        if dtype in BY_KEY:
            table, rows, total = db.batch_rows(c, b, dtype, limit=500)
        return render_template("batch.html", b=b, mapping=db.batch_mapping(c, batch_id),
                               dtype=dtype, table=table, rows=rows, total=total,
                               dtypes=[t for t in (b["data_types"] or "").split(",") if t])

    @app.get("/schema")
    def page_schema():
        return render_template("schema.html", iec=types_for(IEC_TO_ETCHED), etched=types_for(ETCHED_TO_IEC),
                               schema_sql=full_schema_sql())

    @app.get("/api-docs")
    def page_api():
        return render_template("api.html", base=request.host_url.rstrip("/"),
                               iec=types_for(IEC_TO_ETCHED), etched=types_for(ETCHED_TO_IEC))

    # ------------------------------------------------------------ API: meta

    @app.get("/api/v1/health")
    def health():
        return jsonify(ok=True, service="etched-l6-mes-api", version=APP_VERSION,
                       auth_required=bool(app.config["API_KEYS"]), python=platform.python_version(),
                       server_time_utc=db.now_utc())

    @app.get("/api/v1/schema")
    def api_schema():
        d = DIRECTIONS.get(request.args.get("direction", ""))
        return jsonify(data_types=[dt.to_dict() for dt in DATA_TYPES if not d or dt.direction == d])

    @app.get("/api/v1/schema/<data_type>")
    def api_schema_one(data_type):
        dt = BY_KEY.get(data_type) or abort(404)
        return jsonify(dt.to_dict())

    @app.get("/api/v1/schema.sql")
    def api_schema_sql():
        return Response(full_schema_sql(), mimetype="text/plain",
                        headers={"Content-Disposition": "attachment; filename=etched_l6_schema.sql"})

    @app.get("/api/v1/templates/<name>")
    def api_template(name):
        key, _, ext = name.rpartition(".")
        direction = DIRECTIONS.get(request.args.get("direction", "iec"), IEC_TO_ETCHED)
        if key != "all":
            if key not in BY_KEY:
                abort(404)
            direction = BY_KEY[key].direction
        side = "IEC" if direction == IEC_TO_ETCHED else "Etched"
        if ext == "xlsx":
            return Response(excel_template(key, direction),
                            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f"attachment; filename=L6_{side}_{key}_template.xlsx"})
        if ext == "json":
            return Response(json_template(key, direction), mimetype="application/json",
                            headers={"Content-Disposition": f"attachment; filename=L6_{side}_{key}_template.json"})
        abort(404)

    # ------------------------------------------------------------ API: upload

    def _read_request():
        """Return (raw, fmt, file_name, params) from multipart file, pasted text, or raw JSON/YAML body."""
        params = {k: v for k, v in request.args.items()}
        params.update({k: v for k, v in request.form.items() if k != "payload_text"})
        up = request.files.get("file")
        if up and up.filename:
            ext = Path(up.filename).suffix.lower()
            fmt = EXT_FORMAT.get(ext)
            if not fmt:
                raise ingest.IngestError(f"Unsupported file type {ext!r}. Use .xlsx, .xlsm, .json, .yaml")
            return up.read(), fmt, up.filename, params
        if request.form.get("payload_text", "").strip():
            fmt = request.form.get("format", "json").lower()
            return request.form["payload_text"].encode("utf-8"), "yaml" if fmt == "yaml" else "json", None, params
        body = request.get_data()
        if body:
            ctype = (request.content_type or "").lower()
            fmt = "yaml" if "yaml" in ctype else "json"
            meta = ingest.payload_meta(body, fmt)
            for k, v in meta.items():
                params.setdefault(k, v)
            return body, fmt, None, params
        raise ingest.IngestError("No data. Send a file (multipart field 'file'), or a JSON/YAML request body.")

    def _process(direction, commit):
        try:
            raw, fmt, file_name, params = _read_request()
            sheet_map = json.loads(params["sheet_map"]) if params.get("sheet_map") else None
            sheets = ingest.read_payload(raw, fmt)
            if not sheets:
                raise ingest.IngestError("No sheet with a header row and data was found.")
            results = ingest.analyze(sheets, direction, params.get("data_type"), sheet_map)
        except (ingest.IngestError, json.JSONDecodeError) as e:
            return jsonify(ok=False, error=str(e)), 400

        summary = ingest.summarize(results)
        sha = hashlib.sha256(raw).hexdigest()
        c = conn()
        dup = db.find_duplicate(c, sha, direction)
        resp = {"ok": True, "direction": direction, "file_format": fmt, "file_name": file_name,
                "file_sha256": sha, "duplicate_of": dup, "summary": summary}

        if not commit:
            resp["sheets"] = [{k: v for k, v in r.items() if k != "rows"} for r in results]
            return jsonify(resp)

        if not summary["data_types"]:
            return jsonify(ok=False, error="Nothing to store: no sheet matched a data type.",
                           sheets=[_brief(r) for r in results]), 400

        strict = str(params.get("strict", "true")).lower() not in ("0", "false", "no")
        batch_id = db.new_batch_id(direction)
        ext = {"excel": ".xlsx", "json": ".json", "yaml": ".yaml"}[fmt]
        stored = Path(app.config["UPLOAD_DIR"]) / f"{batch_id}_{secure_filename(file_name or 'payload' + ext)}"
        stored.write_bytes(raw)
        meta = {
            "batch_id": batch_id, "direction": direction, "file_format": fmt, "file_name": file_name,
            "file_sha256": sha, "stored_path": str(stored),
            "source_system": params.get("source_system") or ("INVENTEC_MES" if direction == IEC_TO_ETCHED else "ETCHED"),
            "submitted_by": params.get("submitted_by"), "source_tz": params.get("source_tz"),
            "note": params.get("note"), "client_ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        }
        with c:
            batch_id, status, inserted = db.save_batch(c, meta, results, strict)
        resp.update(batch_id=batch_id, status=status, inserted_rows=inserted,
                    batch_url=url_for("page_batch", batch_id=batch_id, _external=True),
                    sheets=[_brief(r) for r in results])
        return jsonify(resp), (422 if status == "REJECTED" else 200)

    def _brief(r):
        errs = [{"row_no": x["row_no"], **e} for x in r["rows"] for e in x["errors"]]
        return {"sheet": r["sheet"], "data_type": r["data_type"], "skip_reason": r["skip_reason"],
                "total_rows": r["total_rows"], "ok_rows": r["ok_rows"], "error_rows": r["error_rows"],
                "missing_required": r["missing_required"], "unknown_columns": r["unknown_columns"],
                "mapping": r["mapping"], "errors": errs[:200], "errors_truncated": len(errs) > 200}

    @app.post("/api/v1/validate")
    def api_validate():
        d = DIRECTIONS.get(request.args.get("direction") or request.form.get("direction") or "iec")
        if not d:
            return jsonify(ok=False, error="direction must be iec or etched"), 400
        return _process(d, commit=False)

    @app.post("/api/v1/iec/upload")
    def api_iec_upload():
        return _process(IEC_TO_ETCHED, commit=True)

    @app.post("/api/v1/etched/upload")
    def api_etched_upload():
        return _process(ETCHED_TO_IEC, commit=True)

    # ------------------------------------------------------------ API: read back

    @app.get("/api/v1/batches")
    def api_batches():
        d = DIRECTIONS.get(request.args.get("direction", ""))
        return jsonify(batches=db.list_batches(conn(), limit=int(request.args.get("limit", 100)), direction=d))

    @app.get("/api/v1/batches/<batch_id>")
    def api_batch(batch_id):
        c = conn()
        b = db.get_batch(c, batch_id)
        if not b:
            return jsonify(ok=False, error="batch not found"), 404
        b.pop("stored_path", None)
        return jsonify(batch=b, mapping=db.batch_mapping(c, batch_id))

    @app.get("/api/v1/batches/<batch_id>/rows")
    def api_batch_rows(batch_id):
        c = conn()
        b = db.get_batch(c, batch_id)
        dtype = request.args.get("data_type") or (b["data_types"].split(",")[0] if b and b["data_types"] else None)
        if not b or dtype not in BY_KEY:
            return jsonify(ok=False, error="batch or data_type not found"), 404
        table, rows, total = db.batch_rows(c, b, dtype, limit=min(int(request.args.get("limit", 500)), 5000),
                                           offset=int(request.args.get("offset", 0)))
        return jsonify(table=table, total=total, rows=rows)

    @app.get("/api/v1/batches/<batch_id>/file")
    def api_batch_file(batch_id):
        b = db.get_batch(conn(), batch_id)
        if not b or not b["stored_path"] or not Path(b["stored_path"]).exists():
            abort(404)
        return send_file(b["stored_path"], as_attachment=True,
                         download_name=b["file_name"] or Path(b["stored_path"]).name)

    @app.get("/api/v1/etched/<data_type>")
    def api_etched_pull(data_type):
        dt = BY_KEY.get(data_type)
        if not dt or dt.direction != ETCHED_TO_IEC:
            return jsonify(ok=False, error=f"unknown Etched data_type. Valid: "
                                           f"{[t.key for t in types_for(ETCHED_TO_IEC)]}"), 404
        since = int(request.args.get("since_id", 0))
        limit = min(int(request.args.get("limit", 500)), 5000)
        recs = db.etched_records(conn(), data_type, since, request.args.get("unit_sn"), limit)
        return jsonify(data_type=data_type, count=len(recs),
                       next_since_id=recs[-1]["id"] if recs else since, records=recs)

    @app.errorhandler(413)
    def _too_big(_e):
        return jsonify(ok=False, error=f"File too large (max {app.config['MAX_CONTENT_LENGTH'] // 1048576} MB)"), 413

    return app


if __name__ == "__main__":
    host = os.environ.get("MES_HOST", "0.0.0.0")
    port = int(os.environ.get("MES_PORT", "5050"))
    create_app().run(host=host, port=port, debug=os.environ.get("MES_DEBUG") == "1")
