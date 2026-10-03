#!/usr/bin/env python3
import sys, asyncio, json, os, time
action = None
BASE = os.path.dirname(__file__) or "."
KASA_CACHE = os.path.join(BASE, "kasa_cache.json")
TOKENS_FILE = os.path.join(BASE, "vue_tokens.json")

v = None
creds = None
pyem = None
_kasa_cred = None

def _get_vue_client():
    global v, creds, pyem
    if v is None:
        import pyemvue as pyem  # lazy: pyemvue drags in botocore (~1.9s)
        v = pyem.PyEmVue()
        with open(os.path.join(BASE, "keys.json")) as f:
            creds = json.load(f)["emporia_vue"]
    return v

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def fast_vue_auth(cached):
    import jwt, types
    from pyemvue.auth import Auth, USER_POOL_URL
    from pyemvue.pyemvue import API_ROOT
    exp = jwt.decode(cached["access_token"], options={"verify_signature": False}).get("exp") or 0
    if exp < time.time() + 300:
        return False
    a = Auth.__new__(Auth)
    a.host = API_ROOT
    a.connect_timeout = v.connect_timeout
    a.read_timeout = v.read_timeout
    a.token_updater = None
    a.max_retry_attempts = 5
    a.initial_retry_delay = 0.5
    a.max_retry_delay = 30.0
    a.pool_wellknown_jwks = None
    a._password = None
    a.cognito = types.SimpleNamespace(user_pool_url=USER_POOL_URL)
    a.tokens = {k: cached[k] for k in ("access_token", "id_token", "refresh_token")}
    v.auth = a
    return True

def vue_devices():
    """Return Emporia outlet devices, fast if cached tokens are still valid."""
    _get_vue_client()
    try:
        cached = load_json(TOKENS_FILE) or {}
        if all(k in cached for k in ("id_token", "access_token", "refresh_token")) and fast_vue_auth(cached):
            return [d for d in v.get_devices() if d.outlet is not None]
    except Exception:
        pass
    if not v.login(username=creds["username"], password=creds["password"], token_storage_file=TOKENS_FILE):
        raise RuntimeError("Emporia Vue login failed")
    return [d for d in v.get_devices() if d.outlet is not None]

def _broadcasts():
    """Per-interface broadcast addresses. On hosts whose default route is not
    the LAN (e.g. cellular), the default 255.255.255.255 leaves the wrong
    interface, so we target each real interface broadcast explicitly."""
    import socket, struct, fcntl
    out = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _idx, name in socket.if_nameindex():
            try:
                b = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8919, struct.pack("256s", name.encode()))[20:24])
            except Exception:
                continue
            if b not in ("0.0.0.0", "127.0.0.1"):
                out.add(b)
    finally:
        s.close()
    return sorted(out) or ["255.255.255.255"]

def discover_kasa(force_rescan=False):
    async def run():
        from kasa import Discover, Device, DeviceConfig, Credentials  # lazy: kasa import is slow
        # Broadcast enumerate the plug IPs. A forced rescan (r) uses a longer
        # window to sweep the subnet more thoroughly.
        timeout = 5 if force_rescan else 2
        results = await asyncio.gather(*(
            Discover.discover(target=t, discovery_timeout=timeout, discovery_packets=4)
            for t in _broadcasts()
        ), return_exceptions=True)
        found = {}
        for raw in results:
            if isinstance(raw, Exception):
                continue
            for d in raw.values():
                dt = str(getattr(d, "device_type", ""))
                # this is a plug script; skip cameras/hubs/etc
                if "Plug" not in dt and "Bulb" not in dt:
                    continue
                found.setdefault(d.host, d)
        # The broadcast is unreliable for name/on-state on KLAP (EP10) plugs, so
        # auth-connect each one in parallel for a guaranteed alias, live state,
        # and a controllable object. Creds are only needed here, not at runtime.
        global _kasa_cred
        kasa_creds = (load_json(os.path.join(BASE, "keys.json")) or {}).get("tplink_kasa") or {}
        if kasa_creds:
            _kasa_cred = Credentials(username=kasa_creds["username"], password=kasa_creds["password"])
            cred = _kasa_cred
            async def conn(host):
                try:
                    dev = await Device.connect(config=DeviceConfig(host=host, credentials=cred, timeout=8))
                    await dev.update()
                    return dev
                except Exception:
                    return found[host]
            found = dict(zip(found, await asyncio.gather(*(conn(h) for h in found))))
        devices = sorted(found.values(), key=lambda d: (0 if "Plug" in str(d.device_type) else 1, [int(p) for p in d.host.split(".")]))
        with open(KASA_CACHE, "w") as f:
            json.dump([{"host": d.host, "alias": d.alias} for d in devices], f, indent=2)
        return devices
    return asyncio.run(run())

