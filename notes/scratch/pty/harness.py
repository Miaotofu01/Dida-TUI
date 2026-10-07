"""Independent real-pty capture for ticket #51: does the app emit ANSI (not truecolor)?"""
import fcntl, os, pty, select, signal, struct, subprocess, sys, termios, time

REPO = "/home/tofu/dida-v2-worktrees/integration"
HOME = "/tmp/dida51-pty/home"
OUT = "/tmp/dida51-pty/capture.bin"

os.makedirs(os.path.join(HOME, ".config", "dida-tui"), exist_ok=True)
cfg = os.path.join(HOME, ".config", "dida-tui", "config.toml")
with open(cfg, "w") as fh:
    fh.write('token = "fake-token-not-a-real-credential"\n'
             'refresh_on_start = false\n'
             'push_on_change = false\n')
os.chmod(cfg, 0o600)

env = os.environ.copy()
env.pop("NO_COLOR", None)
env["TERM"] = "xterm-kitty"
env["COLORTERM"] = "truecolor"
env["HOME"] = HOME
env["PYTHONPATH"] = os.path.join(REPO, "src")

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))

proc = subprocess.Popen(
    [os.path.join(REPO, ".venv/bin/python"), "-m", "dida"],
    stdin=slave, stdout=slave, stderr=slave, env=env, cwd=REPO,
    close_fds=True, start_new_session=True,
)
os.close(slave)

chunks = []
deadline = time.time() + 25.0
script = [(2.0, b"j"), (0.6, b"j"), (0.6, b"\r"), (1.2, b"j"), (0.6, b"\r"),
          (1.2, b"?"), (1.2, b"\x1b"), (1.0, b"\x1b"), (1.0, b"q"), (1.5, b"")]
sent = 0
next_send = time.time() + script[0][0] if script else float("inf")
try:
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        if sent < len(script) and time.time() >= next_send:
            delay, keys = script[sent]
            if keys:
                os.write(master, keys)
            sent += 1
            next_send = time.time() + (script[sent][0] if sent < len(script) else 1e9)
        if proc.poll() is not None and sent >= len(script):
            # drain whatever is left
            for _ in range(20):
                ready, _, _ = select.select([master], [], [], 0.05)
                if not ready:
                    break
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            break
finally:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
os.close(master)

blob = b"".join(chunks)
with open(OUT, "wb") as fh:
    fh.write(blob)
print(f"captured {len(blob)} bytes; exit={proc.returncode}")
