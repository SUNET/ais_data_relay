import os
import secrets
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


# Resolve log file path from environment (with default)
log_file = os.environ.get("LOGGER_FILE", "ais_processor.log")

# Ensure parent directories exist (if any)
log_path = Path(log_file)
if log_path.parent != Path("."):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
# Create handlers
stream_handler = logging.StreamHandler()
stream_handler.addFilter(StreamOnlyFilter())

file_handler = logging.FileHandler(log_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[stream_handler, file_handler],
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

def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AuthConfig:
    """Authentication configuration"""
    web_username: str
    web_password: str
    tcp_username: str
    tcp_password: str
    enable_tcp_auth: bool = False
    enable_web_auth: bool = True
    
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
    
    environment: str = field(default_factory=lambda: os.environ.get("ENVIRONMENT", "production"))


    # ---------------- AUTH (WEB / TCP) ----------------
    web_username: str = field(default_factory=lambda: os.environ.get("WEB_USERNAME", "admin"))
    web_password: str = field(default_factory=lambda: os.environ.get("WEB_PASSWORD", "1234"))

    tcp_username: str = field(default_factory=lambda: os.environ.get("TCP_USERNAME", "admin"))
    tcp_password: str = field(default_factory=lambda: os.environ.get("TCP_PASSWORD", "1234"))
    enable_tcp_auth: bool = field(default_factory=lambda: env_bool("ENABLE_TCP_AUTH", default=False))
    enable_web_auth: bool = field(default_factory=lambda: env_bool("ENABLE_WEB_AUTH", default=True))


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