def parse_namespace(argv):
    if "-n" in argv or "--namespace" in argv:
        fl = "--namespace" if "--namespace" in argv else "-n"
        i = argv.index(fl) + 1
        val = argv[i]
        del argv[i:i + 2]
        return [val.lower()]
    return ["all"]

argv = list(sys.argv[1:])
namespace = parse_namespace(argv)

def collect_devices(rescan=False):
    async def run():
        want_kasa = "kasa" in namespace or namespace == ["all"]
        want_vue = "vue" in namespace or namespace == ["all"]
        kasa_task = asyncio.to_thread(discover_kasa, rescan) if want_kasa else None
        vue_task = asyncio.to_thread(vue_devices) if want_vue else None
        try:
            vue_devs = [("vue", d) for d in await vue_task] if vue_task else []
        except Exception as e:
            print(f"  warning: emporia vue unavailable ({e})", file=sys.stderr)
            vue_devs = []
        kasa_devs = [("kasa", d) for d in await kasa_task] if kasa_task else []
        return kasa_devs + vue_devs
    return asyncio.run(run())

active = collect_devices()

def toggle(which, action):
    kind, obj = active[which]
    if kind == 'vue':
        state = not obj.outlet.outlet_on if action is None else (action == 'on')
        v.update_outlet(obj.outlet, state)
    else:
        from kasa import Device, DeviceConfig  # lazy
        async def _toggle():
            if _kasa_cred is None:
                dev, own = obj, False
            else:
                # Discovery objects are bound to a closed event loop, so reconnect
                # fresh in this loop (by host) to issue the command reliably.
                dev = await Device.connect(config=DeviceConfig(host=obj.host, credentials=_kasa_cred, timeout=8))
                own = True
            try:
                if action is None:
                    await dev.update()
                    await (dev.turn_off() if dev.is_on else dev.turn_on())
                else:
                    await (dev.turn_on() if action == 'on' else dev.turn_off())
            finally:
                if own:
                    await dev.disconnect()
        asyncio.run(_toggle())

def close_kasa():
    async def run():
        for kind, obj in active:
            if kind == 'kasa':
                try:
                    await obj.disconnect()
                except Exception:
                    pass
    kasa_present = any(k == 'kasa' for k, _ in active)
    if kasa_present:
        asyncio.run(run())

def rescanner():
    global active
    print("Rescanning network for Kasa devices...")
    fresh = discover_kasa(force_rescan=True)
    vue_devs = [(k, o) for k, o in active if k == 'vue']
    alldevs = vue_devs + [("kasa", p) for p in fresh]
    active = [d for d in alldevs if namespace == ["all"] or d[0] in namespace]
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
        print(f"action set to {i}")

def show_dev():
    labels = {'vue': 'emporia', 'kasa': 'kasa'}
    last = None
    for i, (kind, obj) in enumerate(active):
        if kind == 'kasa':
            section = "smart bulbs" if "Bulb" in str(getattr(obj, "device_type", "")) else "smart plugs"
        else:
            section = labels[kind]
        if section != last:
            if last is not None:
                print()
            if kind == 'kasa':
                print(f"  {section}")
            else:
                print(f"  {section}:")
            last = section
        try:
            on = bool(obj.outlet.outlet_on) if kind == 'vue' else bool(obj.is_on)
        except Exception:
            on = None  # not yet refreshed (fresh discovery); show neutral marker
        name = obj.device_name if kind == 'vue' else (obj.alias or f"kasa@{obj.host}")
        mark = '\u25A3' if on else ('\u25A1' if on is None else '\u25A2')
        print("    {} {} {}".format(i, mark, name))

show_dev()

if len(argv) > 1:
    for i in argv:
        process(i)
else:
    while True:
        try:
            process(input("> "))
        except (EOFError, KeyboardInterrupt):
            print("\nAlright! Bye bye")
            break
close_kasa()
