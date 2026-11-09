"""Etched L6 data dictionary.

Single source of truth for:
  * the Etched L6 core tables   (l6_<data_type>)
  * the IEC staging tables      (iec_<data_type>)  -- every IEC upload lands here
  * header matching / validation of uploaded Excel / JSON / YAML
  * the downloadable templates and the Data Dictionary page

Direction:
  IEC_TO_ETCHED  -> uploaded on "Inventec MES Import", stored in iec_<data_type>
  ETCHED_TO_IEC  -> uploaded on "Etched Test Results Upload", stored in l6_<data_type>,
                    IEC pulls it back with GET /api/v1/etched/<data_type>
"""

from dataclasses import dataclass, field as dc_field

IEC_TO_ETCHED = "IEC_TO_ETCHED"
ETCHED_TO_IEC = "ETCHED_TO_IEC"

SQL_TYPES = {"TEXT": "TEXT", "INT": "INTEGER", "REAL": "REAL", "DATETIME": "TEXT", "DATE": "TEXT"}

RESULT_ENUM = ("PASS", "FAIL", "ABORT")
BUILD_STAGE_ENUM = ("EVT", "DVT", "PVT", "MP")
SECTION_ENUM = ("SMT", "DIP", "ASSY", "TEST", "QC", "PACK", "REPAIR")

# Header aliases shared by every data type that has the canonical field.
COMMON_ALIASES = {
    "unit_sn": ["sn", "serial", "serial_no", "serial_number", "uut_sn", "uut_serial_number",
                "barcode", "barcode_mb", "board_sn", "module_sn", "pcba_sn", "usn"],
    "work_order": ["wo", "wo_no", "wo_number", "mo", "mo_no", "work_order_no", "workorder"],
    "inventec_pn": ["pn", "part_number", "part_no", "iec_pn", "inventec_part_number", "board_name"],
    "etched_pn": ["etched_part_number", "customer_pn", "cust_pn"],
    "revision": ["rev", "board_rev", "pca_rev"],
    "station_id": ["station", "station_no", "station_code", "ict_no", "machine_id", "equipment_id"],
    "station_name": ["station_desc"],
    "line_id": ["line", "line_no", "line_name"],
    "operator_id": ["oper_id", "op_id", "opid", "operator", "emp_id", "employee_id", "user_id"],
    "fixture_id": ["fixture", "fix_id", "fixture_sn", "jig_id"],
    "start_ts": ["start_time", "starttime", "test_start", "begin_time", "start"],
    "end_ts": ["end_time", "endtime", "test_end", "finish_time", "end"],
    "result": ["test_result", "overall_result", "overallresult", "pass_fail", "judge", "judgement"],
    "failure_code": ["error_code", "fail_code", "err_code", "overallerrorcode", "overall_error_code"],
    "failure_desc": ["fail_desc", "error_desc", "error_message", "failure_text", "fail_reason"],
    "test_time_s": ["test_time", "elapsed_seconds", "duration", "duration_s", "cycle_time"],
    "low_limit": ["lsl", "lower_limit", "lowerlimit", "lo_limit", "min", "min_limit"],
    "high_limit": ["usl", "upper_limit", "upperlimit", "hi_limit", "max", "max_limit"],
    "item_name": ["test_item", "item", "measurement", "measurement_name", "parameter"],
    "build_stage": ["stage", "phase", "build_phase"],
    "site_code": ["site", "plant", "factory"],
    "lot_number": ["lot", "lot_no", "batch_no"],
    "date_code": ["dc", "d_c"],
    "program_version": ["software_rev", "sw_rev", "test_program", "program_rev", "flow_version"],
    "log_uri": ["log_path", "log_file", "log_link", "log_url"],
}


@dataclass
class Field:
    name: str
    type: str = "TEXT"
    required: bool = False
    desc: str = ""
    enum: tuple = ()
    example: object = None
    aliases: tuple = ()

    def all_aliases(self):
        return list(self.aliases) + COMMON_ALIASES.get(self.name, [])

    def to_dict(self):
        return {
            "name": self.name, "type": self.type, "required": self.required,
            "description": self.desc, "allowed_values": list(self.enum),
            "example": self.example, "aliases": self.all_aliases(),
        }


