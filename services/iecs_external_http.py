"""HTTP transport for a Twin acting as a QC and an RTG on a configured IECS link."""

import hmac
import ipaddress
import json
import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


@dataclass(frozen=True)
class ExternalConfig:
    bind: str = '127.0.0.1'
    port: int = 48731
    callback: str = 'http://127.0.0.1:48732'
    peer: str = '127.0.0.1'
    qc: str = 'STS_01'
    rtg: str = 'RTG'
    vehicle: str = 'ITT-001'
    truck_number: int = 1
    token: str = ''

    @classmethod
    def environment(cls):
        callback = os.getenv('SMARTPORT_IECS_CALLBACK', cls.callback).rstrip('/')
        url = urlsplit(callback)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError('IECS 回调地址应为 HTTP(S) 源地址，例如 http://127.0.0.1:48732')
        if url.port == 0:
            raise ValueError('IECS 回调端口不能为 0')
        bind = str(ipaddress.IPv4Address(os.getenv('SMARTPORT_IECS_BIND', cls.bind)))
        peer = str(ipaddress.IPv4Address(os.getenv('SMARTPORT_IECS_PEER', url.hostname)))
        if bind == '0.0.0.0':
            raise ValueError('请指定 Twin 的实际监听 IP')
        port = int(os.getenv('SMARTPORT_IECS_PORT', cls.port))
        truck = int(os.getenv('SMARTPORT_IECS_TRUCK_NUMBER', cls.truck_number))
        if not 1 <= port <= 65535 or not 1 <= truck <= 20:
            raise ValueError('IECS 端口应在 1–65535，场景集卡编号应在 1–20')
        names = [os.getenv('SMARTPORT_IECS_'+key, default).strip()
                 for key, default in (('QC', cls.qc), ('RTG', cls.rtg), ('VEHICLE', cls.vehicle))]
        if any(not name or len(name)>64 for name in names):
            raise ValueError('IECS 映射编号应为 1–64 个字符')
        return cls(bind, port, callback, peer, *names, truck, os.getenv('SMARTPORT_IECS_TOKEN', ''))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TrafficLog:
    """Bounded, per-link HTTP exchanges. Headers are kept out of the log."""

    def __init__(self):
        self.lock = threading.Lock()
        self.business = deque(maxlen=100)
        self.telemetry = deque(maxlen=30)

    def begin(self, direction, path, request, payload):
        fields = payload if isinstance(payload, dict) else {}
        pid = fields.get('pid')
        message = {'id': uuid4().hex, 'time': time.time(), 'direction': direction, 'path': path,
                   'pid': pid if type(pid) is int else None, 'machine': str(fields.get('machNo',''))[:64],
                   'status': None, 'error': '', 'pending': True, 'attempts': 0,
                   'request': request, 'response': None}
        with self.lock:
            group = self.telemetry if pid in (129,156,215,243) else self.business
            group.append(message)
        return message

    def finish(self, message, status, response, error='', attempts=1):
        with self.lock:
            message.update(status=status,response=response,error=error,pending=False,attempts=attempts)

    def read(self, message_id='', direction='', telemetry=False):
        with self.lock:
            entries = [*self.business, *self.telemetry] if telemetry or message_id else list(self.business)
            if message_id:
                message = next((entry for entry in entries if entry['id']==message_id),None)
                return dict(message) if message else None
            entries.sort(key=lambda entry:entry['time'],reverse=True)
            return [{k:v for k,v in entry.items() if k not in ('request','response')}
                    for entry in entries if not direction or entry['direction']==direction]


