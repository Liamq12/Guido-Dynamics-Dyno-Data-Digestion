import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions
import os
import threading
from multiprocessing.connection import Listener
import queue
import multiprocessing.connection
import math

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
mechanical_file_path = os.path.join(os.getcwd(), "configs\\System\\dyno_mechanical.json")
try:
    with open(influx_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        INFLUX_URL = "http://localhost:8086"
        TOKEN = json_data.get("Token")
        ORG = json_data.get("Org")
        BUCKET = json_data.get("Bucket")
    with open(mechanical_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        loadCellM = json_data.get("load_m")
        loadCellB = json_data.get("load_b")
        momentI = json_data.get("moment_of_inertia")
except Exception as e:
    INFLUX_URL = "http://localhost:8086"
    TOKEN = "blank"
    ORG = "blank"
    BUCKET = "blank"

client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)

engineTorque = 0
loadValue = 0
systemAccel = 0

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
query_vehicleConfig = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "ConfigName")' #setup query for vehicle config name from influxdb
query_api = client.query_api()

gr = 1
gr_queue = queue.Queue()
config_queue = queue.Queue()

fbombs = 0
config_name = None
run_num = 0

run_on_trigger_q = queue.Queue()
run_off_trigger_q = queue.Queue()
running_event = threading.Event()

def IPC(conn, run_num):
        #TODO setup UDP control words to send data to the processor
        trigger_on = 0
        while True:
            msg = conn.recv()
            if msg == "Start RPM":
                rpm = conn.recv()
                print(f"Start RPM set to: {rpm}")
                message = f"COPID,RPM,{rpm}"
                trigger_on = rpm*0.75
                run_on_trigger_q.put(trigger_on)
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "End RPM":
                rpm = conn.recv()
                print(f"End RPM set to: {rpm}")
                message = f"FRAMP,RPM,{rpm}"
                run_off_trigger_q.put(rpm)
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
                running_event.set()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Stop":
                print("stop ramp")
                message = f"ERAMP,RPM,0"
                running_event.clear()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Start Hold RPM":
                print("Holding RPM value")
                message = f"ENPID,RPM,1"
                run_off_trigger_q.put(trigger_on*0.8)
                running_event.set()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            elif msg == "Stop Hold RPM":
                print("Stop RPM hold")
                message = f"ENPID,RPM,0"
                running_event.clear()
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
    last_configName = None
    while True:
        try: #read data from influxdb and send to the controller
            #valve_result = query_api.query(query=query_valvePos, org=ORG)
            ppr_result = query_api.query(query=query_valvePPR, org=ORG)
            gro_result = query_api.query(query=query_valveGRO, org=ORG)
            gr_result = query_api.query(query=query_vehicleGR, org=ORG)
            config_result = query_api.query(query=query_vehicleConfig, org=ORG)
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
            if config_result:
                for table in config_result:
                    config_name = (table.records.pop())['_value']
                    if(config_name != last_configName):
                        last_configName = config_name
                        print(f"Config name updated: {config_name}")
                        config_queue.put(config_name)
        except Exception as e:
            print("Unexpected error: ")
            print(e)
            continue
        time.sleep(1)

IPC_t = threading.Thread(target=IPC, daemon=True, args=(ipc_conn,run_num))
IPC_t.start()

read_influx_t = threading.Thread(target=influx_to_stm32, daemon=True)
read_influx_t.start()

def get_last_pull_num(confName):
    query_runName = f'from(bucket: "{BUCKET}") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "runData")' #setup query for vehicle config name from influxdb
    run_result = query_api.query(query=query_runName, org=ORG)
    last_num = 0
    if run_result:
        for table in run_result:
            for record in table:
                run_name = record['_value']
                print(run_name)
                if(confName in run_name):
                    num = int(run_name.replace(confName + "_", ""))
                    if num > last_num:
                        last_num = num
                        print(num)
    return last_num

# ---------- UDP SETUP ----------
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")
    udp_connection = True
