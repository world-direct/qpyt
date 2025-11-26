import argparse
import os
import pathlib
import shutil
import sys
import threading
import time
from io import StringIO

parser = argparse.ArgumentParser(description="Deploy firmware")
subparsers = parser.add_subparsers(dest="command")
parser.add_argument(
    "--verbose", action="store_true", help="Enable verbose output", default=False
)

parser.add_argument(
    "--project", type=str, help="Path to project.yaml", default="./project.yaml"
)

parser.add_argument(
    "--port",
    type=str,
    help="Serial port for deployment",
    default="Quectel USB REPL Port",
)
parser.add_argument(
    "--baud", type=int, help="Baud rate for serial port", default=115200
)
parser.add_argument(
    "--qphy-dir", help="Path of qphy directory, defaults to .qphy", default=r".qphy"
)

watch_parser = subparsers.add_parser(
    "watch", help="Watch source directory for changes and deploys it to the board"
)
attach_parser = subparsers.add_parser(
    "attach", help="Attach to the board's REPL terminal"
)
cleanup_parser = subparsers.add_parser(
    "cleanup", help="deletes all files in /usr on the board"
)
build_parser = subparsers.add_parser(
    "build", help="Build the project output files for flashing / app_fota"
)
build_parser.add_argument(
    "--version", type=str, help="Version string for the build", default="develop"
)
subparsers.add_parser("download-tools", help="Download the required tools from quectel")

args = parser.parse_args()
verbose = args.verbose


def main():
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "watch":
        watch()

    if args.command == "build":
        build_firmware()

    if args.command == "download-tools":
        download_tools()


class Runtime:
    def __init__(self):
        self.qphy_dir = args.qphy_dir
        self.tools_dir = os.path.join(self.qphy_dir, "tools")
        self.temp_dir = os.path.join(self.qphy_dir, "temp")
        self.out_dir = os.path.join(self.qphy_dir, "out")
        self.usrfs_path = os.path.join(self.temp_dir, "fs")

        # tools directory
        if os.name == "nt":
            self.tools_dir = os.path.join(self.qphy_dir, "tools", "win")
        elif os.name == "posix":
            self.tools_dir = os.path.join(self.qphy_dir, "tools", "linux")
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

    def to_native(self, path: str) -> str:
        """Convert a path to native style (with os-specific slashes)"""
        from pathlib import PureWindowsPath

        if os.name == "nt":
            return str(PureWindowsPath(path))
        else:
            return path


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
    def __init__(self, src: str, dest: str, glob: str, compile: bool):
        self.src = src
        self.dest = dest
        self.glob = glob
        self.compile = compile


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

    def to_usr_fs(self, project: "Project"):
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
            print(f"Compiling {self.source_path} to {dest_path}")
            project.compile_mpy(self.source_path, dest_path)
        else:
            # copy file
            print(f"Copying {self.source_path} to {dest_path}")
            shutil.copy2(self.source_path, dest_path)


