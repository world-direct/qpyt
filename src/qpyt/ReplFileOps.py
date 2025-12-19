import logging
import os

from qpyt.ReplTerminal import ReplTerminal


class BoardFile:
    def __init__(self, path: str, size: int):
        self.path = path
        self.size = size


class ReplFileOps:
    """Implements file operations on the QuecPython board via REPL commands"""

    def __init__(self, terminal: ReplTerminal):
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
        logging.info(f"Removing file on board: {path}")
        self.terminal.execute_command(f"import uos;uos.remove('{path}')")

    def mkdir(self, path):
        logging.info(f"Creating directory on board: {path}")
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
        logging.info(f"Copying file to board: {local_src} -> {remove_dest}")

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
