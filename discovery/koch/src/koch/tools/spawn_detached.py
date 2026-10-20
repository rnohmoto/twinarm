"""Start a command in its own session with SIGINT at its default disposition.

A background job of a non-interactive shell inherits SIGINT=ignore, and Python then never
raises KeyboardInterrupt — the teleop's clean exit (release torque, close the bus) would not
run. This wrapper resets the signals, detaches (setsid), redirects output to a log file and
writes the PID so the process can be stopped later with `kill -INT <pid>`.

  python -m koch.tools.spawn_detached <pidfile> <logfile> <cmd> [args...]
"""

import argparse
import os
import signal


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pidfile")
    ap.add_argument("logfile")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="command and its arguments")
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    if not args.cmd:
        ap.error("<cmd> が要ります")
    pidfile, logfile, cmd = args.pidfile, args.logfile, args.cmd
    if os.fork():
        os._exit(0)
    os.setsid()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, signal.SIG_DFL)
    out = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)
    os.dup2(out, 1)
    os.dup2(out, 2)
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    main()
