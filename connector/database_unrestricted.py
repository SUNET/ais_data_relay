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
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    CONSTRAINT imo_mmsi_uniq UNIQUE (mmsi, imo)
                );

                CREATE TABLE IF NOT EXISTS vessel_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vessel_mmsi TEXT,
                    latitude REAL,
                    longitude REAL,
                    speed REAL,
                    heading REAL,
                    course REAL,
                    draught REAL,
                    status TEXT,
                    call_sign TEXT,
                    destination TEXT,
                    received_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),

                    FOREIGN KEY (vessel_mmsi)
                        REFERENCES vessels (mmsi)
                        ON DELETE SET NULL
                        ON UPDATE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_states_mmsi
                    ON vessel_states(vessel_mmsi);
            """)

    # -----------------------------------
    # Fetch recent data
    # -----------------------------------
    def get_recent_vessels_data(self, age=1):
        interval = f"-{int(age)} minute"

        query = """
            SELECT
                vessels.mmsi AS MMSI,
                vessels.imo AS IMO,
                states.latitude AS LAT,
                states.longitude AS LON,
                states.speed AS SPEED,
                states.heading AS HEADING,
                states.course AS COURSE,
                states.status AS STATUS,
                strftime('%Y-%m-%dT%H:%M:%f', 'now') || '+00:00' AS TIMESTAMP,
                vessels.ship_name AS SHIPNAME,
                vessels.ship_type AS TYPE_NAME,
                states.call_sign AS CALLSIGN,
                states.draught as DRAUGHT,
                states.destination AS DESTINATION
            FROM vessels
            JOIN vessel_states AS states
                ON vessels.mmsi = states.vessel_mmsi
            WHERE states.updated_at >= datetime('now', ?)
              AND states.latitude IS NOT NULL
              AND states.longitude IS NOT NULL
            ORDER BY states.updated_at;
        """

        with self.connect() as conn:
            cur = conn.execute(query, (interval,))
            rows = cur.fetchall()
            col_names = [c[0] for c in cur.description]
            return col_names, rows

    # -----------------------------------
    # Vessel CRUD
    # -----------------------------------
    def create_vessel(self, mmsi: str, imo=None, ship_name=None, ship_type=None):
        if self.get_vessel(mmsi):
            return self.update_vessel(mmsi=mmsi, imo=imo, ship_name=ship_name, ship_type=ship_type)

        with self.connect() as conn:
            cur = conn.execute("""
                INSERT INTO vessels (mmsi, imo, ship_name, ship_type)
                VALUES (?, ?, ?, ?)
            """, (mmsi, imo, ship_name, ship_type))
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
        fields = ", ".join(f"{k}=?" for k, v in kwargs.items() if v is not None)
        values = [v for v in kwargs.values() if v is not None]

        if fields:
            set_clause = f"{fields}, updated_at=datetime('now')"
        else:
            set_clause = "updated_at=datetime('now')"

        values.append(mmsi)

        with self.connect() as conn:
            cur = conn.execute(f"UPDATE vessels SET {set_clause} WHERE mmsi=?", values)
            return cur.rowcount > 0

    def delete_vessel(self, mmsi: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM vessels WHERE mmsi=?", (mmsi,))
            return cur.rowcount > 0

    # -----------------------------------
    # Vessel States CRUD
    # -----------------------------------
    def create_vessel_state(self, vessel_mmsi, **fields):
        if not self.get_vessel(vessel_mmsi):
            self.create_vessel(vessel_mmsi)

        # latest = self.get_latest_vessel_state(vessel_mmsi)
        # if latest:
        #     # Update only the latest row
        #     return self.update_vessel_state(vessel_mmsi, **fields)
    
        with self.connect() as conn:
            cur = conn.execute("""
                INSERT INTO vessel_states
                (vessel_mmsi, latitude, longitude, speed, heading, course,
                 draught, status, call_sign, destination, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                vessel_mmsi,
                fields.get("latitude"),
                fields.get("longitude"),
                fields.get("speed"),
                fields.get("heading"),
                fields.get("course"),
                fields.get("draught"),
                fields.get("status"),
                fields.get("call_sign"),
                fields.get("destination")
            ))
            return cur.lastrowid
        
    def update_vessel_state(self, vessel_mmsi: str, **kwargs) -> bool:
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        if not filtered:
            return False

        # Fetch latest state ID
        with self.connect() as conn:
            row = conn.execute("""
                SELECT id FROM vessel_states
                WHERE vessel_mmsi=?
                ORDER BY updated_at DESC
                LIMIT 1
            """, (vessel_mmsi,)).fetchone()

            if not row:
                return False

            latest_id = row["id"]

            fields = ", ".join(f"{k}=?" for k in filtered.keys())
            values = list(filtered.values()) + [latest_id]

            cur = conn.execute(f"""
                UPDATE vessel_states
                SET {fields}, updated_at=datetime('now')
                WHERE id=?
            """, values)

            return cur.rowcount > 0


    def get_vessel_states(self, vessel_mmsi: str):
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT * FROM vessel_states
                WHERE vessel_mmsi=?
                ORDER BY received_at ASC
            """, (vessel_mmsi,)).fetchall()
            return [dict(r) for r in rows]

    def get_latest_vessel_state(self, vessel_mmsi: str):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT * FROM vessel_states
                WHERE vessel_mmsi=?
                ORDER BY updated_at DESC LIMIT 1
            """, (vessel_mmsi,)).fetchone()
            return dict(row) if row else None

    def delete_vessel_state(self, state_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM vessel_states WHERE id=?", (state_id,))
            return cur.rowcount > 0
