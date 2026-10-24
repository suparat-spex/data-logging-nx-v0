# PyInstaller recipe for NX Logger (one-folder build: fast start, fewer antivirus false alarms).
#   python -m PyInstaller packaging/nxlogger.spec --noconfirm      (or run packaging/build.ps1)
# Output: dist/NX Logger/NX Logger.exe
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent
PKG = ROOT / "src" / "nxlogger"
init = (PKG / "__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__ = "([^"]+)"', init).group(1)
APP = re.search(r'APP_NAME = "([^"]+)"', init).group(1)
v4 = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])

version_info = VSVersionInfo(   # what Windows shows in EXE Properties -> Details
    ffi=FixedFileInfo(filevers=v4, prodvers=v4),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "SPEX"), StringStruct("FileDescription", APP),
        StringStruct("FileVersion", VERSION), StringStruct("ProductName", APP),
        StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", f"{APP}.exe"),
        StringStruct("LegalCopyright", "SPEX")])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])])])

a = Analysis(
    [str(PKG / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(PKG / "resources"), "nxlogger/resources")],      # paths.resource_dir() looks here
    # connectors load their libraries on first use: pymodbus / asyncua import parts dynamically
    hiddenimports=["PySide6.QtSvg", "serial", "serial.tools.list_ports", "asyncua.sync"]
                  + collect_submodules("pymodbus") + collect_submodules("nxlogger.comms.drivers"),
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP,
    icon=str(PKG / "resources" / "icons" / "app" / "nxlogger.ico"),
    version=version_info,
    console=False,               # GUI app: no console window
    upx=False,                   # UPX-packed EXEs trigger more antivirus false positives
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP, upx=False)
