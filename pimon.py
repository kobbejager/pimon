# -*- coding: utf-8 -*-
# Python script (runs on 2 and 3) to check cpu load, cpu temperature and free space etc.
# on a Raspberry Pi or Ubuntu computer and publish the data to a MQTT server.
# RUN pip install paho-mqtt
# RUN sudo apt-get install python-pip

from __future__ import division
import subprocess
import time
import sys
import socket
import threading
import signal
import json
from random import randrange
import argparse
import os
from datetime import datetime, timezone

import yaml
import paho.mqtt.client as mqtt


parser = argparse.ArgumentParser(description="Pimon: Raspberry Pi MQTT monitor")
parser.add_argument(
    "-c",
    "--config",
    default="config.yaml",
    help="Configuration yaml file, defaults to `config.yaml`",
    dest="config_file",
)
args = parser.parse_args()


def load_config(config_file):
    """Load the configuration from config yaml file and use it to override the defaults."""
    with open(config_file, "r") as f:
        config_override = yaml.safe_load(f)

    default_config = {
        "mqtt": {
            "broker": "127.0.0.1",
            "port": 1883,
            "username": None,
            "password": None,
            "topic_prefix": "pimon/$HOSTNAME",
            "retain": False,
            "qos": 1
        },
        "bulk": {
            "group_messages": False,
            "format_as_json" : False
        },
        "loop_time": 30,
        "sleep_time": 0.5,
        "discovery_messages": True,
        "messages": {
            "cpu_load": True,
            "cpu_temp": True,
            "diskusage": True,
            "other_diskusage": [],
            "smart_temp": [],
            "voltage": True,
            "sys_clock_speed": True,
            "swap": True,
            "memory": True,
            "uptime": True,
            "wifi_signal": False,
            "wifi_signal_dbm": False,
            "timestamp": False
        }
    }

    config = {**default_config, **config_override}
    return config


def check_wifi_signal():
    try:
        full_cmd = "/sbin/iwconfig wlan0 | grep -i quality"
        wifi_signal = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        wifi_signal = wifi_signal.decode("utf-8").strip().split(' ')[1].split('=')[1].split('/')[0]
        wifi_signal_calc = round((int(wifi_signal) / 70)* 100)
    except Exception:
        wifi_signal_calc = 'NA'
    return wifi_signal_calc


def check_wifi_signal_dbm():
    try:
        full_cmd = "/sbin/iwconfig wlan0 | grep -i quality"
        wifi_signal = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        wifi_signal = wifi_signal.decode("utf-8").strip().split(' ')[4].split('=')[1]
    except Exception:
        wifi_signal = 'NA'
    return wifi_signal


def check_diskusage(path):
    st = os.statvfs(path)
    free_space = st.f_bavail * st.f_frsize
    total_space = st.f_blocks * st.f_frsize
    diskusage = int(100 - ((free_space / total_space) * 100))
    return diskusage


def check_smart_temp(dev):
    full_cmd = "smartctl -A " + dev + " | grep -i temperature | awk '{print $10}'"
    try:
        p = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        smart_temp = int(p.decode("utf-8").replace('\n', ' ').replace('\r', ''))
    except Exception:
        smart_temp = 0
    return smart_temp


def check_cpu_load():
    # bash command to get cpu load from uptime command
    p = subprocess.Popen("uptime", shell=True, stdout=subprocess.PIPE).communicate()[0]
    cores = subprocess.Popen("nproc", shell=True, stdout=subprocess.PIPE).communicate()[0]
    cpu_load = str(p).split("average:")[1].split(", ")[0].replace(' ', '').replace(',', '.')
    cpu_load = float(cpu_load) / int(cores) * 100
    cpu_load = round(float(cpu_load), 1)
    return cpu_load


def check_voltage():
    try:
        full_cmd = "vcgencmd measure_volts | cut -f2 -d= | sed 's/000//'"
        voltage = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0]
        voltage = voltage.strip()[:-1]
    except Exception:
        voltage = 0
    return voltage


def check_swap():
    full_cmd = "free -t |grep -i swap | awk 'NR == 1 {print $3/$2*100}'"
    swap = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
    swap = round(float(swap.decode("utf-8").replace(",", ".")), 1)
    return swap


