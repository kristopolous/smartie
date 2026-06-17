#!/usr/bin/env python3
import pyemvue as pyem, sys, time, asyncio, json
from kasa import Discover

start = time.time()
action = None

with open("keys.json") as f:
    creds = json.load(f)["emporia_vue"]

v = pyem.PyEmVue()
v.login(username=creds["username"], password=creds["password"])

def discover_kasa():
    try:
        raw = asyncio.run(Discover.discover())
        return list(raw.values())
    except:
        return []

# Build unified device list: ('vue', VueDevice) or ('kasa', Device)
devices = []
for d in v.get_devices():
    if d.outlet is not None:
        devices.append(('vue', d))
for p in discover_kasa():
    devices.append(('kasa', p))

def toggle(which, action):
    kind, obj = devices[which]
    if kind == 'vue':
        state = not obj.outlet.outlet_on if action is None else (action == 'on')
        v.update_outlet(obj.outlet, state)
    else:
        async def _toggle():
            if action is None:
                await obj.update()
                await (obj.turn_off() if obj.is_on else obj.turn_on())
            else:
                await (obj.turn_on() if action == 'on' else obj.turn_off())
        asyncio.run(_toggle())

def process(i):
    global action
    if i.isnumeric():
        toggle(int(i), action)
    elif i in ('p', '?'):
        show_dev()
    else:
        action = i

def show_dev():
    print()
    for i, (kind, obj) in enumerate(devices):
        if kind == 'vue':
            on = obj.outlet.outlet_on
            name = obj.device_name
        else:
            on = obj.is_on
            name = obj.alias
        print(" {} {} {}".format(i, '\u25A3' if on else '\u25A2', name))
    print()

show_dev()

if len(sys.argv) > 1:
    for i in sys.argv[1:]:
        process(i)

if len(sys.argv) == 1:
    while True:
        try:
            process(input("> "))
        except:
            print("Alright! Bye bye")
            sys.exit(0)
