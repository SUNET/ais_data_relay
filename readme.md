# AIS Relay Processor Documentation

Run with docker

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d
```

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml --env-file .env.test up -d
```


## Server Information

```bash
Hostname: relay-dco-ais-processor-1.streams.sunet.se
```

## Dependencies

Install required Python packages:

```bash
fastapi==0.125.0
uvicorn==0.38.0
websockets==15.0.1
pyais==2.14.0
python-dotenv==1.2.1
```

Using `pipenv`:

```bash
pipenv install fastapi==0.125.0 uvicorn==0.38.0 websockets==15.0.1 pyais==2.14.0 python-dotenv==1.2.1
```

Or using `pip`:

```bash
pip install fastapi==0.125.0 uvicorn==0.38.0 websockets==15.0.1 pyais==2.14.0 python-dotenv==1.2.1
```

## Running the Server

Start the FastAPI server:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Start TCP relay (port 5000) is handled automatically by the server.

## TCP Relay

Connect to the TCP relay for raw AIS data:

```bash
nc localhost 5000
```

Authentication prompts:

```
Username: ****
Password: ****
```

Once authenticated, AIS messages will start streaming in plain text.

## WebSocket Relay

WebSocket endpoint:

```
ws://localhost:8000/ws/ais
```

### Authentication

Send `Authorization` header using HTTP Basic Auth:

```
Authorization: Basic YWRtaW46MTIzNA==
```

> `YWRtaW46MTIzNA==` is Base64 for `admin:1234`.

### Sending a Filter

You can optionally filter messages by geographic bounding box:

```json
{
  "type": "filter",
  "bbox": {
    "min_lat": 56,
    "max_lat": 58,
    "min_lon": 18,
    "max_lon": 21
  }
}
```

### Example WebSocket Client (Python)

```python
import asyncio
import websockets
import base64
import json

async def ws_client():
    auth = base64.b64encode(b"admin:1234").decode()
    headers = [("Authorization", f"Basic {auth}")]
    async with websockets.connect("ws://localhost:8000/ws/ais", extra_headers=headers) as ws:
        await ws.send(json.dumps({
            "type": "filter",
            "bbox": {
                "min_lat": 56,
                "max_lat": 58,
                "min_lon": 18,
                "max_lon": 21
            }
        }))
        async for msg in ws:
            print(json.loads(msg))

asyncio.run(ws_client())
```

## Database

Live SQLite database path:

```
database/ais_database.db
```

To download a snapshot via the server:

```
GET http://localhost:8000/db/snapshot
```

Authentication is required.
