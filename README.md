# smartie

Personal project to control Emporia Vue outlets and Kasa smart plugs from a single CLI.

## Usage

```bash
python3 plug.py          # interactive mode
python3 plug.py 0 on     # turn device 0 on
python3 plug.py 1 off    # turn device 1 off
python3 plug.py 0 1 off  # turn device 0 on, device 1 off
```

In interactive mode, type a number to toggle, `on`/`off` to set action for subsequent numbers, `p` or `?` to list devices.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `keys.json` with your Emporia Vue credentials (it's in `.gitignore`):
```json
{
  "emporia_vue": {
    "username": "you@email.com",
    "password": "your-password"
  }
}
```

## Disclaimer

Very DIY. Works for my setup. YMMV. Fork and hack as needed.
