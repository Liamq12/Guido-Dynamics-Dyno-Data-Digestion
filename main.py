import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions
import os

UDP_IP = "192.168.0.2"
UDP_PORT = 7

# Load cell constants
loadcellZero = 1.660
loadcellTF = 0.00242304803289  # Volts per lbf

# ---------- UDP SETUP ----------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(5.0)
print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")

# ---------- InfluxDB Setup ----------
#load in the influx db file for user token and such
influx_file_path = os.path.join(os.getcwd(), "configs\\influxdb.json")
try:
    with open(influx_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        INFLUX_URL = "http://localhost:8086"
        TOKEN = json_data.get("Token")
        ORG = json_data.get("Org")
        BUCKET = json_data.get("Bucket")
except Exception as e:
    INFLUX_URL = "http://localhost:8086"
    TOKEN = "blank"
    ORG = "blank"
    BUCKET = "blank"


client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)

loadValue = 0

# For 10Hz: small batch, fast flush
write_api = client.write_api(write_options=WriteOptions(
    batch_size=25,           # Small batch
    flush_interval=100,      # Flush every 100ms (10Hz)
    jitter_interval=0,
    retry_interval=5_000
))

fbombs = 0

try:
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            raw = data.decode("utf-8").strip()
            raw = raw.replace("inf", "-1")
            print(f"Received from {addr}: {raw}")

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                continue

            device = parsed.get("device", "unknown")
            uptime = parsed.get("uptime")
            device_id = parsed.get("id")

            headers = parsed.get("headers", [])
            rows = parsed.get("data", [])

            print(f"Device: {device}, Uptime: {uptime}, ID: {device_id}")

            for row in rows:
                entry = dict(zip(headers, row))

                metric = entry.get("metric")
                timestamp = entry.get("time")
                unit = entry.get("unit")
                value = entry.get("value")

                if value is None:
                    value = -1

                # Load cell conversion
                if metric == "dynoLoad":
                    value = (value - loadcellZero) / loadcellTF
                    loadValue = value
                elif(metric == "wheelSpeed"):
                    point = (
                        Point("power")
                        .tag("device", device)
                        .tag("unit", "HP")
                        .field("value", float(loadValue*value))
                    )
                    write_api.write(bucket=BUCKET, org=ORG, record=point)

                try:
                    point = (
                        Point(metric)
                        .tag("device", device)
                        .tag("unit", unit)
                        .field("value", float(value))
                    )

                    write_api.write(bucket=BUCKET, org=ORG, record=point)
                    print(f"Wrote: {metric}={value} {unit}")

                except Exception as e:
                    print(f"Error writing {metric}: {e}")

            # dumb fun metric
            if random.randint(1, 33) == 2:
                fbombs += 1

            fbomb_point = (
                Point("fbombs")
                .tag("device", "Processor")
                .tag("unit", "FB/m")
                .field("value", float(fbombs))
            )

            write_api.write(bucket=BUCKET, org=ORG, record=fbomb_point)

        except socket.timeout:
            print("No data received in 5 seconds...")
            continue

        except Exception as e:
            print(f"Unexpected error in main loop: {e}")
            continue

except KeyboardInterrupt:
    print("\nShutting down gracefully...")
finally:
    write_api.flush()
    write_api.close()
    client.close()
    sock.close()
    print("Cleanup complete")