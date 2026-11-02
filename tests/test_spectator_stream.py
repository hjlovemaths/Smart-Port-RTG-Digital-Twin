from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'omniverse/scripts'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_spectator_configuration_shares_fixed_framebuffer():
 values={}
 from types import SimpleNamespace
 load('spectator_stream').configure_spectator(SimpleNamespace(set=values.__setitem__))
 base='/exts/omni.kit.livestream.app'
 assert values[base+'/primaryStream/allowDynamicResize'] is False
 assert values[base+'/spectatorStream/0/signalPort']==49200
 assert values[base+'/spectatorStream/0/streamPort']==48000
 assert values[base+'/spectatorStream/0/streamType']=='webrtc'

def test_launcher_has_unique_stream_ports():
 cmd=load('launch_kit_webrtc').build_command(Path('D:/Software/kit-app-template'),2560,1440,60,8011,49100,47998)
 assert '--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=false' in cmd
 assert '--/exts/omni.kit.livestream.app/spectatorStream/0/signalPort=49200' in cmd
