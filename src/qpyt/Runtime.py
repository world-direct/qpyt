import os
import logging

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, qpyt_dir: str):
        self.qpyt_dir = qpyt_dir
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
        log.debug("running tool command: %s", " ".join(command))
        import subprocess

        sub_p = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = sub_p.communicate()

        stdout = stdout.decode()
        stderr = stderr.decode()
        rc = sub_p.returncode

        log.debug("tool command finished with return code %d", rc)
        log.debug("   stdout: %s", stdout)
        log.debug("   stderr: %s", stderr)

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

        dest_path = os.path.join(self.to_native(self.usrfs_path), self.to_native(path))

        return dest_path

    def to_board_fs(self, path: str) -> str:
        """Convert a local path to a board filesystem path (/usr/...)"""
        if not path.startswith("/usr"):
            path = "/usr/" + path.lstrip("/")

        return path

    def compile_mpy(self, source: str, dest: str):
        """Compile a .py file to .mpy using mpy-cross"""
        import mpy_cross

        self.run_tool([mpy_cross.mpy_cross, "-o", dest, "-mno-unicode", source])

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