class Project:
    """Represents a Quectel project defined by project.yaml"""

    APP_INFO_PATH = "/usr/app_info.json"

    def __init__(self, path: str):
        self.path = path
        self.version = "develop"

        self.dir = os.path.dirname(path)
        self.usrfs_entries = []  # type: list[ProjectUsrFsEntry]
        self.usrfs_files = []  # type: list[ProjectUsrFsFile]

        # add the app_info.json to usrfs_sysfiles
        self.usrfs_fileinfo = ProjectUsrFsFile(
            entry=None,
            source_path=None,
            build_path=self.to_temp_usrfs(Project.APP_INFO_PATH),
            target_path=Project.APP_INFO_PATH,
        )

    def set_version(self, version: str):
        """Set the project version"""
        self.version = version

    def build(self):
        """Builds the project into the usrfs"""
        import yaml

        with open(self.path, "r") as f:
            self.config = yaml.safe_load(f)

        self.firmware_pac = self.config.get("firmware", "")

        # delete all existing usrfs files
        shutil.rmtree(runtime.usrfs_path, ignore_errors=True)

        vprint("Reading project filesystem from", self.path)

        for item in self.config["usrfs"]:
            entry = ProjectUsrFsEntry(
                src=item["src"],
                dest=item["dest"],
                glob=item.get("glob", "*"),
                compile=item.get("compile", False),
            )

            self.usrfs_entries.append(entry)

            # glob the source path
            import glob

            rootdir = os.path.join(self.dir, entry.src)
            local_root_path = pathlib.Path(rootdir)
            target_root_path = pathlib.PurePosixPath(entry.dest)

            for res in glob.glob(entry.glob, root_dir=rootdir, recursive=True):
                # res can be in windows style, so we convert it
                res = self.to_posix(res)
                local_path = pathlib.Path.joinpath(local_root_path, res)
                target_path = pathlib.Path.joinpath(target_root_path, res)

                # check compile to change extension
                if entry.compile and local_path.suffix == ".py":
                    target_path = target_path.with_suffix(".mpy")

                fsfile = ProjectUsrFsFile(
                    entry=entry,
                    source_path=str(local_path),
                    build_path=self.to_temp_usrfs(str(target_path)),
                    target_path=self.to_board_fs(str(target_path)),
                )

                vprint("usrfs file:", fsfile.source_path, "->", fsfile.target_path)

                self.usrfs_files.append(fsfile)

        hprint("Building /usr filesystem into", runtime.usrfs_path)

        file_list = []  # type: list[dict]
        for file in self.usrfs_files:
            file.to_usr_fs(self)
            file_list.append(
                {
                    "path": self.to_board_fs(file.target_path),
                    "size": os.path.getsize(file.build_path),
                    "integrity": create_integrity_hash(file.build_path),
                }
            )

        # write file list to output_dir/app_info.json
        print("Generating app_info.json")
        import json

        app_info_json_path = os.path.join(runtime.usrfs_path, "usr", "app_info.json")
        with open(app_info_json_path, "w") as f:
            app_info = {"version": self.version, "files": file_list}
            json.dump(app_info, f, indent=2)
            f.flush()

    def watch(self):
        """Watch source directory for changes and deploy to the board"""

        import watchfiles

        # This naturally batches changes
        for changes in watchfiles.watch(self.dir):
            # 'changes' is a set of all files that changed
            # This waits briefly and consolidates multiple changes
            vprint(f"dedected {len(changes)} changes:")
            for change_type, path in changes:
                # change-type: 1: added, 2: modified, 3: deleted
                vprint(f"  {change_type}: {path}")

    def deploy_to_board(self, fops: "TerminalFileOps"):
        """Deploy the current usrfs files to the board using the given TerminalFileOps"""

        hprint("Deploying files to board...")
        board_files = fops.lsusr()
        project_files = self.usrfs_files
        board_files_dict = {bf.path: bf for bf in board_files}

        # get all files that exist on the board and the project, but have a different size
        files2cp = []
        for pf in project_files + [self.usrfs_fileinfo]:
            bf = board_files_dict.get(pf.target_path)
            if bf is not None:
                if bf.size != os.path.getsize(pf.build_path):
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
            print(f"Copying file to board: {local} -> {remote}")
            fops.cp(local, remote)

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

        dest_path = os.path.join(self.to_native(runtime.usrfs_path), self.to_native(path))

        return dest_path

    def to_board_fs(self, path: str) -> str:
        """Convert a local path to a board filesystem path (/usr/...)"""
        if not path.startswith("/usr"):
            path = "/usr/" + path.lstrip("/")

        return path

    def compile_mpy(self, source: str, dest: str):
        """Compile a .py file to .mpy using mpy-cross"""
        run_tool([runtime.mpy_cross, "-o", dest, "-mno-unicode", source])


def run_tool(command, mayfail=False):
    if verbose:
        print("   exec: %s" % " ".join(command))
    import subprocess

    sub_p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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


def find_serial_port(port: str):
    """Try to find the serial port by name or description"""
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


class Terminal:
    PROMPT = b">>> "

    def __init__(self, port, baud):
        import serial

        port = find_serial_port(port)
        try:
            self._ser = serial.Serial(port, baud)
        except Exception as e:
            print(f"Failed to open serial port {port}: {e}")
            exit(1)
        # Start background reader
        self.stop_event = threading.Event()
        self.command_event = None
        self.command_output = None
        self.enable_print = True
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
        self.command_event.wait(timeout)
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
        self.terminal.execute_command(f"import uos;uos.remove('{path}')")

    def mkdir(self, path):
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
                print(f"Creating directory on board: {path}")
                self.mkdir(path)
                self.usr_files.append(BoardFile(path, -1))

            pass

        check(dirpath)


