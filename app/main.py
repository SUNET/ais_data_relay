import os
import uuid
import base64
import shutil
import hashlib
import asyncio
import uvicorn
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta
from pyais import IterMessages
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Set, Dict, Optional, Tuple
from database_unrestricted import DatabaseManager
from configuration import AppConfig, AuthConfig, logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, status, Depends


async def read_line_limited(reader, max_bytes, timeout):
    buf = bytearray()
    while True:
        chunk = await asyncio.wait_for(reader.read(1), timeout)
        if not chunk:
            raise ConnectionError("Client disconnected")

        buf += chunk

        if len(buf) > max_bytes:
            raise ValueError("Line too long")

        if chunk == b"\n":
            return bytes(buf)


class AISRelayServer:
    def __init__(
        self,
        config: AppConfig,
        use_hashed=False,
        auth_config: Optional[AuthConfig] = None,
    ):
        self.MAX_LINE = 256
        self.config = config
        self.ais_host = config.ais_host
        self.ais_port = config.ais_port
        self.ais_user = config.ais_user
        self.ais_password = config.ais_password
        self.use_hashed = use_hashed
        self.auth_config = auth_config

        # Clients
        self.tcp_clients: Set[asyncio.StreamWriter] = set()

        # AIS connection
        self.ais_reader: Optional[asyncio.StreamReader] = None
        self.ais_writer: Optional[asyncio.StreamWriter] = None
        self.running = False
        self.ais_task = None

        # Initialize database
        self._last_db_check = 0
        self.DB_AGE_CHECK_INTERVAL_SECONDS = 60 * 60
        self.DB_RESET_SECONDS = 24 * 60 * 60
        # Generate date-based database filename with UUID suffix
        self.LIVE_DB = self.config.database_url / self.get_new_db_name()
        self.SNAPSHOT_DB = self.config.database_url / "ais_snapshot.db"
        self.database = DatabaseManager(self.LIVE_DB)
        self.database.init_db()

        self.db_queue: asyncio.Queue = asyncio.Queue(maxsize=200_000)
        self.db_tasks: list[asyncio.Task] = []
        self.number_of_db_workers = 4  # Number of concurrent DB worker tasks

        # Scheduler for periodic tasks
        self.scheduler = AsyncIOScheduler()

    def get_new_db_name(self):
        date_str = datetime.now().strftime("%Y-%m-%d")
        uuid_suffix = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID
        return f"{date_str}_{uuid_suffix}_ais_db.db"

    def reset_db(self):
        self.LIVE_DB = self.config.database_url / self.get_new_db_name()
        self.database = DatabaseManager(self.LIVE_DB)
        self.database.init_db()
        return self.database

    # ---------------- AIS AUTH --------------- #
    def create_logon_msg(self) -> bytearray:
        """Create login message for AIS server"""
        message = bytearray()
        message.append(1)  # Command ID = 1
        message.extend(self.ais_user.encode("ascii"))
        message.append(0)  # Delimiter
        message.extend(self.ais_password.encode("ascii"))
        message.append(0)  # End mark
        return message

    def create_logon_msg_hashed(self) -> bytearray:
        """Create hashed login message for AIS server"""
        # Step 1: MD5 hash of the password
        md5_hash = hashlib.md5(self.ais_password.encode("utf-8")).digest()

        # Step 2: Base64-encode the hash
        password_encoded = base64.b64encode(md5_hash).decode("ascii")

        # Step 3: Build binary message
        message = bytearray()
        message.append(2)  # Command ID = 2
        message.extend(self.ais_user.encode("ascii"))
        message.append(0)  # Delimiter
        message.extend(password_encoded.encode("ascii"))
        message.append(0)  # End mark
        return message

    @staticmethod
    def filter_valid_ais_lines(lines):
        """Filter valid AIS lines from raw data"""
        return [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith(b"$ABVSI")
        ]

    @staticmethod
    def is_enum_instance(value):
        """Check if a value is an Enum instance"""
        return isinstance(value, Enum)

    def normalize_ais_message(self, entry):
        """Normalize AIS message format"""

        def decode_bytes(value, key):
            if isinstance(value, bytes):
                try:
                    if key == "spare_1" or key == "spare_2":
                        return int.from_bytes(value, "big")
                    return str(value)
                except Exception:
                    return str(value)  # fallback to string representation
            return value

        entry = entry.copy()  # avoid mutating the original

        # Convert enum-like fields to strings
        for key in list(entry.keys()):
            if key in entry and self.is_enum_instance(entry.get(key)):
                entry[key] = entry.get(key).name

        # Convert lat/lon to GeoJSON Point
        if "lat" in entry and "lon" in entry:
            lat, lon = entry["lat"], entry["lon"]
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                entry["location"] = {"type": "Point", "coordinates": [lon, lat]}
                # delete lat & lon
                del entry["lat"]
                del entry["lon"]

        # Decode binary fields
        for bin_key in ["spare_1", "spare_2", "data"]:
            if bin_key in entry:
                entry[bin_key] = decode_bytes(entry[bin_key], bin_key)

        return entry

    def is_valid_geo_point(self, lon, lat):
        # type: (float, float) -> bool
        if lon and lat:
            return -180 <= lon <= 180 and -90 <= lat <= 90
        else:
            return True

    def _extract_coordinates(
        self, decoded: Dict
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Extract and validate coordinates from decoded message

        Args:
            decoded: Normalized AIS message dictionary

        Returns:
            Tuple of (longitude, latitude)

        Raises:
            InvalidCoordinatesError: If coordinates are present but invalid
        """
        location = decoded.get("location", {})
        coords = location.get("coordinates", [None, None])
        lon, lat = (
            coords[0] if len(coords) > 0 else None,
            coords[1] if len(coords) > 1 else None,
        )

        if lon is not None and lat is not None:
            if not self.is_valid_geo_point(lon, lat):
                logger.error(
                    f"Invalid geo-coordinates: lng: {lon}, lat: {lat}. MMSI: {decoded.get('mmsi')}"
                )
                raise Exception("Invalid coordinates [{}, {}]".format(lon, lat))

        return lon, lat

    def delete_log_file(self):
        log_file_path = Path(os.environ.get("LOGGER_FILE", "ais_processor.log"))
        if log_file_path.exists():
            log_file_path.unlink(missing_ok=True)
            logger.info("Log file deleted")
        else:
            logger.info("Log file does not exist, nothing to delete")

    def delete_old_database(self, weeks: int = 1):
        """Delete database files older than the specified number of weeks.
        
        Args:
            weeks: Number of weeks to keep database files. Default is 1 week.
        """
        try:
            cutoff_time = datetime.now() - timedelta(weeks=weeks)
            db_dir = self.config.database_url
            
            if not db_dir.exists():
                logger.warning(f"Database directory does not exist: {db_dir}")
                return
            
            deleted_count = 0
            for db_file in db_dir.glob("*.db"):
                # Skip the current live database and snapshot
                if db_file == self.LIVE_DB or db_file == self.SNAPSHOT_DB:
                    continue
                
                # Check file modification time
                file_mtime = datetime.fromtimestamp(db_file.stat().st_mtime)
                if file_mtime < cutoff_time:
                    db_file.unlink(missing_ok=True)
                    deleted_count += 1
                    logger.info(f"Deleted old database file: {db_file.name}")
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} old database file(s)")
            else:
                logger.info("No old database files to delete")
                
        except Exception as e:
            logger.error(f"Error deleting old database files: {e}")

    async def reset_db_on_new_day(self):
        """Delete files older than current day at 23:59"""
        logger.warning("SQLite DB older than 1 day — resetting")
        logger.warning("One day have passed — resetting the database")
        try:
            self.database = self.reset_db()
            if self.database:
                logger.info("SQLite DB reset completed")
            # Delete log file as well
            self.delete_log_file()
            # Delete old database files (older than 1 week)
            self.delete_old_database(weeks=1)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    def process_ais_message(self, decoded_normalized):
        def _extract_vessel_data(decoded: Dict) -> Dict:
            """
            Extract vessel static data from normalized AIS message

            Args:
                decoded: Normalized AIS message dictionary

            Returns:
                Dictionary with vessel static data
            """
            return {
                "mmsi": decoded.get("mmsi"),
                "imo": decoded.get("imo"),
                "ship_name": decoded.get("shipname"),
                "ship_type": decoded.get("ship_type"),
            }

        def _extract_vessel_state(
            decoded: Dict, lon: Optional[float], lat: Optional[float]
        ) -> Dict:
            """
            Extract vessel dynamic state from normalized AIS message

            Args:
                decoded: Normalized AIS message dictionary
                lon: Longitude (can be None)
                lat: Latitude (can be None)

            Returns:
                Dictionary with vessel dynamic state
            """
            return {
                "latitude": lat,
                "longitude": lon,
                "speed": decoded.get("speed"),
                "heading": decoded.get("heading"),
                "course": decoded.get("course"),
                "draught": decoded.get("draught"),
                "status": decoded.get("status"),
                "call_sign": decoded.get("callsign"),
                "destination": decoded.get("destination"),
            }

        # Extract and validate coordinates
        lon, lat = self._extract_coordinates(decoded_normalized)

        # Extract vessel data
        vessel = _extract_vessel_data(decoded_normalized)
        vessel_state = _extract_vessel_state(decoded_normalized, lon, lat)
        vessel_state["vessel_mmsi"] = vessel["mmsi"]
        self.database.create_vessel(**vessel)
        self.database.create_vessel_state(**vessel_state)

    # ---------------- AIS CONNECTION ---------------- #

    async def connect_to_ais(self):
        while self.running:
            try:
                logger.info("Connecting to AIS source...")
                self.ais_reader, self.ais_writer = await asyncio.open_connection(
                    self.ais_host, self.ais_port
                )

                logger.info("Connected to AIS server successfully")

                # Send login message if credentials are provided
                if self.ais_user and self.ais_password:
                    if self.use_hashed:
                        login_message = self.create_logon_msg_hashed()
                        logger.info(
                            f"Sending hashed login message for user: {self.ais_user}"
                        )
                    else:
                        login_message = self.create_logon_msg()
                        logger.info(f"Sending login message for user: {self.ais_user}")

                    # Send the login message
                    self.ais_writer.write(bytes(login_message))
                    await self.ais_writer.drain()
                    logger.info("Login message sent successfully")

                    # Optional: Wait for authentication response
                    try:
                        response = await asyncio.wait_for(
                            self.ais_reader.readline(), timeout=10.0
                        )
                        logger.info(
                            f"Authentication response: {response.decode('utf-8', errors='ignore').strip()}"
                        )
                    except asyncio.TimeoutError:
                        logger.warning("No authentication response received (timeout)")
                else:
                    logger.info(
                        "No credentials provided, connecting without authentication"
                    )

                # Start reading data
                await self.read_ais_data()

            except Exception as e:
                logger.error(f"AIS connection error: {e}")
                if self.ais_writer:
                    self.ais_writer.close()
                    await self.ais_writer.wait_closed()
                await asyncio.sleep(5)  # Retry after 5 seconds

    async def read_ais_data(self):
        while self.running:
            line = await self.ais_reader.readline()
            if not line:
                raise ConnectionError("AIS disconnected")

            # ---- RAW message for TCP relay ----
            raw_message = line.decode(errors="ignore").strip()
            if raw_message:
                await self.broadcast_tcp(raw_message)

            # ---- Decode / normalize for DB ----
            messages = line.split(b"\r\n")
            data_array = self.filter_valid_ais_lines(messages)

            try:
                for msg in IterMessages(data_array):
                    try:
                        decoded_sentence = msg.decode()
                        decoded_ais_line = decoded_sentence.asdict()
                        decoded_normalized = self.normalize_ais_message(
                            decoded_ais_line
                        )

                        # enqueue for DB (DO NOT await DB here)
                        try:
                            self.db_queue.put_nowait(decoded_normalized)
                        except asyncio.QueueFull:
                            logger.warning("DB queue full — dropping AIS message")

                    except Exception as e:
                        logger.error(f"Error processing AIS message: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error iterating AIS messages: {e}")
                continue

    # ---------------- BROADCAST ---------------- #
    async def broadcast_tcp(self, message: str):
        data = (message + "\r\n").encode()

        dead = set()
        for writer in self.tcp_clients:
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                dead.add(writer)

        for writer in dead:
            self.tcp_clients.remove(writer)
            writer.close()
            logger.info("Removed dead TCP client")

    async def is_authenticated_tcp_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bool:
        addr = writer.get_extra_info("peername")
        if not self.auth_config.enable_tcp_auth:
            return True  # No authentication required

        # Require authentication if configured
        if self.auth_config.enable_tcp_auth:
            writer.write(b"AUTH REQUIRED\r\nUsername: ")
            await writer.drain()

            # Read username (with timeout)
            try:
                # username_data = await asyncio.wait_for(reader.readline(), timeout=30.0)
                username_data = await read_line_limited(reader, 64, 30.0)
                username = username_data.decode("utf-8", errors="ignore").strip()

                writer.write(b"Password: ")
                await writer.drain()

                # password_data = await asyncio.wait_for(reader.readline(), timeout=30.0)
                password_data = await read_line_limited(reader, 256, 30.0)
                password = password_data.decode("utf-8", errors="ignore").strip()

                # Verify credentials
                if self.auth_config.verify_tcp_credentials(username, password):
                    writer.write(b"AUTH SUCCESS\r\n")
                    await writer.drain()
                    logger.info(f"TCP client {addr} authenticated as {username}")
                    return True
                else:
                    writer.write(b"AUTH FAILED\r\n")
                    await writer.drain()
                    logger.warning(f"TCP authentication failed for {addr}")
                    return False

            except asyncio.TimeoutError:
                writer.write(b"AUTH TIMEOUT\r\n")
                await writer.drain()
                logger.warning(f"TCP authentication timeout for {addr}")
                return False

    # ---------------- LIFECYCLE ---------------- #
    async def handle_tcp_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        logger.info(f"TCP client connected: {addr}")

        if await self.is_authenticated_tcp_client(reader, writer):
            self.tcp_clients.add(writer)
            try:
                await reader.read()
            finally:
                self.tcp_clients.remove(writer)
                writer.close()
                logger.info(f"TCP client disconnected: {addr}")

    async def start_tcp_server(self, host: str = "0.0.0.0", port: int = 5000):
        self.tcp_server = await asyncio.start_server(self.handle_tcp_client, host, port)
        logger.info(f"TCP relay listening on {host}:{port}")

        async with self.tcp_server:
            await self.tcp_server.serve_forever()

    async def db_worker(self):
        logger.info("DB worker started")
        loop = asyncio.get_running_loop()

        while True:
            try:
                message = await self.db_queue.get()

                # Run blocking DB work in a thread
                await loop.run_in_executor(None, self.process_ais_message, message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DB worker error: {e}")

    async def start(self, tcp_port: int = 5000):
        self.running = True
        self.ais_task = asyncio.create_task(self.connect_to_ais())
        self.db_tasks = asyncio.create_task(self.db_worker())
        # Start multiple DB workers
        self.db_tasks = [
            asyncio.create_task(self.db_worker())
            for _ in range(self.number_of_db_workers)
        ]

        # Start TCP server in background
        asyncio.create_task(self.start_tcp_server(port=tcp_port))

        # Schedule daily cleanup at 23:59
        # self.scheduler.add_job(self.reset_db_on_new_day, "cron", hour=23, minute=59)
        conf_env = self.config.environment.lower()
        if conf_env == "production":
            self.scheduler.add_job(self.reset_db_on_new_day, "cron", hour=23, minute=59)
            logger.info("Cleanup scheduler started (runs daily at 23:59)")
        else:
            self.scheduler.add_job(self.reset_db_on_new_day, "interval", minutes=2)
            logger.info("Cleanup scheduler started (runs every 2 minutes for test environment)")
        self.scheduler.start()

    async def stop(self):
        self.running = False
        if self.ais_writer:
            self.ais_writer.close()
            await self.ais_writer.wait_closed()

        for task in (self.ais_task, *self.db_tasks):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.ais_task = None
        self.db_tasks = []
        logger.info("AIS Relay server stopped")


# ---------------- FASTAPI ---------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await ais_relay.start()
    yield
    # Shutdown
    await ais_relay.stop()


# Load configuration
config = AppConfig()
print("Env:", config.environment)

# Create FastAPI app
app = FastAPI(title="AIS Relay", lifespan=lifespan)
static_dir = Path(config.database_url)
static_dir.mkdir(exist_ok=True)
# Path to index.html
BASE_DIR = Path(__file__).parent
INDEX_FILE = BASE_DIR / "templates" / "index.html"
app.mount("/data", StaticFiles(directory=static_dir, html=True), name="data")


async def require_web_auth(request: Request):
    """Authenticate HTTP requests using the same web credentials as WebSocket."""
    if not ais_relay.auth_config.enable_web_auth:
        return  # No auth required

    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Authentication required",
        )

    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
        if not ais_relay.auth_config.verify_web_credentials(username, password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
                detail="Invalid credentials",
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Invalid Authorization header",
        )


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(INDEX_FILE)


@app.get("/db/snapshot")
async def snapshot_db(_: None = Depends(require_web_auth)):
    if not ais_relay.LIVE_DB.exists():
        raise HTTPException(status_code=404, detail="Live database not found")

    try:
        shutil.copy2(ais_relay.LIVE_DB, ais_relay.SNAPSHOT_DB)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {e}")

    return FileResponse(
        path=ais_relay.SNAPSHOT_DB,
        media_type="application/octet-stream",
        filename="ais_snapshot.db",
    )


@app.get("/db/files")
async def list_db_files(_: None = Depends(require_web_auth)):
    """List all database files in the configured database directory"""
    try:
        static_dir = Path(config.database_url)
        if not static_dir.exists():
            return {"files": []}

        # Get all .db files in the directory
        db_files = []
        for file_path in static_dir.glob("*.db"):
            # Get file stats
            stat = file_path.stat()
            db_files.append(
                {
                    "name": file_path.name,
                    "size": stat.st_size,
                    "created": stat.st_ctime,
                    "modified": stat.st_mtime,
                }
            )

        # Sort by creation time (newest first)
        db_files.sort(key=lambda x: x["created"], reverse=True)

        return {"files": db_files}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list database files: {e}"
        )


@app.get("/db/download/{filename}")
async def download_db_file(filename: str, _: None = Depends(require_web_auth)):
    """Download a specific database file (excluding the live database)"""
    try:
        # Security check: ensure filename is just a filename, not a path
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        # Security check: ensure it's a .db file
        if not filename.endswith(".db"):
            raise HTTPException(
                status_code=400, detail="Only database files (.db) are allowed"
            )

        static_dir = Path(config.database_url)
        file_path = static_dir / filename

        # Check if file exists
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        # Security check: prevent downloading the live database
        live_db_path = Path(ais_relay.LIVE_DB)
        if str(file_path) == str(live_db_path) or file_path.name == live_db_path.name:
            raise HTTPException(
                status_code=403,
                detail="Cannot download the live database as it's currently in use",
            )

        return FileResponse(
            path=file_path,
            media_type="application/octet-stream",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")


@app.get("/health")
async def health():
    return {
        "tcp_clients": len(ais_relay.tcp_clients),
        "ais_connected": ais_relay.ais_reader is not None,
    }


# ---------------- MAIN ---------------- #
logger.info("Loaded configuration: %s", config)
auth_config = AuthConfig(
    web_username=config.web_username,
    web_password=config.web_password,
    tcp_username=config.tcp_username,
    tcp_password=config.tcp_password,
    enable_tcp_auth=config.enable_tcp_auth,
    enable_web_auth=config.enable_web_auth,
)
ais_relay = AISRelayServer(config, use_hashed=False, auth_config=auth_config)


async def main():
    logger.info("TCP relay listening on :5000")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