def check_memory():
    full_cmd = "free -t | awk 'NR == 2 {print $3/$2*100}'"
    memory = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
    memory = round(float(memory.decode("utf-8").replace(",", ".")))
    return memory


def check_cpu_temp():
    full_cmd = "cat /sys/class/thermal/thermal_zone*/temp 2> /dev/null | sed 's/\(.\)..$//' | tail -n 1"
    try:
        p = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]
        cpu_temp = int(p.decode("utf-8").replace('\n', ' ').replace('\r', ''))
    except Exception:
        cpu_temp = 0
    return cpu_temp


def check_sys_clock_speed():
    full_cmd = "awk '{printf (\"%0.0f\",$1/1000); }' </sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    return int(subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0])


def check_uptime():
    full_cmd = "awk '{print int($1/3600/24)}' /proc/uptime"
    return int(subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0])


def check_model_name():
   full_cmd = "cat /sys/firmware/devicetree/base/model"
   model_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
   if model_name == '':
        full_cmd = "cat /proc/cpuinfo  | grep 'name'| uniq"
        model_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
        model_name = model_name.split(':')[1]
   return model_name


def get_timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat('T', 'seconds')


def get_os():
    full_cmd = 'cat /etc/os-release | grep -i pretty_name'
    pretty_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
    pretty_name = pretty_name.split('=')[1].replace('"', '')
    return(pretty_name)


def get_manufacturer():
    if 'Raspberry' not in check_model_name():
        full_cmd = "cat /proc/cpuinfo  | grep 'vendor'| uniq"
        pretty_name = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()[0].decode("utf-8")
        pretty_name = pretty_name.split(':')[1]
    else:
        pretty_name = 'Raspberry Pi'
    return(pretty_name)


def config_json(what_config):
    model_name = check_model_name()
    manufacturer = get_manufacturer()
    os = get_os()
    data = {
        "state_topic": "",
        "icon": "",
        "name": "",
        "unique_id": "",
        "unit_of_measurement": "",
        "device": {
            "identifiers": [hostname],
            "manufacturer": manufacturer,
            "model": model_name,
            "name": hostname,
            "sw_version": os
        }
    }

    data["state_topic"] = f"{base_topic}/{what_config}"
    data["unique_id"] = hostname + "_" + what_config
    if what_config == "cpuload":
        data["icon"] = "mdi:speedometer"
        data["name"] = hostname + " CPU Usage"
        data["unit_of_measurement"] = "%"
    elif what_config == "cputemp":
        data["icon"] = "hass:thermometer"
        data["name"] = hostname + " CPU Temperature"
        data["unit_of_measurement"] = "°C"
    elif what_config == "diskusage":
        data["icon"] = "mdi:harddisk"
        data["name"] = hostname + " Disk Usage"
        data["unit_of_measurement"] = "%"
    elif what_config == "voltage":
        data["icon"] = "mdi:flash"
        data["name"] = hostname + " CPU Voltage"
        data["unit_of_measurement"] = "V"
    elif what_config == "swap":
        data["icon"] = "mdi:harddisk"
        data["name"] = hostname + " Disk Swap"
        data["unit_of_measurement"] = "%"
    elif what_config == "memory":
        data["icon"] = "mdi:memory"
        data["name"] = hostname + " Memory Usage"
        data["unit_of_measurement"] = "%"
    elif what_config == "sys_clock_speed":
        data["icon"] = "mdi:speedometer"
        data["name"] = hostname + " CPU Clock Speed"
        data["unit_of_measurement"] = "MHz"
    elif what_config == "uptime_days":
        data["icon"] = "mdi:calendar"
        data["name"] = hostname + " Uptime"
        data["unit_of_measurement"] = "days"
    elif what_config == "wifi_signal":
        data["icon"] = "mdi:wifi"
        data["name"] = hostname + " Wifi Signal"
        data["unit_of_measurement"] = "%"
    elif what_config == "wifi_signal_dbm":
        data["icon"] = "mdi:wifi"
        data["name"] = hostname + " Wifi Signal"
        data["unit_of_measurement"] = "dBm"
    elif what_config == "timestamp":
        data["icon"] = "mdi:calendar"
        data["name"] = hostname + " Timestamp"
    else:
        return ""
    # Return our built discovery config
    return json.dumps(data)


