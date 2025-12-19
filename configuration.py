import os
import logging
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv
from dataclasses import dataclass, field, fields
load_dotenv()  # take environment variables

class StreamOnlyFilter(logging.Filter):
    """Allow all levels except ERROR for StreamHandler."""
    def filter(self, record):
        return record.levelno == logging.INFO

# Create handlers
stream_handler = logging.StreamHandler()
stream_handler.addFilter(StreamOnlyFilter())


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[stream_handler, logging.FileHandler("ais_processor.log")],
)
logger = logging.getLogger("ais_processor")

def _parse_tuple(env_value: str, default: Tuple[float, float]) -> Tuple[float, float]:
    if not env_value:
        return default
    try:
        a, b = env_value.split(",")
        return float(a), float(b)
    except ValueError:
        raise ValueError(f"Invalid tuple format: {env_value}, expected 'min,max'")


@dataclass
class AppConfig:
    """Configuration class for AIS data processor"""
    
    # AIS-Server
    # export LIM_LAT="57.6,59.1"
    ais_host: str = field(default_factory=lambda: os.environ.get("AIS_SERVER_HOST", "localhost"))
    ais_port: int = field(default_factory=lambda: int(os.environ.get("AIS_SERVER_PORT", "8040")))
    ais_user: str = field(default_factory=lambda: os.environ.get("AIS_USER", "user"))
    ais_password: str = field(default_factory=lambda: os.environ.get("AIS_USER_PASSWORD", "pass"))
    retry_interval: int = field(default_factory=lambda: int(os.environ.get("RETRY_INTERVAL", "5")))
    max_retries: int = field(default_factory=lambda: int(os.environ.get("MAX_RETRIES", "3")))
    connection_timeout: int = field(default_factory=lambda: int(os.environ.get("CONNECTION_TIMEOUT", "30")))
    enable_variable: bool = field(default_factory=lambda: os.environ.get("ENABLE_VARIABLE", False))
    # Geo limits
    lim_lat: Tuple[float, float] = field(
        default_factory=lambda: _parse_tuple(os.environ.get("LIM_LAT"), (57.6, 59.1))
    )
    lim_lon: Tuple[float, float] = field(
        default_factory=lambda: _parse_tuple(os.environ.get("LIM_LON"), (17.6, 19.4))
    )

    # ---------------- DATABASE ----------------
    database_url: str = field(
        default_factory=lambda: Path(os.environ.get("DATABASE_URL", "database"))
    )


    # ---------------- AUTH (WEB / TCP) ----------------
    web_username: str = field(default_factory=lambda: os.environ.get("WEB_USERNAME", "admin"))
    web_password: str = field(default_factory=lambda: os.environ.get("WEB_PASSWORD", "1234"))

    tcp_username: str = field(default_factory=lambda: os.environ.get("TCP_USERNAME", "admin"))
    tcp_password: str = field(default_factory=lambda: os.environ.get("TCP_PASSWORD", "1234"))


    # Class-level list of sensitive fields
    _sensitive_fields = {
        "ais_password",
        "web_password",
        "tcp_password",
    }
    
    def __post_init__(self):
        self._normalize_flags()

    def _normalize_flags(self):
        self.enable_variable = self._str_to_bool(self.enable_variable)

    def _str_to_bool(self, value: str) -> bool:
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on', True)

    def __repr__(self):
        # Build dict dynamically masking sensitive fields
        masked = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in self._sensitive_fields:
                masked[f.name] = "***"
            else:
                masked[f.name] = value
        return f"{self.__class__.__name__}({masked})"

    __str__ = __repr__  # Optional: make str() same as repr()
