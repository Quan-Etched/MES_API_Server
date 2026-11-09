(() => {
  const M = window.MES;
  const $ = (id) => document.getElementById(id);
  const esc = (v) => (v === null || v === undefined ? "" : String(v))
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const state = { fmt: "excel", file: null, sheetMap: {}, last: null, activeSheet: 0 };

  // ---------------------------------------------------------------- format + inputs
  document.querySelectorAll('input[name="fmt"]').forEach((r) => r.addEventListener("change", (e) => {
    state.fmt = e.target.value;
    $("excel-box").classList.toggle("hidden", state.fmt !== "excel");
    $("json-box").classList.toggle("hidden", state.fmt !== "json");
    resetPreview();
  }));

  const drop = $("drop");
  drop.addEventListener("click", () => $("file-excel").click());
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("over");
    if (e.dataTransfer.files.length) setExcel(e.dataTransfer.files[0]);
  });
  $("file-excel").addEventListener("change", (e) => e.target.files[0] && setExcel(e.target.files[0]));
  function setExcel(f) {
    state.file = f; state.sheetMap = {};
    $("file-name").textContent = `${f.name} · ${(f.size / 1024).toFixed(1)} KB`;
    resetPreview();
  }

  $("json-file-btn").addEventListener("click", () => $("file-json").click());
  $("file-json").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    $("json-text").value = await f.text();
    $("json-file-name").textContent = f.name;
    state.sheetMap = {}; resetPreview();
  });
  $("json-text").addEventListener("input", () => { state.sheetMap = {}; resetPreview(); });
  $("json-clear").addEventListener("click", () => { $("json-text").value = ""; resetPreview(); });
  $("json-format").addEventListener("click", () => {
    try { $("json-text").value = JSON.stringify(JSON.parse($("json-text").value), null, 2); }
    catch (err) { showMsg("err", "JSON parse error: " + err.message); }
  });
  $("json-example").addEventListener("click", async () => {
    const r = await fetch(templateUrl("json"));
    $("json-text").value = await r.text();
    state.sheetMap = {}; resetPreview();
  });

  function templateUrl(ext) {
    const t = $("data_type").value;
    return t === "auto" ? `/api/v1/templates/all.${ext}?direction=${M.direction}` : `/api/v1/templates/${t}.${ext}`;
  }
  function refreshTemplateLinks() {
    $("tpl-xlsx").href = templateUrl("xlsx");
    $("tpl-json").href = templateUrl("json");
  }
  $("data_type").addEventListener("change", () => { state.sheetMap = {}; refreshTemplateLinks(); resetPreview(); });
  refreshTemplateLinks();

  // ---------------------------------------------------------------- requests
  function buildForm() {
    const fd = new FormData();
    if (state.fmt === "excel") {
      if (!state.file) throw new Error("Choose an Excel file first.");
      fd.append("file", state.file);
    } else {
      const txt = $("json-text").value.trim();
      if (!txt) throw new Error("Paste JSON or load a .json / .yaml file first.");
      const name = $("json-file-name").textContent;
      fd.append("payload_text", txt);
      fd.append("format", /\.ya?ml$/i.test(name) ? "yaml" : "json");
    }
    fd.append("direction", M.direction);
    fd.append("data_type", $("data_type").value);
    fd.append("submitted_by", $("submitted_by").value);
    fd.append("source_tz", $("source_tz").value);
    fd.append("note", $("note").value);
    if (Object.keys(state.sheetMap).length) fd.append("sheet_map", JSON.stringify(state.sheetMap));
    if ($("strict")) fd.append("strict", $("strict").checked ? "true" : "false");
    return fd;
  }

  async function post(url) {
    let fd;
    try { fd = buildForm(); } catch (e) { showMsg("err", e.message); return null; }
    $("status").textContent = "Working…";
    try {
      const r = await fetch(url, { method: "POST", body: fd });
      const j = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
      $("status").textContent = "";
      return { http: r.status, body: j };
    } catch (e) {
      $("status").textContent = "";
      showMsg("err", "Network error: " + e.message);
      return null;
    }
  }

  async function validate() {
    showMsg(null);
    const res = await post(`/api/v1/validate?direction=${M.direction}`);
    if (!res) return;
    if (!res.body.ok) { resetPreview(); showMsg("err", res.body.error); return; }
    state.last = res.body;
    if (state.activeSheet >= res.body.sheets.length) state.activeSheet = 0;
    renderPreview();
    $("btn-submit").disabled = res.body.summary.sheets_mapped === 0;
  }

  async function submit() {
    if (!state.last) return;
    const s = state.last.summary;
    if (s.error_rows > 0 || s.missing_required > 0) {
      const what = M.direction === "iec"
        ? "Rows with errors will be stored with row_status=ERROR so both sides can review them."
        : ($("strict") && $("strict").checked ? "The batch will be REJECTED." : "Rows with errors will be skipped.");
      if (!confirm(`${s.error_rows} row(s) have errors. ${what}\n\nSubmit anyway?`)) return;
    }
    const res = await post(`/api/v1/${M.direction}/upload`);
    if (!res) return;
    const b = res.body;
    if (!b.ok) { showMsg("err", b.error || "Upload failed"); return; }
    const cls = b.status === "REJECTED" ? "err" : (b.summary.error_rows ? "warn" : "ok");
    showMsg(cls, `<b>${esc(b.status)}</b> · batch <a class="mono" href="/batch/${esc(b.batch_id)}">${esc(b.batch_id)}</a>` +
      ` · ${b.inserted_rows} row(s) stored of ${b.summary.total_rows}` +
      (b.summary.error_rows ? ` · ${b.summary.error_rows} row(s) with errors` : ""), true);
    $("btn-submit").disabled = true;
  }

  $("btn-validate").addEventListener("click", validate);
  $("btn-submit").addEventListener("click", submit);

  // ---------------------------------------------------------------- rendering
  function showMsg(kind, text, html) {
    const el = $("msg");
    if (!kind) { el.innerHTML = ""; return; }
    el.innerHTML = `<div class="alert ${kind}">${html ? text : esc(text)}</div>`;
  }
  function resetPreview() {
    state.last = null;
    $("preview-panel").classList.add("hidden");
    $("btn-submit").disabled = true;
  }

  function chip(n, label, cls) { return `<div class="chip ${cls || ""}"><span class="n">${n}</span><span class="l">${label}</span></div>`; }

  function renderPreview() {
    const r = state.last, s = r.summary;
    $("preview-panel").classList.remove("hidden");
    $("chips").innerHTML =
      chip(s.sheets_mapped + " / " + s.sheets, "sheets mapped") +
      chip(s.total_rows, "rows") +
      chip(s.ok_rows, "rows OK", "ok") +
      chip(s.error_rows, "rows with errors", s.error_rows ? "err" : "") +
      chip(s.missing_required, "missing required columns", s.missing_required ? "err" : "") +
      chip(s.unknown_columns, "unmatched columns", s.unknown_columns ? "warn" : "") +
      chip(esc(r.file_format.toUpperCase()), "format");
    $("dup").innerHTML = r.duplicate_of
      ? `<div class="alert warn">This exact file was already uploaded as batch <a class="mono" href="/batch/${esc(r.duplicate_of.batch_id)}">${esc(r.duplicate_of.batch_id)}</a> (${esc(r.duplicate_of.received_ts)} UTC). Submitting again creates a new batch.</div>`
      : "";
    $("sheet-tabs").innerHTML = r.sheets.map((sh, i) => {
      const bad = sh.error_rows || sh.missing_required.length;
      const tag = sh.data_type ? `<span class="badge ${bad ? "err" : "ok"}">${esc(sh.data_type)}</span>` : `<span class="badge">skipped</span>`;
      return `<button class="tab ${i === state.activeSheet ? "active" : ""}" data-i="${i}">${esc(sh.sheet)} ${tag}</button>`;
    }).join("");
    $("sheet-tabs").querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
      state.activeSheet = +t.dataset.i; renderPreview();
    }));
    renderSheet(r.sheets[state.activeSheet]);
  }

  function renderSheet(sh) {
    const opts = [`<option value="">Auto-detect</option>`]
      .concat(Object.entries(M.types).map(([k, v]) => `<option value="${k}" ${sh.data_type === k ? "selected" : ""}>${esc(v.label)} · ${k}</option>`))
      .concat([`<option value="skip" ${!sh.data_type && state.sheetMap[sh.sheet] === "skip" ? "selected" : ""}>Skip this sheet</option>`]);
    let h = `<div class="form-row" style="grid-template-columns:2fr 1fr 1fr 1fr;margin-top:0">
      <div class="field"><label>Sheet "${esc(sh.sheet)}" → Etched data type</label><select id="sheet-type">${opts.join("")}</select></div>
      <div class="field"><label>Detected by</label><div>${esc(sh.detected_by || "—")}</div></div>
      <div class="field"><label>Header row</label><div>${esc(sh.header_row || "—")}</div></div>
      <div class="field"><label>Rows</label><div>${sh.total_rows} total · ${sh.ok_rows} OK · ${sh.error_rows} error</div></div>
    </div>`;
    if (!sh.data_type) {
      h += `<div class="alert warn">${esc(sh.skip_reason)}. Pick a data type above to map this sheet.</div>`;
      $("sheet-body").innerHTML = h; bindSheetType(sh); return;
    }
    const meta = M.types[sh.data_type];
    if (sh.missing_required.length) {
      h += `<div class="alert err">Missing required Etched fields: ${sh.missing_required.map((f) => `<code>${esc(f)}</code>`).join(", ")}</div>`;
    }
    if (sh.unknown_columns.length) {
      h += `<div class="alert warn">${sh.unknown_columns.length} column(s) do not match any Etched field. They are kept in <code>extra_json</code> but not mapped: ${sh.unknown_columns.map((c) => `<code>${esc(c)}</code>`).join(", ")}</div>`;
    }
    const mappedTargets = new Set(sh.mapping.filter((m) => m.target).map((m) => m.target));
    const notSupplied = meta.fields.filter((f) => !mappedTargets.has(f) && !meta.required.includes(f));

    h += `<div class="grid2">
      <div><h3>Field mapping (${sh.mapping.length} columns)</h3>
        <div class="scroll" style="max-height:340px"><table class="t">
          <tr><th>Source column</th><th>Etched field</th><th>Match</th><th>Suggestion</th></tr>
          ${sh.mapping.map((m) => `<tr><td class="mono">${esc(m.source)}</td>
            <td class="mono">${m.target ? esc(m.target) + (meta.required.includes(m.target) ? ' <span class="badge req">required</span>' : "") : "—"}</td>
            <td><span class="badge ${m.match}">${m.match}</span></td>
            <td class="mono muted">${m.suggestion ? "did you mean " + esc(m.suggestion) + "?" : ""}</td></tr>`).join("")}
        </table></div></div>
      <div><h3>Etched fields not supplied (optional)</h3>
        <p class="mono muted" style="margin:0 0 10px">${notSupplied.length ? notSupplied.map(esc).join(", ") : "none — all fields supplied"}</p>
        <h3>Row errors</h3>${rowErrors(sh)}</div>
    </div>`;

    const fields = meta.fields.filter((f) => mappedTargets.has(f) || meta.required.includes(f));
    const extras = sh.unknown_columns;
    h += `<h3>Data preview ${sh.preview.length < sh.total_rows ? `(first ${sh.preview.length} of ${sh.total_rows} rows)` : `(${sh.total_rows} rows)`}</h3>
      <div class="scroll"><table class="t"><tr><th>Row</th><th>Status</th>
      ${fields.map((f) => `<th class="mono ${meta.required.includes(f) ? "col-req" : ""}">${esc(f)}</th>`).join("")}
      ${extras.map((c) => `<th class="mono col-extra" title="Not an Etched field">${esc(c)}</th>`).join("")}</tr>
      ${sh.preview.map((row) => {
        const errBy = {};
        row.errors.forEach((e) => { if (e.field) errBy[e.field] = (errBy[e.field] ? errBy[e.field] + "; " : "") + e.message; });
        return `<tr class="${row.status === "ERROR" ? "row-err" : ""}"><td>${row.row_no}</td>
          <td><span class="badge ${row.status}">${row.status}</span></td>
          ${fields.map((f) => errBy[f] ? `<td class="cell-err" title="${esc(errBy[f])}">${esc(row.values[f]) || "∅"}</td>` : `<td>${esc(row.values[f])}</td>`).join("")}
          ${extras.map((c) => `<td class="cell-extra">${esc(row.extras[c])}</td>`).join("")}</tr>`;
      }).join("")}
      </table></div>`;
    $("sheet-body").innerHTML = h;
    bindSheetType(sh);
  }

  function rowErrors(sh) {
    const list = [];
    sh.preview.forEach((r) => r.errors.forEach((e) => list.push([r.row_no, e.field, e.message])));
    if (!list.length) return `<p class="muted" style="margin:0">${sh.error_rows ? "Errors are beyond the preview window." : "No errors."}</p>`;
    return `<div class="scroll" style="max-height:260px"><table class="t"><tr><th>Row</th><th>Field</th><th>Message</th></tr>
      ${list.slice(0, 300).map(([n, f, m]) => `<tr><td>${n}</td><td class="mono">${esc(f || "")}</td><td>${esc(m)}</td></tr>`).join("")}</table></div>`;
  }

  function bindSheetType(sh) {
    $("sheet-type").addEventListener("change", (e) => {
      if (e.target.value) state.sheetMap[sh.sheet] = e.target.value; else delete state.sheetMap[sh.sheet];
      validate();
    });
  }
})();
