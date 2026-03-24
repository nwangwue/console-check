import csv
import os

import openpyxl
from sqlalchemy.orm import Session

from app.models.site import Site
from app.models.device import Device
from app.schemas.import_config import ColumnMapping, ImportPreview, ImportResult, UnresolvedSite
from app.services.unlocode import UnlocodeService


def _read_file(file_path: str) -> tuple[list[str], list[list[str]]]:
    """Read headers and all data rows from xlsx or csv."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ('.xlsx', '.xls'):
        wb = openpyxl.load_workbook(file_path, read_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([str(cell) if cell is not None else "" for cell in row])
        wb.close()
        if not rows:
            return [], []
        return rows[0], rows[1:]
    else:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            all_rows = list(reader)
        if not all_rows:
            return [], []
        return all_rows[0], all_rows[1:]


class SiteImporter:
    def __init__(self, unlocode_service: UnlocodeService, db: Session):
        self.unlocode = unlocode_service
        self.db = db

    def preview(self, file_path: str) -> ImportPreview:
        """Read headers and first 5 rows for column mapping UI."""
        headers, data_rows = _read_file(file_path)
        return ImportPreview(
            headers=headers,
            sample_rows=data_rows[:5],
            total_rows=len(data_rows),
        )

    def execute(self, file_path: str, column_map: ColumnMapping) -> ImportResult:
        """Full import with column mapping and UNLOCODE resolution."""
        headers, data_rows = _read_file(file_path)

        total = len(data_rows)
        imported = 0
        resolved = 0
        unresolved_sites: list[UnresolvedSite] = []
        errors: list[str] = []

        for i, row in enumerate(data_rows):
            try:
                site_id = self._get_cell(row, column_map.site_id).strip()
                if not site_id:
                    errors.append(f"Row {i + 2}: empty site_id, skipped")
                    continue

                city = self._get_cell(row, column_map.city).strip()
                state = self._get_cell(row, column_map.state).strip()
                bank_name = self._get_cell(row, column_map.bank_name).strip()
                site_type = self._get_cell(row, column_map.site_type).strip().lower()
                if site_type not in ("single", "ha"):
                    site_type = "single"

                full_address = ""
                if column_map.full_address is not None:
                    full_address = self._get_cell(row, column_map.full_address).strip()

                # Resolve UNLOCODE
                locode_result = self.unlocode.resolve(city, state)
                unlocode = locode_result.code
                unlocode_resolved = locode_result.resolved
                cp_search_key = f"US{unlocode}" if unlocode else None

                if unlocode_resolved:
                    resolved += 1
                else:
                    unresolved_sites.append(UnresolvedSite(
                        site_id=site_id,
                        city=city,
                        state=state,
                        suggestions=locode_result.suggestions,
                    ))

                # Upsert site
                site = self.db.query(Site).filter(Site.id == site_id).first()
                if site:
                    site.city = city
                    site.state = state
                    site.bank_name = bank_name
                    site.full_address = full_address
                    site.site_type = site_type
                    site.unlocode = unlocode
                    site.unlocode_resolved = unlocode_resolved
                    site.cp_search_key = cp_search_key
                else:
                    site = Site(
                        id=site_id,
                        city=city,
                        state=state,
                        bank_name=bank_name,
                        full_address=full_address,
                        site_type=site_type,
                        unlocode=unlocode,
                        unlocode_resolved=unlocode_resolved,
                        cp_search_key=cp_search_key,
                    )
                    self.db.add(site)

                self.db.flush()

                # Build device records
                self._upsert_devices(site_id, site_type, row, column_map)
                imported += 1

            except Exception as e:
                errors.append(f"Row {i + 2}: {str(e)}")

        self.db.commit()

        return ImportResult(
            total=total,
            imported=imported,
            resolved=resolved,
            unresolved=len(unresolved_sites),
            unresolved_sites=unresolved_sites,
            errors=errors,
        )

    def _upsert_devices(self, site_id: str, site_type: str, row: list[str], column_map: ColumnMapping):
        """Create/update device records for a site."""
        # Delete existing devices for this site (re-import replaces them)
        self.db.query(Device).filter(Device.site_id == site_id).delete()

        devices = []

        # Port 1: old primary (always)
        old_primary = self._get_cell(row, column_map.old_primary_hostname).strip()
        if old_primary:
            devices.append(Device(
                site_id=site_id, port=1, role="old_primary",
                expected_hostname=old_primary,
            ))

        # Port 2: old secondary (HA only)
        if site_type == "ha" and column_map.old_secondary_hostname is not None:
            old_secondary = self._get_cell(row, column_map.old_secondary_hostname).strip()
            if old_secondary:
                devices.append(Device(
                    site_id=site_id, port=2, role="old_secondary",
                    expected_hostname=old_secondary,
                ))

        # Port 3: new primary (always)
        new_primary = self._get_cell(row, column_map.new_primary_hostname).strip()
        if new_primary:
            devices.append(Device(
                site_id=site_id, port=3, role="new_primary",
                expected_hostname=new_primary,
            ))

        # Port 4: new secondary (HA only)
        if site_type == "ha" and column_map.new_secondary_hostname is not None:
            new_secondary = self._get_cell(row, column_map.new_secondary_hostname).strip()
            if new_secondary:
                devices.append(Device(
                    site_id=site_id, port=4, role="new_secondary",
                    expected_hostname=new_secondary,
                ))

        for device in devices:
            self.db.add(device)

    def _get_cell(self, row: list[str], index: int) -> str:
        """Safely get a cell value by index."""
        if index < 0 or index >= len(row):
            return ""
        return row[index] or ""

    def resolve_manual(self, db: Session, site_id: str, unlocode: str):
        """Manually set UNLOCODE for a site."""
        site = db.query(Site).filter(Site.id == site_id).first()
        if site:
            site.unlocode = unlocode
            site.unlocode_resolved = True
            site.cp_search_key = f"US{unlocode}"
            db.commit()
