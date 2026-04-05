import socket, json, time, random
from influxdb_client import InfluxDBClient, Point, WriteOptions
import os
import threading
from multiprocessing.connection import Listener
import queue
import multiprocessing.connection
import math
import pytz
from datetime import timedelta, timezone, datetime

#IP address and port for receiving from DAQ
UDP_IP = "192.168.0.2"
UDP_PORT = 7

#IP address and port for sending to DAQ
UDP_IP_SEND = "192.168.0.123"
UDP_PORT_SEND = 8
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

#setup inter-process communication to user terminal
ipc_address = ('localhost', 31205)
ipc_listener = Listener(ipc_address, authkey=b'key')
ipc_conn = ipc_listener.accept()
#var for debug if the computer is not connected to the DAQ
udp_connection = False

# Load cell constants, hardcoded
loadcellZero = 1.660
loadcellTF = 0.002  # Volts per lbf

# ---------- System Config Setup ----------
#load in the influx db file and mechanical config
influx_file_path = os.path.join(os.getcwd(), "configs\\System\\influxdb.json")
mechanical_file_path = os.path.join(os.getcwd(), "configs\\System\\dyno_mechanical.json")
try:
    #open the influxdb.json file for token, bucket, org
    with open(influx_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        INFLUX_URL = "http://localhost:8086"
        TOKEN = json_data.get("Token")
        ORG = json_data.get("Org")
        BUCKET = json_data.get("Bucket")
    #open the dyno_mechanical.json file for moment of inertia and load cell equation values, y=mx+b
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

#influxdb client using paramaters from json
client = InfluxDBClient(url=INFLUX_URL, token=TOKEN, org=ORG)

#variables that are stored to calculate data that's a function of multiple measured values
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

#setup influx querys for data that is posted/read
query_valvePos = f'from(bucket: "{BUCKET}") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "ValvePos")' #setup query for valvepos from influxdb
query_valvePPR = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "PPR")' #setup query for valve pulses per revolution from influxdb
query_valveGRO = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "GRO")' #setup query for valve gear ratio from influxdb
query_vehicleGR = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "GearRatio")' #setup query for vehicle gear ratio from influxdb
query_vehicleConfig = f'from(bucket: "{BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "ConfigName")' #setup query for vehicle config name from influxdb
query_api = client.query_api()

#gear ratio and config data that needs a queue for multiple threads
gr = 1
gr_queue = queue.Queue()
config_queue = queue.Queue()
#config name and run number are used for putting data in batches
config_name = None
run_num = 0

#queue and event for threads that's used to determine when data should be batched into runs
run_on_trigger_q = queue.Queue()
run_off_trigger_q = queue.Queue()
start_rpm_q = queue.Queue()
running_event = threading.Event()
run_started = threading.Event()

#interprocess communication that uses a socket to receive data from the user terminal
def IPC(conn):
        trigger_on = 0
        while True:
            msg = conn.recv()
            #when start RPM is set, we automatically target this value until the user starts the ramp
            if msg == "Start RPM":
                rpm = conn.recv()
                print(f"Start RPM set to: {rpm}")
                message = f"COPID,RPM,{rpm}"
                trigger_on = rpm*0.9
                run_on_trigger_q.put(trigger_on)
                start_rpm_q.put(rpm)
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                message = f"ENPID,RPM,1"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #end RPM setting for ramp
            elif msg == "End RPM":
                rpm = conn.recv()
                print(f"End RPM set to: {rpm}")
                message = f"FRAMP,RPM,{rpm}"
                run_off_trigger_q.put(rpm)
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #ramp rate
            elif msg == "Rate":
                rate = conn.recv()
                print(f"RPM Rate set to: {rate}")
                message = f"FRAMP,RTE,{rate}"
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #command to start the ramp
            elif msg == "Start":
                print("start ramp")
                message = f"FRAMP,ENA,1"
                running_event.set()
                run_started.set()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #command to stop the ramp - TODO not implemented in controller yet
            elif msg == "Stop":
                print("stop ramp")
                message = f"ERAMP,RPM,0"
                running_event.clear()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #hold RMP is began. TODO this doesn't do anything other than tell the program to batch run data. The rpm is already being held immediately after it is set
            elif msg == "Start Hold RPM":
                print("Holding RPM value")
                message = f"ENPID,RPM,1"
                run_off_trigger_q.put(trigger_on*0.8)
                running_event.set()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #hold RMP is stopped. TODO determine if this is the functionality that we actually want
            elif msg == "Stop Hold RPM":
                print("Stop RPM hold")
                message = f"ENPID,RPM,0"
                running_event.clear()
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
            #manually set valve position. TODO we may want to get rid of this functionality, or hide it in a debug window
            elif msg == "ValvePos":
                pos = conn.recv()
                print(f"Setting valve pos: {pos}")
                message = f"VALVE,POS,{pos}"  
                if(udp_connection):
                    sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))

