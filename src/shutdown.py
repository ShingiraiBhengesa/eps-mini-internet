
import signal


def stop_on_signals():
    """Turn Ctrl-C and SIGTERM into KeyboardInterrupt for a clean exit.

    A process started in the background from a script ignores Ctrl-C
    (SIGINT) by default, and SIGTERM would otherwise end it without running
    cleanup code. With this installed, either signal unwinds through the
    caller's try/finally, so sockets are closed and log files flushed.
    """
    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
