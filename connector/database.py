import os
import sqlite3
import contextlib
from typing import Optional, List, Dict


class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or "database/ais_database.db"
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    # -----------------------------------
    # Connection context manager
    # -----------------------------------
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.commit()
            conn.close()

    # -----------------------------------
    # Initialization
    # -----------------------------------
    def init_db(self):
        self._apply_pragma()
        self._create_tables()

    def _apply_pragma(self):
        pragmas = [
            ("journal_mode", "WAL"),
            ("synchronous", "NORMAL"),
            ("cache_size", -64000),
            ("temp_store", "MEMORY"),
            ("foreign_keys", "ON"),
        ]
        with self.connect() as conn:
            for pragma, value in pragmas:
                conn.execute(f"PRAGMA {pragma}={value}")

    def _create_tables(self):
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS vessels (
                    mmsi TEXT PRIMARY KEY,
                    imo TEXT,
                    ship_name TEXT,
                    ship_type TEXT,
                    latitude REAL,
                    longitude REAL,
                    speed REAL,
                    heading REAL,
                    course REAL,
                    draught REAL,
                    status TEXT,
                    call_sign TEXT,
                    destination TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    CONSTRAINT imo_mmsi_uniq UNIQUE (mmsi, imo)
                );

                CREATE INDEX IF NOT EXISTS idx_states_mmsi
                    ON vessels(mmsi);
            """)

    # -----------------------------------
    # Fetch recent data
    # -----------------------------------
    def get_recent_vessels_data(self, age=1):
        interval = f"-{int(age)} minute"

        query = """
            SELECT
                mmsi AS MMSI,
                imo AS IMO,
                latitude AS LAT,
                longitude AS LON,
                speed AS SPEED,
                heading AS HEADING,
                course AS COURSE,
                status AS STATUS,
                strftime('%Y-%m-%dT%H:%M:%f', 'now') || '+00:00' AS TIMESTAMP,
                ship_name AS SHIPNAME,
                ship_type AS TYPE_NAME,
                call_sign AS CALLSIGN,
                draught AS DRAUGHT,
                destination AS DESTINATION
            FROM vessels
            WHERE updated_at >= datetime('now', ?)
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            ORDER BY updated_at;
        """

        with self.connect() as conn:
            cur = conn.execute(query, (interval,))
            rows = cur.fetchall()
            col_names = [c[0] for c in cur.description]
            return col_names, rows

    # -----------------------------------
    # Vessel CRUD (Now includes state fields)
    # -----------------------------------
    def create_vessel(self, mmsi: str, **fields):
        if self.get_vessel(mmsi):
            return self.update_vessel(mmsi, **fields)

        # Dynamically build column names + values
        col_names = ["mmsi"] + list(fields.keys())
        placeholders = ["?"] * len(col_names)
        values = [mmsi] + list(fields.values())

        with self.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO vessels ({', '.join(col_names)}) VALUES ({', '.join(placeholders)})",
                values
            )
            return cur.lastrowid


    def get_vessel(self, mmsi: str) -> Optional[Dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM vessels WHERE mmsi=?", (mmsi,)).fetchone()
            return dict(row) if row else None

    def get_all_vessels(self) -> List[Dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM vessels").fetchall()
            return [dict(r) for r in rows]

    def update_vessel(self, mmsi: str, **kwargs) -> bool:
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        if not filtered:
            return False

        fields = ", ".join(f"{k}=?" for k in filtered.keys())
        values = list(filtered.values())
        values.append(mmsi)

        with self.connect() as conn:
            cur = conn.execute(f"""
                UPDATE vessels
                SET {fields},
                    updated_at=datetime('now')
                WHERE mmsi=?
            """, values)

            return cur.rowcount > 0

    def delete_vessel(self, mmsi: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM vessels WHERE mmsi=?", (mmsi,))
            return cur.rowcount > 0
