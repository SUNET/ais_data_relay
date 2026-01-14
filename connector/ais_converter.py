import os
import sys
import json
import time
import socket
import asyncio
import argparse
import traceback
from enum import Enum
from pathlib import Path
from datetime import datetime
from database import DatabaseManager
from utils import save_sql_to_csv_atomic
from configuration import AppConfig, logger, logging
from pyais import decode as pyais_decode, IterMessages
from typing import Dict,  Optional, Callable, Tuple, Set
from database_unrestricted import DatabaseManager as DatabaseManager2

class AISProcessor:
    def __init__(self, config: AppConfig, csv_interval: int = 60, csv_output: str = "ais_live_data.csv", is_asn: bool = True) -> None:
        """
        Initialize AIS processor
        
        Args:
            config: Application configuration object
            csv_interval: Interval for CSV output in seconds
            csv_output: Path to CSV output file
            is_asn: Whether to use ASN mode (combined vessel data) or standard mode (separate tables)
        """
        self.config = config
        self.saved_msg_types: Set[int] = set()  # Use set for O(1) lookup
        self.csv_interval = csv_interval
        self.csv_output = Path(csv_output)
        self.is_asn = is_asn
        self._csv_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        # Geographic bounds for Stockholm area - TODO: should be in config
        self.lim_lat = self.config.lim_lat
        self.lim_lon = self.config.lim_lon
        
        # Initialize database
        DATABASE_PATH = "/var/lib/ais_converter/database/ais_database.db" if self.config.environment == "production" else "database/ais_database.db"
        database_url = os.getenv("DATABASE_URL", DATABASE_PATH)
        DatabaseClass = DatabaseManager if is_asn else DatabaseManager2
        self.database = DatabaseClass(database_url)
        self.database.init_db()
        

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

    def log_to_file(self, data):
        # type: (Dict) -> None
        """Log AIS data to file"""
        try:
            with open(self.config.log_file, "a") as log_file:
                log_file.write("{}\n".format(json.dumps(data)))
        except IOError as e:
            logger.error(f"Failed to write to log file: {e}")

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

        # Add timestamp if not present
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat()

        return entry

    def is_valid_geo_point(self, lon, lat):
        # type: (float, float) -> bool
        if lon and lat:
            return -180 <= lon <= 180 and -90 <= lat <= 90
        else:
            return True
    
    def save_vessels_to_csv(self):
        # type: () -> None
        """Save vessel data to CSV file"""
        try:
            # Get data from database
            col_names, rows = self.database.get_recent_vessels_data()
            
            if not rows:
                logger.warning("No vessel data to save to CSV")
                return
            
            # Save to CSV atomically
            save_sql_to_csv_atomic(col_names, rows, self.csv_output)
            logger.info("Successfully saved {} vessel records to {}".format(len(rows), self.csv_output))
            
        except Exception as e:
            logger.error("Error saving vessels to CSV: {}".format(e), exc_info=True)
            
    def _track_message_type(self, decoded: Dict) -> None:
        """Track and log unique AIS message types"""
        msg_type = int(decoded.get("msg_type", 0))
        
        if msg_type not in self.saved_msg_types:
            self.saved_msg_types.add(msg_type)
            logger.info(f"New message type encountered: {msg_type}")

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
    
    def _extract_vessel_data(self, decoded: Dict) -> Dict:
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
        self, 
        decoded: Dict, 
        lon: Optional[float], 
        lat: Optional[float]
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
        
    def _is_within_bounds(self, lon: Optional[float], lat: Optional[float]) -> bool:
        """
        Check if coordinates are within configured geographic bounds
        
        Args:
            lon: Longitude
            lat: Latitude
            
        Returns:
            True if within bounds, False otherwise
        """
        if lon is None or lat is None:
            return False
        
        min_lat, max_lat = self.lim_lat
        min_lon, max_lon = self.lim_lon
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
    
    def process_ais_message(self, decoded_normalized):
        
        # Extract and validate coordinates
        lon, lat = self._extract_coordinates(decoded_normalized)
        
        # Track message types
        self._track_message_type(decoded_normalized)
        
        # Extract vessel data
        vessel = self._extract_vessel_data(decoded_normalized)
        vessel_state = self._extract_vessel_state(decoded_normalized, lon, lat)
        
        if self.is_asn:
            combined_data = {**vessel, **vessel_state}
            # Only store if within bounds when coordinates are present
            if lon is None or lat is None or self._is_within_bounds(lon, lat):
                self.database.create_vessel(**combined_data)
            else:
                logger.debug(f"Vessel {vessel.get('mmsi')} outside geographic bounds, skipping")
        else:
            vessel_state["vessel_mmsi"] = vessel["mmsi"]
            self.database.create_vessel(**vessel)
            self.database.create_vessel_state(**vessel_state)
            
            
    def set_sock_options(self, sock: socket.socket):
        # type: (socket.socket) -> None
        """Set socket options for TCP keep-alive and timeouts"""
        # Set socket timeout
        sock.settimeout(self.config.connection_timeout)
        
        # Enable TCP keep-alive
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        
        # Additional socket options for production systems
        if hasattr(socket, 'TCP_KEEPIDLE'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        if hasattr(socket, 'TCP_KEEPINTVL'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, 'TCP_KEEPCNT'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
            
        return sock
    
    def connect_and_process(self):
        # type: () -> None
        """Connect to AIS server and process incoming data"""
        retries = 0
        while retries < self.config.max_retries:
            try:
                logger.info("Connecting to {}:{}".format(self.config.ais_host, self.config.ais_port))
                with socket.create_connection((self.config.ais_host, self.config.ais_port)) as sock:
                    sock = self.set_sock_options(sock)
                    logger.info("Connected to {}:{}".format(self.config.ais_host, self.config.ais_port))
                    # Reset retry counter upon successful connection
                    retries = 0
                    # Keep receiving messages until server closes connection
                    while not self._shutdown_event.is_set():
                        data = sock.recv(4096)  # Increased buffer size
                        if not data:
                            logger.warning("Server closed connection.")
                            break

                        data_split = data.split(b'\r\n')
                        data_str_split = self.filter_valid_ais_lines(data_split)
                        
                        try:
                            for msg in IterMessages(data_str_split):
                                try:
                                    decoded_sentence = msg.decode()
                                    decoded_ais_line = decoded_sentence.asdict()
                                    decoded_normalized = self.normalize_ais_message(decoded_ais_line)
                                    
                                    # Process AIS message asynchronously
                                    self.process_ais_message(decoded_normalized)
                                except Exception as e:
                                    logger.error("Error processing AIS message: {}".format(e))
                                    continue
                        except Exception as e:
                            logger.error("Error iterating messages: {}".format(e))
                            continue
            except socket.timeout:
                logger.error("Connection timeout after {} seconds".format(self.config.connection_timeout))
                retries += 1
            except ConnectionRefusedError:
                logger.error("Connection refused by server")
                retries += 1
            except Exception as e:
                logger.error("Connection error: {}".format(e))
                retries += 1
            
            # Wait before retrying
            if retries < self.config.max_retries:
                logger.info("Retrying connection in {} seconds... (Attempt {}/{})".format(
                    self.config.retry_interval, retries+1, self.config.max_retries))
                time.sleep(self.config.retry_interval)
            else:
                logger.error("Failed to connect after {} attempts".format(self.config.max_retries))
                break
    
    async def run(self):
        # type: () -> None
        """Run the AIS processor with periodic CSV export"""
        try:
            # Run the main AIS processing
            asyncio_to_thread = get_asyncio_to_thread()
            await asyncio_to_thread(self.connect_and_process)
            
        except KeyboardInterrupt:
            logger.info("Process interrupted by user")
        except Exception as e:
            logger.critical("Critical error: {}".format(e), exc_info=True)
        finally:
            # Clean up resources
            await self.shutdown()
    
    async def shutdown(self):
        # type: () -> None
        """Shutdown all connections and clean up resources"""
        try:
            logger.info("Shutting down AIS processor...")
            
            # Signal shutdown to background tasks
            self._shutdown_event.set()
            
            # Wait for CSV task to complete
            if self._csv_task and not self._csv_task.done():
                logger.info("Waiting for CSV saver task to complete...")
                try:
                    await asyncio.wait_for(self._csv_task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("CSV saver task did not complete in time, cancelling...")
                    self._csv_task.cancel()
                    # Python 3.6 compatible: wait for cancellation to complete
                    try:
                        await self._csv_task
                    except asyncio.CancelledError:
                        pass
            
            # Perform final CSV save
            logger.info("Performing final CSV save before shutdown...")
            self.save_vessels_to_csv()
            
            logger.info("AIS processor shutdown complete")
        except Exception as e:
            logger.error("Error during shutdown: {}".format(e))


def get_asyncio_to_thread() -> Callable:
    """
    Return a function that runs a blocking function in a thread,
    compatible with Python 3.6+.
    Usage:
        asyncio_to_thread = get_asyncio_to_thread()
        await asyncio_to_thread(blocking_func, arg1, arg2)
    """
    if (sys.version_info.major, sys.version_info.minor) >= (3, 9):
        return asyncio.to_thread
    else:
        loop = asyncio.get_event_loop()
        async def to_thread(func, *args, **kwargs):
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
        return to_thread
    

async def save_periodically(processor: AISProcessor, output_file, interval=60):
    OUTPUT_FILE = Path(output_file)
    while not processor._shutdown_event.is_set():
        await asyncio.sleep(interval)
        try:
            logger.info(f"Saving aggregated vessel data to {OUTPUT_FILE}...")
            processor.save_vessels_to_csv()
        except Exception as e:
            logger.error(f"Error saving data: {e}")


async def run_ais_processor(processor):
    """Main entry point for the application"""
    try:
        await processor.run()
    except Exception as e:
        logger.critical("Application failed: {}".format(e), exc_info=True)
        sys.exit(1)
        

async def main_wrapper(csv_interval, csv_output, is_asn):
    print("========= MAIN_WRAPPER =============") 
    # Initialize processor
    config = AppConfig()
    print("config={}".format(config))
    processor = AISProcessor(config, csv_interval=csv_interval, csv_output=csv_output, is_asn=is_asn)

    # Run AIS processing + periodic CSV saver concurrently
    try:
        await asyncio.gather(
            run_ais_processor(processor),          # AIS feed loop
            save_periodically(processor, csv_output, csv_interval)  # Periodic CSV save
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
        processor._shutdown_event.set()
    except Exception as e:
        logger.critical("Application failed: {}".format(e), exc_info=True)
        sys.exit(1)


def asyncio_run(coro):
    """Compat wrapper for Python 3.6+"""
    if sys.version_info >= (3, 7):
        return asyncio.run(coro)
    else:
        loop = asyncio.get_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# -----------------------
# Command-line arguments
# -----------------------
def parse_args():
    # ais_converter.py --interval 60 --output ais_live_data.csv
    parser = argparse.ArgumentParser(
        description="AIS Processor with periodic CSV save"
    )
    parser.add_argument(
        "-i", "--interval", type=int, default=60,
        help="Interval in seconds to save aggregated vessels data"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "-o", "--output", type=str, default="ais_live_data.csv",
        help="Output CSV file for aggregated vessel data"
    )
    parser.add_argument(
        "--no-asn",
        dest="is_asn",
        action="store_false",
        help="Disable ASN mode (default: enabled)"
    )
    return parser.parse_args()


# -----------------------
# Entry point
# -----------------------
if __name__ == "__main__":
    # python ais_converter.py --interval 60 --output ais_live_data.csv
    args = parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        
    print("ARGUMENTS={}".format(args))
    print("CSV will be saved every {} seconds to {} and asn: {}".format(args.interval, args.output, args.is_asn))
        
    try:
        asyncio_run(main_wrapper(csv_interval=args.interval, csv_output=args.output, is_asn=args.is_asn))
    except KeyboardInterrupt:
        print("Program interrupted by user")
    except Exception as e:
        print("Unhandled exception: {}".format(e))
        traceback.print_exc()
        sys.exit(1)
