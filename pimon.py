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
import yaml
import os

import paho.mqtt.client as mqtt

# get device host name - used in mqtt topic
hostname = socket.gethostname()

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
            "topic_prefix": "rpi-MQTT-monitor"
        },
        "group_messages": False,
        "publish_period": 30,
        "sleep_time": 0.5,
        "discovery_messages": True,
        "delay": {
            "random_delay": True,
            "fixed_delay": 1
        },
        "messages": {
            "cpu_load": True,
            "cpu_temp": True,
            "used_space": True,
            "voltage": True,
            "sys_clock_speed": True,
            "swap": True,
            "memory": True,
            "uptime": True,
            "wifi_signal": False,
            "wifi_signal_dbm": False,
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


def check_used_space(path):
    st = os.statvfs(path)
    free_space = st.f_bavail * st.f_frsize
    total_space = st.f_blocks * st.f_frsize
    used_space = int(100 - ((free_space / total_space) * 100))
    return used_space


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
        cpu_temp = p.decode("utf-8").replace('\n', ' ').replace('\r', '')
    except Exception:
        cpu_temp = 0
    return cpu_temp


def check_sys_clock_speed():
    full_cmd = "awk '{printf (\"%0.0f\",$1/1000); }' </sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    return subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE).communicate()[0]


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

    data["state_topic"] = config["mqtt"]["topic_prefix"] + "/" + hostname + "/" + what_config
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
    else:
        return ""
    # Return our built discovery config
    return json.dumps(data)


def mqtt_on_connect(client):
    """Renew subscriptions and set Last Will message when connect to broker."""
    
    # Set up Last Will, and then set services' status to 'online'
    client.will_set(
        f'{config["mqtt"]["topic_prefix"]}/{hostname}',
        payload="offline",
        qos=1,
        retain=True,
    )
    client.publish(
        f'{config["mqtt"]["topic_prefix"]}/{hostname}',
        payload="online",
        qos=1,
        retain=True,
    )

    # Home Assistant MQTT autoconfig
    if config["discovery_messages"] and not config["group_messages"]:
        print("Publishing Home Assistant MQTT autoconfig")
        if config["messages"]["cpu_load"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_cpuload/config",
                config_json('cpuload'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["cpu_temp"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_cputemp/config",
                config_json('cputemp'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["used_space"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_diskusage/config",
                config_json('diskusage'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["voltage"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_voltage/config",
                config_json('voltage'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["swap"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_swap/config",
                config_json('swap'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["memory"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_memory/config",
                config_json('memory'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["sys_clock_speed"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_sys_clock_speed/config",
                config_json('sys_clock_speed'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["uptime"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_uptime_days/config",
                config_json('uptime_days'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["wifi_signal"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_wifi_signal/config",
                config_json('wifi_signal'),
                qos=0,
                retain=True,
            )
            time.sleep(config["sleep_time"])
        if config["messages"]["wifi_signal_dbm"]:
            client.publish(
                "homeassistant/sensor/" + config["mqtt"]["topic_prefix"] + "/" + hostname + "_wifi_signal_dbm/config",
                config_json('wifi_signal_dbm'),
                qos=0,
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
        f'{config["mqtt"]["topic_prefix"]}/{hostname}',
        payload="offline",
        qos=1,
        retain=True,
    )
    timer_thread.cancel()
    timer_thread.join()
    sys.exit(0)


def publish_to_mqtt(cpu_load=0, cpu_temp=0, used_space=0, voltage=0, sys_clock_speed=0, swap=0, memory=0,
                    uptime_days=0, wifi_signal=0, wifi_signal_dbm=0):
    # publish monitored values to MQTT
    if config["messages"]["cpu_load"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/cpuload", cpu_load, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["cpu_temp"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/cputemp", cpu_temp, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["used_space"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/diskusage", used_space, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["voltage"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/voltage", voltage, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["swap"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/swap", swap, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["memory"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/memory", memory, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["sys_clock_speed"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/sys_clock_speed", sys_clock_speed, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["uptime"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/uptime_days", uptime_days, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["wifi_signal"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/wifi_signal", wifi_signal, qos=1)
        time.sleep(config["sleep_time"])
    if config["messages"]["wifi_signal_dbm"]:
        client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname + "/wifi_signal_dbm", wifi_signal_dbm, qos=1)
        time.sleep(config["sleep_time"])



def bulk_publish_to_mqtt(cpu_load=0, cpu_temp=0, used_space=0, voltage=0, sys_clock_speed=0, swap=0, memory=0,
                         uptime_days=0, wifi_signal=0, wifi_signal_dbm=0):
    # compose the CSV message containing the measured values

    values = cpu_load, float(cpu_temp), used_space, float(voltage), int(sys_clock_speed), swap, memory, uptime_days, wifi_signal, wifi_signal_dbm
    values = str(values)[1:-1]

    # publish monitored values to MQTT
    client.publish(config["mqtt"]["topic_prefix"] + "/" + hostname, values, qos=1)


config = load_config(args.config_file)

# if __name__ == '__main__':
#     # set all monitored values to False in case they are turned off in the config
#     cpu_load = cpu_temp = used_space = voltage = sys_clock_speed = swap = memory = uptime_days = wifi_signal = wifi_signal_dbm =  False

#     # delay the execution of the script
#     if config["delay"]["random_delay"]:
#         delay = randrange(1)
#     else:
#         delay = config["delay"]["fixed_delay"]
#     time.sleep(delay)

#     # collect the monitored values
#     if config["messages"]["cpu_load"]:
#         cpu_load = check_cpu_load()
#     if config["messages"]["cpu_temp"]:
#         cpu_temp = check_cpu_temp()
#     if config["messages"]["used_space"]:
#         used_space = check_used_space('/')
#     if config["messages"]["voltage"]:
#         voltage = check_voltage()
#     if config["messages"]["sys_clock_speed"]:
#         sys_clock_speed = check_sys_clock_speed()
#     if config["messages"]["swap"]:
#         swap = check_swap()
#     if config["messages"]["memory"]:
#         memory = check_memory()
#     if config["messages"]["uptime"]:
#         uptime_days = check_uptime()
#     if config["messages"]["wifi_signal"]:
#         wifi_signal = check_wifi_signal()
#     if config["messages"]["wifi_signal_dbm"]:
#         wifi_signal_dbm = check_wifi_signal_dbm()

#     # Publish messages to MQTT
#     if config["group_messages"]:
#         bulk_publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, wifi_signal, wifi_signal_dbm)
#     else:
#         publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, wifi_signal, wifi_signal_dbm)


def publish():
    global timer_thread
    timer_thread = threading.Timer(config["publish_period"], publish)
    timer_thread.start()

    try:

        # if "publish_online_status" in config and config["publish_online_status"]:
        #     client.publish(
        #         f"{SERVICE_NAME}/{config['hostname']}/service",
        #         payload="online",
        #         qos=1,
        #         retain=True,
        #     )

        # set all monitored values to False in case they are turned off in the config
        cpu_load = cpu_temp = used_space = voltage = sys_clock_speed = swap = memory = uptime_days = wifi_signal = wifi_signal_dbm =  False

        # delay the execution of the script
        if config["delay"]["random_delay"]:
            delay = randrange(1)
        else:
            delay = config["delay"]["fixed_delay"]
        time.sleep(delay)

        # collect the monitored values
        if config["messages"]["cpu_load"]:
            cpu_load = check_cpu_load()
        if config["messages"]["cpu_temp"]:
            cpu_temp = check_cpu_temp()
        if config["messages"]["used_space"]:
            used_space = check_used_space('/')
        if config["messages"]["voltage"]:
            voltage = check_voltage()
        if config["messages"]["sys_clock_speed"]:
            sys_clock_speed = check_sys_clock_speed()
        if config["messages"]["swap"]:
            swap = check_swap()
        if config["messages"]["memory"]:
            memory = check_memory()
        if config["messages"]["uptime"]:
            uptime_days = check_uptime()
        if config["messages"]["wifi_signal"]:
            wifi_signal = check_wifi_signal()
        if config["messages"]["wifi_signal_dbm"]:
            wifi_signal_dbm = check_wifi_signal_dbm()

        # Publish messages to MQTT
        if config["group_messages"]:
            bulk_publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, wifi_signal, wifi_signal_dbm)
        else:
            publish_to_mqtt(cpu_load, cpu_temp, used_space, voltage, sys_clock_speed, swap, memory, uptime_days, wifi_signal, wifi_signal_dbm)
            
    except KeyError:
        print("Could not read data, skipping")


if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = mqtt_on_connect
    client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
    client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)
    print("Pimon connected to MQTT broker")

    # signal.signal(signal.SIGINT, on_exit)
    # signal.signal(signal.SIGTERM, on_exit)

    publish()
    client.loop_forever()