#thread for reading data from influxdb
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
            #pulses per revolution for valve TODO may want to move this to IPC
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
            #gear ratio for valve TODO may want to move this to IPC
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
            #gear ratio for engine TODO may want to move this to IPC
            if gr_result:
                for table in gr_result:
                    gr = (table.records.pop())['_value']
                    if(gr != last_gr):
                        last_gr = gr
                        print(f"gear ratio updated: {gr}")
                        gr_queue.put(gr)
            #last config name uploaded to influx for run batching purposes
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

#start the IPC thread
IPC_t = threading.Thread(target=IPC, daemon=True, args=(ipc_conn,))
IPC_t.start()
#start the influxdb reading thread
read_influx_t = threading.Thread(target=influx_to_stm32, daemon=True)
read_influx_t.start()

#function for finding the last pull number that was logged using the given configuration name. Used to increment run batching on startup or config change
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

# ---------- UDP SETUP --------
# Begin the UDP connection to the DAQ--
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(5.0)
    print(f"Listening for UDP packets on {UDP_IP}:{UDP_PORT}...")
    #enable sending/receiving data
    udp_connection = True
except:
    #duplicate of the real while loop, below, that runs the bulk of the program. This is just for debug purposes and is likely out of date. 
    # TODO make a function so this is more readable and comparable to the real loop
    running = False
    trigger_on = 0
    trigger_off = 0
    udp_connection = False
    print("no connection, just chilling")
    try:
        freq = 10
        dt = 1/freq
        tau = dt / (0.1 + dt)
        a_est = 0
        w_est = 0
        T_filtered_prev = 0
        alpha = 0.4
        beta = 0.1
        #creates functions to generate fake torque and speed data for debug
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
        #the loop of the program
        while True:
            time.sleep(1/freq)
            #check queues to see if the variable have been updated in another thread
            if not gr_queue.empty():
                gr = gr_queue.get()
            if not config_queue.empty():
                config_name = config_queue.get()
                run_num = get_last_pull_num(config_name)
            if not run_on_trigger_q.empty():
                trigger_on = run_on_trigger_q.get()
            if not run_off_trigger_q.empty():
                trigger_off = run_off_trigger_q.get()
        
            #speed is pulled from fake speed data function
            value = fake_speed_data()
            loadValue = fake_torque_data()
            device = 'test'
            unit = 'rpm'

            rawSpeed = value
            w_measured = rawSpeed
            innovation = w_measured - w_est - a_est * dt
            w_est = w_est + a_est * dt + alpha * innovation
            a_est = a_est + beta * innovation / dt
            rollerSpeed = w_est
            systemAccel = a_est

            T_filtered = tau * loadValue + (1 - tau) * T_filtered_prev
            T_filtered_prev = T_filtered

            engineSpeed = rollerSpeed*gr
            turbineSpeed = rollerSpeed*5/4
            roadSpeed = (rollerSpeed*(2*3.14159/60)*4.5)/17.6
            speedLabels = ['rawSpeed', 'rollerSpeed', 'engineSpeed', 'turbineSpeed', 'roadSpeed']
            speedValues = [rawSpeed, rollerSpeed, engineSpeed, turbineSpeed, roadSpeed]
            engineTorque = (T_filtered*(5/4) + momentI*systemAccel)/gr #calculate engine torque using gear ratio and acceleration of rollers with moment of inertia
            
            #run name for batching purposes
            run_name = "None"
            #the user terminal sets the event that a run is "running". It then uses the triggers to determine if we should actually put it in a batch or not
            if running_event.is_set():
                if (trigger_off > trigger_on): #we know we are in ramp mode when trigger_off > trigger_on
                    if(value > trigger_off and running): #we have exceeded trigger off but still in a run, so let's disable the run
                        running_event.clear()
                        running = False
                        run_name = "None"
                        print(f"Run Turned Off. On trigger is: {trigger_on}")
                    elif (value > trigger_off and not running): #we have exceeded the trigger off and run is already disabled, do nothing
                        run_name = "None"
                    elif (value > trigger_on and not running): #we have exceeded the trigger on and not in a run, so we want to enable a run
                        run_num += 1 #increment run number
                        running = True
                        run_name = f"{config_name}_{run_num}"
                        print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                        point = (
                            Point("runData")
                            .tag("device", device)
                            .tag("unit", "none")
                            .field("value", run_name)
                            )
                        write_api.write(bucket=BUCKET, org=ORG, record=point) #write the new run name to influx so we can pull from it
                    elif ( value < trigger_on*0.75 and running): #we're in a run but we fell so far below the trigger on that we're going to cancel it
                        running_event.clear()
                        running = False
                        run_name = "None"
                        print(f"Run Turned Off. On trigger is: {trigger_on}")
                    elif (running): #nothing told us to turn off the run so we'll continue as normal
                        run_name = f"{config_name}_{run_num}"
                else: #we know we are in hold mode
                    if(value < trigger_off and running): #in hold mode trigger off is below trigger on. The speed dipped below trigger off so we cancel the run
                        running = False
                        run_name = "None"
                        print(f"Run Turned Off. On trigger is: {trigger_on}")
                    elif (value < trigger_off and not running): #not running
                        run_name = "None"
                    elif (value > trigger_on and not running): #we have passed the trigger on so we will start a new run
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
                if running: #the running flag was disabled so we will stop the current run
                    running = False
                    run_name = "None"

            for i in range(0,len(speedLabels)): #iterate through each speed label and post its corresponding value to influx
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
                Point("wheelAccel")
                .tag("device", device)
                .tag("unit", "RPM/s")
                .tag("runName", run_name)
                .field("value", systemAccel)
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("power")
                .tag("device", device)
                .tag("unit", "HP")
                .tag("runName", run_name)
                .field("value", float(loadValue*turbineSpeed/5252))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point) #post power value to influx

            point = (
                Point("engineTorque")
                .tag("device", device)
                .tag("unit", "lbf-ft")
                .tag("runName", run_name)
                .field("value", float(engineTorque))
            )
            write_api.write(bucket=BUCKET, org=ORG, record=point) #post engine torque value to influx

            point = (
                Point("dynoLoad")
                .tag("device", device)
                .tag("unit", "ft-lbf")
                .tag("runName", run_name)
                .field("value", float(loadValue)) #post measured torque value to influx
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

    except KeyboardInterrupt:
        print("cancelled")
    except Exception as e:
        print(e)

#---This is the main loop that runs, takes data from the UDP connection and posts it to influx -----#
try:
    #setup default var values
    running = False
    run_name = "None"
    trigger_off = 0
    trigger_on = 0
    start_rpm = 0
    run_num = 0
    config_name = "None"
    point = (
        Point("runData")
        .tag("device", "CMD")
        .tag("unit", "none")
        .field("value", run_name)
        )
    write_api.write(bucket=BUCKET, org=ORG, record=point) #post blank run data to make sure we don't have null value problems
    
    a_est = 0
    w_est = 0
    T_filtered_prev = 0
    alpha = 0.4
    beta = 0.1
    while True:
        try: #read data from ethernet connection and upload to influxdb
            #check queues to see if the variable have been updated in another thread
            if not gr_queue.empty():
                gr = gr_queue.get()
            if not start_rpm_q.empty():
                start_rpm = start_rpm_q.get()
            if not config_queue.empty():
                config_name = config_queue.get()
                run_num = get_last_pull_num(config_name)
            if not run_on_trigger_q.empty():
                trigger_on = run_on_trigger_q.get()
            if not run_off_trigger_q.empty():
                trigger_off = run_off_trigger_q.get()

            #take in data from ethernet connection
            data, addr = sock.recvfrom(16384)
            raw = data.decode("utf-8").strip()
            raw = raw.replace("inf", "-1")
            print(f"Received from {addr}: {raw}")

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                continue

            #get basic fields from json
            device = parsed.get("device", "unknown")
            uptime = parsed.get("uptime")
            device_id = parsed.get("id")
            data_packet = parsed.get("data")
            #go into data packet to get headers and each individual data packet
            headers = data_packet.get("headers", [])
            cycles = data_packet.get("cycles")
            # recieve_time = datetime.datetime.now() # This time format is not supported by influxdb, need utc olson tz format
            recieve_time = datetime.now(pytz.timezone("America/Denver"))
            freq = data_packet.get("freq")
            temp = data_packet.get("tmp")
            pressure = data_packet.get("prs")
            humidity = data_packet.get("hum")
            timestamp = recieve_time - timedelta(seconds=0.5) # compensate and sync data for how the STM32 calculates temp, pres, humidity

            # Post temp, pressure, humidity data point to influx. Lazy way, will refactor later
            point = (
                Point("ambTemp")
                .tag("device", device)
                .tag("unit", "C")
                .field("value", temp)
                .time(timestamp)
                )
            write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("ambPressure")
                .tag("device", device)
                .tag("unit", "hPa")
                .field("value", pressure/100) # Convert from pascals to hPa
                .time(timestamp)
                )
            write_api.write(bucket=BUCKET, org=ORG, record=point)

            point = (
                Point("ambHumidity")
                .tag("device", device)
                .tag("unit", "none")
                .field("value", humidity)
                .time(timestamp)
                )
            write_api.write(bucket=BUCKET, org=ORG, record=point)
            dt = 1/freq
            tau = dt / (0.1 + dt)

            print(f"Device: {device}, Uptime: {uptime}, ID: {device_id}")

            for cycle in range(cycles): #iterate through each numbered data packet and post its data at the correct time
                rows = data_packet.get(f"data{cycle}", []) 
                for row in rows: #iterate through the data packets values
                    entry = dict(zip(headers, row))

                    metric = entry.get("metric")
                    # unit = entry.get("unit") # Removed due to ethernet packet limitations
                    value = entry.get("value")
                    cycle_back = cycles - cycle
                    
                    seconds_back = cycle_back/freq #calculate what time each data point was recorded
                    timestamp = recieve_time - timedelta(seconds=seconds_back)

                    if value is None:
                        value = -1

                    # Load cell conversion
                    if metric == "dyLd":
                        metric = "dynoLoad" # Real name
                        print(f"Raw val is: {value}")
                        value = (value - loadcellZero) / loadcellTF #hardcoded load cell values
                        value = value * loadCellM + loadCellB #mx+b equation from torque wrench callibration. These values are in the mechanical config json
                        loadValue = value #save load cell value for power calculation
                    elif metric == "acel":
                        metric = "wheelAccel"
                        #systemAccel = value*gr #TODO check units for this
                    elif metric == "RPMT":
                        metric = "RPMTarget"
                    elif metric == "vPos":
                        metric = "valvePos"
                    elif(metric == "rSpd"): #a lot of logic gets done here with the wheel speed metric
                        metric = "wheelSpeed" # Real name
                        #the user terminal sets the event that a run is "running". It then uses the triggers to determine if we should actually put it in a batch or not
                        if running_event.is_set():
                            if (trigger_off > trigger_on): #we know we are in ramp mode when trigger_off > trigger_on
                                if(value > trigger_off and running): #we have exceeded trigger off but still in a run, so let's disable the run
                                    running_event.clear()
                                    running = False
                                    run_name = "None"
                                    print(f"Run Turned Off. On trigger is: {trigger_on}")
                                elif (value > trigger_off and not running): #we have exceeded the trigger off and run is already disabled, do nothing
                                    run_name = "None"
                                elif (value > trigger_on and not running): #we have exceeded the trigger on and not in a run, so we want to enable a run
                                    run_num += 1 #increment run number
                                    running = True
                                    run_name = f"{config_name}_{run_num}"
                                    print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                                    point = (
                                        Point("runData")
                                        .tag("device", device)
                                        .tag("unit", "none")
                                        .field("value", run_name)
                                        .time(timestamp)
                                        )
                                    write_api.write(bucket=BUCKET, org=ORG, record=point) #write the new run name to influx so we can pull from it
                                elif ( value < trigger_on*0.75 and running): #we're in a run but we fell so far below the trigger on that we're going to cancel it
                                    running_event.clear()
                                    running = False
                                    run_name = "None"
                                    print(f"Run Turned Off. On trigger is: {trigger_on}")
                                elif (running): #nothing told us to turn off the run so we'll continue as normal
                                    run_name = f"{config_name}_{run_num}"
                            else: #we know we are in hold mode
                                if(value < trigger_off and running): #in hold mode trigger off is below trigger on. The speed dipped below trigger off so we cancel the run
                                    running = False
                                    run_name = "None"
                                    print(f"Run Turned Off. On trigger is: {trigger_on}")
                                elif (value < trigger_off and not running): #not running
                                    run_name = "None"
                                elif (value > trigger_on and not running): #we have passed the trigger on so we will start a new run
                                    run_num += 1
                                    running = True
                                    run_name = f"{config_name}_{run_num}"
                                    print(f"Run Triggered for {run_name} Off trigger is: {trigger_off}")
                                    point = (
                                        Point("runData")
                                        .tag("device", device)
                                        .tag("unit", "none")
                                        .field("value", run_name)
                                        .time(timestamp)
                                        )
                                    write_api.write(bucket=BUCKET, org=ORG, record=point)
                                elif (running):
                                    run_name = f"{config_name}_{run_num}"
                        else:
                            if running: #the running flag was disabled so we will stop the current run
                                running = False
                                run_name = "None"
                        if run_started.is_set() and value < trigger_on: #the run was started but rpms have dipped below start rpm - either before or after a full pull. Resend start rpm to STM
                            run_started.clear()
                            message = f"COPID,RPM,{start_rpm}"
                            if(udp_connection):
                                sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                            message = f"ENPID,RPM,1"
                            if(udp_connection):
                                sock_send.sendto(message.encode(), (UDP_IP_SEND, UDP_PORT_SEND))
                            
                        #calculate all the rotational speeds. Assign each speed a label and value
                        rawSpeed = value
                        w_measured = rawSpeed
                        innovation = w_measured - w_est - a_est * dt
                        w_est = w_est + a_est * dt + alpha * innovation
                        a_est = a_est + beta * innovation / dt
                        rollerSpeed = w_est
                        systemAccel = a_est

                        T_filtered = tau * loadValue + (1 - tau) * T_filtered_prev
                        T_filtered_prev = T_filtered

                        engineSpeed = rollerSpeed*gr
                        turbineSpeed = rollerSpeed*5/4
                        roadSpeed = (rollerSpeed*(2*3.14159/60)*4.5)/17.6
                        speedLabels = ['rawSpeed', 'rollerSpeed', 'engineSpeed', 'turbineSpeed', 'roadSpeed']
                        speedValues = [rawSpeed, rollerSpeed, engineSpeed, turbineSpeed, roadSpeed]
                        engineTorque = (T_filtered*(5/4) + momentI*systemAccel)/gr #calculate engine torque using gear ratio and acceleration of rollers with moment of inertia
                        
                        for i in range(0,len(speedLabels)): #iterate through all of the speeds and push them to influx
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
                            .time(timestamp)
                            )
                            write_api.write(bucket=BUCKET, org=ORG, record=point)

                        point = (
                            Point("wheelAccel")
                            .tag("device", device)
                            .tag("unit", "RPM/s")
                            .tag("runName", run_name)
                            .field("value", systemAccel)
                            .time(timestamp)
                        )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)

                        point = (
                            Point("power")
                            .tag("device", device)
                            .tag("unit", "HP")
                            .tag("runName", run_name)
                            .field("value", float(loadValue*turbineSpeed/5252))
                            .time(timestamp)
                        )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)

                        point = (
                            Point("enginePower")
                            .tag("device", device)
                            .tag("unit", "HP")
                            .tag("runName", run_name)
                            .field("value", float(engineTorque*engineSpeed/5252))
                            .time(timestamp)
                        )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)

                        point = (
                            Point("engineTorque")
                            .tag("device", device)
                            .tag("unit", "lbf-ft")
                            .tag("runName", run_name)
                            .field("value", float(engineTorque))
                            .time(timestamp)
                        )
                        write_api.write(bucket=BUCKET, org=ORG, record=point)
                    try: #for all data we receive we post it to influx
                        point = (
                            Point(metric)
                            .tag("device", device)
                            .tag("unit", unit)
                            .tag("runName", run_name)
                            .field("value", float(value))
                            .time(timestamp)
                        )

                        write_api.write(bucket=BUCKET, org=ORG, record=point)
                        # print(f"Wrote: {metric}={float(value)} {unit} @ {timestamp} @ {cycle} @ {seconds_back}")

                    except Exception as e:
                        print(f"Error writing {metric}: {e}")

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