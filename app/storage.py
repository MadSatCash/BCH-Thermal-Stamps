"""SQLite storage for stamp templates (designs) and individual BCH stamps.

The manageable unit is one stamp: it owns a BCH key (address + WIF), a status and
its rendered image. "Creating a batch" is just creating several individual stamps
in a row -- there is no separate batch entity.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import wallet
from .renderer import StampDesign, save_stamp_image


def _base_dir() -> Path:
    # When bundled into a single .exe (PyInstaller), keep the data folder next to
    # the executable so stamps persist between runs. Otherwise use the package dir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DATA_DIR = _base_dir() / "data"
DB_PATH = DATA_DIR / "bch_thermal_stamps.db"
STAMP_IMAGE_DIR = DATA_DIR / "stamps"

STATUS_CREATED = "creada"
STATUS_FUNDED = "fondeada"
STATUS_EMPTY = "sin fondos"
STATUS_RECOVERED = "recuperada"


@dataclass
class StampRecord:
    id: str
    created_at: str
    label: str
    address: str
    wif: str
    amount: str
    status: str
    balance_sats: int
    peak_balance_sats: int
    tx_count: int
    history_json: str  # JSON list of on-chain movements (see wallet.Movement)
    checked_at: str
    image_path: str

    @property
    def address_short(self) -> str:
        return wallet._short_address(self.address)


class Storage:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS designs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stamps (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    wif TEXT NOT NULL DEFAULT '',
                    amount TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'creada',
                    balance_sats INTEGER NOT NULL DEFAULT 0,
                    peak_balance_sats INTEGER NOT NULL DEFAULT 0,
                    tx_count INTEGER NOT NULL DEFAULT 0,
                    history_json TEXT NOT NULL DEFAULT '[]',
                    checked_at TEXT NOT NULL DEFAULT '',
                    design_data TEXT NOT NULL DEFAULT '{}',
                    image_path TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._ensure_stamp_columns()
            self.connection.commit()

    def _ensure_stamp_columns(self) -> None:
        """Add any columns missing from a database created by an older version."""
        existing = {row["name"] for row in self.connection.execute("PRAGMA table_info(stamps)")}
        needed = {
            "label": "TEXT NOT NULL DEFAULT ''",
            "address": "TEXT NOT NULL DEFAULT ''",
            "wif": "TEXT NOT NULL DEFAULT ''",
            "balance_sats": "INTEGER NOT NULL DEFAULT 0",
            "peak_balance_sats": "INTEGER NOT NULL DEFAULT 0",
            "tx_count": "INTEGER NOT NULL DEFAULT 0",
            "history_json": "TEXT NOT NULL DEFAULT '[]'",
            "checked_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, decl in needed.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE stamps ADD COLUMN {name} {decl}")
                if name == "peak_balance_sats":
                    # Best guess for stamps that predate this column: the highest
                    # balance we can know about is whatever we last observed.
                    self.connection.execute("UPDATE stamps SET peak_balance_sats = balance_sats")

    # -- designs (visual templates) -------------------------------------------

    def save_design(self, name: str, design: StampDesign) -> None:
        payload = json.dumps(design.to_dict(), ensure_ascii=True)
        now = _now()
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO designs (name, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (name, payload, now),
            )
            self.connection.commit()

    def list_design_names(self) -> list[str]:
        with self._lock:
            rows = self.connection.execute("SELECT name FROM designs ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def load_design(self, name: str) -> StampDesign | None:
        with self._lock:
            row = self.connection.execute("SELECT data FROM designs WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return StampDesign.from_dict(json.loads(row["data"]))

    # -- stamps (individual BCH instances) ------------------------------------

    def create_stamp(self, design: StampDesign, label: str = "", amount: str | None = None) -> StampRecord:
        """Generate a new BCH key, render its stamp image and persist it.

        The freshly generated WIF becomes the data of the claim QR so the printed
        stamp is directly sweepable.
        """
        stamp_id = uuid.uuid4().hex
        key = wallet.new_key()

        instance = StampDesign.from_dict(design.to_dict())
        instance.claim_qr_data = key.wif
        if amount is not None:
            instance.amount = amount

        image_path = STAMP_IMAGE_DIR / f"{stamp_id}.png"
        save_stamp_image(image_path, instance, stamp_id)

        record = StampRecord(
            id=stamp_id,
            created_at=_now(),
            label=label,
            address=key.address,
            wif=key.wif,
            amount=instance.amount,
            status=STATUS_CREATED,
            balance_sats=0,
            peak_balance_sats=0,
            tx_count=0,
            history_json="[]",
            checked_at="",
            image_path=str(image_path),
        )
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO stamps
                    (id, created_at, label, address, wif, amount, status, balance_sats, peak_balance_sats, tx_count, history_json, checked_at, design_data, image_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.created_at,
                    record.label,
                    record.address,
                    record.wif,
                    record.amount,
                    record.status,
                    record.balance_sats,
                    record.peak_balance_sats,
                    record.tx_count,
                    record.history_json,
                    record.checked_at,
                    json.dumps(instance.to_dict(), ensure_ascii=True),
                    record.image_path,
                ),
            )
            self.connection.commit()
        return record

    def list_stamps(self, limit: int = 500) -> list[StampRecord]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT id, created_at, label, address, wif, amount, status, balance_sats, peak_balance_sats, tx_count, history_json, checked_at, image_path
                FROM stamps
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def get_stamp(self, stamp_id: str) -> StampRecord | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT id, created_at, label, address, wif, amount, status, balance_sats, peak_balance_sats, tx_count, history_json, checked_at, image_path
                FROM stamps WHERE id = ?
                """,
                (stamp_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def update_stamp_status(self, stamp_id: str, status: str, balance_sats: int | None = None) -> None:
        with self._lock:
            if balance_sats is None:
                self.connection.execute(
                    "UPDATE stamps SET status = ?, checked_at = ? WHERE id = ?",
                    (status, _now(), stamp_id),
                )
            else:
                self.connection.execute(
                    "UPDATE stamps SET status = ?, balance_sats = ?, checked_at = ? WHERE id = ?",
                    (status, balance_sats, _now(), stamp_id),
                )
            self.connection.commit()

    def record_balance_check(
        self,
        stamp_id: str,
        status: str,
        balance_sats: int,
        peak_balance_sats: int,
        tx_count: int,
        history_json: str = "[]",
    ) -> None:
        """Persist an online balance check: current balance, the highest balance
        ever seen, the address's transaction count, and the movement history.

        `tx_count`/`history_json` are what let the UI say a stamp "received funds
        and they were claimed" - and show how much and when - even when the funds
        came and went between checks (so the peak we observed is still zero). The
        on-chain history doesn't lie.
        """
        with self._lock:
            self.connection.execute(
                "UPDATE stamps SET status = ?, balance_sats = ?, peak_balance_sats = ?, tx_count = ?, history_json = ?, checked_at = ? WHERE id = ?",
                (status, balance_sats, peak_balance_sats, tx_count, history_json, _now(), stamp_id),
            )
            self.connection.commit()

    def set_stamp_label(self, stamp_id: str, label: str) -> None:
        with self._lock:
            self.connection.execute("UPDATE stamps SET label = ? WHERE id = ?", (label, stamp_id))
            self.connection.commit()

    def delete_stamp(self, stamp_id: str) -> None:
        record = self.get_stamp(stamp_id)
        with self._lock:
            self.connection.execute("DELETE FROM stamps WHERE id = ?", (stamp_id,))
            self.connection.commit()
        if record:
            image = Path(record.image_path)
            try:
                image.unlink(missing_ok=True)
            except OSError:
                pass


def _row_to_record(row: sqlite3.Row) -> StampRecord:
    return StampRecord(
        id=row["id"],
        created_at=row["created_at"],
        label=row["label"],
        address=row["address"],
        wif=row["wif"],
        amount=row["amount"],
        status=row["status"],
        balance_sats=row["balance_sats"],
        peak_balance_sats=row["peak_balance_sats"],
        tx_count=row["tx_count"],
        history_json=row["history_json"],
        checked_at=row["checked_at"],
        image_path=row["image_path"],
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