def watch():
    """Watch source directory for changes and deploy to the board"""

    project = Project(args.project)
    project.build()

    terminal = Terminal(args.port, args.baud)
    terminal.interrupt()
    fops = TerminalFileOps(terminal)
    project.deploy_to_board(fops)
    hprint("Resetting device and watch for changes...")
    terminal.soft_reset()

    for change in project.watch():
        print(change)
        pass


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

    print("Created usr.zip for app fota: %s" % create_integrity_hash(usr_fs_zip_path))

    # create the customer_fs.bin using mklfs
    hprint("create customer_fs.bin using mklfs")
    customer_fs_bin = os.path.join(runtime.temp_dir, "customer_fs.bin")
    run_tool(
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
            "393216",
            "-i",
            customer_fs_bin,
        ]
    )

    # create the customer_backup_fs.bin using mklfs (we currently don't use that, but we want to have a valid pac file)
    hprint("create customer_backup_fs.bin using mklfs")
    customer_backup_fs_bin = os.path.join(runtime.temp_dir, "customer_backup_fs.bin")
    bak_output_dir = os.path.join(runtime.temp_dir, "bak")
    os.makedirs(bak_output_dir, exist_ok=True)
    run_tool(
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
            "393216",
            "-i",
            customer_backup_fs_bin,
        ]
    )

    # ------------------pacgen creating and replacing customer_fs.bin and customer_backup_fs.bin: ------------------[2025-10-30 12:14:54]
    hprint("create app.pac package using packgen")
    # C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\pacgen.exe cfg-init --pname UIX8910_MODEM --palias APPIMG --pversion "8910 MODULE" --version BP_R1.0.0 --flashtype 1 cfg-host-fdl -a 0x8000c0 -s 0xff40 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\images\EC200UCNAA\fdl1.img cfg-fdl2 -a 0x810000 -s 0x30000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\exes\Unisoc\images\EC200UCNAA\fdl2.img cfg-image -i PY_FS_U -a 0x604e0000 -s 0x60000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_fs.bin cfg-image -i PY_FS_B -a 0x60540000 -s 0x20000 -p C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_backup_fs.bin pac-gen C:\Users\guenter.prossliner\Downloads\QPYcom\QPYcom_V3.9.0\fw\images\customer_fs.pac[2025-10-30 12:14:54]
    app_pac = os.path.join(runtime.temp_dir, "app.pac")
    # fmt: off
    run_tool(
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
    output_pac = os.path.join(output_dir, "firmware.pac")
    run_tool(
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
    print("Hash of output pac: %s" % create_integrity_hash(output_pac))

def download_tools():
    """Download required tools from Quectel"""
    import shutil
    import os
    import urllib.request
    import tempfile

    if os.name=='nt':
        url="https://developer.quectel.com/en/wp-content/uploads/sites/2/2024/11/QPYcom_V3.9.0.zip"
        file="QPYcom_V3.9.0.zip"
        root=r"QPYcom_V3.9.0\exes"
    elif os.name=='posix':
        url="https://developer.quectel.com/en/wp-content/uploads/sites/2/2025/04/QPYcom_V3.0.1_Ubuntu24.tar.gz"
        file="QPYcom_V3.0.1_Ubuntu24.tar.gz"
        root="QPYcom_V3.0.1_Ubuntu24/exes/linux"
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
                percent = min(100, downloaded * 100 // total_size) if total_size > 0 else 0
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
    if os.name=='nt':
        import zipfile
        with zipfile.ZipFile(archive_file, 'r') as zip_ref:
            zip_ref.extractall(tempfile.gettempdir())
    elif os.name=='posix':
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
    

def create_integrity_hash(file_path):
    import base64
    import hashlib

    hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hash.update(chunk)
    # base64 encode the hash
    base64_hash = base64.b64encode(hash.digest()).decode()
    return hash.name + "-" + base64_hash


main()
