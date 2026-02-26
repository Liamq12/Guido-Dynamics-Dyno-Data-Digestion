import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions
import os
import threading
from multiprocessing.connection import Listener
import multiprocessing.connection

UDP_IP = "192.168.0.2"
UDP_PORT = 7

UDP_IP_SEND = "192.168.0.123"
UDP_PORT_SEND = 8

#setup ipc to user terminal
ipc_address = ('localhost', 31205)
ipc_listener = Listener(ipc_address, authkey=b'key')
ipc_conn = ipc_listener.accept()
def IPC(conn):
        #TODO setup UDP control words to send data to the processor
        while True:
            msg = conn.recv()
            if msg == "Start RPM":
                rpm = conn.recv()
                print(f"Start RPM set to: {rpm}")
            elif msg == "End RPM":
                rpm = conn.recv()
                print(f"End RPM set to: {rpm}")
            elif msg == "Rate":
                rate = conn.recv()
                print(f"RPM Rate set to: {rate}")
            elif msg == "Start":
                print("start ramp")
            elif msg == "Stop":
                print("stop ramp")


IPC_t = threading.Thread(target=IPC, daemon=True, args=(ipc_conn,))
IPC_t.start()

# Load cell constants
loadcellZero = 1.660
loadcellTF = 0.00242304803289  # Volts per lbf

# ---------- UDP SETUP ----------
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")
except:
    print("no connection, just chilling")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("cancelled")

sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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

query_valvePos = f'from(bucket: "{BUCKET}") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "ValvePos")' #setup query for valvepos from influxdb
query_valvePPR = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "PPR")' #setup query for valvepos from influxdb
query_valveGRO = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "GRO")' #setup query for valvepos from influxdb
query_api = client.query_api()

fbombs = 0

try:
    def influx_to_stm32():
        last_valve_pos = None
        last_ppr = None
        last_gro = None
        while True:
            try: #read data from influxdb and send to the controller
                valve_result = query_api.query(query=query_valvePos, org=ORG)
                ppr_result = query_api.query(query=query_valvePPR, org=ORG)
                gro_result = query_api.query(query=query_valveGRO, org=ORG)
                for table in valve_result:
                    last_record = table.records.pop()
                    valve_pos = last_record['_value']
                    if valve_pos != last_valve_pos:
                        last_valve_pos = valve_pos
                        message = f"VALVE,POS,{valve_pos}"
                        sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                    #print(f"Last value is: {last_record['_value']}")
                if ppr_result:
                    for table in ppr_result:
                        ppr = (table.records.pop())['_value']
                        if ppr != last_ppr:
                            last_ppr = ppr
                            print("PPR updated:")
                            print(last_ppr)
                            message = f"VALVE,PPR,{last_ppr}"
                            sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                if gro_result:
                    for table in gro_result:
                        gro = (table.records.pop())['_value']
                        if gro != last_gro:
                            last_gro = gro
                            print("GRO updated:")
                            print(last_gro)
                            message = f"VALVE,GRO,{last_gro}"
                            sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))

            except Exception as e:
                print("Unexpected error: ")
                print(e)
                continue
            time.sleep(0.1)
    
    read_influx_t = threading.Thread(target=influx_to_stm32, daemon=True)
    read_influx_t.start()

    while True:
        try: #read data from ethernet connection and upload to influxdb
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
            time.sleep(5)
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
    ipc_listener.close()
    print("Cleanup complete")