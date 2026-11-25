"""Ship read-only collectors without installing files on the monitored host."""
import base64
import json
from pathlib import Path
import zlib


def build_remote_source(source=None, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    files = {'process_snapshot': (root / 'process_snapshot.py').read_text(),
             'resource_probe': (root / 'resource_probe.py').read_text(),
             'remote_probe': source if source is not None else (root / 'remote_probe.py').read_text()}
    payload = base64.b64encode(zlib.compress(json.dumps(files).encode(), 9)).decode('ascii')
    return (
        'import base64,json,sys,types,zlib\n'
        '_orbit_sources=json.loads(zlib.decompress(base64.b64decode(' + repr(payload) + ')))\n'
        'for _orbit_name in ("process_snapshot","resource_probe"):\n'
        ' _orbit_module=types.ModuleType(_orbit_name)\n'
        ' _orbit_module.__file__=_orbit_name+".py"\n'
        ' sys.modules[_orbit_name]=_orbit_module\n'
        ' exec(compile(_orbit_sources[_orbit_name],_orbit_module.__file__,"exec"),_orbit_module.__dict__)\n'
        '__file__="remote_probe.py"\n'
        'exec(compile(_orbit_sources["remote_probe"],__file__,"exec"),globals())\n'
    )
