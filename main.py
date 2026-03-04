import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions
import os
import threading
from multiprocessing.connection import Listener
import queue
import multiprocessing.connection

UDP_IP = "192.168.0.2"
UDP_PORT = 7

UDP_IP_SEND = "192.168.0.123"
UDP_PORT_SEND = 8

#setup ipc to user terminal
ipc_address = ('localhost', 31205)
ipc_listener = Listener(ipc_address, authkey=b'key')
ipc_conn = ipc_listener.accept()

udp_connection = False

# Load cell constants
loadcellZero = 1.660
loadcellTF = 0.002  # Volts per lbf

sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ---------- InfluxDB Setup ----------
#load in the influx db file for user token and such
influx_file_path = os.path.join(os.getcwd(), "configs\\System\\influxdb.json")
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
query_valvePPR = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "PPR")' #setup query for valve pulses per revolution from influxdb
query_valveGRO = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "GRO")' #setup query for valve gear ratio from influxdb
query_vehicleGR = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "GearRatio")' #setup query for vehicle gear ratio from influxdb
query_api = client.query_api()

gr = 1
gr_queue = queue.Queue()

fbombs = 0

def IPC(conn):
        #TODO setup UDP control words to send data to the processor
        while True:
            msg = conn.recv()
            if msg == "Start RPM":
                rpm = conn.recv()
                print(f"Start RPM set to: {rpm}")
                message = f"COPID,RPM,{rpm}"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "End RPM":
                rpm = conn.recv()
                print(f"End RPM set to: {rpm}")
                message = f"FRAMP,RPM,{rpm}"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Rate":
                rate = conn.recv()
                print(f"RPM Rate set to: {rate}")
                message = f"FRAMP,RTE,{rate}"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Start":
                print("start ramp")
                message = f"ERAMP,RPM,1"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Stop":
                print("stop ramp")
                message = f"ERAMP,RPM,0"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Start Hold RPM":
                print("Holding RPM value")
                message = f"ENPID,RPM,1"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Stop Hold RPM":
                print("Stop RPM hold")
                message = f"ENPID,RPM,0"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "ValvePos":
                pos = conn.recv()
                print(f"Setting valve pos: {pos}")
                message = f"VALVE,POS,{pos}"     
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))

def influx_to_stm32():
    last_valve_pos = None
    last_ppr = None
    last_gro = None
    last_gr = None
    while True:
        try: #read data from influxdb and send to the controller
            #valve_result = query_api.query(query=query_valvePos, org=ORG)
            ppr_result = query_api.query(query=query_valvePPR, org=ORG)
            gro_result = query_api.query(query=query_valveGRO, org=ORG)
            gr_result = query_api.query(query=query_vehicleGR, org=ORG)
            '''
                for table in valve_result:
                    last_record = table.records.pop()
                    valve_pos = last_record['_value']
                    if valve_pos != last_valve_pos:
                        last_valve_pos = valve_pos
                        message = f"VALVE,POS,{valve_pos}"
                        sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                    #print(f"Last value is: {last_record['_value']}")
'''
            if ppr_result:
                for table in ppr_result:
                    ppr = (table.records.pop())['_value']
                    if ppr != last_ppr:
                        
                        last_ppr = ppr
                        print("PPR updated:")
                        print(last_ppr)
                        message = f"VALVE,PPR,{last_ppr}"
                        if(udp_connection):
                            sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            if gro_result:
                for table in gro_result:
                    gro = (table.records.pop())['_value']
                    if gro != last_gro:
                        last_gro = gro
                        print("GRO updated:")
                        print(last_gro)
                        message = f"VALVE,GRO,{last_gro}"
                        if(udp_connection):
                            sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            if gr_result:
                for table in gr_result:
                    gr = (table.records.pop())['_value']
                    if(gr != last_gr):
                        last_gr = gr
                        print(f"gear ratio updated: {gr}")
                        gr_queue.put(gr)
        except Exception as e:
            print("Unexpected error: ")
            print(e)
            continue
        time.sleep(1)

IPC_t = threading.Thread(target=IPC, daemon=True, args=(ipc_conn,))
IPC_t.start()

read_influx_t = threading.Thread(target=influx_to_stm32, daemon=True)
read_influx_t.start()

# ---------- UDP SETUP ----------
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")
    udp_connection = True
except:
    udp_connection = False
    print("no connection, just chilling")
    try:
        while True:
            time.sleep(1)
            if not gr_queue.empty():
                gr = gr_queue.get()
            #print(f'GR: {gr}')
            value = 1000
            device = 'test'
            unit = 'rpm'

            rollerSpeed = value
            engineSpeed = value*gr
            turbineSpeed = value*5/4
            roadSpeed = (value*(2*3.14159/60)*4.5)/17.6
            speedLabels = ['rollerSpeed', 'engineSpeed', 'turbineSpeed', 'roadSpeed']
            speedValues = [rollerSpeed, engineSpeed, turbineSpeed, roadSpeed]
            loadValue = 0

            for i in range(0,len(speedLabels)):
                if(speedLabels[i] == 'roadSpeed'):
                    unit = 'mph'
                else:
                    unit = 'rpm'
                point = (
                    Point(speedLabels[i])
                    .tag("device", device)
                    .tag("unit", unit)
                    .field("value", float(speedValues[i]))
                    )
                write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("power")
                .tag("device", device)
                .tag("unit", "HP")
                .field("value", float(loadValue*turbineSpeed))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point)
    except KeyboardInterrupt:
        print("cancelled")
    except Exception as e:
        print(e)

try:
    while True:
        try: #read data from ethernet connection and upload to influxdb
            if not gr_queue.empty():
                gr = gr_queue.get()

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
                    value = value * 0.6066 + 0.092
                    loadValue = value
                elif(metric == "wheelSpeed"):
                    rollerSpeed = value
                    engineSpeed = value*gr
                    turbineSpeed = value*5/4
                    roadSpeed = (value*(2*3.14159/60)*4.5)/17.6
                    speedLabels = ['rollerSpeed', 'engineSpeed', 'turbineSpeed', 'roadSpeed']
                    speedValues = [rollerSpeed, engineSpeed, turbineSpeed, roadSpeed]

                    for i in range(0,len(speedLabels)):
                        if(speedLabels[i] == 'roadSpeed'):
                            unit = 'mph'
                        else:
                            unit = 'rpm'
                        point = (
                        Point(speedLabels[i])
                        .tag("device", device)
                        .tag("unit", unit)
                        .field("value", float(speedValues[i]))
                        )
                    write_api.write(bucket=BUCKET, org=ORG, record=point)

                    point = (
                        Point("power")
                        .tag("device", device)
                        .tag("unit", "HP")
                        .field("value", float(loadValue*turbineSpeed))
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