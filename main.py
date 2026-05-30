import threading

import ATRHandler
import utils
from daemon import Daemon
from tui import main as run_tui

if __name__ == '__main__':
    utils.configure_logging()
    utils.get_client_id()
    server = Daemon(('127.0.0.1', 1234), ATRHandler.ATRHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.start()  # begin polling immediately
    run_tui()
    server.exit()