except:
    running = False
    trigger_on = 0
    trigger_off = 0
    udp_connection = False
    print("no connection, just chilling")
    try:
        def fake_speed_data():
            now = time.time()
            f1 = 1000*math.sin(now*0.1) + 1250
            f2 = 100*math.sin(now)
            speed = f1 + f2
            return speed
        def fake_torque_data():
            now = time.time()
            f1 = 30
            f2 = 10*math.sin(now)
            torque = f1 + f2
            return torque
        
        while True:
            time.sleep(0.2)
            if not gr_queue.empty():
                gr = gr_queue.get()
            if not config_queue.empty():
                config_name = config_queue.get()
                run_num = get_last_pull_num(config_name)
            if not run_on_trigger_q.empty():
                trigger_on = run_on_trigger_q.get()
            if not run_off_trigger_q.empty():
                trigger_off = run_off_trigger_q.get()
        
            #print(f'GR: {gr}')
            value = fake_speed_data()
            device = 'test'
            unit = 'rpm'

            rollerSpeed = value
            engineSpeed = value*gr
            turbineSpeed = value*5/4
            roadSpeed = (value*(2*3.14159/60)*4.5)/17.6
            speedLabels = ['rollerSpeed', 'engineSpeed', 'turbineSpeed', 'roadSpeed']
            speedValues = [rollerSpeed, engineSpeed, turbineSpeed, roadSpeed]
            loadValue = fake_torque_data()
            engineTorque = loadValue*(5/4)/gr
            run_name = "None"
            if running_event.is_set():
                if (trigger_off > trigger_on):
                    if(value > trigger_off and running):
                        running_event.clear()
                        running = False
                        run_name = "None"
                        print(f"Run Turned Off. On trigger is: {trigger_on}")
                    elif (value > trigger_off and not running):
                        run_name = "None"
                    elif (value > trigger_on and not running):
                        run_num += 1
                        running = True
                        run_name = f"{config_name}_{run_num}"
                        print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                        point = (
                            Point("runData")
                            .tag("device", device)
                            .tag("unit", "none")
                            .field("value", run_name)
                            )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)
                    elif (running):
                        run_name = f"{config_name}_{run_num}"
                else:
                    if(value < trigger_off and running):
                        running = False
                        run_name = "None"
                        print(f"Run Turned Off. On trigger is: {trigger_on}")
                    elif (value < trigger_off and not running):
                        run_name = "None"
                    elif (value > trigger_on and not running):
                        run_num += 1
                        running = True
                        run_name = f"{config_name}_{run_num}"
                        print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                        point = (
                            Point("runData")
                            .tag("device", device)
                            .tag("unit", "none")
                            .field("value", run_name)
                            )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)
                    elif (running):
                        run_name = f"{config_name}_{run_num}"
            else:
                if running:
                    running = False
                    run_name = "None"


            for i in range(0,len(speedLabels)):
                if(speedLabels[i] == 'roadSpeed'):
                    unit = 'mph'
                else:
                    unit = 'rpm'
                point = (
                    Point(speedLabels[i])
                    .tag("device", device)
                    .tag("unit", unit)
                    .tag("runName", run_name)
                    .field("value", float(speedValues[i]))
                    )
                write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("power")
                .tag("device", device)
                .tag("unit", "HP")
                .tag("runName", run_name)
                .field("value", float(loadValue*turbineSpeed/5252))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("engineTorque")
                .tag("device", device)
                .tag("unit", "lbf-ft")
                .tag("runName", run_name)
                .field("value", float(engineTorque))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("dynoLoad")
                .tag("device", device)
                .tag("unit", "ft-lbf")
                .tag("runName", run_name)
                .field("value", float(loadValue))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point)
    except KeyboardInterrupt:
        print("cancelled")
    except Exception as e:
        print(e)

try:
    running = False
    run_name = "None"
    trigger_off = 0
    trigger_on = 0
    run_num = 0
    config_name = "None"
    while True:
        try: #read data from ethernet connection and upload to influxdb
            if not gr_queue.empty():
                gr = gr_queue.get()
            if not config_queue.empty():
                config_name = config_queue.get()
                run_num = get_last_pull_num(config_name)
            if not run_on_trigger_q.empty():
                trigger_on = run_on_trigger_q.get()
            if not run_off_trigger_q.empty():
                trigger_off = run_off_trigger_q.get()

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
                    value = value * loadCellM + loadCellB
                    loadValue = value
                    engineTorque = loadValue*(5/4)/gr + momentI*systemAccel
                elif metric == "wheelAccel":
                    systemAccel = value*gr
                elif(metric == "wheelSpeed"):
                    if running_event.is_set():
                        if (trigger_off > trigger_on):
                            if(value > trigger_off and running):
                                running_event.clear()
                                running = False
                                run_name = "None"
                                print(f"Run Turned Off. On trigger is: {trigger_on}")
                            elif (value > trigger_off and not running):
                                run_name = "None"
                            elif (value > trigger_on and not running):
                                run_num += 1
                                running = True
                                run_name = f"{config_name}_{run_num}"
                                print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                                point = (
                                    Point("runData")
                                    .tag("device", device)
                                    .tag("unit", "none")
                                    .field("value", run_name)
                                    )
                                write_api.write(bucket=BUCKET, org=ORG, record=point)
                            elif (running):
                                run_name = f"{config_name}_{run_num}"
                        else:
                            if(value < trigger_off and running):
                                running = False
                                run_name = "None"
                                print(f"Run Turned Off. On trigger is: {trigger_on}")
                            elif (value < trigger_off and not running):
                                run_name = "None"
                            elif (value > trigger_on and not running):
                                run_num += 1
                                running = True
                                run_name = f"{config_name}_{run_num}"
                                print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                                point = (
                                    Point("runData")
                                    .tag("device", device)
                                    .tag("unit", "none")
                                    .field("value", run_name)
                                    )
                                write_api.write(bucket=BUCKET, org=ORG, record=point)
                            elif (running):
                                run_name = f"{config_name}_{run_num}"
                    else:
                        if running:
                            running = False
                            run_name = "None"
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
                        .tag("runName", run_name)
                        .field("value", float(speedValues[i]))
                        )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)

                    point = (
                        Point("power")
                        .tag("device", device)
                        .tag("unit", "HP")
                        .tag("runName", run_name)
                        .field("value", float(loadValue*turbineSpeed/5252))
                    )
                    write_api.write(bucket=BUCKET, org=ORG, record=point)

                    point = (
                        Point("enginePower")
                        .tag("device", device)
                        .tag("unit", "HP")
                        .tag("runName", run_name)
                        .field("value", float(engineTorque*engineSpeed/5252))
                    )
                    write_api.write(bucket=BUCKET, org=ORG, record=point)

                    point = (
                        Point("engineTorque")
                        .tag("device", device)
                        .tag("unit", "lbf-ft")
                        .tag("runName", run_name)
                        .field("value", float(engineTorque))
                    )
                    write_api.write(bucket=BUCKET, org=ORG, record=point)
                try:
                    point = (
                        Point(metric)
                        .tag("device", device)
                        .tag("unit", unit)
                        .tag("runName", run_name)
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