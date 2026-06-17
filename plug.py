#!/usr/bin/env python3
import pyemvue as pyem, sys, asyncio, json, os
from kasa import Discover, Device
action = None
KASA_CACHE = os.path.join(os.path.dirname(__file__) or ".", "kasa_cache.json")

with open(os.path.join(os.path.dirname(__file__) or ".", "keys.json")) as f:
    creds = json.load(f)["emporia_vue"]

v = pyem.PyEmVue()
v.login(username=creds["username"], password=creds["password"])

def load_cache():
    try:
        with open(KASA_CACHE) as f:
            return json.load(f)
    except:
        return None

def save_cache(devices):
    data = [{"host": d.host, "alias": d.alias} for d in devices]
    with open(KASA_CACHE, "w") as f:
        json.dump(data, f, indent=2)

def discover_kasa(force_rescan=False):
    if not force_rescan:
        cached = load_cache()
        if cached:
            async def connect_all():
                result = []
                for entry in cached:
                    try:
                        d = await Device.connect(host=entry["host"])
                        await d.update()
                        result.append(d)
                    except:
                        continue
                return result
            devices = asyncio.run(connect_all())
            if devices:
                return devices
    try:
        raw = asyncio.run(Discover.discover())
        devices = list(raw.values())
        save_cache(devices)
        return devices
    except:
        return []

rescan = "--rescan" in sys.argv
if rescan:
    sys.argv.remove("--rescan")

devices = []
for d in v.get_devices():
    if d.outlet is not None:
        devices.append(('vue', d))
for p in discover_kasa(force_rescan=rescan):
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

def rescanner():
    global devices
    print("Rescanning network for Kasa devices...")
    fresh = discover_kasa(force_rescan=True)
    devices = [(k, o) for k, o in devices if k == 'vue']
    for p in fresh:
        devices.append(('kasa', p))
    show_dev()

def process(i):
    global action
    if i.isnumeric():
        toggle(int(i), action)
    elif i in ('p', '?'):
        show_dev()
    elif i == 'r':
        rescanner()
    else:
        action = i

def show_dev():
    print()
    labels = {'vue': 'emporia', 'kasa': 'kasa'}
    last = None
    for i, (kind, obj) in enumerate(devices):
        if kind != last:
            print(f"  {labels[kind]}:")
            last = kind
        if kind == 'vue':
            on = obj.outlet.outlet_on
            name = obj.device_name
        else:
            on = obj.is_on
            name = obj.alias
        print("    {} {} {}".format(i, '\u25A3' if on else '\u25A2', name))
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
            print("\nAlright! Bye bye")
            sys.exit(0)
