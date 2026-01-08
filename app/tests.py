import asyncio
import websockets
import base64
import json


async def tcp_client(USERNAME = "admin", PASSWORD = "1234"):
    reader, writer = await asyncio.open_connection("localhost", 5000)

    # Read "Username: " prompt
    prompt = await reader.readline()
    print(prompt.decode().strip())

    writer.write((USERNAME + "\n").encode())
    await writer.drain()

    # Read "Password: " prompt
    prompt = await reader.readline()
    print(prompt.decode().strip())

    writer.write((PASSWORD + "\n").encode())
    await writer.drain()

    # Read authentication result
    result = await reader.readline()
    print(result.decode().strip())

    # Start receiving AIS data
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            print(line.decode().strip())
    except KeyboardInterrupt:
        writer.close()
        await writer.wait_closed()
        
async def ws_client(USERNAME = "admin", PASSWORD = "1234"):
    auth = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    headers = [("Authorization", f"Basic {auth}")]
    async with websockets.connect("ws://localhost:8000/ws/ais", extra_headers=headers) as ws:

        # Optional: send filter
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

async def main():
    asyncio.run(ws_client())
    asyncio.run(tcp_client())

def test_(USERNAME = "admin", PASSWORD = "1234"):
    auth = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    headers = [("Authorization", f"Basic {auth}")]
    print(f"auth: {auth}, headers: {headers}")



if __name__ == "__main__":
    test_()
    