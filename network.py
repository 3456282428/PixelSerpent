"""联机对战网络层：基于 TCP 的 JSON 行协议（每条消息一行 JSON）。

架构：主机权威。建房方（NetHost）监听端口并接受一个客户端；
加入方（NetClient）主动连接主机。双方建立连接后：
  - 客户端 -> 主机：方向 / 加速 / 离开 等输入消息
  - 主机 -> 客户端：完整游戏状态快照
接收在后台线程进行，主线程通过 poll() 取消息，不阻塞渲染。
"""
import socket
import json
import sys
import threading
import queue

from config import NET_PORT

_CODE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _to_base36(n):
    s = ""
    while n:
        s = _CODE_ALPHABET[n % 36] + s
        n //= 36
    return s or "0"


def _from_base36(text):
    n = 0
    for ch in text.upper():
        i = _CODE_ALPHABET.find(ch)
        if i < 0:
            return None
        n = n * 36 + i
    return n


def make_room_code(ip):
    """把 IPv4 地址编码为 7 位房间码（端口固定 NET_PORT）"""
    try:
        parts = [int(p) for p in ip.split(".")]
        if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
            return ""
        ip_int = (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
    except Exception:
        return ""
    return _to_base36(ip_int).rjust(7, "0")


def decode_room_code(code):
    """房间码还原为 IPv4 地址，非法返回 None"""
    n = _from_base36(code.strip())
    if n is None or n < 0 or n > 0xFFFFFFFF:
        return None
    return f"{n >> 24}.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"


def parse_join_target(text):
    """解析加入目标：支持房间码或 IP[:端口]，失败返回 None"""
    text = text.strip().upper()
    if not text:
        return None
    port = NET_PORT
    if ":" in text:
        ip_part, port_part = text.rsplit(":", 1)
        if port_part.isdigit():
            text = ip_part
            port = int(port_part)
    if text.count(".") == 3:
        try:
            parts = [int(p) for p in text.split(".")]
            if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
                return (text, port)
        except Exception:
            return None
        return None
    ip = decode_room_code(text)
    return (ip, port) if ip else None



class _Conn:
    """一条 TCP 连接 + 后台读线程 + 消息队列"""

    def __init__(self):
        self.sock = None
        self.alive = False
        self._recv_q = queue.Queue()
        self._send_lock = threading.Lock()
        self._thread = None

    def _start_reader(self):
        self.alive = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        buf = b""
        try:
            while True:
                data = self.sock.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._recv_q.put(json.loads(line.decode("utf-8")))
                    except Exception:
                        pass
        except Exception:
            pass
        self.alive = False

    def send(self, obj):
        """发送一个 JSON 对象，连接已断开时返回 False"""
        if not self.alive:
            return False
        try:
            data = (json.dumps(obj) + "\n").encode("utf-8")
            with self._send_lock:
                self.sock.sendall(data)
            return True
        except Exception:
            self.alive = False
            return False

    def poll(self):
        """取出所有已到达的消息（列表，可能为空）"""
        msgs = []
        while True:
            try:
                msgs.append(self._recv_q.get_nowait())
            except queue.Empty:
                break
        return msgs

    def close(self):
        self.alive = False
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class NetHost(_Conn):
    """建房方：监听端口，等待一个客户端加入"""

    def __init__(self, port):
        super().__init__()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            # Windows 下 SO_REUSEADDR 语义不同：允许重复绑定同一端口（端口劫持），
            # 会导致“端口被占用”检测失效；SO_EXCLUSIVEADDRUSE 保证独占绑定。
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(1)
        # 非阻塞：等待阶段每帧都会调用 try_accept，带超时的 accept 会把主线程
        # 阻塞到下一次超时（0.1s 超时即 8 FPS），严重拖低游戏帧率。
        self.listener.setblocking(False)

    def try_accept(self):
        """非阻塞尝试接受连接，成功返回 True（每帧调用一次）"""
        if self.alive:
            return True
        try:
            conn, _addr = self.listener.accept()
        except socket.timeout:
            return False
        except Exception:
            return False
        # Windows 下 accept 出的连接会继承监听套接字的非阻塞模式，
        # 后台读线程遇到暂时无数据就会抛 BlockingIOError 退出，这里显式恢复阻塞。
        conn.setblocking(True)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = conn
        # 只服务一个客户端：接受后立即关闭监听，
        # 后续加入者会立刻收到 connection refused，而不是挂在 backlog 里无限等待
        try:
            self.listener.close()
        except Exception:
            pass
        self._start_reader()
        return True

    def close(self):
        super().close()
        try:
            self.listener.close()
        except Exception:
            pass


class NetClient(_Conn):
    """加入方：主动连接主机（连接失败抛异常）"""

    def __init__(self, ip, port, timeout=3.0):
        super().__init__()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = sock
        self._start_reader()


def get_local_ip():
    """获取本机局域网 IP，失败返回 127.0.0.1"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
