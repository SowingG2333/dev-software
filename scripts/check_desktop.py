"""Windows acceptance: real QWebChannel -> C++ adapter -> engine -> disk.

Uses an isolated temporary workspace and no mouse automation. Requires the same
Qt SDK as desktop.py. Independent SHA-256 comparison follows the Qt process.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from build import ROOT, build

sys.path.insert(0, str(ROOT))
from server import DemoServer


def main():
    if os.name != 'nt':
        raise RuntimeError('This acceptance runner targets Windows.')
    qt = ROOT / '.venv/Qt/6.5.3/msvc2019_64'
    build(force=True)  # Initializes the installed MSVC environment as well.
    output = ROOT / 'build-cmake'
    output.mkdir(exist_ok=True)
    modules = ['Core', 'Gui', 'Widgets', 'Network', 'WebChannel', 'WebEngineCore', 'WebEngineWidgets']
    includes = [qt / 'include', output] + [qt / 'include' / ('Qt' + m) for m in modules]
    subprocess.run([str(qt / 'bin/moc.exe'), *['-I' + str(p) for p in includes],
                    str(ROOT / 'desktop/main.cpp'), '-o', str(output / 'main.moc')], check=True)
    binary = output / 'desktop-check.exe'
    subprocess.run([shutil.which('cl'), '/nologo', '/std:c++17', '/Zc:__cplusplus', '/permissive-',
                    '/EHsc', '/utf-8', '/MD', '/O2', '/DQT_NO_DEBUG', *['/I' + str(p) for p in includes],
                    str(ROOT / 'tests/desktop_smoke.cpp'), '/Fe:' + str(binary),
                    '/Fo:' + str(output / 'desktop-check.obj'), '/link', '/LIBPATH:' + str(qt / 'lib'),
                    *['Qt6' + m + '.lib' for m in modules]], check=True)
    with tempfile.TemporaryDirectory(prefix='backnest-qt-') as temporary:
        root = Path(temporary).resolve()
        source = root / 'source'
        (source / '中文目录' / '空目录').mkdir(parents=True)
        (source / '中文目录' / '数据.txt').write_bytes(b'abc' * 50000)
        (source / 'empty.txt').touch()
        (source / 'skip.bin').write_bytes(b'excluded')
        target = root / 'restored'
        config = {'source': str(source), 'destination': str(root / 'versions'),
                  'options': {'filters': {'paths': {'exclude': ['*.bin']}},
                              'compression': {'algorithm': 'rle-block-v1'}}}
        script = (ROOT / 'desktop-transport.js').read_text(encoding='utf-8')
        script += '''
(async()=>{try{
 const invoke=await connectQtTransport();
 if(!invoke)throw Error('Qt transport unavailable');
 async function job(op,body){
  const accepted=await invoke(op,{body});
  for(let i=0;i<500;i++){
   const value=await invoke('jobs.get',{path:{jobId:accepted.id}});
   if(value.state==='completed')return value.result;
   if(value.state!=='running')throw Error(JSON.stringify(value));
   await new Promise(r=>setTimeout(r,20));
  }throw Error('Job timeout');
 }
 const config=CONFIG;
 const preview=await invoke('filters.preview',{body:{source:config.source,filters:config.options.filters}});
 if(preview.files!==2)throw Error('Preview mismatch');
 const result=await job('backup.start',config);
 if(!result.path.endsWith('.bka'))throw Error('Container not produced');
 await job('snapshots.verify',{snapshot:result.path});
 await job('restore.start',{snapshot:result.path,destination:TARGET});
 const workspace=await invoke('workspace.load',{});
 await invoke('workspace.save',{body:{revision:workspace.revision,state:{schemaVersion:2,tasks:[],logs:[],settings:{qtChecked:true}}}});
 const saved=await invoke('workspace.load',{});
 if(!saved.state.settings.qtChecked)throw Error('Workspace not saved');
 console.log('BACKNEST_CHECK:PASS');
}catch(error){console.log('BACKNEST_CHECK:FAIL '+error.stack)}})();
'''.replace('CONFIG', json.dumps(config)).replace('TARGET', json.dumps(str(target)))
        page = root / 'check.html'
        page.write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><script>' + script + '</script>', encoding='utf-8')
        server = DemoServer(0, root / 'workspace')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**os.environ, 'PATH': str(qt / 'bin') + os.pathsep + os.environ['PATH'],
                   'QT_PLUGIN_PATH': str(qt / 'plugins')}
            subprocess.run([str(binary), f'http://127.0.0.1:{server.server_port}/', str(page)],
                           env=env, check=True, timeout=75)
            def inventory(folder):
                return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                        if p.is_file() else None for p in folder.rglob('*') if p.name != 'skip.bin'}
            assert inventory(source) == inventory(target), 'Independent content comparison failed'
            assert not (target / 'skip.bin').exists(), 'Excluded file was restored'
            print('PASS: Qt WebChannel, compressed backup, verify, restore, persistence, independent SHA-256')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    main()
