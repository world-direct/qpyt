import argparse
import logging
import os
import shutil
import sys
import time

from qpyt.ReplFileOps import ReplFileOps
from qpyt.ReplTerminal import ReplTerminal
from qpyt.Runtime import Runtime
from qpyt.Project import Project

log = logging.getLogger(__name__)

# check for minimum python version 3.11
if sys.version_info < (3, 11):
    log.error("This script requires Python 3.11 or higher")
    sys.exit(1)

# Create parent parsers with common arguments
global_flags = argparse.ArgumentParser(add_help=False)
global_flags.add_argument(
    "--verbose", action="store_true", help="Enable verbose output", default=False
)

build_flags = argparse.ArgumentParser(add_help=False)
build_flags.add_argument(
    "--env",
    type=str,
    help="Build environment, can be used for conditions, can be overridden by QPYT_ENV environment variable",
    default="",
)

serial_flags = argparse.ArgumentParser(add_help=False)
serial_flags.add_argument(
    "--port",
    type=str,
    help="Serial port for deployment, can be a COM port or part of the description to auto-detect, can be overridden by QPYT_PORT environment variable",
    default="Quectel USB REPL Port",
)

serial_flags.add_argument(
    "--baud", type=int, help="Baud rate for serial port", default=115200
)

parser = argparse.ArgumentParser(
    description="QuecPython project script", parents=[global_flags]
)
subparsers = parser.add_subparsers(dest="command")
parser.add_argument(
    "--project", type=str, help="Path to project.yaml", default="./project.yaml"
)

parser.add_argument(
    "--qpyt-dir", help="Path of qpyt directory, defaults to .qpyt", default=r".qpyt"
)

watch_parser = subparsers.add_parser(
    "watch",
    parents=[global_flags, serial_flags, build_flags],
    help="Watch source directory for changes and deploys it to the board",
)
attach_parser = subparsers.add_parser(
    "attach",
    parents=[global_flags, serial_flags],
    help="Attach to the board's REPL terminal",
)
cleanup_parser = subparsers.add_parser(
    "cleanup",
    parents=[global_flags, serial_flags],
    help="deletes all files in /usr on the board",
)
build_parser = subparsers.add_parser(
    "build",
    parents=[global_flags, build_flags],
    help="Build the project output files for flashing / app_fota",
)
build_parser.add_argument(
    "--version", type=str, help="Version string for the build", default="develop"
)
build_parser.add_argument(
    "--usrfs-only",
    action="store_true",
    help="Only build the usr filesystem",
    default=False,
)
build_parser.add_argument(
    "--out-dir", type=str, help="Output directory for built firmware", default=None
)
subparsers.add_parser(
    "download-tools",
    parents=[global_flags],
    help="Download the required tools from quectel",
)

portserver_parser = subparsers.add_parser(
    "port-server",
    parents=[global_flags, serial_flags],
    help="Start a serial port server to share the serial port over TCP",
)

portserver_parser.add_argument(
    "--listen-port",
    type=int,
    help="Port to listen on for incoming TCP connections",
    default=15612,
)

portserver_parser.add_argument(
    "--listen-ip",
    type=str,
    help="IP address to listen on for incoming TCP connections",
    default="0.0.0.0",
)

args = parser.parse_args()
verbose = args.verbose
llevel = logging.DEBUG if verbose else logging.INFO

