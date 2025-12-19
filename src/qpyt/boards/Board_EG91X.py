
import os
from qpyt.Project import Project
from qpyt.Runtime import Runtime

import logging
log = logging.getLogger(__name__)

def build(runtime: Runtime, project: Project, output_dir: str):
    """Build the EG91X firmware package for flashing"""

    # create the customer_fs.bin using mklfs
    # usage: mklfs -c <pack-dir> -b <block-size> -r <read-size> -p <prog-size> -s <filesystem-size> -i <image-file-path>
    log.info("create customer_fs.bin using mklfs")
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
    log.info("create customer_backup_fs.bin using mklfs")
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
    log.info("create app.pac package using packgen")
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

    log.info("merge final .pac using dttools pacmerge")
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

    log.info("Firmware build completed. Output pac file: %s" % output_pac)
    log.info("Hash of output pac: %s" % runtime.create_integrity_hash(output_pac))
