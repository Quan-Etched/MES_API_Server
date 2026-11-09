import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["MES_DATA_DIR"] = self.tmp.name
        os.environ.pop("MES_API_KEYS", None)
        from app import create_app
        self.app = create_app()
        self.c = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _xlsx(self, sheets):
        wb = Workbook()
        wb.remove(wb.active)
        for name, rows in sheets.items():
            ws = wb.create_sheet(name)
            for r in rows:
                ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_pages_render(self):
        for p in ("/", "/import", "/etched-upload", "/history", "/schema", "/api-docs"):
            self.assertEqual(self.c.get(p).status_code, 200, p)

    def test_template_roundtrip_all_iec_types(self):
        tpl = self.c.get("/api/v1/templates/all.xlsx?direction=iec").data
        r = self.c.post("/api/v1/iec/upload", data={"file": (io.BytesIO(tpl), "tpl.xlsx"), "submitted_by": "test"})
        j = r.get_json()
        self.assertEqual(r.status_code, 200, j)
        self.assertEqual(j["status"], "RECEIVED", j["sheets"])
        self.assertEqual(j["summary"]["sheets_mapped"], 10)
        self.assertEqual(j["summary"]["error_rows"], 0)

    def test_iec_messy_headers_aliases_and_errors(self):
        f = self._xlsx({"Sheet1": [
            ["L6 WIP report"],
            ["SN", "Station", "Arrive Time", "Status", "Shift"],
            ["LV5ARP0046", "IV-ICT-01", datetime(2026, 11, 1, 10, 20), "queue", "A"],
            ["LV5ARP0047", "IV-ICT-01", "not a time", "QUEUE", "B"],
        ]})
        raw = f.getvalue()
        r = self.c.post("/api/v1/validate?direction=iec", data={"file": (io.BytesIO(raw), "wip.xlsx")})
        sh = r.get_json()["sheets"][0]
        self.assertEqual(sh["data_type"], "wip")
        self.assertEqual(sh["header_row"], 2)
        self.assertEqual(sh["unknown_columns"], ["Shift"])
        self.assertEqual(sh["preview"][0]["values"]["in_ts"], "2026-11-01 10:20:00")
        self.assertEqual(sh["preview"][0]["values"]["wip_status"], "QUEUE")
        self.assertEqual(sh["error_rows"], 1)

        j = self.c.post("/api/v1/iec/upload", data={"file": (io.BytesIO(raw), "wip.xlsx")}).get_json()
        self.assertEqual(j["status"], "RECEIVED_WITH_ERRORS")
        self.assertEqual(j["inserted_rows"], 2)
        rows = self.c.get(f"/api/v1/batches/{j['batch_id']}/rows?data_type=wip").get_json()
        self.assertEqual(rows["table"], "iec_wip")
        self.assertEqual([x["row_status"] for x in rows["rows"]], ["OK", "ERROR"])
        self.assertEqual(json.loads(rows["rows"][0]["extra_json"]), {"Shift": "A"})
        self.assertEqual(self.c.get(f"/batch/{j['batch_id']}").status_code, 200)

    def test_json_body_multi_type(self):
        body = {"submitted_by": "BING", "data": {
            "unit": [{"unit_sn": "A1", "unit_type": "pcba", "inventec_pn": "PN1", "work_order": "WO1"}],
            "test_result": [{"UUT_Serial_Number": "A1", "test_type": "ICT", "station_id": "ICT2",
                             "Result": "Passed", "START_TIME": "20261001152416"}],
        }}
        j = self.c.post("/api/v1/iec/upload", json=body).get_json()
        self.assertEqual(j["status"], "RECEIVED", j)
        self.assertEqual(j["summary"]["data_types"], ["test_result", "unit"])
        b = self.c.get(f"/api/v1/batches/{j['batch_id']}").get_json()["batch"]
        self.assertEqual(b["submitted_by"], "BING")

    def test_etched_strict_reject_then_pull(self):
        good = {"unit_sn": "M1", "station_id": "MLT-01", "test_stage": "MLT", "result": "PASS",
                "suite_name": "mlt", "suite_run_id": "R1", "start_ts": "2026-11-01 14:00:00",
                "end_ts": "2026-11-01 14:40:00"}
        bad = dict(good, result="MAYBE")
        r = self.c.post("/api/v1/etched/upload", json={"data_type": "etched_station_result", "records": [good, bad]})
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/v1/etched/upload", json={"data_type": "etched_station_result", "records": [good]})
        self.assertEqual(r.get_json()["status"], "COMMITTED")
        pull = self.c.get("/api/v1/etched/etched_station_result?since_id=0").get_json()
        self.assertEqual(pull["count"], 1)
        self.assertEqual(pull["records"][0]["unit_sn"], "M1")

    def test_api_key(self):
        os.environ["MES_API_KEYS"] = "k1"
        from app import create_app
        c = create_app().test_client()
        self.assertEqual(c.get("/api/v1/health").status_code, 200)
        self.assertEqual(c.get("/api/v1/batches").status_code, 401)
        self.assertEqual(c.get("/api/v1/batches", headers={"X-API-Key": "k1"}).status_code, 200)
        self.assertEqual(c.get("/import").status_code, 302)


if __name__ == "__main__":
    unittest.main()