logging.basicConfig(
    level=llevel,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# disable logging for some noisy modules
logging.getLogger("watchfiles").setLevel(logging.WARNING)

# environment overrides
if "QPYT_PORT" in os.environ:
    args.port = os.environ["QPYT_PORT"]

if "QPYT_ENV" in os.environ:
    args.env = os.environ["QPYT_ENV"]


def main():
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "watch":
        try:
            watch()
        except KeyboardInterrupt:
            log.info("Watch interrupted by user")
            exit(0)

    if args.command == "build":
        build_firmware(args.out_dir)
        exit(0)

    if args.command == "download-tools":
        download_tools()
        exit(0)

    if args.command == "attach":
        attach_terminal()
        exit(0)

    if args.command == "cleanup":
        cleanup_board()
        exit(0)

    if args.command == "port-server":
        start_port_server(args.listen_ip, args.listen_port)
        exit(0)


runtime = Runtime(args.qpyt_dir)

def watch():
    """Watch source directory for changes and deploy to the board"""

    project = Project(args.project, runtime, args.env)
    project.build()

    repl_terminal = ReplTerminal(args.port)
    repl_terminal.ensure_ready()
    fops = ReplFileOps(repl_terminal)
    project.deploy_to_board(fops)
    log.info("Resetting device and watch for changes...")
    repl_terminal.soft_reset()
    project.watch(repl_terminal, fops)


def build_firmware(output_dir: str = None):
    """Build the firmware package for flashing / app_fota"""
    project = Project(args.project, runtime, args.env)
    project.version = args.version

    if output_dir is None:
        output_dir = runtime.out_dir

    log.info(f"Building firmware version {project.version} into {output_dir}")
    project.build()

    # recreate output dir
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    os.makedirs(output_dir, exist_ok=True)

    # create usr.zip for app fota
    log.info("create usr.zip for app fota")
    import zipfile

    usr_fs_zip_path = os.path.join(output_dir, "usr.zip")
    with zipfile.ZipFile(usr_fs_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(runtime.usrfs_path):
            for file in files:
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, runtime.usrfs_path)
                zipf.write(file_path, relative_path)

    logging.info("Created usr.zip for app")

    if args.usrfs_only:
        logging.info("usrfs-only flag set, skipping firmware package build")
        return

    from qpyt.boards.Board_EG91X import build

    build(runtime, project, output_dir)


def download_tools():
    """Download required tools from Quectel"""
    import os
    import shutil
    import tempfile
    import urllib.request

    if os.name == "nt":
        url = "https://developer.quectel.com/en/wp-content/uploads/sites/2/2024/11/QPYcom_V3.9.0.zip"
        file = "QPYcom_V3.9.0.zip"
        root = r"QPYcom_V3.9.0\exes"
    elif os.name == "posix":
        url = "https://developer.quectel.com/en/wp-content/uploads/sites/2/2025/04/QPYcom_V3.0.1_Ubuntu24.tar.gz"
        file = "QPYcom_V3.0.1_Ubuntu24.tar.gz"
        root = "QPYcom_V3.0.1_Ubuntu24/exes/linux"
    else:
        raise Exception("Unsupported OS")

    dest_dir = runtime.tools_dir

    # delete and recreate tools directory if exist
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)

    os.makedirs(dest_dir)

    # download the tool to tmp directory
    archive_file = os.path.join(tempfile.gettempdir(), file)
    if not os.path.exists(archive_file):
        log(f"Downloading {url} to {archive_file}")

        def download_with_progress(url, filename):
            prev_percent = -1

            def download_progress(block_num, block_size, total_size):
                nonlocal prev_percent
                downloaded = block_num * block_size
                percent = (
                    min(100, downloaded * 100 // total_size) if total_size > 0 else 0
                )
                if percent != prev_percent:
                    log.info(f"\rDownloading... {percent}%")
                    prev_percent = percent

            urllib.request.urlretrieve(url, filename, reporthook=download_progress)

        download_with_progress(url, archive_file)
    else:
        log.warning(f"File {archive_file} already exists, skipping download")

    # extract the tar.gz file
    log.info("Extracting tools...")
    if os.name == "nt":
        import zipfile

        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            zip_ref.extractall(tempfile.gettempdir())
    elif os.name == "posix":
        import tarfile

        with tarfile.open(archive_file, "r:gz") as tar:
            tar.extractall(path=tempfile.gettempdir(), filter="data")

    log.info("Copying extracted files...")
    # move extracted files from subdirectory to tools directory
    extracted_subdir = os.path.join(tempfile.gettempdir(), root)
    for item in os.listdir(extracted_subdir):
        s = os.path.join(extracted_subdir, item)
        d = os.path.join(dest_dir, item)
        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            for subitem in os.listdir(s):
                shutil.move(os.path.join(s, subitem), d)
        else:
            shutil.move(s, d)

    # delete the empty directories
    log.info("Cleaning up...")
    shutil.rmtree(extracted_subdir)

    log.info("Tools downloaded and extracted to %s", dest_dir)


def attach_terminal():
    """Attach a terminal to the board"""
    import select
    import signal

    repl_terminal = ReplTerminal(args.port)

    # Track Ctrl+C presses for exit
    last_interrupt = [0.0]

    def handle_interrupt(sig, frame):
        """Handle Ctrl+C: first time sends to device, second time exits"""
        current_time = time.time()

        if current_time - last_interrupt[0] < 1.0:
            # Second Ctrl+C within 1 second - exit
            log.info("Detaching from terminal...")
            repl_terminal.close()
            sys.exit(0)
        else:
            # First Ctrl+C - send to device
            repl_terminal.port.write(b"\x03")
            log.info("(press Ctrl+C again within 1s to detach)")
            last_interrupt[0] = current_time

    # Install signal handler
    original_handler = signal.signal(signal.SIGINT, handle_interrupt)

    # Setup for reading keyboard input
    if sys.platform == "win32":
        # Windows - use msvcrt
        import msvcrt

        def read_keyboard():
            """Read keyboard input on Windows, handling special keys"""
            if msvcrt.kbhit():
                ch = msvcrt.getch()

                # Handle special keys (arrow keys, etc.)
                if ch in (b"\x00", b"\xe0"):
                    # Special key prefix - wait briefly for the actual key code
                    time.sleep(0.001)  # 1ms wait
                    if msvcrt.kbhit():
                        key_code = msvcrt.getch()

                        # Map Windows key codes to VT100 escape sequences
                        key_map = {
                            b"H": b"\x1b[A",  # Up arrow
                            b"P": b"\x1b[B",  # Down arrow
                            b"M": b"\x1b[C",  # Right arrow
                            b"K": b"\x1b[D",  # Left arrow
                            b"G": b"\x1b[H",  # Home
                            b"O": b"\x1b[F",  # End
                            b"S": b"\x1b[3~",  # Delete
                        }
                        mapped = key_map.get(key_code)
                        if mapped:
                            return mapped
                        # For unmapped special keys, ignore them
                        return None
                    else:
                        # No second byte received, ignore the prefix
                        return None

                return ch
            return None
    else:
        # Unix-like systems - use termios for raw mode
        import termios
        import tty

        old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())

        def read_keyboard():
            """Read keyboard input on Unix"""
            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                return ch.encode()
            return None

    try:
        log.info("Attached to terminal. Type commands and press Enter.")
        log.info("Press Ctrl+C twice (within 1s) to exit.")
        log.info("-" * 60)

        # Input loop - read keyboard and send to serial
        while True:
            ch = read_keyboard()
            if ch is not None:
                # Check for Ctrl+C (0x03) in raw mode
                if ch == b"\x03":
                    handle_interrupt(None, None)
                    continue

                # Debug: show what we're sending for arrow keys
                if args.verbose and ch.startswith(b"\x1b"):
                    log.debug(f"[Sending escape sequence: {ch!r}]")

                # Send character to device
                repl_terminal.port.write(ch)
            else:
                # Small delay to prevent CPU spinning
                time.sleep(0.01)

    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal settings on Unix
        if sys.platform != "win32":
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        # Restore original signal handler
        signal.signal(signal.SIGINT, original_handler)
        repl_terminal.close()


def cleanup_board():
    """Cleanup /usr filesystem on the board"""
    repl_terminal = ReplTerminal(args.port, args.baud)
    repl_terminal.ensure_ready()
    fops = ReplFileOps(repl_terminal)
    log.info("Deleting all files in /usr on the board...")
    fops.delete_all_usr_files()
    repl_terminal.soft_reset()
    time.sleep(1)
    repl_terminal.close()


def start_port_server(listen_ip: str, listen_port: int):
    """Start TCP server that can be used with the socket:// pyserial URL to connect to the serial port remotely"""
    from qpyt.SocketServer import SocketServer

    try:
        server = SocketServer(
            port_name=args.port, listen_ip=listen_ip, listen_port=listen_port
        )
        server.start()
    except KeyboardInterrupt:
        log.info("Stopping port server...")


main()
