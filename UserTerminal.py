#!/usr/bin/env python3
"""
Terminal Interface (htop-style)
Cross-platform terminal UI using rich library

Install: pip install rich
Run: python terminal_interface.py
"""

import time
import random
import json
import os
import threading
from influxdb_client import InfluxDBClient, Point, WriteOptions
from tkinter import Tk, filedialog
from datetime import datetime, timedelta
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import BarColumn, Progress, TextColumn
from rich.live import Live
from rich.text import Text
from multiprocessing.connection import Client

class TerminalInterface:
    def __init__(self):
        self.console = Console()
        self.active_tab = 0
        self.tabs = ['VALVE', 'RUN', 'GEAR SYNCH', 'CONTROLS']
        self.input_value = ""
        self.valve_entries = ["", "", "SAVE"]
        self.submitted_value = None
        self.selected_button = 0
        self.selected_entry = 0
        self.button_labels = ['Setup Valve', 'Load Run Plan', 'Load Configuration File', 'Setup InfluxDB', 'Exit']
        self.entries = ["[green]Enter valve controller pulses per rotation:\n", "[green]Enter planetary gearbox ratio (output/input):\n", "[green]Save Values:\n"]
        self.button_status = ""
        self.json_file_path = None
        self.json_data = None
        self.json_error = None
        self.start_time = datetime.now() # - timedelta(days=3, hours=12, minutes=45)
        self.ramp_t = None
        self.run_config = None
        self.run_mode = None
        self.start_rpm = None
        self.end_rpm = None
        self.ramp_rate = None
        self.target_rpm = 0
        self.current_engine_speed = 0

        self.gr_calc = 0

        self.is_ramping = False
        self.stop_event = threading.Event()

        #load in the influx db file for user token and such
        self.influx_file_path = os.path.join(os.getcwd(), "configs\\System\\influxdb.json")
        self.system_config_file_path = os.path.join(os.getcwd(), "configs\\System\\valve_config.json")
        try:
            with open(self.influx_file_path, 'r', encoding='utf-8') as f:
                self.json_data = json.load(f)
                self.INFLUX_URL = "http://localhost:8086"
                self.TOKEN = self.json_data.get("Token")
                self.ORG = self.json_data.get("Org")
                self.BUCKET = self.json_data.get("Bucket")
                self.client = InfluxDBClient(url=self.INFLUX_URL, token=self.TOKEN, org=self.ORG)
                self.write_api = self.client.write_api(write_options=WriteOptions(batch_size=1))
                self.query_api = self.client.query_api()
                self.influx_json_read = True
        except Exception as e:
            self.influx_json_read = False

        try:
            with open(self.system_config_file_path, 'r', encoding='utf-8') as f:
                self.json_data = json.load(f)
                self.valve_entries[0] = self.json_data.get("PPR")
                self.valve_entries[1] = self.json_data.get("GRO")

                self.send_valve_params()
        except Exception as e:
            print(e)
        
        self.gear_ratio = 1
        self.running = True
        
    def send_valve_params(self):
        try:
            metric = "PPR"
            device = "terminal"
            unit = "none"
            value = self.valve_entries[0]
            point = (
                Point(metric)
                .tag("device", device)
                .tag("unit", unit)
                .field("value", float(value))
                )

            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=point)
            print(f"Wrote: {metric}={value} {unit}")
            metric = "GRO"
            device = "terminal"
            unit = "none"
            value = self.valve_entries[1]
            point = (
                    Point(metric)
                    .tag("device", device)
                    .tag("unit", unit)
                    .field("value", float(value))
                )
            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=point)
            print(f"Wrote: {metric}={value} {unit}")

        except Exception as e:
            print(f"Error writing {metric}: {e}")  
            
    def get_color(self, value):
        """Return color based on value"""
        if value < 30:
            return "green"
        elif value < 70:
            return "yellow"
        else:
            return "red"
    
    def create_bar(self, value, max_val, width=30):
        """Create a text-based progress bar"""
        filled = int((value / max_val) * width)
        percentage = int((value / max_val) * 100)
        bar = "█" * filled + "░" * (width - filled)
        color = self.get_color(percentage)
        return f"[{color}]{bar}[/{color}] [{color}]{percentage:3d}%[/{color}]"
    
    def make_header(self):
        """Create header panel"""
        now = datetime.now()
        uptime = now - self.start_time
        
        header_text = Text()
        header_text.append("Guido Dyno Terminal", style="bold white")
        header_text.append(" v2.1.0", style="green")
        header_text.append(f"\n{now.strftime('%H:%M:%S')} | ", style="green")
        header_text.append(f"Uptime: {uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m", style="green")
        
        return Panel(header_text, style="green")
    
    def make_menu(self):
        """Create menu bar"""
        menu_parts = []
        for i, tab in enumerate(self.tabs):
            if i == self.active_tab:
                menu_parts.append(f"[black on green] F{i+1} {tab} [/black on green]")
            else:
                menu_parts.append(f"[green] F{i+1} {tab} [/green]")
        
        menu_text = "  ".join(menu_parts)
        return Panel(menu_text, style="green")
    
    def get_last_speed(self):
        query_speed = f'from(bucket: "{self.BUCKET}") |> range(start: -1s) |> filter(fn: (r) => r._measurement == "rollerSpeed")'
        speed_result = self.query_api.query(query=query_speed, org=self.ORG)  
        self.gr_calc = 'trying to query speed'
        if speed_result:
            for table in speed_result:
                speed = (table.records.pop())['_value']
                #self.gr_calc = speed
                #turbine_speed = speed/self.gear_ratio
                rollerSpeed = speed
        else:
            rollerSpeed = 0
        return rollerSpeed

    def make_valvepos_tab(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Number Input Form[/bold white]\n")
        content.append("[green]Enter a numeric value below (0-100) corresponding to valve opening percentage:[/green]\n")
        
        # Input field display
        input_display = f"[cyan]> {self.input_value}_[/cyan]" if len(self.input_value) < 20 else f"[cyan]> {self.input_value}[/cyan]"
        content.append(f"  {input_display}\n")
        
        # Instructions
        content.append("[yellow]Type numbers (0-9), Backspace to delete, Enter to submit[/yellow]\n")
        
        # Show submitted value
        if self.submitted_value is not None:
            content.append(f"\n[bold green]Last Submitted Value: {self.submitted_value}[/bold green]")
        else:
            content.append("\n[dim]No value submitted yet[/dim]")
        
        return Panel("\n".join(content), title="Input", style="green")
    
        """Create network information view"""
        content = []
        
        content.append("[bold white]Network Interfaces:[/bold white]\n")
        
        # eth0
        content.append("[green]eth0: 192.168.1.100[/green]")
        rx_speed = random.randint(50, 80)
        tx_speed = random.randint(30, 60)
        content.append(f"  RX: {self.create_bar(rx_speed, 100, 30)}  [green]{rx_speed} Mbps[/green]")
        content.append(f"  TX: {self.create_bar(tx_speed, 100, 30)}  [green]{tx_speed} Mbps[/green]")
        
        content.append("\n[green]wlan0: 10.0.0.25[/green]")
        rx_speed = random.randint(15, 35)
        tx_speed = random.randint(10, 25)
        content.append(f"  RX: {self.create_bar(rx_speed, 100, 30)}  [green]{rx_speed} Mbps[/green]")
        content.append(f"  TX: {self.create_bar(tx_speed, 100, 30)}  [green]{tx_speed} Mbps[/green]")
        
        return Panel("\n".join(content), title="Network", style="green")
    
    def make_run_tab(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Dyno Run Control[/bold white]\n")
        
        if self.run_config == None:
            content.append("[yellow]No Run Plan Loaded[/yellow]\n")
        else:
            if(not self.is_ramping):
                content.append("[yellow]Run Plan Loaded[/yellow]\n")
            else:
                content.append("[black on green]Running[/black on green]\n")
            if self.run_mode == "Ramp":
                content.append("[bold white]Ramp Mode[/bold white]\n")
                content.append(f"[bold green]Start RPM:[/bold green] [bold white]{self.start_rpm}[/bold white] \n")
                content.append(f"[bold green]End RPM:[/bold green] [bold white]{self.end_rpm}[/bold white]\n")
                content.append(f"[bold green]RPM Rate (RPM/s):[/bold green] [bold white]{self.ramp_rate}[/bold white]\n")
            elif self.run_mode == "Hold":
                content.append("[bold white]Hold Mode[/bold white]\n")
                content.append(f"[bold green]Hold RPM:[/bold green] [bold white]{self.start_rpm}[/bold white] \n")
            content.append("[green]Press 'Enter' key to start/stop[/green]\n")


        content.append(f"[green]Current RPM: {round(self.current_engine_speed)}[/green]")
        return Panel("\n".join(content), title="Input", style="green")

    def make_buttons_tab(self):
        """Create buttons/menu view"""
        content = []
        
        content.append("[bold white]Button Menu[/bold white]\n")
        content.append("[green]Use LEFT/RIGHT arrow keys to select, ENTER to activate[/green]\n\n")
        
        # Display buttons
        button_line = "  "
        for i, label in enumerate(self.button_labels):
            if i == self.selected_button:
                button_line += f"[black on cyan] [ {label} ] [/black on cyan]  "
            else:
                button_line += f"[cyan][ {label} ][/cyan]  "
        
        content.append(button_line)
        content.append("\n")
        
        # Show button status
        if self.button_status:
            content.append(f"\n[bold yellow]Action:[/bold yellow] {self.button_status}\n")
        else:
            content.append("\n[dim]No action performed yet[/dim]\n")
        
        # Button descriptions
        content.append("\n[bold white]Button Descriptions:[/bold white]")
        content.append("[green]Start[/green]     - Initialize the system")
        content.append("[green]Stop[/green]      - Halt all processes")
        content.append("[green]Restart[/green]   - Reboot the system")
        content.append("[green]Configure[/green] - Open configuration menu")
        content.append("[green]Exit[/green]      - Close application")
        
        return Panel("\n".join(content), title="Buttons", style="green")
    
    def open_file_dialog(self):
        """Open file dialog to select JSON file"""
        try:
            # Create a hidden Tkinter root window
            root = Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            
            self.button_status = f'Selecting File'

            # Open file dialog
            file_path = filedialog.askopenfilename(
                title="Select a JSON file",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )

            root.update()
            root.destroy()
            
            if file_path:
                self.json_file_path = file_path
                self.json_error = None
                
                # Read and parse JSON file
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.json_data = json.load(f)
                except json.JSONDecodeError as e:
                    self.json_error = f"Invalid JSON: {e}"
                    self.json_data = None
                except Exception as e:
                    self.json_error = f"Error reading file: {e}"
                    self.json_data = None
        except Exception as e:
            self.json_error = f"Error opening dialog: {e}"

    def load_config_file(self):
        try:
            # Construct InfluxDB point Name
            self.point = (
                Point("ConfigName")
                .tag("device", "CMD")
                .tag("unit", "Text")
                .field("value_str", self.json_data.get("Name"))
            )

            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=self.point)

            # Construct InfluxDB point Gear Ratio
            self.gear_ratio = self.json_data.get("Gear Ratio")
            self.point = (
                Point("GearRatio")
                .tag("device", "CMD")
                .tag("unit", "Text")
                .field("value", float(self.gear_ratio))
            )

            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=self.point)

            if(self.json_data.get("Mode") != None):
                self.load_run_plan()
            

        except Exception as e:
            print("Error parsing packet:", e)
            self.button_status = (f"Error parsing packet:", e)

    def load_run_plan(self):

        if self.run_mode == "Ramp":
            self.ipc_conn.send("Stop")
        else:
            self.ipc_conn.send("Stop Hold RPM")
        self.stop_event.set()
        self.is_ramping = False   
        
        self.run_config = self.json_data
        self.run_mode = self.run_config.get("Mode")
                   
        time.sleep(0.1)
        if self.run_mode == "Ramp":
            self.start_rpm = int(self.run_config.get("Start"))
            self.end_rpm = int(self.run_config.get("End"))
            self.ramp_rate = float(self.run_config.get("Rate"))
            self.ipc_conn.send("Start RPM")
            self.ipc_conn.send(self.start_rpm/self.gear_ratio)
            self.ipc_conn.send("End RPM")
            self.ipc_conn.send(self.end_rpm/self.gear_ratio)
            self.ipc_conn.send("Rate")
            self.ipc_conn.send(self.ramp_rate/self.gear_ratio)
        elif self.run_mode == "Hold":
            self.start_rpm = int(self.run_config.get("RPM"))
            self.ipc_conn.send("Start RPM")
            self.ipc_conn.send(self.start_rpm/self.gear_ratio)

    def make_influx_config_token(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Inlfux DB Setup[/bold white]\n")
        content.append("[green]Enter InfluxDB Token:[/green]\n")
        
        # Input field display
        input_display = f"[cyan]> {self.input_value}_[/cyan]"
        content.append(f"  {input_display}\n")
        
        # Show submitted value
        if self.submitted_value is not None:
            self.influx_token = self.submitted_value
            self.submitted_value = None
            self.active_tab = -2
                
        else:
            content.append("\n[dim]No value submitted yet[/dim]")
        
        return Panel("\n".join(content), title="Input", style="green")

    def make_influx_config_org(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Inlfux DB Setup[/bold white]\n")
        content.append("[green]Enter InfluxDB Org:[/green]\n")
        
        # Input field display
        input_display = f"[cyan]> {self.input_value}_[/cyan]"
        content.append(f"  {input_display}\n")
        
        # Show submitted value
        if self.submitted_value is not None:
            self.influx_org = self.submitted_value
            self.submitted_value = None
            self.active_tab = -3
                
        else:
            content.append("\n[dim]No value submitted yet[/dim]")
        
        return Panel("\n".join(content), title="Input", style="green") 

    def make_influx_config_bucket(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Inlfux DB Setup[/bold white]\n")
        content.append("[green]Enter InfluxDB Bucket:[/green]\n")
        
        # Input field display
        input_display = f"[cyan]> {self.input_value}_[/cyan]"
        content.append(f"  {input_display}\n")
        
        # Show submitted value
        if self.submitted_value is not None:
            self.influx_bucket = self.submitted_value
            self.submitted_value = None
            self.set_influx_data()
            self.active_tab = 0
                
        else:
            content.append("\n[dim]No value submitted yet[/dim]")
        
        return Panel("\n".join(content), title="Input", style="green")         

    def make_input_tab(self):

        """Create input form view"""
        content = []
        
        content.append("[bold white]Number Input Form[/bold white]\n")
        content.append("[green]Enter the current motor RPM and press Enter:\n")
        
        # Input field display
        input_display = f"[cyan]> {self.input_value}_[/cyan]" if len(self.input_value) < 20 else f"[cyan]> {self.input_value}[/cyan]"
        content.append(f"  {input_display}\n")
        content.append(f"Calculated Gear Ratio: {self.gr_calc}\n")
        content.append(f"Loaded Gear Ratio: {self.gear_ratio}\n")
        
        return Panel("\n".join(content), title="Input", style="green")
    
    def make_valve_setup_tab(self):
        """Create input form view"""
        content = []
        
        content.append("[bold white]Valve Setup Form[/bold white]\n")

        for i, label in enumerate(self.valve_entries):
            content.append(self.entries[i])
            if i == self.selected_entry:
                input_display = f"[black on cyan] [ {self.valve_entries[i]} ] [/black on cyan]  "
                content.append(f"  {input_display}\n")
            else:
                input_display = f"[cyan][ {self.valve_entries[i]} ][/cyan]  "
                content.append(f"  {input_display}\n")

        '''
        content.append("[green]Enter valve controller pulses per rotation:\n")
        
        # Input field display pulses
        input_display = f"[cyan]> {self.valve_ppr}_[/cyan]" if len(self.valve_ppr) < 20 else f"[cyan]> {self.valve_ppr}[/cyan]"
        content.append(f"  {input_display}\n")

        content.append("[green]Enter planetary gearbox ratio (output/input):\n")
        
        # Input field display gear ratio
        input_display = f"[cyan]> {self.valve_gro}_[/cyan]" if len(self.valve_gro) < 20 else f"[cyan]> {self.valve_gro}[/cyan]"
        content.append(f"  {input_display}\n")
        '''
        
        return Panel("\n".join(content), title="Input", style="green")

        """Create disk information view"""
        content = []
        
        content.append("[bold white]Disk Usage:[/bold white]\n")
        
        # /dev/sda1
        content.append("[green]/dev/sda1 mounted on /[/green]")
        content.append(f"  {self.create_bar(456, 512, 40)}  [green]456GB / 512GB (89%)[/green]")
        
        content.append("\n[green]/dev/sdb1 mounted on /home[/green]")
        content.append(f"  {self.create_bar(789, 1024, 40)}  [green]789GB / 1024GB (77%)[/green]")
        
        return Panel("\n".join(content), title="Disk", style="green")
    
    def make_footer(self):
        """Create footer panel"""
        footer_text = "[green]F1:System F2:Processes F3:Network F4:Disk F5:Input F6:Buttons | Q:Quit[/green]"
        return Panel(footer_text, style="green")
    
    def make_layout(self):
        """Create the layout"""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="menu", size=3),
            Layout(name="content"),
            Layout(name="footer", size=3)
        )
        
        layout["header"].update(self.make_header())
        layout["menu"].update(self.make_menu())
        layout["footer"].update(self.make_footer())
        
        # Content based on active tab
        if self.active_tab == -1:
            layout["content"].update(self.make_influx_config_token())
        elif self.active_tab == -2:
            layout["content"].update(self.make_influx_config_org())
        elif self.active_tab == -3:
            layout["content"].update(self.make_influx_config_bucket())
        elif self.active_tab == -4:
            layout["content"].update(self.make_valve_setup_tab())
        elif self.active_tab == 0:
            layout["content"].update(self.make_valvepos_tab())
        elif self.active_tab == 1:
            layout["content"].update(self.make_run_tab())
        elif self.active_tab == 2:
            layout["content"].update(self.make_input_tab())
        elif self.active_tab == 3:
            layout["content"].update(self.make_buttons_tab())
        
        return layout
    
    def set_influx_data(self):

        data = {
        "Token": self.influx_token,
        "Org": self.influx_org,
        "Bucket": self.influx_bucket
        }

        # Write the data to a JSON file
        with open(self.influx_file_path, "w") as json_file:
            json.dump(data, json_file, indent=4)    

        #read data from JSON and start influxdb
        with open(self.influx_file_path, 'r', encoding='utf-8') as f:
                self.json_data = json.load(f)
                self.INFLUX_URL = "http://localhost:8086"
                self.TOKEN = self.json_data.get("Token")
                self.ORG = self.json_data.get("Org")
                self.BUCKET = self.json_data.get("Bucket")
                self.client = InfluxDBClient(url=self.INFLUX_URL, token=self.TOKEN, org=self.ORG)
                self.write_api = self.client.write_api(write_options=WriteOptions(batch_size=1))
                self.influx_json_read = True

    def send_target_rpm(self, rpm):
        try:
            metric = "targetRPM"
            device = "terminal"
            unit = "rpm"
            value = rpm
            point = (
                    Point(metric)
                    .tag("device", device)
                    .tag("unit", unit)
                    .field("value", float(value))
                    )
            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=point)
            print(f"Wrote: {metric}={value} {unit}")
        except Exception as e:
            print(f"Error writing {metric}: {e}")

    def send_valve_pos(self, valve_pos):
        try:
            metric = "ValvePos"
            device = "terminal"
            unit = "none"
            value = valve_pos
            point = (
                    Point(metric)
                    .tag("device", device)
                    .tag("unit", unit)
                    .field("value", float(value))
                    )
            self.write_api.write(bucket=self.BUCKET, org=self.ORG, record=point)
            print(f"Wrote: {metric}={value} {unit}")
            self.target_rpm = value

            self.ipc_conn.send("ValvePos")
            time.sleep(0.05)
            self.ipc_conn.send(value)
        except Exception as e:
            print(f"Error writing {metric}: {e}")

    def target_rpm_ramp(self, start, end, rate, loop = False):
        valve_pos = start
        prev_time = time.time()
        while (valve_pos < end and not self.stop_event.is_set()):
            ct = time.time()
            dt = ct - prev_time
            valve_pos = valve_pos + rate*dt
            if(valve_pos > end):
                valve_pos = end
            self.send_target_rpm(valve_pos)
            prev_time = ct
            time.sleep(0.1)
        self.is_ramping = False
        
    def run(self):
        """Main loop with Live display"""
        print("[green]Terminal Interface - Press Q to quit[/green]")
        print("[green]Use F1-F6 to switch tabs[/green]")
        print("\nStarting in 2 seconds...")
        time.sleep(2)
        #setup IPC to main python program
        self.ipc_address = ('localhost', 31205)
        try:
            self.ipc_conn = Client(self.ipc_address, authkey=b'key')
        except Exception as e:
            print(e)
            time.sleep(5)
        if self.ipc_conn == None:
            print("No Connection")
            time.sleep(5)
            self.ipc_conn = Client(self.ipc_address)
        self.ipc_conn.send('connecting')
        
        try:
            import sys
            
            def input_thread():
                """Background thread for keyboard input"""
                while self.running:
                    try:
                        if sys.platform == 'win32':
                            import msvcrt
                            if msvcrt.kbhit():
                                key = msvcrt.getch()
                                
                                # Check for function keys (F1-F5) on Windows
                                if key == b'\x00' or key == b'\xe0':  # Extended key prefix
                                    extended = msvcrt.getch()
                                    if extended == b';':  # F1
                                        self.active_tab = 0
                                    elif extended == b'<':  # F2
                                        self.active_tab = 1
                                    elif extended == b'=':  # F3
                                        self.active_tab = 2
                                    elif extended == b'>':  # F4
                                        self.active_tab = 3
                                    elif extended == b'K':  # Left arrow
                                        if self.active_tab == 3:
                                            self.selected_button = (self.selected_button - 1) % len(self.button_labels)
                                    elif extended == b'M':  # Right arrow
                                        if self.active_tab == 3:
                                            self.selected_button = (self.selected_button + 1) % len(self.button_labels)
                                    elif extended == b'H':  # Up arrow
                                        if self.active_tab == -4:
                                            self.selected_entry = (self.selected_entry - 1) % len(self.entries)
                                            self.input_value = self.valve_entries[self.selected_entry]
                                    elif extended == b'P':  # Down arrow
                                        if self.active_tab == -4:
                                            self.selected_entry = (self.selected_entry + 1) % len(self.entries)
                                            self.input_value = self.valve_entries[self.selected_entry]
                                # Handle special keys
                                elif key == b'\r':  # Enter
                                    if (self.active_tab == 4) and self.input_value:
                                        try:
                                            self.submitted_value = int(self.input_value)
                                            self.input_value = ""
                                        except ValueError:
                                            pass
                                    elif self.active_tab == 0 and self.input_value:
                                        self.submitted_value = int(self.input_value)
                                        self.input_value = ""
                                        self.send_valve_pos(self.submitted_value)

                                    elif self.active_tab == -4 and self.selected_entry == 2:
                                        data = {
                                            "PPR": self.valve_entries[0],
                                            "GRO": self.valve_entries[1]
                                                }

                                            # Write the data to a JSON file
                                        with open(self.system_config_file_path, "w") as json_file:
                                            json.dump(data, json_file, indent=4) 

                                        self.send_valve_params()
                                        self.input_value = ""
                                        self.active_tab = 5 

                                    elif self.active_tab == 3:  # Buttons tab
                                        self.button_status = f"Button '{self.button_labels[self.selected_button]}' pressed!"
                                        if(self.button_status == f"Button \'Load Configuration File\' pressed!"):
                                            self.button_status = f'Loading Config'
                                            self.open_file_dialog()
                                            self.button_status = f'Config file loaded successfully'
                                            self.load_config_file()
                                            # self.button_status = f'MASHALLAH3'
                                        elif (self.button_status == f"Button \'Setup InfluxDB\' pressed!"):
                                            self.active_tab = -1
                                        elif (self.button_status == f"Button \'Setup Valve\' pressed!"):
                                            self.active_tab = -4
                                        if(self.button_status == f"Button \'Load Run Plan\' pressed!"):
                                            self.button_status = f'Loading Config'
                                            self.open_file_dialog()
                                            self.button_status = f'Config file loaded successfully'
                                            self.load_run_plan()
                                            # self.button_status = f'MASHALLAH3'
                                    elif self.active_tab == 1: #run tab
                                        if self.run_mode == "Ramp":
                                            if(self.is_ramping):
                                                self.ipc_conn.send("Stop")
                                                self.stop_event.set()
                                                self.is_ramping = False
                                            else:
                                                self.ipc_conn.send("Start")
                                                self.is_ramping = True
                                                start_rpm = self.start_rpm
                                                end_rpm = self.end_rpm
                                                ramp_rate = self.ramp_rate
                                                self.stop_event.clear()
                                                self.ramp_t = threading.Thread(target=self.target_rpm_ramp, args=(start_rpm, end_rpm, ramp_rate, False), daemon=True)
                                                self.ramp_t.start()
                                        else:
                                            if(self.is_ramping):
                                                self.ipc_conn.send("Stop Hold RPM")
                                                self.stop_event.set()
                                                self.is_ramping = False
                                            else:
                                                self.send_target_rpm(self.start_rpm)
                                                self.ipc_conn.send("Start Hold RPM")
                                                self.is_ramping = True
                                                self.stop_event.clear()
                                    elif self.active_tab == 2: #gear sync tab
                                        self.submitted_value = self.input_value
                                        self.input_value = ""
                                        '''
                                        query_speed = f'from(bucket: "{self.BUCKET}") |> range(start: -10s) |> filter(fn: (r) => r._measurement == "rollerSpeed")'
                                        speed_result = self.query_api.query(query=query_speed, org=self.ORG)  
                                        self.gr_calc = 'trying to query speed'
                                        if speed_result:
                                            for table in speed_result:
                                                speed = (table.records.pop())['_value']
                                                #self.gr_calc = speed
                                                #turbine_speed = speed/self.gear_ratio
                                                rollerSpeed = speed
                                                '''
                                        rollerSpeed = self.get_last_speed()
                                        self.gr_calc = float(self.submitted_value)/rollerSpeed

                                    elif self.active_tab < 0:
                                        self.submitted_value = self.input_value
                                        self.input_value = ""
                                elif key == b'\x08':  # Backspace
                                    if self.active_tab == -4 and self.selected_entry != (len(self.entries) - 1):
                                        self.input_value = self.input_value[:-1]
                                        self.valve_entries[self.selected_entry] = self.input_value
                                    elif self.active_tab == 4 or (self.active_tab < 0 and self.active_tab >= -3) or self.active_tab == 0:
                                        self.input_value = self.input_value[:-1]
                                    elif self.active_tab == 2: #gear sync tab:
                                        self.input_value = self.input_value[:-1]
                                else:
                                    try:
                                        char = key.decode('utf-8')
                                        if self.active_tab > 0:
                                            char = key.decode('utf-8').lower()
                                        if char == 'q' and self.active_tab >= 0:
                                            self.running = False
                                        elif (self.active_tab == 4  or self.active_tab == 0) and char.isdigit():
                                            self.input_value += char
                                        elif self.active_tab == -4 and (char.isdecimal() or char == '.') and self.selected_entry != (len(self.entries) - 1):
                                            self.input_value += char
                                            self.valve_entries[self.selected_entry] = self.input_value
                                        elif self.active_tab < 0 and self.active_tab >= -3:
                                            self.input_value += char
                                        elif self.active_tab == 2 and (char.isdecimal() or char == '.'):
                                            self.input_value += char
                                        
                                    except:
                                        pass
                        else:
                            # # Unix-like systems
                            # import select
                            # import tty
                            # import termios
                            
                            # old_settings = termios.tcgetattr(sys.stdin)
                            # try:
                            #     tty.setcbreak(sys.stdin.fileno())
                            #     if select.select([sys.stdin], [], [], 0.1)[0]:
                            #         key = sys.stdin.read(1)
                                    
                            #         # Check for escape sequences (function keys)
                            #         if key == '\x1b':  # ESC character
                            #             # Read the next character
                            #             if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                 next_char = sys.stdin.read(1)
                            #                 if next_char == 'O':  # Function key sequence
                            #                     if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                         func_key = sys.stdin.read(1)
                            #                         if func_key == 'P':  # F1
                            #                             self.active_tab = 0
                            #                         elif func_key == 'Q':  # F2
                            #                             self.active_tab = 1
                            #                         elif func_key == 'R':  # F3
                            #                             self.active_tab = 2
                            #                         elif func_key == 'S':  # F4
                            #                             self.active_tab = 3
                            #                 elif next_char == '[':  # Alternative F-key sequence
                            #                     if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                         func_seq = sys.stdin.read(1)
                            #                         if func_seq == '1':
                            #                             if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                                 end = sys.stdin.read(1)
                            #                                 if end == '5':  # F5
                            #                                     if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                                         sys.stdin.read(1)  # Read the final ~
                            #                                     self.active_tab = 4
                            #                         elif func_seq == '1':
                            #                             if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                                 end = sys.stdin.read(1)
                            #                                 if end == '7':  # F6
                            #                                     if select.select([sys.stdin], [], [], 0.1)[0]:
                            #                                         sys.stdin.read(1)  # Read the final ~
                            #                                     self.active_tab = 5
                            #                         elif func_seq == 'D':  # Left arrow
                            #                             if self.active_tab == 5:
                            #                                 self.selected_button = (self.selected_button - 1) % len(self.button_labels)
                            #                         elif func_seq == 'C':  # Right arrow
                            #                             if self.active_tab == 5:
                            #                                 self.selected_button = (self.selected_button + 1) % len(self.button_labels)
                            #         elif key == '\r' or key == '\n':  # Enter
                            #             if self.active_tab == 4 and self.input_value:
                            #                 try:
                            #                     self.submitted_value = int(self.input_value)
                            #                     self.input_value = ""
                            #                 except ValueError:
                            #                     pass
                            #             elif self.active_tab == 5:  # Buttons tab
                            #                 self.button_status = f"Button '{self.button_labels[self.selected_button]}' pressed!"
                            #         elif key == '\x7f' or key == '\x08':  # Backspace
                            #             if self.active_tab == 4:
                            #                 self.input_value = self.input_value[:-1]
                            #         elif key.lower() == 'q':
                            #             self.running = False
                            #         elif self.active_tab == 4 and key.isdigit():
                            #             self.input_value += key
                            # finally:
                            #     termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                            print("not used")
                    except:
                        pass
                    time.sleep(0.1)
            
            # Start input thread
            input_t = threading.Thread(target=input_thread, daemon=True)
            input_t.start()
            
            # Main display loop
            with Live(self.make_layout(), refresh_per_second=10, screen=True) as live:
                if(not self.influx_json_read):
                    self.active_tab = -1 #fake tab for one-time setup of influxdb
                while self.running:
                    live.update(self.make_layout())
                    self.current_engine_speed = self.get_last_speed()*self.gear_ratio
        
        except KeyboardInterrupt:
            pass
        
        self.console.print("\n[green]Interface closed.[/green]")

if __name__ == "__main__":
    app = TerminalInterface()
    app.run()