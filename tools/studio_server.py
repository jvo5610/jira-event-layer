"""Local static editor + private API tunnel. Never exposes credentials to the browser."""
import argparse
import functools
import gzip
import json
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root, upstream, token, environment='Fett'):
        self.root = Path(root).resolve()
        self.upstream = upstream.rstrip('/')
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
        self.token = token
        self.environment = environment
        super().__init__(address, Handler)

    def server_close(self):
        self.client.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # No tokens, queries or payloads in access logs.

    def reply(self, status, data, content_type='application/json', immutable=False):
        if not isinstance(data, bytes): data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control','public, max-age=31536000, immutable' if immutable else 'no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if len(data)>512 and 'gzip' in self.headers.get('Accept-Encoding',''):
            data=gzip.compress(data);self.send_header('Content-Encoding','gzip');self.send_header('Vary','Accept-Encoding')
        self.send_header('Content-Length',str(len(data)));self.end_headers()
        if self.command!='HEAD': self.wfile.write(data)

    def handle_request(self):
        port=self.server.server_address[1]
        hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        origins={'http://'+host for host in hosts}
        if self.headers.get('Host') not in hosts or self.headers.get('Origin','') not in origins|{''} or self.headers.get('Sec-Fetch-Site','')=='cross-site':
            return self.reply(403,{'detail':'This editor only accepts requests from its local address.'})
        path=urlsplit(self.path).path
        if path=='/studio-config' and self.command=='GET':
            return self.reply(200,{'environment':self.server.environment,'mode':'remote','api':'infra/jira-event-layer-api','authentication':'local administrator gateway'})
        if path.startswith('/api/'):
            route=self.path[4:]
            if not (path.startswith('/api/v1/') or path in {'/api/metrics','/api/readyz','/api/healthz'}): return self.reply(404,{'detail':'Not found'})
            if self.command not in {'GET','POST'}: return self.reply(405,{'detail':'Method not allowed'})
            try: size=int(self.headers.get('Content-Length','0'))
            except ValueError: return self.reply(400,{'detail':'Invalid content length'})
            if size<0 or size>1048576 or self.headers.get('Transfer-Encoding'): return self.reply(413,{'detail':'Body exceeds the configured limit'})
            headers={'Authorization':'Bearer '+self.server.token,'Content-Type':self.headers.get('Content-Type','application/json')}
            if self.headers.get('Idempotency-Key'):headers['Idempotency-Key']=self.headers['Idempotency-Key']
            try:
                response=self.server.client.request(self.command,self.server.upstream+route,headers=headers,content=self.rfile.read(size) if size else None)
                return self.reply(response.status_code,response.content,response.headers.get('Content-Type','application/json'))
            except httpx.HTTPError:
                return self.reply(502,{'detail':'The private API connection is unavailable. Check the Fett tunnel.'})
        if self.command not in {'GET','HEAD'}:return self.reply(405,{'detail':'Method not allowed'})
        relative=unquote(path).lstrip('/') or 'index.html'
        target=(self.server.root/relative).resolve()
        if not target.is_relative_to(self.server.root):return self.reply(404,{'detail':'Not found'})
        if not target.is_file():
            if path.startswith('/assets/'):return self.reply(404,{'detail':'Not found'})
            target=self.server.root/'index.html'
        if not target.is_file():return self.reply(503,{'detail':'Build the frontend first: cd frontend && npm run build'})
        types={'.html':'text/html; charset=utf-8','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}
        return self.reply(200,target.read_bytes(),types.get(target.suffix,'application/octet-stream'),immutable=path.startswith('/assets/'))

    do_GET=handle_request
    do_HEAD=handle_request
    do_POST=handle_request
    do_PUT=handle_request
    do_DELETE=handle_request
    do_OPTIONS=handle_request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8091)
    parser.add_argument('--tunnel-port',type=int,default=58188)
    parser.add_argument('--ssm-parameter',default='/k8s/jira-event-layer')
    args=parser.parse_args()
    import boto3
    cfg=json.loads(boto3.client('ssm',region_name='us-east-1').get_parameter(Name=args.ssm_parameter,WithDecryption=True)['Parameter']['Value'])
    root=Path(__file__).resolve().parents[1]/'frontend'/'dist'
    if not (root/'index.html').is_file():raise SystemExit('Build the frontend first.')
    tunnel=subprocess.Popen(['kubectl','--context','fett','--request-timeout=10s','-n','infra','port-forward','svc/jira-event-layer-api',f'{args.tunnel_port}:80','--address','127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if tunnel.poll() is not None:raise RuntimeError('Private tunnel failed to start; inspect the selected port and kubectl access.')
            try:
                with socket.create_connection(('127.0.0.1',args.tunnel_port),timeout=.2):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Private tunnel startup timed out.')
        server=Gateway(('127.0.0.1',args.port),root,f'http://127.0.0.1:{args.tunnel_port}',cfg['ADMIN_TOKEN'])
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        print(f'Editor: http://127.0.0.1:{args.port} · API and database: Fett',flush=True)
        try:server.serve_forever()
        finally:server.server_close()
    finally:
        tunnel.terminate()
        try:tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait()


if __name__=='__main__':main()
