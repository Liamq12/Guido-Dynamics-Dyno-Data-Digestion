import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions

UDP_IP = "192.168.0.2"
UDP_PORT = 7

# Create UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Bind socket to the given IP and port
sock.bind((UDP_IP, UDP_PORT))

print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")

# ---------- InfluxDB Setup ----------
INFLUX_URL = "http://localhost:8086"
TOKEN = "oWfIXrWjWZvTSD9d54G7mIWVxd8pqhSlrV98CA6I3aDXh86_g1U9_n4VKMdUpNEeevFkfKlsSjeS0XTJLphRbw=="
ORG = "Me"
BUCKET = "Test1"

client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)
write_api = client.write_api(write_options=WriteOptions(batch_size=1))

fbombs = 0

while True:
    data, addr = sock.recvfrom(4096)  # buffer size 4096 bytes
    print(f"Received from {addr}: {data.decode(errors='replace')}")
    
    # Parse JSON into a Python dictionary
    parsed = json.loads(data.decode(errors='replace'))

    # Extract top-level fields
    name = parsed.get("name")
    uptime = parsed.get("uptime")
    device_id = parsed.get("id")

    print("Name:", name)
    print("Uptime:", uptime)
    print("Device ID:", device_id)

    # Parse the data list
    data_list = parsed.get("data", [])

    for entry in data_list:
        metric = entry.get("metric")
        timestamp = entry.get("time")
        unit = entry.get("unit")
        value = entry.get("value")

        try:
            # Construct InfluxDB point
            point = (
                Point(metric)
                .tag("device", name)
                .tag("unit", unit)
                .field("value", float(value))
            )

            write_api.write(bucket=BUCKET, org=ORG, record=point)
            print(f"Wrote: {metric}={value} {unit} @ t={time.time()}")

        except Exception as e:
            print("Error parsing packet:", e)

        print(f"Metric: {metric}, Time: {time.time()}, Value: {value} {unit}")

    if(random.randint(1, 33) == 2):
        fbombs += 1

    point = (
        Point("fbombs")
        .tag("device", "Processor")
        .tag("unit", "FB/m")
        .field("value", float(fbombs))
    )

    write_api.write(bucket=BUCKET, org=ORG, record=point)
    print(f"Wrote: {"fbombs"}={value} {unit} @ t={time.time()}")