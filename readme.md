# qpyt - QuecPython Project Tool

The `qpyt` script is a comprehensive tool for QuecPython development. It manages the complete development lifecycle from building to deployment.

## Features

* **Build** - Create `.pac` firmware files for flashing and `usr.zip` for app FOTA
* **Download Tools** - Automatically download required Quectel build tools
* **Watch** - Deploy application with hot reload on file changes
* **Attach** - Interactive REPL terminal access to the board
* **Cleanup** - Delete all files in `/usr` on the board
* **Port Server** - Share serial port over TCP/IP using RFC 2217 protocol

## Supported Operating Systems

I have manually tested running on:

* Windows 11 locally
* MacOS Tahoe 26.1 (25B78) on M1 over RFC 2217 connection
* Ubuntu for build in GitHub actions

## Supported board(s)

Currently only the [EG91X Evaluation Board](https://developer.quectel.com/doc/quecpython/Dev_board_guide/en/eg91x-evb.html)
is supported and tested.

As we use hardcoded (relative) paths, and parameters for building the firmware, 
it's unlikely that any other board works.

## Installation

You should install qpyt over pip, as it is published to [qpyt on pypi](https://pypi.org/project/qpyt/).

Run `pip install qpyt` to fetch and install it locally. It is also recommented to use 
a [Python virtual environment](https://docs.python.org/3/library/venv.html).


### Editable install from repository

To test versions that are not released yet, or to perform local debugging,
install the package editable.

* Clone the repo
* Install it editable `pip install -e .`

## Project Configuration (project.yaml)

The `project.yaml` file defines your QuecPython project structure, build configuration, and deployment rules. It specifies which files to include, if to compile them, and where to place them on the board.

### Basic Structure

```yaml
firmware: <path-to-base-firmware.pac>

usrfs:
  - src: <source-directory>
    glob: <file-pattern>
    dest: <destination-on-board>
    compile: <true|false>
    when: <condition-expression>
```

### Configuration Fields

#### `firmware`

Path to the base firmware `.pac` file from Quectel that will be merged with your application.

```yaml
firmware: ./build/firmware/8915DM_cat1_open_EG915UEUABR03A06M08_OCPU_QPY_01.300.01.300_merge.pac
```

**Note**: This field is required for building complete firmware packages. It's not needed for `--usrfs-only` builds or any other commands.

#### `usrfs` (User Filesystem Entries)

An array of file deployment rules. Each entry defines:

##### `src` (required) - Source directory path relative to project root
```yaml
src: ./src/app/portable/
```

**`glob`** (optional, default `*`) - File pattern to match (supports wildcards)
- `*.py` - All Python files in the directory
- `**/*.py` - All Python files recursively in subdirectories
- `specific-file.ini` - Single file by name

```yaml
glob: "**/*.py"         # All .py files recursively
glob: "*.py"            # Only .py files in root directory
glob: "0-factory.ini"   # Single file
```

##### `dest` (required) - Destination path on the board (must be in `/usr` or subdirectories)
```yaml
dest: /usr           # Root of user filesystem
dest: /usr/app       # Application directory
dest: /usr/etc       # Configuration directory
```

##### `compile` (optional, default: `false`) - Whether to compile `.py` files to `.mpy` bytecode
```yaml
compile: true   # Compile Python files with mpy-cross
compile: false  # Copy files as-is
```

**Benefits of compilation**:
- Reduces file size (~30-50% smaller)
- Faster loading times
- Lower memory usage
- Basic code obfuscation
- Basic code (syntax) validation

**When NOT to compile**:
- Entry points (`main.py`, `boot.py`) - QuecPython cannot execute `.mpy` as entry points
- Files that need to be edited on the board
- Configuration and data files

**Note:** We use `mpy-cross` from [mpy-cross on pip](https://pypi.org/project/mpy-cross/).
This is available for Linux, Windows and MacOS incl ARM Chipset. This allows us
to compile to .mpy without downloading the Quectel tools.

Currently the version is pinned to `1.12` because this is the version used in 
the used QuecPython version. `mpy-cross` emits a binary format that need to match
the target micropython version, so it **should not be updated**!. If we will 
support different board or version in the future we may need to implement dynamic
fetching or the package.

##### `when` (optional, default: `true`) - Conditional deployment using expressions
```yaml
when: ${{ env=="dev" }}           # Only when --env=dev
when: ${{ env=="production" }}    # Only when --env=production
when: ${{ env!="dev" }}           # When NOT dev environment
```

Expression syntax: `${{ <python-expression> }}`

Available variables:
- `env` - The value passed via `--env` flag (empty string if not specified)

### Complete Example

Here's the project.yaml from this project with explanations:

```yaml
# Base firmware to merge with application
firmware: ./build/firmware/8915DM_cat1_open_EG915UEUABR03A06M08_OCPU_QPY_01.300.01.300_merge.pac

# User filesystem deployment rules
usrfs:

# Entry point files - NOT compiled (QuecPython limitation)
- src: ./src
  glob: "*.py"
  dest: /usr
  # No compile: false, so files are copied as plain .py

# Factory configuration (always deployed)
- src: ./src/etc
  glob: 0-factory.ini
  dest: /usr/etc

# Development configuration (conditional deployment)
- src: ./src/etc
  glob: 9-dev-*.ini
  dest: /usr/etc
  when: ${{ env=="dev" }}  # Only deployed with --env=dev

# Portable application code (compiled)
- src: ./src/app/portable/
  glob: "**/*.py"
  dest: /usr/app
  compile: true

# Board-specific code (compiled)
- src: ./src/app/board/
  glob: "**/*.py"
  dest: /usr/app
  compile: true
```

### File Organization Best Practices

**Entry Points** (`/usr/main.py`, `/usr/boot.py`)
- Place in `./src/` directory
- Use `glob: "*.py"` without `compile: true`
- These bootstrap the application

**Application Code** (`/usr/app/`)
- Place in `./src/app/` directory
- Use `glob: "**/*.py"` with `compile: true`
- Compiled to `.mpy` for efficiency

**Configuration** (`/usr/etc/`)
- Place in `./src/etc/` directory
- NOT compiled (need to be readable text files)
- Use numbered prefixes for loading order (e.g., `0-factory.ini`, `9-dev.ini`)
- Higher numbers override lower numbers

**Conditional Deployment**
- Use `when:` expressions for environment-specific files
- Development configs: `when: ${{ env=="dev" }}`
- Production configs: `when: ${{ env=="production" }}`
- Test configs: `when: ${{ env in ["dev", "test"] }}`

### Version specification

The `build` command has a `--version` argument that allows specifing a version
string. It it recommented but not required to use [Semantic Versioning](https://semver.org/).

The version will be emitted into the generated `manifest.json` file and can 
be read at runtime.

**NOTE:** QuecPython tool `pacgen` would also support specifing a `--version` and
`--pversion` argument that may allow putting a version into the resulting binary.
Because we don't know the actual semantics of that fields yet, we leave them
untouched.

### Generated Files

During build, qpyt automatically generates:

**`/usr/manifest.json`** - File manifest with integrity hashes
```json
{
  "files": [
    {
      "path": "/usr/app/framework.mpy",
      "size": 4832,
      "hash": "sha256-AbCd123..."
    }
  ],
  "version": "1.0.0"
}
```

This file is used for:
- Verification of deployed files
- FOTA update integrity checks
- Deployment tracking

## qpyt Commands

### Prerequisites

**Python Version**: Requires Python 3.11 or higher

### Common Options

* `--project` - Path to project.yaml (default: `./project.yaml`)
* `--qpyt-dir` - Path to qpyt working directory (default: `.qpyt`)
* `--verbose` - Enable verbose output for debugging
* `--env` - Build environment for conditional configuration (e.g., `dev`, `staging`, `production`)

### Port handling

For the `--port` argument you can use:
* The port device name, like `COM11` or `/dev/ttyUSB0`
* A part of the port description, like `Quectel USB REPL Port`. This is the default
value. So if your board enumerates this description, you don't need to specify a 
port at all.
* Any valid [pyserial URL handler](https://pyserial.readthedocs.io/en/latest/url_handlers.html)

You can also set the `QPYT_PORT` environment variable to specify the port.

### download-tools

Download required Quectel build tools automatically. The tools are only required
if you build the .pac file, not for any other commands, or to build `--usrfs-only`.

```bash
qpyt download-tools [--verbose]
```

Downloads platform-specific tools to `.qpyt/tools/` directory:
- **Windows**: QPYcom_V3.9.0 (~170 MB)
- **Linux**: QPYcom_V3.0.1_Ubuntu24 (~170 MB)

Includes: `mpy-cross`, `mklfs`, `pacgen`, `dtools`, and FDL files.

### build

Build firmware package for flashing or app FOTA.

```bash
qpyt build [OPTIONS]
```

**Options**:
* `--version <version>` - Version string for the build (default: `develop`)
* `--env <environment>` - Build environment (default: empty)
* `--out-dir <path>` - Output directory (default: `.qpyt/out`)
* `--usrfs-only` - Only build the `usr.zip` file, skip firmware package
* `--verbose` - Show detailed build steps

**Output Files**:
- `usr.zip` - User filesystem for app FOTA updates
- `image.pac` - Complete firmware package for flashing (unless `--usrfs-only`)

**Example**:
```bash
# Build with version string
qpyt build --version 1.0.0 --env production

# Build only usr.zip for FOTA
qpyt build --usrfs-only
```

### watch

Deploy application with automatic hot reload on file changes.

```bash
qpyt watch [OPTIONS]
```

**Options**:
* `--port <port>` - Serial port (name or description, default: `Quectel USB REPL Port`)
* `--baud <rate>` - Baud rate (default: `115200`)
* `--env <environment>` - Build environment for conditional deployment
* `--verbose` - Show detailed deployment steps

**Behavior**:
1. Builds usr filesystem with `.py` to `.mpy` compilation
2. Syncs all files to the board
3. Performs soft reset
4. Monitors local files for changes
5. On change detection (2-second consolidation), redeploys and resets

**Example**:
```bash
# Watch with default port
qpyt watch

# Watch on specific port and env
qpyt watch --port COM3 --env dev

# Watch and use a remote port over RFC2217 (port-server)
qpyt watch --port 10.0.0.50:15612
```

Press `Ctrl+C` to stop watching.

### attach

Attach to the board's REPL terminal for interactive Python access. It contains
a terminal emulation supporting completions and history.

```bash
qpyt attach [OPTIONS]
```

**Options**:
* `--port <port>` - Serial port (default: `Quectel USB REPL Port`)
* `--baud <rate>` - Baud rate (default: `115200`)

**Usage**:
- Type Python commands and press Enter
- Press `Ctrl+C` once to interrupt running code
- Press `Ctrl+C` twice (within 1 second) to exit
- Press `Ctrl+D` for soft reboot while code is interrupted

**Example**:
```bash
qpyt attach
```

### cleanup

Delete all files in `/usr` on the board.

```bash
qpyt cleanup [OPTIONS]
```

**Options**:
* `--port <port>` - Serial port (default: `Quectel USB REPL Port`)
* `--baud <rate>` - Baud rate (default: `115200`)

**Example**:
```bash
qpyt cleanup
```

### port-server

Start an RFC 2217 serial port server to share the board over TCP/IP.

```bash
qpyt port-server [OPTIONS]
```

**Options**:
* `--port <port>` - Serial port to share (default: `Quectel USB REPL Port`)
* `--baud <rate>` - Baud rate (default: `115200`)
* `--listen-ip <ip>` - IP address to bind (default: `0.0.0.0`)
* `--listen-port <port>` - TCP port to listen on (default: `15612`)
* `--verbose` - Show detailed connection logs

**Use Case**: Share a board connected to one machine with other machines on the network.

**Example**:
```bash
# Start server on default port
qpyt port-server

# Start server on custom port
qpyt port-server --listen-port 2217
```

**Client Connection**:
```bash
# From another machine, connect using:
qpyt watch --port rfc2217://<server-ip>:15612
qpyt attach --port rfc2217://<server-ip>:15612
```

**NOTE:** `port-server` is adapted from the pyserial [rfc2217_server.py example](pyserialhttps://github.com/pyserial/pyserial/blob/master/examples/rfc2217_server.py)

## Development Workflow

### Hot Reload Development

The `watch` command provides a fully automated development experience:

```bash
qpyt watch
```

**Important**: Only one application can access the serial port at a time. Close other applications like QPYcom or the [VSCode QuecPython extension](https://marketplace.visualstudio.com/items?itemName=Quectel.qpy-ide) before running watch mode.

**What happens during watch mode**:

1. **Build** - Constructs usr filesystem in `.qpyt/temp/fs`, compiling `.py` to `.mpy`
2. **Sync** - Deploys all files to `/usr` on the board
3. **Reset** - Performs soft reset to restart the application
4. **Monitor** - Watches local files for changes
5. **Auto-deploy** - On file change (2-second consolidation delay):
   - Rebuilds changed files
   - Syncs to board
   - Performs soft reset
   - Continues monitoring

**Console Output**:
- All Python `print()` statements from the board
- Log messages from the application
- Error messages and stack traces

Press `Ctrl+C` to stop watch mode.
Note that currently the full terminal is just available in `attach`, not `watch`,
so you can't interrupt a program or enter REPL. This may be implemented in the
future.

## Flashing Firmware

Currently `qpyt` doens't support flashing the `.pac` image. To flash the built firmware 
package use the QFlash tool. 
See [Firmware Burning](https://developer.quectel.com/doc/quecpython/Application_guide/en/firmware-upgrade/firmware-burning.html) for other flashing tools.

**QFlash Settings**:
* **Port**: USB-AT Port (e.g., COM22)
* **Baud Rate**: 115200
* **Firmware File**: `.qpyt/out/firmware.pac` (output from `qpyt.py build`)

## CI/CD Integration

### GitHub Actions Workflow

The project includes a GitHub Actions workflow (`.github/workflows/build.yaml`) that automatically builds firmware on every push to main or manual trigger.

#### Workflow Features

**Automatic Versioning with GitVersion**
- Uses [GitVersion](https://gitversion.net/) to automatically calculate semantic versions
- Version is based on Git tags and commit history
- Configuration in `GitVersion.yml`
- Displays version in build summary and outputs

**Build Caching**
- Caches downloaded Quectel tools (~170 MB) using `actions/cache`
- Significantly speeds up builds after the first run
- Cache key: `${{ runner.os }}-tools`

**Automated Testing**
- Runs tests from `./src/tests/main.py` before building
- Build fails if tests fail, preventing bad builds

**Artifact Storage**
- Uploads build outputs to GitHub Artifacts
- Artifacts include:
  - `image.pac` - Complete firmware package
  - `usr.zip` - User filesystem for FOTA
  - `version.txt` - Build version information

#### Workflow Configuration

```yaml
name: firmware build

on:
  workflow_dispatch:  # Manual trigger
  push:
    branches:
      - main           # Automatic on main branch push

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      # Checkout with full history for GitVersion
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0

      # Setup Python 3.11+
      - uses: actions/setup-python@v5
        with:
          python-version: '>=3.11'

      - run: pip install -r requirements.txt

      # Cache Quectel tools to avoid re-downloading
      - name: Check for build tools in cache
        id: cache-tools
        uses: actions/cache@v4
        with:
          path: .qpyt/tools
          key: ${{ runner.os }}-tools

      - name: Install tools if not cached
        if: steps.cache-tools.outputs.cache-hit != 'true'
        run: qpyt download-tools


      # Build firmware with GitVersion
      - name: Build firmware
        run: |
          echo "Building"
          qpyt build --version "0.1"
      
      # Upload build artifacts
      - name: Upload artifacts
        uses: actions/upload-artifact@v5
        with:
          name: firmware
          path: .qpyt/out/*
```

#### Using Build Artifacts

After a successful build, download artifacts from:
- GitHub Actions run page → Artifacts section
- Or use GitHub CLI: `gh run download <run-id>`

Artifacts include:
- `image.pac` - Flash this to the board using QFlash
- `usr.zip` - Use for app FOTA updates
- `version.txt` - Version information for deployment tracking

#### Environment-Specific Builds

To build for different environments in CI/CD, modify the build step:

```yaml
# Development build
- name: Build firmware (dev)
  run: qpyt build --version "${{ steps.gitversion.outputs.fullSemVer }}" --env dev

# Production build
- name: Build firmware (production)
  run: qpyt build --version "${{ steps.gitversion.outputs.fullSemVer }}" --env production
```

This will deploy different configuration files based on the `when:` conditions in `project.yaml`.

#### Local Testing of CI Build

To test the CI build process locally:

```bash
# Install dependencies
pip install -r requirements.txt

# Download tools (cached in CI)
qpyt download-tools

# Run tests
cd ./src/tests
./main.py
cd ../..

# Build with a test version
qpyt build --version 1.0.0-test
```

## Background Information

Because of lacking documentation, the build process was reverse-engineered from QPYcom log files and procmon traces. To adapt for other boards or configurations, check the logfile written by QPYcom at `QPYcom_V3.9.0\logs\software\std`.
