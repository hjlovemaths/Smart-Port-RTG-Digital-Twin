"""Local IECS test peer for one Twin unload, using the same HTTP device link."""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TestPeer:
    __test__ = False

    def __init__(self, port=48732, *, allow_landing=True):
        self.allow_landing = allow_landing
        self.messages = []
        self.updated = threading.Condition()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path not in ('/process','/landBoxApply'):
                    self.send_error(404)
                    return
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with peer.updated:
                    peer.messages.append(data)
                    peer.updated.notify_all()
                response = {'success': True}
                if self.path == '/landBoxApply':
                    response['cntrReleaseFlag'] = '1' if peer.allow_landing else '0'
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.1},daemon=True).start()

    def wait_callback(self, pid, timeout, **fields):
        def matching():
            return next((m for m in self.messages if m['pid']==pid and
                         all(m.get(key)==value for key,value in fields.items())),None)
        with self.updated:
            if not self.updated.wait_for(matching,timeout=timeout):
                raise TimeoutError(f'等待 PID {pid} {fields} 超时')
            return matching()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def unload(peer, address, token='', timeout=180, *, box='TWIN000001', log=print):
    address = address.rstrip('/')
    headers = {'Content-Type':'application/json','X-IECS-Token':token}

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        try:
            with urlopen(Request(address+path,data=data,headers=headers),timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(exc.read().decode()) from exc

    def wait_state(condition):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            state = request('/health')
            if state.get('fault'):
                raise RuntimeError(state['fault'])
            if condition(state):
                return state
            time.sleep(.12)
        raise TimeoutError(f'Twin 未到预期阶段：{state}')

    state = wait_state(lambda data:data.get('ready'))
    source, target = state['sample']['ship_slot'],state['sample']['yard_slot']
    if not target:
        raise RuntimeError('Twin 场景没有建议的空目标箱位')
    vehicle = state['vehicle']

    def send(kind, pid, **fields):
        return request('/process',{
            'pid':pid,'machType':kind,'machNo':state[kind.lower()],
            'curTime':str(time.time_ns()//1_000_000),**fields,
        })

    qc_task, rtg_task, vehicle_task, tos_task = 'QC-001','RTG-001','TRUCK-001','TOS-001'
    qc = {'taskId':qc_task,'tosTaskId':tos_task,'taskType':1,'soaTaskType':1,'soaWorkType':'DSCH',
          'soaWorkflow':1,'oisVlocation':source,'soaCntrnoHb':box,'vehicleType':'ITK'}
    send('QC',114,taskList=[qc])
    assert send('QC',114,taskList=[qc])['duplicate']
    peer.wait_callback(115,timeout,taskId=qc_task)
    send('QC',146,taskId=qc_task,receiptType=0,updateStatus=1)
    send('QC',120,vehicleNo=vehicle,taskId=vehicle_task,reportStatus=0,reportType=1)
    log(f'桥吊接单，集卡驶往船箱位 {source}')
    wait_state(lambda data:data['qc_ready'])
    send('QC',123,vehicleNo=vehicle,taskId=vehicle_task,cmdIdx=1,cmdStatus=1)
    guide = peer.wait_callback(117,timeout)
    send('QC',123,vehicleNo=vehicle,taskId=vehicle_task,cmdIdx=guide['sotTruckCmdIdx'],cmdStatus=3)
    peer.wait_callback(173,timeout,cntrNo=box)
    if not peer.allow_landing:
        return
    send('RTG',211,taskList=[{'taskId':rtg_task,'tosTaskId':tos_task,'taskType':0,'soaTaskType':0,
                             'soaWorkType':'DSCH','soaCntrnoHb':box,'soaCntrSize':'20',
                             'vehicleNo':vehicle,'soaTruckType':'ITK','soaTcntrPos':target,
                             'soaTgtbayMap':'000000'}])
    peer.wait_callback(212,timeout,taskId=rtg_task)
    send('RTG',229,taskId=rtg_task,receiptType=0,updateStatus=1)
    peer.wait_callback(116,timeout,taskStatus='2',departureStatus=1)
    send('RTG',219,vehicleNo=vehicle,taskId=vehicle_task,reportStatus=0)
    log(f'桥吊放行，集卡载箱驶往 {target}')
    wait_state(lambda data:data['rtg_ready'])
    send('RTG',220,vehicleNo=vehicle,taskId=vehicle_task,rtgTaskId=rtg_task,cmdIdx=1,cmdStatus=1)
    guide = peer.wait_callback(214,timeout)
    send('RTG',220,vehicleNo=vehicle,taskId=vehicle_task,rtgTaskId=rtg_task,cmdIdx=guide['sotTruckCmdIdx'],cmdStatus=3)
    move = peer.wait_callback(234,timeout,rtgState=2)
    send('RTG',235,rtgState=2,moveType=0,usableRtgTargetBay=move['rtgTargetPosition'])
    peer.wait_callback(213,timeout,taskStatus='3',realOisYlocation=target)
    peer.wait_callback(225,timeout,taskId=rtg_task)
    send('RTG',226,taskId=rtg_task,vehicleNo=vehicle,cntrNo=box)
    finished = wait_state(lambda data:data['status']=='completed')
    log(f'卸船入场完成，{box} 已落在 {finished["location"]}，集卡已归位')
    return finished


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--twin', default='http://127.0.0.1:48731')
    parser.add_argument('--port', type=int, default=48732)
    parser.add_argument('--token', default='')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    peer = TestPeer(args.port)
    try:
        unload(peer,args.twin,args.token,args.timeout)
    finally:
        peer.close()
