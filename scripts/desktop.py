"""Launch the Qt C++ shell and its private loopback application service."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import shutil
from build import ROOT, build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qt', type=Path, default=ROOT / '.venv/Qt/6.5.3/msvc2019_64')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.backnest')
    args = parser.parse_args()
    qt = args.qt.resolve()
    binary = ROOT / 'build-cmake' / ('backnest-desktop.exe' if os.name == 'nt' else 'backnest-desktop')
    if args.build or not binary.exists():
        build(force=True)  # Also establishes the MSVC environment on Windows.
        if os.name == 'nt':
            # Direct argv avoids NMake/response-file codepage problems in Chinese paths.
            binary.parent.mkdir(exist_ok=True)
            modules = ['Core', 'Gui', 'Widgets', 'Network', 'WebChannel', 'WebEngineCore', 'WebEngineWidgets']
            includes = [qt / 'include', binary.parent] + [qt / 'include' / ('Qt' + m) for m in modules]
            source = ROOT / 'desktop/main.cpp'
            subprocess.run([str(qt / 'bin/moc.exe'), *['-I' + str(p) for p in includes], str(source), '-o', str(binary.parent / 'main.moc')], check=True)
            subprocess.run([shutil.which('cl'), '/nologo', '/std:c++17', '/Zc:__cplusplus', '/permissive-', '/EHsc', '/utf-8', '/MD', '/O2', '/DQT_NO_DEBUG',
                            *['/I' + str(p) for p in includes], str(source), '/Fe:' + str(binary), '/Fo:' + str(binary.parent / 'desktop.obj'),
                            '/link', '/LIBPATH:' + str(qt / 'lib'), *['Qt6' + m + '.lib' for m in modules]], check=True)
        else:
            bundled = ROOT / '.venv/bin/cmake'
            cmake = str(bundled) if bundled.exists() else 'cmake'
            subprocess.run([cmake, '-S', str(ROOT), '-B', str(ROOT / 'build-cmake'), '-DBACKNEST_DESKTOP=ON',
                            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_PREFIX_PATH=' + str(qt)], check=True)
            subprocess.run([cmake, '--build', str(ROOT / 'build-cmake'), '--parallel', '4'], check=True)
    if args.build_only:
        return
    env = {**os.environ, 'PATH': str(qt / 'bin') + os.pathsep + os.environ['PATH'], 'QT_PLUGIN_PATH': str(qt / 'plugins')}
    service = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0', '--data-dir', str(args.data_dir), '--desktop-parent'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', env=env)
    try:
        line = service.stdout.readline()
        if 'http://127.0.0.1:' not in line: raise RuntimeError('Application service did not start: ' + line)
        url = 'http://127.0.0.1:' + line.split('http://127.0.0.1:', 1)[1].strip()
        subprocess.run([str(binary), '--url', url], check=True, env=env)
    finally:
        if service.poll() is None:
            service.stdin.write('stop\n'); service.stdin.flush()
            service.wait()  # Backend cooperatively cancels and joins jobs before closing SQLite.


if __name__ == '__main__':
    main()
