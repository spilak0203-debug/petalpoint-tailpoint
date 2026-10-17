"""Tiny Photocraft control client for the live app (`photocraft --control <port>`).

Usage:
  python pc.py [--port N] [--token-file PATH] METHOD [JSON_PARAMS]
  python pc.py [--port N] [--token-file PATH] -f steps.json   # [[method, params], ...] on one connection
Prints each reply (truncated base64).

Port:       --port, else $PHOTOCRAFT_PORT, else 7878.
Token file: --token-file, else $PHOTOCRAFT_CONTROL_TOKEN_FILE, else ~/.photocraft/control-<port>.token.
Keep token files outside the repository; the token is never printed.
"""
import json, os, pathlib, socket, sys


def settings(port_env, token_env, default_port):
    return {"port": int(os.environ.get(port_env) or default_port),
            "token_file": os.environ.get(token_env) or None}


CONFIG = settings("PHOTOCRAFT_PORT", "PHOTOCRAFT_CONTROL_TOKEN_FILE", 7878)


def token_path(cfg):
    if cfg["token_file"]:
        return pathlib.Path(cfg["token_file"]).expanduser()
    return pathlib.Path.home() / ".photocraft" / f"control-{cfg['port']}.token"


def take_args(argv, cfg):
    """Remove --port / --token-file from argv into cfg; return the remaining arguments."""
    rest, it = [], iter(argv)
    for a in it:
        if a == "--port":
            cfg["port"] = int(next(it))
        elif a == "--token-file":
            cfg["token_file"] = next(it)
        else:
            rest.append(a)
    return rest


def connect(cfg=None):
    cfg = cfg or CONFIG
    tf = token_path(cfg)
    if not tf.is_file():
        raise SystemExit(f"token file not found: {tf} (start Photocraft with --control-token-file {tf})")
    s = socket.create_connection(("127.0.0.1", cfg["port"]), timeout=70)
    f = s.makefile("rwb")
    send(f, "auth", "auth", {"token": tf.read_text().strip()})
    return s, f


def send(f, rid, method, params):
    f.write((json.dumps({"id": rid, "method": method, "params": params}) + "\n").encode())
    f.flush()
    while True:
        line = f.readline()
        if not line:
            raise SystemExit("connection closed")
        msg = json.loads(line)
        if msg.get("id") == rid:
            if not msg.get("ok", True) and rid == "auth":
                raise SystemExit(f"auth failed: {msg.get('error')}")
            return msg


def short(o):
    s = json.dumps(o, ensure_ascii=False)
    return s if len(s) < 3000 else s[:3000] + f"... ({len(s)} chars)"


def main(cfg=None):
    cfg = cfg or CONFIG
    a = take_args(sys.argv[1:], cfg)
    if a and a[0] == "-f":
        steps = json.loads(pathlib.Path(a[1]).read_text(encoding="utf-8"))
    else:
        steps = [[a[0], json.loads(a[1]) if len(a) > 1 else {}]]
    s, f = connect(cfg)
    ok = True
    for i, (m, p) in enumerate(steps, 1):
        r = send(f, i, m, p)
        print(f"[{i}] {m} -> {short(r)}")
        if not r.get("ok"):
            ok = False
            break
    s.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
