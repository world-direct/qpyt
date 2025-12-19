import logging
import os
import pathlib
import shutil

from qpyt.ReplFileOps import ReplFileOps
from qpyt.ReplTerminal import ReplTerminal
from qpyt.Runtime import Runtime

log = logging.getLogger(__name__)


class ProjectUsrFsEntry:
    def __init__(
        self,
        project: "Project",
        src: str,
        dest: str,
        glob: str,
        compile: bool,
        when: bool,
    ):
        self.project = project
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
            res = self.project.runtime.to_posix(res)
            local_path = pathlib.Path.joinpath(local_root_path, res)
            target_path = pathlib.Path.joinpath(target_root_path, res)

            # check compile to change extension
            if self.compile and local_path.suffix == ".py":
                target_path = target_path.with_suffix(".mpy")

            fsfile = ProjectUsrFsFile(
                entry=self,
                source_path=str(local_path),
                build_path=self.project.runtime.to_temp_usrfs(str(target_path)),
                target_path=self.project.runtime.to_board_fs(str(target_path)),
            )

            log.debug("usrfs file: %s -> %s", fsfile.source_path, fsfile.target_path)

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
                log.debug(
                    f"Skipping compile of {self.source_path} to {dest_path} because modification time is the same"
                )
            else:
                log.info(f"Compiling {self.source_path} to {dest_path}")
                self.entry.project.runtime.compile_mpy(self.source_path, dest_path)

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
                log.debug(
                    f"Skipping copy of {self.source_path} to {dest_path} because modification time is the same"
                )
            else:
                log.info(f"Copying {self.source_path} to {dest_path}")
                shutil.copy2(self.source_path, dest_path)


class ProjectUsrFs:
    def __init__(self, project: "Project"):
        self.files = []  # type: list[ProjectUsrFsFile]
        self.project = project

        # add the manifest.json to usrfs_sysfiles
        self.fileinfo = ProjectUsrFsFile(
            entry=None,
            source_path=None,
            build_path=self.project.runtime.to_temp_usrfs(Project.MANIFEST_PATH),
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

        # ensure usrfs path exists
        os.makedirs(os.path.join(self.project.runtime.usrfs_path, "usr"), exist_ok=True)

        file_list = []  # type: list[dict]
        for file in self.files:
            file.to_usr_fs()
            file_list.append(
                {
                    "file_name": self.project.runtime.to_board_fs(file.target_path),
                    "size": os.path.getsize(file.build_path),
                    "integrity": self.project.runtime.create_integrity_hash(
                        file.build_path
                    ),
                }
            )

        # write file list to output_dir/manifest.json
        log.info("Generating manifest.json")
        import json

        manifest_json_path = os.path.join(
            self.project.runtime.usrfs_path, "usr", "manifest.json"
        )
        with open(manifest_json_path, "w") as f:
            manifest = {"version": project.version, "files": file_list}
            json.dump(manifest, f, indent=2)
            f.flush()

        manifest_hash = self.project.runtime.create_integrity_hash(manifest_json_path)
        log.info("manifest.json integrity hash: %s", manifest_hash)


class Project:
    """Represents a Quectel project defined by project.yaml"""

    MANIFEST_PATH = "/usr/manifest.json"

    def __init__(self, path: str, runtime: "Runtime", env: str):
        self.path = path
        self.version = "develop"

        self.runtime = runtime
        self.dir = os.path.dirname(path)
        self.usrfs_entries = []  # type: list[ProjectUsrFsEntry]
        self.usrfs = ProjectUsrFs(self)
        self.env = env

    def set_version(self, version: str):
        """Set the project version"""
        self.version = version

    def load_project(self):
        import re

        import yaml

        try:
            with open(self.path, "r") as f:
                self.config = yaml.safe_load(f)
        except yaml.scanner.ScannerError as e:
            log.error(
                f"Error while parsing project: {self.path}:{e.problem_mark.line + 1}:{e.problem_mark.column + 1}"
            )
            exit(1)
        except yaml.parser.ParserError as e:
            log.error(
                f"Error while parsing project: {self.path}:{e.problem_mark.line + 1}:{e.problem_mark.column + 1}"
            )
            exit(1)
        except yaml.YAMLError as e:
            log.error(f"Error while parsing project: {e}")
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
                log.error(f"Syntax error in expression '{expr}': {se.msg}")
                exit(1)
                return result
            except Exception as e:
                log.error(f"Error evaluating expression '{expr}': {e}")
                return None

        processed = evaluate_expressions(self.config)
        self.config = processed

    def build(self):
        """Builds the project into the usrfs"""

        log.info(f"Building project for env={self.env}")
        self.load_project()
        self.firmware_pac = self.config.get("firmware", "")

        # delete all existing usrfs files
        shutil.rmtree(self.runtime.usrfs_path, ignore_errors=True)

        log.debug("Reading project filesystem from", self.path)

        for item in self.config["usrfs"]:
            entry = ProjectUsrFsEntry(
                project=self,
                src=item["src"],
                dest=item["dest"],
                glob=item.get("glob", "*"),
                compile=bool(item.get("compile", False)),
                when=bool(item.get("when", True)),
            )

            self.usrfs_entries.append(entry)
            files = entry.glob_files(self)
            self.usrfs.add_files(files)

        log.info("Building /usr filesystem into %s", self.runtime.usrfs_path)
        self.usrfs.build(self)

    def watch(self, repl_terminal: "ReplTerminal", fops: "ReplFileOps"):
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
            repl_terminal.ensure_ready()

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

            repl_terminal.soft_reset()

    def deploy_to_board(self, fops: "ReplFileOps"):
        """Deploy the current usrfs files to the board using the given ReplFileOps"""

        log.info("Deploying files to board...")
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
                    log.info(
                        "File modified: %s (board size: %d, project size: %d)",
                        pf.target_path,
                        bf.size,
                        os.path.getsize(pf.build_path),
                    )
                    files2cp.append(pf)
            else:
                log.info("File added: %s", pf.target_path)
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
