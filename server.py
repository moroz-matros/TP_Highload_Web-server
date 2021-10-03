import datetime
import os
import queue
import socket
import urllib
from functools import lru_cache
from urllib.parse import parse_qs, urlparse
from email.parser import Parser
import threading
import mimetypes

MAX_LINE = 64 * 1024
MAX_HEADERS = 100
DEFAULT_THREAD_NUM = 256

DEFAULT_DIR = '/var/www/html'

OK = "OK"
NOT_FOUND = "Not Found"
METHOD_NOT_ALLOWED = "Method Not Allowed"
FORBIDDEN = "Forbidden"

HOST = '0.0.0.0'
PORT = 80


class MyHTTPServer:
    def __init__(self, host, port, threads, document_root):
        print("starting server...")
        self._host = host
        self._port = port
        self._request_conns = queue.SimpleQueue()
        self._thread_live = True
        self._lock = threading.Lock()
        self._DIR = document_root
        self._N = threads

        cid = 0
        for i in range(self._N):
            t = threading.Thread(target=self.thread_init, args=(cid,), daemon=True)
            cid += 1
            t.start()
        print("done")

    def thread_init(self, cid):
        while self._thread_live:
            conn = self._request_conns.get()
            if conn:
                # print("Thread " + str(cid) + "got connection")
                self.handle_request(conn)
                conn.close()

    def serve_no_file(self, conn):
        headers = [('Content-Length', 0), ('Server', 'server'),
                   ('Date', datetime.datetime.now()), ('Connection', 'close')]
        resp = Response(404, NOT_FOUND, headers=headers)
        self.send_response(conn, resp)

    def serve_no_index(self, conn):
        headers = [('Content-Length', 0), ('Server', 'server'),
                   ('Date', datetime.datetime.now()), ('Connection', 'close')]
        resp = Response(403, FORBIDDEN, headers=headers)
        self.send_response(conn, resp)

    def serve_has_file(self, conn, path):
        size = os.path.getsize(self._DIR + path)
        t, _ = mimetypes.guess_type(path)
        headers = [('Content-Type', t),
                   ('Content-Length', size), ('Server', 'server'),
                   ('Date', datetime.datetime.now()), ('Connection', 'close')]
        resp = Response(200, 'OK', headers=headers)
        self.send_response(conn, resp)

    def handle_request(self, conn):
        req = self.parse_request(conn)
        new_path = urllib.parse.unquote(urllib.parse.urlparse(req.path).path)
        if req.method == "GET" or req.method == "HEAD":
            # go to upper levels
            if new_path.find('/../') != -1:
                headers = [('Content-Length', 0)]
                resp = Response(403, METHOD_NOT_ALLOWED, headers=headers)
                self.send_response(conn, resp)
            # directory
            elif new_path[-1] == '/' and new_path.find('.') == -1:
                try:
                    new_path = new_path+'index.html'
                    file = open(self._DIR + new_path, 'rb')
                except:
                    self.serve_no_index(conn)
                    req.rfile.close()
                    return

                self.serve_has_file(conn, new_path)
                if req.method == "GET":
                    conn.sendfile(file)
                req.rfile.close()
                file.close()
            else:
                # file
                try:
                    file = open(self._DIR+new_path, 'rb')
                except:
                    self.serve_no_file(conn)
                    req.rfile.close()
                    return
                self.serve_has_file(conn, new_path)
                if req.method == "GET":
                    conn.sendfile(file)
                req.rfile.close()
                file.close()
        else:
            headers = [('Content-Length', 0)]
            resp = Response(405, METHOD_NOT_ALLOWED, headers=headers)
            self.send_response(conn, resp)

    def serve_forever(self):
        serv_sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
            proto=0)

        try:
            serv_sock.bind((self._host, self._port))
            serv_sock.listen()

            while True:
                conn, _ = serv_sock.accept()
                self._request_conns.put(conn)
        finally:
            serv_sock.close()
            self._thread_live = False
            print("server down")

    def parse_request(self, conn):
        rfile = conn.makefile('rb')
        method, target, ver = self.parse_request_line(rfile)
        headers = self.parse_headers(rfile)
        return Request(method, target, ver, headers, rfile)

    def parse_request_line(self, rfile):
        raw = rfile.readline(MAX_LINE + 1)
        if len(raw) > MAX_LINE:
            raise HTTPError(400, 'Bad request',
                            'Request line is too long')

        req_line = str(raw, 'iso-8859-1')
        words = req_line.split()
        if len(words) != 3:
            raise HTTPError(400, 'Bad request',
                            'Malformed request line')

        method, target, ver = words
        if ver != 'HTTP/1.1' and ver != 'HTTP/1.0':
            raise HTTPError(505, 'HTTP Version Not Supported')
        return method, target, ver

    def parse_headers(self, rfile):
        headers = []
        while True:
            line = rfile.readline(MAX_LINE + 1)
            if len(line) > MAX_LINE:
                raise HTTPError(494, 'Request header too large')

            if line in (b'\r\n', b'\n', b''):
                break
            headers.append(line)
            if len(headers) > MAX_HEADERS:
                raise HTTPError(494, 'Too many headers')

        sheaders = b''.join(headers).decode('iso-8859-1')
        return Parser().parsestr(sheaders)

    def send_response(self, conn, resp):
        wfile = conn.makefile('wb')
        status_line = f'HTTP/1.1 {resp.status} {resp.reason}\r\n'
        wfile.write(status_line.encode('iso-8859-1'))

        if resp.headers:
            for (key, value) in resp.headers:
                header_line = f'{key}: {value}\r\n'
                wfile.write(header_line.encode('iso-8859-1'))
        wfile.write(b'\r\n')

        if resp.body:
            wfile.write(resp.body)

        wfile.flush()
        wfile.close()

    def send_error(self, conn, err):
        try:
            status = err.status
            reason = err.reason
            body = (err.body or err.reason).encode('utf-8')
        except:
            status = 500
            reason = b'Internal Server Error'
            body = b'Internal Server Error'
        resp = Response(status, reason,
                        [('Content-Length', len(body))],
                        body)
        self.send_response(conn, resp)


class Request:
    def __init__(self, method, target, version, headers, rfile):
        self.method = method
        self.target = target
        self.version = version
        self.headers = headers
        self.rfile = rfile

    @property
    def path(self):
        return self.url.path

    @property
    @lru_cache(maxsize=None)
    def query(self):
        return parse_qs(self.url.query)

    @property
    @lru_cache(maxsize=None)
    def url(self):
        return urlparse(self.target)

    def body(self):
        size = self.headers.get('Content-Length')
        if not size:
            return None
        return self.rfile.read(size)


class Response:
    def __init__(self, status, reason, headers=None, body=None):
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body


class HTTPError(Exception):
    def __init__(self, status, reason, body=None):
        super()
        self.status = status
        self.reason = reason
        self.body = body

def parse():
    threads = DEFAULT_THREAD_NUM
    document_root = DEFAULT_DIR
    try:
        file = open('/etc/httpd.conf', 'rb')
    except:
        return threads, document_root
    text = file.read().split()
    i = 1
    for word in text:
        if word == 'thread_limit':
            threads = text[i]
        elif word == 'document_root':
            document_root = text[i]
        i += 1
    return threads, document_root


if __name__ == '__main__':
    threads, document_root = parse()
    serv = MyHTTPServer(HOST, PORT, threads, document_root)

    try:
        serv.serve_forever()
    except KeyboardInterrupt:
        pass