def mqtt_on_connect(client, userdata, flags, rc):
    """Renew subscriptions and set Last Will message when connect to broker."""
    
    # Set up Last Will, and then set services' status to 'online'
    client.will_set(
        base_topic,
        payload="offline",
        qos=config["mqtt"]["qos"],
        retain=True,
    )
    client.publish(
        base_topic,
        payload="online",
        qos=config["mqtt"]["qos"],
        retain=True,
    )

    # Home Assistant MQTT autoconfig
    if config["discovery_messages"] and not config["bulk"]["group_messages"]:
        print("Publishing Home Assistant MQTT autoconfig")
        for item, show in config["messages"].items():
            if show:
                client.publish(
                    f"homeassistant/sensor/pimon/{hostname}_{item}/config",
                    config_json(item),
                    qos=config["mqtt"]["qos"],
                    retain=True,
                )
                time.sleep(config["sleep_time"])


def on_exit(signum, frame):
    """
    Update MQTT services' status to `offline` and stop the timer thread.
    Called when program exit is received.
    """
    print("Exiting...")
    client.publish(
        base_topic,
        payload="offline",
        qos=config["mqtt"]["qos"],
        retain=True,
    )
    timer_thread.cancel()
    timer_thread.join()
    sys.exit(0)


def publish_individual(data):
    # publish monitored values to MQTT
    for item, value in data.items():
        client.publish(
            f"{base_topic}/{item}", 
            value, 
            qos=config["mqtt"]["qos"],
            retain=config["mqtt"]["retain"])
        time.sleep(config["sleep_time"])


def publish_bulk(data):
    # publish monitored values to MQTT
    if config["bulk"]["format_as_json"]:
        client.publish(
            f"{base_topic}/status", 
            json.dumps(data), 
            qos=config["mqtt"]["qos"],
            retain=config["mqtt"]["retain"])
    else:
        # compose the CSV message containing the measured values
        values = list(data.values())
        values = [str(v) for v in values]
        values = ', '.join(values)
        client.publish(
            f"{base_topic}/status", 
            values, 
            qos=config["mqtt"]["qos"],
            retain=config["mqtt"]["retain"])

def publish():
    global timer_thread
    timer_thread = threading.Timer(config["loop_time"], publish)
    timer_thread.start()

    try:
        # collect the monitored values
        data = {}
        if config["messages"]["cpu_load"]:
            data["cpu_load"] = check_cpu_load()
        if config["messages"]["cpu_temp"]:
            data["cpu_temp"] = check_cpu_temp()
        if config["messages"]["diskusage"]:
            data["diskusage"] = check_diskusage('/')
        if len(config["messages"]["other_diskusage"]) > 0:
            for item in config["messages"]["other_diskusage"]:
                data[item] = check_diskusage(item)
        if len(config["messages"]["smart_temp"]) > 0:
            for item in config["messages"]["smart_temp"]:
                data[item] = check_smart_temp(item)
        if config["messages"]["voltage"]:
            data["voltage"] = check_voltage()
        if config["messages"]["sys_clock_speed"]:
            data["sys_clock_speed"] = check_sys_clock_speed()
        if config["messages"]["swap"]:
            data["swap"] = check_swap()
        if config["messages"]["memory"]:
            data["memory"] = check_memory()
        if config["messages"]["uptime"]:
            data["uptime_days"] = check_uptime()
        if config["messages"]["wifi_signal"]:
            data["wifi_signal"] = check_wifi_signal()
        if config["messages"]["wifi_signal_dbm"]:
            data["wifi_signal_dbm"] = check_wifi_signal_dbm()
        if config["messages"]["timestamp"]:
            data["timestamp"] = get_timestamp()

        # Publish messages to MQTT
        if config["bulk"]["group_messages"]:
            publish_bulk(data)
        else:
            publish_individual(data)
            
    except KeyError:
        print("Could not read data, skipping")


config = load_config(args.config_file)

# get device host name - used in mqtt topic
hostname = socket.gethostname()
base_topic = config["mqtt"]["topic_prefix"]
base_topic = base_topic.replace("$HOSTNAME", hostname)


if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = mqtt_on_connect
    client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
    client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)
    print("Pimon connected to MQTT broker")

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    publish()
    client.loop_forever()
