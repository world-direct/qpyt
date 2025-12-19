import argparse
import os
import pathlib
import shutil
import sys
import threading
import time
from io import StringIO

# check for minimum python version 3.11
if sys.version_info < (3, 11):
    print("This script requires Python 3.11 or higher")
    sys.exit(1)

# check for required packages
try:
    import serial  # noqa: F401
    import watchfiles  # noqa: F401
    import yaml  # noqa: F401
except ImportError as e:
    print("Missing required package:", e.name)
    print("Please install the required packages with:")
    print("    pip install -r requirements.txt")
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
            hprint("Watch interrupted by user")

    if args.command == "build":
        build_firmware(args.out_dir)

    if args.command == "download-tools":
        download_tools()

    if args.command == "attach":
        attach_terminal()

    if args.command == "cleanup":
        cleanup_board()

    if args.command == "port-server":
        start_port_server(args.listen_ip, args.listen_port)


class Runtime:
    def __init__(self):
        self.qpyt_dir = args.qpyt_dir
        self.tools_dir = os.path.join(self.qpyt_dir, "tools")
        self.temp_dir = os.path.join(self.qpyt_dir, "temp")
        self.out_dir = os.path.join(self.qpyt_dir, "out")
        self.usrfs_path = os.path.join(self.temp_dir, "fs")

        # tools directory
        if os.name == "nt":
            self.tools_dir = os.path.join(self.qpyt_dir, "tools", "win")
        elif os.name == "posix":
            self.tools_dir = os.path.join(self.qpyt_dir, "tools", "linux")
        else:
            raise Exception("Unsupported OS: %s" % os.name)

        # create mandatory dirs
        os.makedirs(self.tools_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(self.usrfs_path, exist_ok=True)

        td = self.tools_dir
        # windows tools
        if os.name == "nt":
            self.mpy_cross = os.path.join(td, r"mpy-cross\mpy-cross-amd64.exe")

            # usage: mklfs -c <pack-dir> -b <block-size> -r <read-size> -p <prog-size> -s <filesystem-size> -i <image-file-path>
            self.mklfs = os.path.join(td, r"aboot\mklfs.exe")
            self.pacgen = os.path.join(td, r"Unisoc\pacgen.exe")
            self.dtools = os.path.join(td, r"Unisoc_Fotatools\dtools")
            self.fdl_path = os.path.join(td, r"Unisoc\images\EC200UCNAA")
        elif os.name == "posix":
            # linux tools
            self.mpy_cross = os.path.join(td, r"mpy-cross/mpy-cross")
            self.mklfs = os.path.join(td, r"aboot/mklfs")
            self.pacgen = os.path.join(td, r"Unisoc/pacgen")
            self.dtools = os.path.join(td, r"Unisoc_Fotatools_8850/dtools")
            self.fdl_path = os.path.join(td, r"Unisoc/images/EC200UCNAA")
        else:
            raise Exception("Unsupported OS: %s" % os.name)

    def run_tool(self, command, mayfail=False):
        if verbose:
            print("   exec: %s" % " ".join(command))
        import subprocess

        sub_p = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = sub_p.communicate()

        stdout = stdout.decode()
        stderr = stderr.decode()
        rc = sub_p.returncode

        if verbose:
            print("   returncode:", rc)
            print("   stdout:", stdout)
            print("   stderr:", stderr)

        if sub_p.returncode != 0 and not mayfail:
            raise RuntimeError(
                f"Command failed: {' '.join(command)}\nStdout: {stdout}\nStderr: {stderr}"
            )

        return rc, stdout, stderr

    def to_posix(self, path: str) -> str:
        """Convert a path to POSIX style (with forward slashes)"""
        from pathlib import Path

        return Path(path).as_posix()

    def to_native(self, path: str) -> str:
        """Convert a path to native style (with os-specific slashes)"""
        from pathlib import PureWindowsPath

        if os.name == "nt":
            return str(PureWindowsPath(path))
        else:
            return path

    def to_temp_usrfs(self, path: str) -> str:
        """Convert a target path to a native local path in temp usrfs"""

        # if path is rooted, which is default, we add the / "." to make it relative
        if path.startswith("/"):
            path = "." + path

        dest_path = os.path.join(
            self.to_native(runtime.usrfs_path), self.to_native(path)
        )

        return dest_path

    def to_board_fs(self, path: str) -> str:
        """Convert a local path to a board filesystem path (/usr/...)"""
        if not path.startswith("/usr"):
            path = "/usr/" + path.lstrip("/")

        return path

    def compile_mpy(self, source: str, dest: str):
        """Compile a .py file to .mpy using mpy-cross"""
        import mpy_cross

        runtime.run_tool([mpy_cross.mpy_cross, "-o", dest, "-mno-unicode", source])

    def create_integrity_hash(self, file_path):
        import base64
        import hashlib

        hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hash.update(chunk)
        # base64 encode the hash
        base64_hash = base64.b64encode(hash.digest()).decode()
        return hash.name + "-" + base64_hash


runtime = Runtime()


def vprint(*args, **kwargs):
    """Prints arguments if verbose is enabled"""
    if verbose:
        print(*args, **kwargs)


def hprint(*args, **kwargs):
    """Prints a header always"""
    # print_ansi("95")  # BRIGHT_CYAN
    # print(*args, **kwargs)
    # print_ansi("0")  # RESET
    # print()

    print("---------", *args, "------------")


def print_ansi(sequence: str):
    """Prints an ANSI escape sequence"""
    print(f"\033[{sequence}m", end="")


class ProjectUsrFsEntry:
    def __init__(self, src: str, dest: str, glob: str, compile: bool, when: bool):
        self.src = src
        self.dest = dest
        self.glob = glob
        self.compile = compile
        self.when = when

    def glob_files(self, project: "Project"):
        # glob the source path
        import glob

        if not self.when:
            # condition is false, skip this entry
            return []

        rootdir = os.path.join(project.dir, self.src)
        local_root_path = pathlib.Path(rootdir)
        target_root_path = pathlib.PurePosixPath(self.dest)

        files = []  # type: list["ProjectUsrFsFile"]
        for res in glob.glob(self.glob, root_dir=rootdir, recursive=True):
            # res can be in windows style, so we convert it
            res = runtime.to_posix(res)
            local_path = pathlib.Path.joinpath(local_root_path, res)
            target_path = pathlib.Path.joinpath(target_root_path, res)

            # check compile to change extension
            if self.compile and local_path.suffix == ".py":
                target_path = target_path.with_suffix(".mpy")

            fsfile = ProjectUsrFsFile(
                entry=self,
                source_path=str(local_path),
                build_path=runtime.to_temp_usrfs(str(target_path)),
                target_path=runtime.to_board_fs(str(target_path)),
            )

            vprint("usrfs file:", fsfile.source_path, "->", fsfile.target_path)

            files.append(fsfile)

        return files


class ProjectUsrFsFile:
    r"""Represents a file in the usr filesystem of the project

    Attributes:
    - entry: ProjectUsrFsEntry The entry this file belongs to
    - source_path: str The local source path of the file like .\src\app\util.py
    - build_path: str The build path of the tempoary usr filesystem .\build\temp\fs\usr\app\util.mpy
    - target_path: str The target path of the file in the usr filesystem like /usr/app/util.mpy

    """

    def __init__(
        self,
        entry: ProjectUsrFsEntry,
        source_path: str,
        build_path: str,
        target_path: str,
    ):
        self.entry = entry
        self.source_path = source_path
        self.build_path = build_path
        self.target_path = target_path

    def to_usr_fs(self):
        """Copy or compile the file to the temp usr fs directory"""
        # build output path
        dest_path = self.build_path
        dest_dir = os.path.dirname(dest_path)

        # create dest directory if not exist
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        # copy or compile file
        if self.entry.compile and self.source_path.endswith(".py"):
            # compile to .mpy
            if os.path.exists(dest_path) and os.path.getmtime(
                self.source_path
            ) == os.path.getmtime(dest_path):
                vprint(
                    f"Skipping compile of {self.source_path} to {dest_path} because modification time is the same"
                )
            else:
                print(f"Compiling {self.source_path} to {dest_path}")
                runtime.compile_mpy(self.source_path, dest_path)

                # set modification time of dest to source
                os.utime(
                    dest_path,
                    (
                        os.path.getatime(self.source_path),
                        os.path.getmtime(self.source_path),
                    ),
                )
        else:
            # copy file if modification time is different
            if os.path.exists(dest_path) and os.path.getmtime(
                self.source_path
            ) == os.path.getmtime(dest_path):
                vprint(
                    f"Skipping copy of {self.source_path} to {dest_path} because modification time is the same"
                )
            else:
                print(f"Copying {self.source_path} to {dest_path}")
                shutil.copy2(self.source_path, dest_path)


class ProjectUsrFs:
    def __init__(self):
        self.files = []  # type: list[ProjectUsrFsFile]

        # add the manifest.json to usrfs_sysfiles
        self.fileinfo = ProjectUsrFsFile(
            entry=None,
            source_path=None,
            build_path=runtime.to_temp_usrfs(Project.MANIFEST_PATH),
            target_path=Project.MANIFEST_PATH,
        )

    def add_files(self, files: list["ProjectUsrFsFile"]):
        """Add files to the usrfs"""
        self.files.extend(files)

    def all_files(self) -> list["ProjectUsrFsFile"]:
        """Get all files including manifest.json"""
        return self.files + [self.fileinfo]

    def remove(self, fsfile: "ProjectUsrFsFile"):
        """Remove a file from the usrfs"""
        self.files.remove(fsfile)

    def usr_files(self) -> list["ProjectUsrFsFile"]:
        """Get all usr files without manifest.json"""
        return self.files

    def build(self, project: "Project"):
        """Builds the usrfs into the temp directory including creating manifest.json"""

        file_list = []  # type: list[dict]
        for file in self.files:
            file.to_usr_fs()
            file_list.append(
                {
                    "file_name": runtime.to_board_fs(file.target_path),
                    "size": os.path.getsize(file.build_path),
                    "integrity": runtime.create_integrity_hash(file.build_path),
                }
            )

        # write file list to output_dir/manifest.json
        print("Generating manifest.json")
        import json

        manifest_json_path = os.path.join(runtime.usrfs_path, "usr", "manifest.json")
        with open(manifest_json_path, "w") as f:
            manifest = {"version": project.version, "files": file_list}
            json.dump(manifest, f, indent=2)
            f.flush()

        manifest_hash = runtime.create_integrity_hash(manifest_json_path)
        print("manifest.json integrity hash:", manifest_hash)


class Project:
    """Represents a Quectel project defined by project.yaml"""

    MANIFEST_PATH = "/usr/manifest.json"

    def __init__(self, path: str):
        self.path = path
        self.version = "develop"

        self.dir = os.path.dirname(path)
        self.usrfs_entries = []  # type: list[ProjectUsrFsEntry]
        self.usrfs = ProjectUsrFs()
        self.env = args.env

    def set_version(self, version: str):
        """Set the project version"""
        self.version = version

    def load_project(self):
        import yaml
        import re

        try:
            with open(self.path, "r") as f:
                self.config = yaml.safe_load(f)
        except yaml.scanner.ScannerError as e:
            print(
                f"Error while parsing project: {self.path}:{e.problem_mark.line + 1}:{e.problem_mark.column + 1}"
            )
            exit(1)
        except yaml.parser.ParserError as e:
            print(
                f"Error while parsing project: {self.path}:{e.problem_mark.line + 1}:{e.problem_mark.column + 1}"
            )
            exit(1)
        except yaml.YAMLError as e:
            print(f"Error while parsing project: {e}")
            exit(1)

        # evaluate expressions
        def evaluate_expressions(obj):
            """Recursively evaluate ${{ }} expressions in dict/list/str"""
            if isinstance(obj, dict):
                return {k: evaluate_expressions(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [evaluate_expressions(item) for item in obj]
            elif isinstance(obj, str):
                pattern = r"\$\{\{\s*(.*?)\s*\}\}"
                match = re.search(pattern, obj)
                if match:
                    expr = match.group(1)
                    safe_globals = {"__builtins__": {}, "env": self.env}
                    result = evaluate_expression(expr, safe_globals)
                    return result
            return obj

        def evaluate_expression(expr: str, context: dict):
            try:
                result = eval(expr, context)
                return result
            except SyntaxError as se:
                print(f"Syntax error in expression '{expr}': {se.msg}")
                exit(1)
                return result
            except Exception as e:
                print(f"Error evaluating expression '{expr}': {e}")
                return None

        processed = evaluate_expressions(self.config)
        self.config = processed

    def build(self):
        """Builds the project into the usrfs"""

        hprint(f"Building project for env={self.env}")
        self.load_project()
        self.firmware_pac = self.config.get("firmware", "")

        # delete all existing usrfs files
        shutil.rmtree(runtime.usrfs_path, ignore_errors=True)

        vprint("Reading project filesystem from", self.path)

        for item in self.config["usrfs"]:
            entry = ProjectUsrFsEntry(
                src=item["src"],
                dest=item["dest"],
                glob=item.get("glob", "*"),
                compile=bool(item.get("compile", False)),
                when=bool(item.get("when", True)),
            )

            self.usrfs_entries.append(entry)
            files = entry.glob_files(self)
            self.usrfs.add_files(files)

        hprint("Building /usr filesystem into", runtime.usrfs_path)
        self.usrfs.build(self)

    def watch(self, terminal: "Terminal", fops: "TerminalFileOps"):
        """Watch source directory for changes and deploy to the board"""

        import watchfiles

        def find_usrfs_file_by_path(files: list[ProjectUsrFsFile], path: str):
            rel_path = os.path.relpath(path, self.dir)
            for fsfile in files:
                fs_rel_path = os.path.relpath(fsfile.source_path, self.dir)
                if fs_rel_path == rel_path:
                    return fsfile

            return None

        for changes in watchfiles.watch(self.dir):
            changed_files = []  # type: list[ProjectUsrFsFile]
            new_files = []  # type: list[ProjectUsrFsFile]
            deleted_files = []  # type: list[ProjectUsrFsFile]

            # 'changes' is a set of all files that changed
            # This waits briefly and consolidates multiple changes
            for change_type, path in changes:
                # change-type: 1: added, 2: modified, 3: deleted

                # for new files glob all entries again to check if the file
                # matches any entry
                if change_type == 1:
                    for entry in self.usrfs_entries:
                        files = entry.glob_files(self)
                        file = find_usrfs_file_by_path(files, path)

                        if file is not None:
                            new_files.append(file)
                            self.usrfs.add_files(files)
                            break

                # for existing files check if path is in usrfs_files
                fsfile = find_usrfs_file_by_path(self.usrfs.files, path)
                if fsfile is None:
                    # change not in project usrfs, skip
                    continue

                if change_type == 2:
                    changed_files.append(fsfile)

                elif change_type == 3:
                    self.usrfs.remove(fsfile)
                    deleted_files.append(fsfile)

            # check if there are any changes
            if not changed_files and not deleted_files and not new_files:
                continue

            self.usrfs.build(self)
            terminal.ensure_ready()

            # process changed files
            for fsfile in changed_files:
                fops.cp(fsfile.build_path, fsfile.target_path)

            # process deleted files
            for fsfile in deleted_files:
                fops.remove(fsfile.target_path)

            # process new files
            for fsfile in new_files:
                fsfile.to_usr_fs()
                fops.cp(fsfile.build_path, fsfile.target_path)

            terminal.soft_reset()

    def deploy_to_board(self, fops: "TerminalFileOps"):
        """Deploy the current usrfs files to the board using the given TerminalFileOps"""

        hprint("Deploying files to board...")
        board_files = fops.lsusr()
        project_files = self.usrfs.all_files()  # type: list[ProjectUsrFsFile]
        board_files_dict = {bf.path: bf for bf in board_files}

        # get all files that exist on the board and the project, but have a different size
        files2cp = []
        for pf in project_files:
            bf = board_files_dict.get(pf.target_path)
            if bf is not None:
                # currently we always assume manifest.json is modified, bc for edits the size will not change
                if (
                    bf.size != os.path.getsize(pf.build_path)
                    or pf.target_path == Project.MANIFEST_PATH
                ):
                    print(
                        f"File modified: {pf.target_path} (board size: {bf.size}, project size: {os.path.getsize(pf.build_path)})"
                    )
                    files2cp.append(pf)
            else:
                print(f"File added: {pf.target_path}")
                files2cp.append(pf)

        # copy files
        for pf in files2cp:
            local = pf.build_path
            remote = pf.target_path

            # get the directory of the remote file+
            remote_dir = os.path.dirname(remote)
            fops.ensure_dir(remote_dir)

            # copy file
            fops.cp(local, remote)


class Terminal:
    PROMPT = b">>> "

    def __init__(self, port, baud):
        import serial

        port = Terminal.find_serial_port(port)
        try:
            self._ser = serial.serial_for_url(port)
        except Exception as e:
            print(f"Failed to open serial port {port}: {e}")
            exit(1)
        # Start background reader
        self.stop_event = threading.Event()
        self.command_event = None
        self.command_output = None
        self.enable_print = True
        self.is_busy = True
        self._reader_thread = threading.Thread(target=self.serial_reader, daemon=True)
        self._reader_thread.start()

    def close(self):
        self.stop_event.set()
        self._reader_thread.join()
        self._ser.close()

    def serial_reader(self):
        """Background thread to read from serial and write to stdout"""
        while not self.stop_event.is_set():
            if self._ser.in_waiting > 0:
                data = self._ser.read(self._ser.in_waiting)
                if len(data) == 0:
                    continue

                if self.enable_print:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()

                # check if data ends with the prompt ">>> "
                if data.endswith(self.PROMPT) and self.command_event is not None:
                    # remove the prompt from the end to add to response
                    self.command_output.write(data[: -len(self.PROMPT)].decode("utf-8"))
                    self.command_event.set()

                elif self.command_output is not None:
                    self.command_output.write(data.decode("utf-8"))

            else:
                # Small sleep to prevent CPU spinning
                import time

                time.sleep(0.01)

    def execute_command(self, command, data: StringIO = None, timeout=5.0):
        """Write a command to the serial port and wait for prompt"""
        if args.verbose:
            print(f"Writing command: {command}")

        self.enable_print = args.verbose
        self.command_event = threading.Event()

        self.command_output = StringIO() if data is None else data

        self._ser.write(command.encode("utf-8") + b"\r\n")
        if not self.command_event.wait(timeout):
            raise TimeoutError(f"Timeout waiting for command response: {command}")

        self.enable_print = True

        response = self.command_output.getvalue()

        # strip command echo from the start of the response
        if response.startswith(command):
            response = response[len(command) :].strip("\r\n")

        self.command_response = None
        self.command_event = None
        return response

    def soft_reset(self):
        """Soft reboot the board"""
        self._ser.write(b"\x04")  # Send Ctrl+D
        self._ser.write(b"\r\n")
        self.is_busy = True

    def ensure_ready(self):
        """Ensure that the board is in REPL mode"""
        if self.is_busy:
            self.interrupt()
            self.is_busy = False

    def interrupt(self, attempts=3):
        """
        Interrupt running QuecPython program and return to REPL
        Sends Ctrl+C (ASCII 3) multiple times
        """
        if args.verbose:
            print("Interrupting running program...")

        self.enable_print = args.verbose
        for i in range(attempts):
            self._ser.write(b"\x03")  # Send Ctrl+C
            time.sleep(0.1)

        # execute empty command to get fresh REPL prompt
        self.execute_command("print('READY')")

        if args.verbose:
            print("Program interrupted, returned to REPL")

    @staticmethod
    def find_serial_port(port: str):
        """Try to find the serial port by name or description"""

        # rfc2217://localhost:1111
        if port.startswith("rfc2217://"):
            print("Using RFC2217 port:", port)
            return port

        # check if it is just HOST:PORT
        if ":" in port and not port.startswith("/"):
            uri = "rfc2217://" + port
            print("Using RFC2217 port:", uri)
            return uri

        import serial.tools.list_ports

        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            if p.name == port:
                # port port is specifed directly, use it
                print("Using specified port:", p.name)
                return p.name

            if p.description.find(port) != -1:
                # found matching port
                print(f"Auto-detected device on port: {p.name}")
                return p.name

        raise RuntimeError(f"Could not find serial port matching: {port}")


class BoardFile:
    def __init__(self, path: str, size: int):
        self.path = path
        self.size = size


class TerminalFileOps:
    def __init__(self, terminal: Terminal):
        self.terminal = terminal
        self.usr_files = []  # type: list[BoardFile]

    def ls(self, path):
        res = self.terminal.execute_command(
            f"import uos;print(list(uos.ilistdir('{path}')))"
        )

        # res is returned from ilistdir
        # https://developer.quectel.com/doc/quecpython/API_reference/en/stdlib/uos.html#Listing-the-Parameters-of-the-Current-Directory
        # list of tuple (name, type, inode[, size])
        data = eval(res)
        return data

    def remove(self, path):
        print(f"Removing file on board: {path}")
        self.terminal.execute_command(f"import uos;uos.remove('{path}')")

    def mkdir(self, path):
        print(f"Creating directory on board: {path}")
        self.terminal.execute_command(f"import uos;uos.mkdir('{path}')")

    def lsusr(self) -> list[BoardFile]:
        """List all files in /usr directory recursively"""

        def lsdir(path, file_list: list[BoardFile]):
            items = self.ls(path)
            for d in items:
                name, type, inode, size = d
                if type == 0x4000:
                    # directory
                    dir_path = f"{path}/{name}"
                    lsdir(dir_path, file_list)

                    # directory adds itself with size -1 so that we know it exists
                    file_list.append(BoardFile(dir_path, -1))
                elif type == 0x8000:
                    # file
                    file_list.append(BoardFile(path + "/" + name, size))
                else:
                    # other
                    raise Exception("Unknown file type: %s %s" % (type, name))

        file_list = []
        lsdir("/usr", file_list)
        self.usr_files = file_list
        return file_list

    def cp(self, local_src, remove_dest, block_size=512):
        print(f"Copying file to board: {local_src} -> {remove_dest}")

        # open local file for reading
        with open(local_src, "rb") as f:
            # open remote file for writing
            self.terminal.execute_command(f"dest_file=open('{remove_dest}', 'wb')")

            while True:
                chunk = f.read(block_size)
                if not chunk:
                    break
                # write chunk to board
                # use repr to get byte string representation
                byte_str = repr(chunk)
                self.terminal.execute_command(f"dest_file.write({byte_str})")

            # close remote file
            self.terminal.execute_command("dest_file.close()")

    def delete_all_usr_files(self):
        """Delete all files in /usr on the board"""
        self.terminal.execute_command("import ql_fs;ql_fs.rmdirs('/usr')")

    def ensure_dir(self, dirpath):
        """Ensure that a directory exists on the board, creating it if necessary"""

        if not self.usr_files:
            self.lsusr()

        def dir_exits(path):
            for d in self.usr_files:
                if d.path == path and d.size == -1:
                    return True
            return False

        def check(path):
            if path == "/usr":
                return

            # check parent directory until we reach /usr
            parent = os.path.dirname(path)
            check(parent)

            if not dir_exits(path):
                self.mkdir(path)
                self.usr_files.append(BoardFile(path, -1))

            pass

        check(dirpath)


def watch():
    """Watch source directory for changes and deploy to the board"""

    project = Project(args.project)
    project.build()

    terminal = Terminal(args.port, args.baud)
    terminal.ensure_ready()
    fops = TerminalFileOps(terminal)
    project.deploy_to_board(fops)
    hprint("Resetting device and watch for changes...")
    terminal.soft_reset()
    project.watch(terminal, fops)


def build_firmware(output_dir: str = None):
    """Build the firmware package for flashing / app_fota"""
    project = Project(args.project)
    project.version = args.version

    if output_dir is None:
        output_dir = runtime.out_dir

    hprint(f"Building firmware version {project.version} into {output_dir}")
    project.build()

    # recreate output dir
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    os.makedirs(output_dir, exist_ok=True)

    # create usr.zip for app fota
    hprint("create usr.zip for app fota")
    import zipfile

    usr_fs_zip_path = os.path.join(output_dir, "usr.zip")
    with zipfile.ZipFile(usr_fs_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(runtime.usrfs_path):
            for file in files:
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, runtime.usrfs_path)
                zipf.write(file_path, relative_path)

    print("Created usr.zip for app")

    if args.usrfs_only:
        hprint("usrfs-only flag set, skipping firmware package build")
        return

    build_eg91X(project, output_dir)


def build_eg91X(project: Project, output_dir: str):
    """Build the EG91X firmware package for flashing"""

    # create the customer_fs.bin using mklfs
    # usage: mklfs -c <pack-dir> -b <block-size> -r <read-size> -p <prog-size> -s <filesystem-size> -i <image-file-path>
    hprint("create customer_fs.bin using mklfs")
    customer_fs_bin = os.path.join(runtime.temp_dir, "customer_fs.bin")
    runtime.run_tool(
        [
            runtime.mklfs,
            "-c",
            runtime.usrfs_path,
            "-b",
            "4096",
            "-r",
            "4096",
            "-p",
            "4096",
            "-s",
            str(0x60000),
            "-i",
            customer_fs_bin,
        ]
    )

    # create the customer_backup_fs.bin using mklfs (we currently don't use that, but we want to have a valid pac file)
    hprint("create customer_backup_fs.bin using mklfs")
    customer_backup_fs_bin = os.path.join(runtime.temp_dir, "customer_backup_fs.bin")
    bak_output_dir = os.path.join(runtime.temp_dir, "bak")
    os.makedirs(bak_output_dir, exist_ok=True)
    runtime.run_tool(
        [
            runtime.mklfs,
            "-c",
            bak_output_dir,
            "-b",
            "4096",
            "-r",
            "4096",
            "-p",
            "4096",
            "-s",
            str(0x60000),
            "-i",
            customer_backup_fs_bin,
        ]
    )

    # ------------------pacgen creating and replacing customer_fs.bin and customer_backup_fs.bin: ------------------[2025-10-30 12:14:54]
    hprint("create app.pac package using packgen")
    # C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\pacgen.exe cfg-init --pname UIX8910_MODEM --palias APPIMG --pversion "8910 MODULE" --version BP_R1.0.0 --flashtype 1 cfg-host-fdl -a 0x8000c0 -s 0xff40 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\images\EC200UCNAA\fdl1.img cfg-fdl2 -a 0x810000 -s 0x30000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\images\EC200UCNAA\fdl2.img cfg-image -i PY_FS_U -a 0x604e0000 -s 0x60000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_fs.bin cfg-image -i PY_FS_B -a 0x60540000 -s 0x20000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_backup_fs.bin pac-gen C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_fs.pac[2025-10-30 12:14:54]
    app_pac = os.path.join(runtime.temp_dir, "app.pac")
    # fmt: off
    runtime.run_tool(
        [
            runtime.pacgen,
            "cfg-init", "--pname", "UIX8910_MODEM", "--palias", "APPIMG", "--pversion", "8910 MODULE", "--version", "BP_R1.0.0", "--flashtype", "1",
            "cfg-host-fdl", "-a", "0x8000c0", "-s", "0xff40", "-p", os.path.join(runtime.fdl_path, "fdl1.img"),
            "cfg-fdl2",     "-a", "0x810000", "-s", "0x30000","-p", os.path.join(runtime.fdl_path, "fdl2.img"),
            "cfg-image", "-i", "PY_FS_U", "-a", "0x604e0000", "-s", "0x60000", "-p", customer_fs_bin,
            "cfg-image", "-i", "PY_FS_B", "-a", "0x60540000", "-s", "0x20000", "-p", customer_backup_fs_bin,
            "pac-gen", app_pac,
        ]
        # fmt: on
    )

    # C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc_Fotatools\dtools pacmerge --id PY_FS_U,PS --id PY_FS_B,PS
    hprint("merge final .pac using dttools pacmerge")
    # "C:\Users\guenter.prossliner\Downloads\QPY_OCPU_EG915U_EUAB_FW\QPY_OCPU_V0006_EG915U_EUAB_FW\EG915UEUABR03A06M08_OCPU_QPY_01.300.01.300\8915DM_cat1_open_EG915UEUABR03A06M08_OCPU_QPY_01.300.01.300_merge.pac" C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_fs.pac "C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\outFW\8915DM_cat1_open_EG915UEUABR03A06M08_OCPU_QPY_01.300.01.300_merge_20251030-1214.pac"[2025-10-30 12:14:56]
    output_pac = os.path.join(output_dir, "image.pac")
    runtime.run_tool(
        [
            runtime.dtools,
            "pacmerge",
            "--id",
            "PY_FS_U,PS",
            "--id",
            "PY_FS_B,PS",
            project.firmware_pac,
            app_pac,
            output_pac,
        ]
    )

    print("Firmware build completed. Output pac file: %s" % output_pac)
    print("Hash of output pac: %s" % runtime.create_integrity_hash(output_pac))


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
        print(f"Downloading {url} to {archive_file}")

        def download_with_progress(url, filename):
            prev_percent = -1

            def download_progress(block_num, block_size, total_size):
                nonlocal prev_percent
                downloaded = block_num * block_size
                percent = (
                    min(100, downloaded * 100 // total_size) if total_size > 0 else 0
                )
                if percent != prev_percent:
                    print(f"\rDownloading... {percent}%", end="", flush=True)
                    prev_percent = percent

            urllib.request.urlretrieve(url, filename, reporthook=download_progress)
            print()  # Move to next line after download

        download_with_progress(url, archive_file)
    else:
        print(f"File {archive_file} already exists, skipping download")

    # extract the tar.gz file
    print("Extracting tools...")
    if os.name == "nt":
        import zipfile

        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            zip_ref.extractall(tempfile.gettempdir())
    elif os.name == "posix":
        import tarfile

        with tarfile.open(archive_file, "r:gz") as tar:
            tar.extractall(path=tempfile.gettempdir(), filter="data")

    print("Copying extracted files...")
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
    print("Cleaning up...")
    shutil.rmtree(extracted_subdir)

    print("Tools downloaded and extracted to %s" % dest_dir)


def attach_terminal():
    """Attach a terminal to the board"""
    import select
    import signal

    terminal = Terminal(args.port, args.baud)

    # Track Ctrl+C presses for exit
    last_interrupt = [0.0]

    def handle_interrupt(sig, frame):
        """Handle Ctrl+C: first time sends to device, second time exits"""
        current_time = time.time()

        if current_time - last_interrupt[0] < 1.0:
            # Second Ctrl+C within 1 second - exit
            print("\n\nDetaching from terminal...")
            terminal.close()
            sys.exit(0)
        else:
            # First Ctrl+C - send to device
            terminal._ser.write(b"\x03")
            print("\r^C (press Ctrl+C again within 1s to detach)", end="", flush=True)
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
        print("Attached to terminal. Type commands and press Enter.")
        print("Press Ctrl+C twice (within 1s) to exit.")
        print("-" * 60)

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
                    print(f"\r[Sending escape sequence: {ch!r}]", end="", flush=True)

                # Send character to device
                terminal._ser.write(ch)
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
        terminal.close()


def cleanup_board():
    """Cleanup /usr filesystem on the board"""
    terminal = Terminal(args.port, args.baud)
    terminal.ensure_ready()
    fops = TerminalFileOps(terminal)
    hprint("Deleting all files in /usr on the board...")
    fops.delete_all_usr_files()
    terminal.soft_reset()
    time.sleep(1)
    terminal.close()


def start_port_server(listen_ip: str, listen_port: int):
    """Start a RFC2217 serial port server to share the device over network"""

    # this is adapted from the rfc2217_server.py example in pyserial#
    # https://github.com/pyserial/pyserial/blob/master/examples/rfc2217_server.py
    #
    # redirect data from a TCP/IP connection to a serial port and vice versa
    # using RFC 2217
    #
    # (C) 2009-2015 Chris Liechti <cliechti@gmx.net>
    #
    # SPDX-License-Identifier:    BSD-3-Clause

    port = Terminal.find_serial_port(args.port)
    print(f"Starting RFC2217 server on port {listen_port}, forwarding to {port}")

    import logging
    import socket
    import sys
    import threading
    import time

    import serial
    import serial.rfc2217

    class Redirector(object):
        def __init__(self, serial_instance, socket, debug=False):
            self.serial = serial_instance
            self.socket = socket
            self._write_lock = threading.Lock()
            self.rfc2217 = serial.rfc2217.PortManager(
                self.serial,
                self,
                logger=logging.getLogger("rfc2217.server") if debug else None,
            )
            self.log = logging.getLogger("redirector")

        def statusline_poller(self):
            self.log.debug("status line poll thread started")
            while self.alive:
                time.sleep(1)
                self.rfc2217.check_modem_lines()
            self.log.debug("status line poll thread terminated")

        def shortcircuit(self):
            """connect the serial port to the TCP port by copying everything
            from one side to the other"""
            self.alive = True
            self.thread_read = threading.Thread(target=self.reader)
            self.thread_read.daemon = True
            self.thread_read.name = "serial->socket"
            self.thread_read.start()
            self.thread_poll = threading.Thread(target=self.statusline_poller)
            self.thread_poll.daemon = True
            self.thread_poll.name = "status line poll"
            self.thread_poll.start()
            self.writer()

        def reader(self):
            """loop forever and copy serial->socket"""
            self.log.debug("reader thread started")
            while self.alive:
                try:
                    data = self.serial.read(self.serial.in_waiting or 1)
                    if data:
                        # escape outgoing data when needed (Telnet IAC (0xff) character)
                        self.write(b"".join(self.rfc2217.escape(data)))
                except socket.error as msg:
                    self.log.error("{}".format(msg))
                    # probably got disconnected
                    break
            self.alive = False
            self.log.debug("reader thread terminated")

        def write(self, data):
            """thread safe socket write with no data escaping. used to send telnet stuff"""
            with self._write_lock:
                self.socket.sendall(data)

        def writer(self):
            """loop forever and copy socket->serial"""
            while self.alive:
                try:
                    data = self.socket.recv(1024)
                    if not data:
                        break
                    self.serial.write(b"".join(self.rfc2217.filter(data)))
                except socket.error as msg:
                    self.log.error("{}".format(msg))
                    # probably got disconnected
                    break
            self.stop()

        def stop(self):
            """Stop copying"""
            self.log.debug("stopping")
            if self.alive:
                self.alive = False
                self.thread_read.join()
                self.thread_poll.join()

    if verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(level=level)

    # ~ logging.getLogger('root').setLevel(logging.INFO)
    logging.getLogger("rfc2217").setLevel(level)

    # connect to serial port
    ser = serial.serial_for_url(port, do_not_open=True)
    ser.timeout = 3  # required so that the reader thread can exit
    # reset control line as no _remote_ "terminal" has been connected yet
    ser.dtr = False
    ser.rts = False

    logging.info("RFC 2217 TCP/IP to Serial redirector - type Ctrl-C / BREAK to quit")

    try:
        ser.open()
    except serial.SerialException as e:
        logging.error("Could not open serial port {}: {}".format(ser.name, e))
        sys.exit(1)

    logging.info("Serving serial port: {}".format(ser.name))
    settings = ser.get_settings()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_ip, listen_port))
    srv.listen(1)
    logging.info("TCP/IP port: {}".format(listen_port))

    import select

    while True:
        try:
            ready, _, _ = select.select([srv], [], [], 1.0)  # 1 second timeout
            if not ready:
                time.sleep(0.1)
                continue

            client_socket, addr = srv.accept()
            logging.info("Connected by {}:{}".format(addr[0], addr[1]))
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            ser.rts = True
            ser.dtr = True
            # enter network <-> serial loop
            r = Redirector(ser, client_socket, verbose)
            try:
                r.shortcircuit()
            finally:
                logging.info("Disconnected")
                r.stop()
                client_socket.close()
                ser.dtr = False
                ser.rts = False
                # Restore port settings (may have been changed by RFC 2217
                # capable client)
                ser.apply_settings(settings)
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            break
        except socket.error as msg:
            logging.error(str(msg))

    logging.info("--- exit ---")


main()