@dataclass
class DataType:
    key: str
    label: str
    direction: str
    ref: str
    desc: str
    fields: list
    aliases: tuple = ()
    require_one_of: tuple = ()
    extra_sql: list = dc_field(default_factory=list)

    @property
    def core_table(self):
        return f"l6_{self.key}"

    @property
    def iec_table(self):
        return f"iec_{self.key}"

    def field(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def example_row(self):
        return {f.name: f.example for f in self.fields if f.example is not None}

    def to_dict(self):
        return {
            "key": self.key, "label": self.label, "direction": self.direction,
            "reference": self.ref, "description": self.desc,
            "core_table": self.core_table,
            "staging_table": self.iec_table if self.direction == IEC_TO_ETCHED else None,
            "require_one_of": list(self.require_one_of),
            "fields": [f.to_dict() for f in self.fields],
        }


F = Field

DATA_TYPES = [
    # ------------------------------------------------------------------ IEC -> Etched
    DataType(
        "work_order", "Work Order", IEC_TO_ETCHED, "P-02",
        "Work order / lot master. One row per work order.",
        [
            F("work_order", required=True, desc="Inventec work order number", example="WO-L6-20261101-001"),
            F("inventec_pn", required=True, desc="Inventec part number of the built item", example="1395T3757903"),
            F("etched_pn", desc="Etched part number", example="ET-128X-L6-MOD-A01"),
            F("revision", desc="Hardware revision built on this WO", example="X01"),
            F("bom_revision", desc="BOM revision used by the WO", example="BOM-A03"),
            F("build_stage", required=True, enum=BUILD_STAGE_ENUM, desc="EVT / DVT / PVT / MP", example="DVT"),
            F("qty", "INT", required=True, desc="Planned quantity", example=50, aliases=("wo_qty", "quantity", "plan_qty")),
            F("line_id", desc="SMT / assembly line", example="F6-L03"),
            F("site_code", desc="Inventec site", example="IEC-TH"),
            F("route_id", desc="Planned route name", example="L6-MODULE-ROUTE-V1", aliases=("route", "planned_route")),
            F("wo_status", enum=("RELEASED", "IN_PROGRESS", "CLOSED", "HOLD"), example="IN_PROGRESS", aliases=("status",)),
            F("release_ts", "DATETIME", desc="WO release time", example="2026-11-01 08:00:00"),
        ],
        aliases=("wo", "work_orders", "lot"),
    ),
    DataType(
        "unit", "Unit Master", IEC_TO_ETCHED, "P-01",
        "One row per serialized L6 unit: module, PCBA, ASIC, stiffener, cold plate, VBB, 4U node.",
        [
            F("unit_sn", required=True, desc="Unit serial number", example="LV5ARP0046"),
            F("unit_type", required=True,
              enum=("MODULE", "PCBA", "ASIC", "STIFFENER", "COLD_PLATE", "VBB", "MCU", "NODE_4U", "OTHER"),
              desc="Kind of unit", example="PCBA", aliases=("type", "category", "component_type")),
            F("inventec_pn", required=True, example="1395T3757903"),
            F("etched_pn", example="ET-128X-L6-PCBA-A01"),
            F("revision", example="X01"),
            F("work_order", required=True, example="WO-L6-20261101-001"),
            F("build_stage", enum=BUILD_STAGE_ENUM, example="DVT"),
            F("mfg_date", "DATE", desc="Manufacturing date", example="2026-11-01"),
            F("site_code", example="IEC-TH"),
            F("unit_status", enum=("ACTIVE", "HOLD", "SCRAP", "SHIPPED", "CONSUMED"), example="ACTIVE", aliases=("status",)),
        ],
        aliases=("units", "unit_master", "serial", "sn_list"),
    ),
    DataType(
        "genealogy", "Genealogy (Parent / Child)", IEC_TO_ETCHED, "P-03 / P-11",
        "As-built tree. One row per parent-slot-child link. Swap = DELINK old row + LINK new row.",
        [
            F("parent_sn", required=True, desc="Parent serial", example="GPUM00000000046",
              aliases=("parent", "parent_serial", "assembly_sn")),
            F("child_sn", required=True, desc="Child serial", example="LV5ARP0046",
              aliases=("child", "child_serial", "component_sn", "part_sn")),
            F("slot", required=True, desc="Slot / location on the parent (DUB_0, BO_0, COLD_PLATE_0 ...)", example="DUB_0",
              aliases=("location", "position", "ref_des")),
            F("component_type", required=True,
              enum=("MODULE", "PCBA", "ASIC", "STIFFENER", "COLD_PLATE", "VBB", "MCU", "NODE_4U", "OTHER"),
              example="PCBA", aliases=("category", "child_type")),
            F("child_inventec_pn", example="1395T3757903", aliases=("child_pn",)),
            F("child_etched_pn", example="ET-128X-L6-PCBA-A01"),
            F("vendor", example="Inventec"),
            F("vendor_mpn", example="T3821302B01X020PMA", aliases=("mpn",)),
            F("vendor_lot", example="LOT2610A", aliases=("lot", "lot_no", "lot_number")),
            F("date_code", example="2643"),
            F("link_status", required=True, enum=("LINK", "DELINK"), example="LINK", aliases=("action",)),
            F("link_ts", "DATETIME", required=True, example="2026-11-01 10:15:00", aliases=("ts", "timestamp", "link_time")),
            F("station_id", example="IV-ASSY-01"),
            F("operator_id", example="1209967"),
        ],
        aliases=("bom", "as_built", "hierarchy", "link"),
    ),
    DataType(
        "wip", "WIP Snapshot", IEC_TO_ETCHED, "P-14",
        "Current location of each unit at snapshot time.",
        [
            F("unit_sn", required=True, example="LV5ARP0046"),
            F("work_order", example="WO-L6-20261101-001"),
            F("current_station", required=True, example="IV-ICT-01", aliases=("station_id", "station", "current_stage", "location")),
            F("section", enum=SECTION_ENUM, example="TEST"),
            F("line_id", example="F6-L03"),
            F("wip_status", enum=("QUEUE", "IN_PROCESS", "HOLD", "REPAIR", "DONE"), example="QUEUE", aliases=("status",)),
            F("in_ts", "DATETIME", required=True, desc="Time the unit arrived at current station", example="2026-11-01 10:20:00",
              aliases=("arrive_time", "in_time")),
            F("hold_code", example=None),
            F("snapshot_ts", "DATETIME", desc="Snapshot time (defaults to receive time)", example="2026-11-01 12:00:00"),
        ],
        aliases=("wip_status", "wip_list", "location"),
    ),
    DataType(
        "station_history", "Station History (Route Visits)", IEC_TO_ETCHED, "P-05",
        "One row per scan-in / scan-out at an Inventec station. Used for FPY, misflow and cycle time.",
        [
            F("unit_sn", required=True, example="LV5ARP0046"),
            F("work_order", example="WO-L6-20261101-001"),
            F("station_id", required=True, example="IV-AOI-01"),
            F("station_name", example="AOI after reflow"),
            F("section", enum=SECTION_ENUM, example="SMT"),
            F("line_id", example="F6-L03"),
            F("scan_in_ts", "DATETIME", required=True, example="2026-11-01 09:00:00", aliases=("in_time", "scan_in")),
            F("scan_out_ts", "DATETIME", example="2026-11-01 09:01:30", aliases=("out_time", "scan_out")),
            F("result", enum=RESULT_ENUM, example="PASS"),
            F("failure_code", example=None),
            F("attempt", "INT", example=1, aliases=("retry", "test_count", "faon_times")),
            F("operator_id", example="1209967"),
            F("fixture_id", example=None),
        ],
        aliases=("history", "route_history", "process", "station_visit", "travel"),
    ),
    DataType(
        "test_result", "Inventec Test Result", IEC_TO_ETCHED, "P-08",
        "One row per Inventec test run (ICT, AOI, SPI, X-Ray, BFT, FCT, Hi-pot, burn-in).",
        [
            F("unit_sn", required=True, example="GS69HK0001"),
            F("test_type", required=True,
              enum=("SPI", "AOI", "XRAY", "ICT", "BFT", "FCT", "HIPOT", "BURN_IN", "OTHER"), example="ICT"),
            F("station_id", required=True, example="ATX14-ICT2"),
            F("result", required=True, enum=RESULT_ENUM, example="PASS"),
            F("failure_code", example=None),
            F("failure_desc", example=None),
            F("program_version", example="X01"),
            F("hardware_rev", example="B01"),
            F("start_ts", "DATETIME", required=True, example="2026-10-01 15:24:16"),
            F("end_ts", "DATETIME", example="2026-10-01 15:24:39"),
            F("test_time_s", "REAL", example=23),
            F("attempt", "INT", example=1, aliases=("faon_times", "retry", "test_count")),
            F("operator_id", example="1209967"),
            F("fixture_id", example="T3821302B01X010PMA"),
            F("log_uri", desc="Link to raw log (FTP / S3 / share)", example="ftp://10.2.1.28/ict/GS69HK0001_20261001152416.log"),
        ],
        aliases=("test", "test_log", "ict", "bft", "fct", "results"),
    ),
    DataType(
        "test_measurement", "Inventec Test Measurement", IEC_TO_ETCHED, "P-08",
        "Measured values with limits. Link to the run by unit_sn + station_id + start_ts.",
        [
            F("unit_sn", required=True, example="GS69HK0001"),
            F("test_type", enum=("SPI", "AOI", "XRAY", "ICT", "BFT", "FCT", "HIPOT", "BURN_IN", "OTHER"), example="ICT"),
            F("station_id", example="ATX14-ICT2"),
            F("start_ts", "DATETIME", required=True, desc="start_ts of the parent test run", example="2026-10-01 15:24:16"),
            F("item_name", required=True, example="P0V8_VDD_CORE"),
            F("value", "REAL", example=0.801),
            F("value_text", desc="Non-numeric reading (ok / ns / string)", example=None),
            F("unit", example="V", aliases=("uom", "units")),
            F("low_limit", "REAL", example=0.776),
            F("high_limit", "REAL", example=0.824),
            F("result", required=True, enum=RESULT_ENUM, example="PASS", aliases=("status",)),
        ],
        aliases=("measurement", "measurements", "detail", "test_detail", "parametric"),
        require_one_of=("value", "value_text"),
    ),
    DataType(
        "qc_inspection", "QC / IPQC Inspection", IEC_TO_ETCHED, "P-09",
        "VI / OQC / IPQC check items. unit_sn or lot_number is required.",
        [
            F("unit_sn", example="LV5ARP0046"),
            F("lot_number", example=None),
            F("qc_station", required=True, example="IV-VI-01", aliases=("station_id", "station")),
            F("inspection_type", enum=("FULL", "SAMPLE"), example="FULL"),
            F("sample_size", "INT", example=None),
            F("check_item", required=True, example="Cold plate screw torque mark", aliases=("item", "inspection_item")),
            F("result", required=True, enum=("PASS", "FAIL"), example="PASS"),
            F("defect_code", example=None),
            F("defect_location", example=None),
            F("inspector_id", required=True, example="QC0012", aliases=("inspector", "operator_id")),
            F("inspect_ts", "DATETIME", required=True, example="2026-11-01 11:00:00", aliases=("ts", "inspect_time", "check_time")),
            F("evidence_uri", example=None, aliases=("photo", "evidence", "attachment")),
        ],
        aliases=("qc", "ipqc", "oqc", "vi", "inspection"),
        require_one_of=("unit_sn", "lot_number"),
    ),
    DataType(
        "rework", "Rework / Repair", IEC_TO_ETCHED, "P-07",
        "Repair and component swap records.",
        [
            F("unit_sn", required=True, example="GPUM00000000046", aliases=("parent_sn",)),
            F("slot", example="COLD_PLATE_0"),
            F("old_component_sn", example="CP0000000123", aliases=("old_sn", "removed_sn")),
            F("new_component_sn", example="CP0000000456", aliases=("new_sn", "installed_sn")),
            F("defect_code", example="ICT-SHORT"),
            F("failure_code", desc="Test failure that triggered the repair", example=None),
            F("repair_action", required=True, example="Replace cold plate", aliases=("action", "repair")),
            F("station_id", example="IV-REPAIR-01"),
            F("operator_id", required=True, example="1209967"),
            F("rework_ts", "DATETIME", required=True, example="2026-11-01 13:30:00", aliases=("ts", "repair_time")),
        ],
        aliases=("repair", "swap", "rma"),
    ),
    DataType(
        "material_lot", "Material / Consumable Lot", IEC_TO_ETCHED, "P-10",
        "TIM, thermal pad, gap filler, solder paste, underfill lots applied to a unit.",
        [
            F("unit_sn", required=True, example="GPUM00000000046"),
            F("material_type", required=True,
              enum=("TIM", "THERMAL_PAD", "GAP_FILLER", "SOLDER_PASTE", "UNDERFILL", "ADHESIVE", "OTHER"), example="TIM"),
            F("material_pn", required=True, example="TIM-PN-0001", aliases=("pn", "part_number")),
            F("lot_number", required=True, example="TL2610-07"),
            F("date_code", example="2642"),
            F("expiry_date", "DATE", example="2027-04-30"),
            F("applied_ts", "DATETIME", example="2026-11-01 10:40:00"),
            F("station_id", example="IV-TIM-01"),
            F("operator_id", example="1209967"),
        ],
        aliases=("material", "consumable", "tim"),
    ),
    # ------------------------------------------------------------------ Etched -> IEC
    DataType(
        "etched_station_result", "Etched Station Result", ETCHED_TO_IEC, "U-02 / U-03",
        "Short result for gating, one row per unit per Etched station run.",
        [
            F("unit_sn", required=True, example="GPUM00000000046"),
            F("station_id", required=True, example="ETCHED-MLT-01"),
            F("test_stage", required=True, desc="VBB / MLT / SLT / TIM_BAKE ...", example="MLT"),
            F("result", required=True, enum=RESULT_ENUM, example="PASS"),
            F("failure_code", desc="TH-xxx-NNNN", example=None),
            F("failure_desc", example=None),
            F("suite_name", required=True, example="mlt_full"),
            F("suite_version", example="2026.305.1-gabc1234"),
            F("suite_run_id", required=True, example="RUN-20261101-0001"),
            F("slot_no", "INT", example=0),
            F("start_ts", "DATETIME", required=True, example="2026-11-01 14:00:00"),
            F("end_ts", "DATETIME", required=True, example="2026-11-01 14:42:10"),
            F("duration_s", "REAL", example=2530),
            F("attempt", "INT", example=1),
            F("operator_id", example="ETCH-OP-01"),
            F("run_link", example="s3://etched-mfg-logs/RUN-20261101-0001/"),
        ],
        aliases=("station_result", "result", "summary"),
    ),
    DataType(
        "etched_test_measurement", "Etched Test Measurement", ETCHED_TO_IEC, "U-04",
        "Key measurements from an Etched run.",
        [
            F("suite_run_id", required=True, example="RUN-20261101-0001"),
            F("unit_sn", required=True, example="GPUM00000000046"),
            F("test_name", required=True, example="PowerRailCheck"),
            F("item_name", required=True, example="VDD_CORE_CHIP0"),
            F("value", "REAL", example=0.752),
            F("value_text", example=None),
            F("unit", example="V"),
            F("low_limit", "REAL", example=0.72),
            F("high_limit", "REAL", example=0.78),
            F("result", required=True, enum=("PASS", "FAIL"), example="PASS"),
        ],
        aliases=("measurement", "test_detail"),
        require_one_of=("value", "value_text"),
    ),
    DataType(
        "etched_identity", "Etched Identity / Provisioning", ETCHED_TO_IEC, "U-07 / U-10",
        "Identities created at Etched stations (MCU SN, TPM EK cert, DevIK, FRU, chip bin).",
        [
            F("unit_sn", required=True, example="VBB000000012"),
            F("identity_type", required=True,
              enum=("MCU_SN", "TPM_EK_CERT", "DEVIK_ID", "FRU_WRITTEN", "SCREENING_BIN", "OTHER"), example="MCU_SN"),
            F("identity_value", required=True, example="MCU-7F3A0012"),
            F("suite_run_id", example="RUN-20261101-0001"),
            F("created_ts", "DATETIME", required=True, example="2026-11-01 14:05:00"),
        ],
        aliases=("identity", "provisioning"),
    ),
]

BY_KEY = {dt.key: dt for dt in DATA_TYPES}


def types_for(direction):
    return [dt for dt in DATA_TYPES if dt.direction == direction]


def core_table_sql(dt):
    cols = ["    id INTEGER PRIMARY KEY AUTOINCREMENT"]
    for f in dt.fields:
        col = f"    {f.name} {SQL_TYPES[f.type]}"
        if f.required:
            col += " NOT NULL"
        if f.enum:
            vals = ",".join(f"'{v}'" for v in f.enum)
            col += f" CHECK ({f.name} IN ({vals}))"
        cols.append(col)
    cols.append("    batch_id TEXT REFERENCES upload_batch(batch_id)")
    cols.append("    loaded_ts TEXT NOT NULL")
    body = ",\n".join(cols)
    return f"-- {dt.label} ({dt.ref}): {dt.desc}\nCREATE TABLE IF NOT EXISTS {dt.core_table} (\n{body}\n);"


def iec_table_sql(dt):
    cols = [
        "    id INTEGER PRIMARY KEY AUTOINCREMENT",
        "    batch_id TEXT NOT NULL REFERENCES upload_batch(batch_id)",
        "    sheet_name TEXT",
        "    row_no INTEGER",
        "    row_status TEXT NOT NULL CHECK (row_status IN ('OK','ERROR'))",
        "    row_errors TEXT",
    ]
    cols += [f"    {f.name} TEXT" for f in dt.fields]
    cols.append("    extra_json TEXT")
    cols.append("    received_ts TEXT NOT NULL")
    body = ",\n".join(cols)
    return (f"-- IEC staging copy of {dt.core_table}. Values kept as text so bad rows are still visible.\n"
            f"CREATE TABLE IF NOT EXISTS {dt.iec_table} (\n{body}\n);\n"
            f"CREATE INDEX IF NOT EXISTS ix_{dt.iec_table}_batch ON {dt.iec_table}(batch_id);")


BATCH_SQL = """
-- One row per upload (both directions).
CREATE TABLE IF NOT EXISTS upload_batch (
    batch_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL CHECK (direction IN ('IEC_TO_ETCHED','ETCHED_TO_IEC')),
    source_system TEXT NOT NULL,
    submitted_by TEXT,
    file_format TEXT NOT NULL,
    file_name TEXT,
    file_sha256 TEXT,
    stored_path TEXT,
    source_tz TEXT,
    note TEXT,
    data_types TEXT,
    total_rows INTEGER NOT NULL,
    ok_rows INTEGER NOT NULL,
    error_rows INTEGER NOT NULL,
    status TEXT NOT NULL,
    client_ip TEXT,
    received_ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_batch_ts ON upload_batch(received_ts);

-- How each uploaded column was matched to an Etched field (the field-match evidence for IEC).
CREATE TABLE IF NOT EXISTS upload_field_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES upload_batch(batch_id),
    sheet_name TEXT,
    data_type TEXT,
    source_column TEXT NOT NULL,
    target_field TEXT,
    match_type TEXT NOT NULL CHECK (match_type IN ('EXACT','ALIAS','UNMATCHED','DUPLICATE')),
    suggestion TEXT
);
CREATE INDEX IF NOT EXISTS ix_mapping_batch ON upload_field_mapping(batch_id);
"""


def full_schema_sql():
    parts = ["-- Etched L6 MES integration database (SQLite).", BATCH_SQL.strip(), "",
             "-- ===== Etched L6 core tables ====="]
    parts += [core_table_sql(dt) for dt in DATA_TYPES]
    parts += ["", "-- ===== IEC upload staging tables (all IEC data lands here) ====="]
    parts += [iec_table_sql(dt) for dt in types_for(IEC_TO_ETCHED)]
    return "\n\n".join(parts) + "\n"