class ExternalHTTP:
    def __init__(self, config, snapshot):
        self.config = config
        self.incoming = queue.Queue(maxsize=32)
        self.outgoing = queue.Queue(maxsize=64)
        self.results = queue.SimpleQueue()
        self.traffic = TrafficLog()
        self.stopping = threading.Event()
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())
        transport = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                self.request.settimeout(5)
                super().setup()

            def reply(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                if hasattr(self,'exchange'):
                    transport.traffic.finish(self.exchange,status,data.decode('utf-8'))
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                if self.client_address[0] != config.peer:
                    return False
                return not config.token or hmac.compare_digest(
                    self.headers.get('X-IECS-Token', '').encode(), config.token.encode())

            def do_GET(self):
                if not self.authorized():
                    return self.reply(403, {'success': False})
                if self.path != '/health':
                    return self.reply(404, {'success': False})
                self.reply(200, snapshot())

            def do_POST(self):
                if not self.authorized():
                    return self.reply(403, {'success': False})
                if self.path != '/process':
                    return self.reply(404, {'success': False})
                raw = b''
                payload = None
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise ValueError('Invalid request length')
                    raw = self.rfile.read(length)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError('Expected a JSON object')
                except (ValueError, OSError, RecursionError) as exc:
                    self.exchange = transport.traffic.begin('received',self.path,raw.decode('utf-8','replace'),payload)
                    return self.reply(400, {'success': False, 'message': str(exc)})
                self.exchange = transport.traffic.begin('received',self.path,raw.decode('utf-8','replace'),payload)
                result = Future()
                try:
                    if transport.stopping.is_set():
                        raise queue.Full
                    transport.incoming.put_nowait((payload, result))
                    status, response = result.result(timeout=8)
                    self.reply(status, response)
                except (queue.Full, TimeoutError):
                    result.cancel()
                    self.reply(503, {'success': False, 'message': 'Twin is busy. Retry the same message.'})

        self.server = ThreadingHTTPServer((config.bind, config.port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self._work, name='IECS callbacks', daemon=True).start()
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1},
                         name='IECS device API', daemon=True).start()

    def send(self, path, body):
        self.outgoing.put_nowait((path, body))

    def _work(self):
        while not self.stopping.is_set():
            try:
                path, body = self.outgoing.get(timeout=.2)
            except queue.Empty:
                continue
            response, error = None, None
            headers = {'Content-Type': 'application/json'}
            if self.config.token:
                headers['X-IECS-Token'] = self.config.token
            encoded = json.dumps(body, ensure_ascii=False)
            request = Request(self.config.callback + path, encoded.encode(),
                              headers=headers, method='POST')
            exchange = self.traffic.begin('sent',path,encoded,body)
            telemetry = body['pid'] in (129,215)
            attempts = 1 if telemetry else 3
            for attempt in range(attempts):
                if self.stopping.is_set():
                    return
                status, received = None, None
                try:
                    with self.opener.open(request, timeout=.7 if telemetry else 2) as reply:
                        status = reply.status
                        raw = reply.read(65537)
                        received = raw[:65536].decode('utf-8','replace')
                        if len(raw) > 65536:
                            raise ValueError('回执响应体过大')
                        if path == '/landBoxApply':
                            response = json.loads(raw)
                        else:
                            # Some deployed IECS endpoints return an empty body for successful receipt.
                            if raw.lstrip().startswith(b'{'):
                                response = json.loads(raw)
                                if response.get('success') is False:
                                    raise ValueError('IECS 拒绝该回报')
                    error = None
                    break
                except (OSError, ValueError, RecursionError, HTTPException) as exc:
                    error = str(exc)
                    if isinstance(exc, HTTPError):
                        status = exc.code
                        with exc:
                            try:
                                received = exc.read(65536).decode('utf-8','replace')
                            except (OSError, HTTPException):
                                pass
                    if isinstance(exc, (ValueError, RecursionError)) or isinstance(exc, HTTPError) and exc.code < 500:
                        break
                    if attempt+1 < attempts:
                        self.stopping.wait(.35 * (attempt + 1))
            self.traffic.finish(exchange,status,received,error or '',attempt+1)
            self.results.put((body, response, error))

    def close(self):
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        while True:
            try:
                _, result = self.incoming.get_nowait()
            except queue.Empty:
                break
            if result.set_running_or_notify_cancel():
                result.set_result((503, {'success': False, 'message': 'IECS mode stopped'}))
