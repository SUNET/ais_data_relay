import os
import json
import time
import base64
import shutil
import hashlib
import asyncio
import uvicorn
import secrets
from enum import Enum
from pathlib import Path
from pyais import IterMessages
from dataclasses import dataclass
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from configuration import AppConfig, logger
from typing import Set, Dict, Optional, Tuple
from database_unrestricted import DatabaseManager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, status, Depends

@dataclass
class GeographicBounds:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: float, lon: float) -> bool:
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )


@dataclass
class AuthConfig:
    """Authentication configuration"""
    web_username: str
    web_password: str
    tcp_username: str
    tcp_password: str
    
    def verify_web_credentials(self, username: str, password: str) -> bool:
        """Verify web interface credentials using constant-time comparison"""
        username_match = secrets.compare_digest(username, self.web_username)
        password_match = secrets.compare_digest(password, self.web_password)
        return username_match and password_match
    
    def verify_tcp_credentials(self, username: str, password: str) -> bool:
        """Verify TCP client credentials using constant-time comparison"""
        username_match = secrets.compare_digest(username, self.tcp_username)
        password_match = secrets.compare_digest(password, self.tcp_password)
        return username_match and password_match


class AISRelayServer:
    def __init__(self, config: AppConfig, use_hashed=False, auth_config: Optional[AuthConfig] = None):
        self.config = config
        self.ais_host = config.ais_host
        self.ais_port = config.ais_port
        self.ais_user = config.ais_user
        self.ais_password = config.ais_password
        self.use_hashed = use_hashed
        self.auth_config = auth_config

        # Clients
        self.ws_clients: Dict[WebSocket, Optional[GeographicBounds]] = {}
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
        self.LIVE_DB = config.database_url / "ais_database.db"
        self.SNAPSHOT_DB = config.database_url / "ais_snapshot.db"
        self.database = DatabaseManager(self.LIVE_DB)
        self.database.init_db() 
        self.db_queue: asyncio.Queue = asyncio.Queue(maxsize=50000)
        self.db_task: asyncio.Task | None = None

        
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
    
    def _extract_coordinates(self, decoded: Dict) -> Tuple[Optional[float], Optional[float]]:
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
        lon, lat = coords[0] if len(coords) > 0 else None, coords[1] if len(coords) > 1 else None
        
        if lon is not None and lat is not None:
            if not self.is_valid_geo_point(lon, lat):
                logger.error(f"Invalid geo-coordinates: lng: {lon}, lat: {lat}. MMSI: {decoded.get('mmsi')}")
                raise Exception("Invalid coordinates [{}, {}]".format(lon, lat))
        
        return lon, lat
    
    def _reset_db_if_too_old(self):
        db_path = Path(self.config.database_url)

        if not db_path.exists():
            return

        now = time.time()
        if now - self._last_db_check < self.DB_AGE_CHECK_INTERVAL_SECONDS:
            return
        
        age = now - db_path.stat().st_mtime

        if age > self.DB_RESET_SECONDS:
            logger.warning("SQLite DB older than 1 day — resetting")

            # Close connections safely
            self.database.close()

            # Delete DB file
            db_path.unlink(missing_ok=True)

            # Recreate DB
            self.database.init_db()

            logger.info("SQLite DB reset completed")

    
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

        def _extract_vessel_state(decoded: Dict, lon: Optional[float], lat: Optional[float]) -> Dict:
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
        
        # Reset DB if needed
        self._reset_db_if_too_old()
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
                        logger.info(f"Sending hashed login message for user: {self.ais_user}")
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
                            self.ais_reader.readline(), 
                            timeout=10.0
                        )
                        logger.info(f"Authentication response: {response.decode('utf-8', errors='ignore').strip()}")
                    except asyncio.TimeoutError:
                        logger.warning("No authentication response received (timeout)")
                else:
                    logger.info("No credentials provided, connecting without authentication")
                
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

            # ---- Decode / normalize for WS ----
            messages = line.split(b"\r\n")
            data_array = self.filter_valid_ais_lines(messages)

            try:
                for msg in IterMessages(data_array):
                    try:
                        decoded_sentence = msg.decode()
                        decoded_ais_line = decoded_sentence.asdict()
                        decoded_normalized = self.normalize_ais_message(decoded_ais_line)
                        print("decoded_normalized={}".format(decoded_normalized))

                        if decoded_normalized:
                            await self.broadcast_ws(decoded_normalized)
                            
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

    async def broadcast_ws(self, message: dict):
        dead = []

        for ws, bounds in self.ws_clients.items():
            try:
                # Optional: geographic filtering
                if bounds:
                    loc = message.get("location")
                    if not loc:
                        continue

                    lon, lat = loc["coordinates"]
                    if not bounds.contains(lat, lon):
                        continue

                await ws.send_json(message)

            except Exception:
                dead.append(ws)

        for ws in dead:
            self.ws_clients.pop(ws, None)
            
    async def is_authenticated_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        addr = writer.get_extra_info("peername")
        if not ais_relay.auth_config:
            return True  # No authentication required
        
        # Require authentication if configured
        if self.auth_config:
            writer.write(b"AUTH REQUIRED\r\nUsername: ")
            await writer.drain()
            
            # Read username (with timeout)
            try:
                username_data = await asyncio.wait_for(reader.readline(), timeout=30.0)
                username = username_data.decode('utf-8', errors='ignore').strip()
                
                writer.write(b"Password: ")
                await writer.drain()
                
                password_data = await asyncio.wait_for(reader.readline(), timeout=30.0)
                password = password_data.decode('utf-8', errors='ignore').strip()
                
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
    async def handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
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
        self.tcp_server = await asyncio.start_server(
            self.handle_tcp_client, host, port
        )
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
                await loop.run_in_executor(
                    None,
                    self.process_ais_message,
                    message
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DB worker error: {e}")


    async def start(self, tcp_port: int = 5000):
        self.running = True
        self.ais_task = asyncio.create_task(self.connect_to_ais())
        self.db_task = asyncio.create_task(self.db_worker())
        
        # Start TCP server in background
        asyncio.create_task(self.start_tcp_server(port=tcp_port))
        
        
    async def stop(self):
        self.running = False
        if self.ais_writer:
            self.ais_writer.close()
            await self.ais_writer.wait_closed()

        for task in (self.ais_task, self.db_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


# ---------------- FASTAPI ---------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await ais_relay.start()
    yield
    # Shutdown
    await ais_relay.stop()
    
    

app = FastAPI(
    title="AIS Relay",
    lifespan=lifespan
)
static_dir = Path("database")
static_dir.mkdir(exist_ok=True)
# Path to index.html
BASE_DIR = Path(__file__).parent
INDEX_FILE = BASE_DIR / "templates" / "index.html"
app.mount("/data", StaticFiles(directory=static_dir, html=True), name="data")


async def is_authenticated_websocket(ws: WebSocket) -> bool:
    """Check if WebSocket is authenticated using query parameters"""
    if ais_relay.auth_config is None:
        return True  # No authentication required
    # Extract basic auth from headers
    auth_header = ws.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        await ws.close(code=1008)
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
        if ais_relay.auth_config.verify_web_credentials(username, password):
            return True
        else:
            await ws.close(code=1008)
            return False
    except Exception:
        await ws.close(code=1008)
        return False


async def require_web_auth(request: Request):
    """Authenticate HTTP requests using the same web credentials as WebSocket."""
    if ais_relay.auth_config is None:
        return  # No auth required

    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Authentication required"
        )
    
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
        if not ais_relay.auth_config.verify_web_credentials(username, password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
                detail="Invalid credentials"
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
            detail="Invalid Authorization header"
        )




@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(INDEX_FILE)

@app.websocket("/ws/ais")
async def ws_ais(ws: WebSocket):
    if not await is_authenticated_websocket(ws):
        return
    
    await ws.accept()
    # Add client with no filter initially
    ais_relay.ws_clients[ws] = None

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)

            if data.get("type") == "filter":
                ais_relay.ws_clients[ws] = GeographicBounds(**data["bbox"])

    except WebSocketDisconnect:
        pass
    finally:
        ais_relay.ws_clients.pop(ws, None)

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
        filename="ais_snapshot.db"
    )

@app.get("/health")
async def health():
    return {
        "tcp_clients": len(ais_relay.tcp_clients),
        "ws_clients": len(ais_relay.ws_clients),
        "ais_connected": ais_relay.ais_reader is not None,
    }
    

# ---------------- MAIN ---------------- #
config = AppConfig()
logger.info("Loaded configuration: %s", config)
auth_config = AuthConfig(
    web_username=config.web_username,
    web_password=config.web_password,
    tcp_username=config.tcp_username,
    tcp_password=config.tcp_password
)
ais_relay = AISRelayServer(config, use_hashed=False, auth_config=auth_config)


async def main():
    logger.info("TCP relay listening on :5000")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
