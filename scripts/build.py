"""Build the dependency-free C++17 engine with the installed native compiler."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build" / ("backnest.exe" if os.name == "nt" else "backnest")


def build(force=False):
    source = ROOT / "core" / "main.cpp"
    if not force and BINARY.exists() and BINARY.stat().st_mtime >= max(p.stat().st_mtime for p in (ROOT / 'core').glob('*') if p.suffix in ('.cpp', '.hpp')):
        return BINARY
    BINARY.parent.mkdir(exist_ok=True)
    if os.name == "nt":
        compiler = shutil.which("cl")
        if not compiler:
            vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
            if vswhere.exists():
                install = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip()
                setup = Path(install) / 'VC/Auxiliary/Build/vcvars64.bat'
                if setup.exists():
                    env = subprocess.check_output(['cmd.exe', '/s', '/c', f'""{setup}" >nul && set"'], text=True, errors='replace')
                    for line in env.splitlines():
                        if '=' in line and not line.startswith('='):
                            key, value = line.split('=', 1)
                            os.environ[key] = value
                    compiler = shutil.which('cl')
        if not compiler:
            # Also support installations whose Installer/vswhere was removed.
            base = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
            candidates = sorted(base.glob('Microsoft Visual Studio/*/*/VC/Tools/MSVC/*'), reverse=True)
            kits = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Windows Kits/10'
            sdks = sorted((kits / 'Include').glob('*'), reverse=True)
            if candidates and sdks:
                vc, sdk = candidates[0], sdks[0].name
                compiler = str(vc / 'bin/Hostx64/x64/cl.exe')
                os.environ['PATH'] = str(vc / 'bin/Hostx64/x64') + os.pathsep + str(kits / 'bin' / sdk / 'x64') + os.pathsep + os.environ['PATH']
                os.environ['INCLUDE'] = ';'.join(map(str, [vc / 'include'] + [kits / 'Include' / sdk / p for p in ('ucrt', 'shared', 'um', 'winrt')]))
                os.environ['LIB'] = ';'.join(map(str, [vc / 'lib/x64'] + [kits / 'Lib' / sdk / p / 'x64' for p in ('ucrt', 'um')]))
        if not compiler:
            raise RuntimeError("Install Visual Studio C++ build tools and Windows SDK.")
        command = [compiler, "/nologo", "/std:c++17", "/EHsc", "/utf-8", "/W4", str(source),
                   "/Fe:" + str(BINARY), "/Fo:" + str(BINARY.parent / "backnest.obj")]
    else:
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler:
            raise RuntimeError("Install a C++17 compiler (Xcode Command Line Tools on macOS).")
        command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic", str(source), "-o", str(BINARY)]
    subprocess.run(command, check=True, cwd=ROOT)
    return BINARY


if __name__ == "__main__":
    print(build(force=True))
