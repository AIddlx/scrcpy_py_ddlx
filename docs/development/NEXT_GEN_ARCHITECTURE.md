# 下一代架构设计 (Scrcpy Assistant)

> **状态**: 设想阶段
> **日期**: 2026-02-18
> **目标**: 构建一个**安全优先**、可扩展的 Android 设备远程控制框架

---

## 0. 核心原则

### ⚠️ 安全第一

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   所有连接必须加密，不传输任何明文数据                         │
│                                                             │
│   ✅ 强制: 控制通道加密 (TLS 1.3)                            │
│   ✅ 强制: 消息负载加密 (AES-256-GCM)                        │
│   ✅ 强制: 媒体通道加密 (DTLS/SRTP)                          │
│   ✅ 强制: 配对认证 (PIN + 证书固定)                         │
│                                                             │
│   ❌ 禁止: 明文传输任何用户数据                              │
│   ❌ 禁止: 跳过认证                                         │
│   ❌ 禁止: 弱加密算法                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 加密覆盖范围

| 通道 | 加密方式 | 说明 |
|-----|---------|------|
| TCP 控制 | TLS 1.3 | 强制，无条件加密 |
| UDP 视频 | DTLS / SRTP | 强制，媒体加密 |
| UDP 音频 | DTLS / SRTP | 强制，媒体加密 |
| UDP 发现 | 预共享密钥 | 强制，防止伪造 |

---

## 1. 设计目标

### 1.1 现有问题

| 问题 | 说明 |
|-----|------|
| **无加密** | 所有通信明文传输，严重安全风险 |
| 通道单一 | 三个通道挤在一起，扩展困难 |
| 单向控制 | PC → 设备，设备无法主动推送消息 |
| 无文件传输 | 截图/文件需要额外处理 |
| 无事件订阅 | 状态变化需要轮询，效率低 |

### 1.2 设计目标

- **🔒 安全性**: 全链路加密 + 强制认证，零明文
- **可扩展**: 插件式服务架构
- **双向通信**: 设备可主动推送事件
- **功能丰富**: 通知、文件、传感器、通话等

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                          PC 客户端 (Python)                          │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │视频/音频 │ │ 控制器  │ │ 通知器  │ │ 文件器  │ │ 状态器  │       │
│  │ Client  │ │ Client  │ │ Client  │ │ Client  │ │ Client  │       │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘       │
│       │           │           │           │           │             │
│       └───────────┴───────────┼───────────┴───────────┘             │
│                               │                                      │
│                    ┌──────────┴──────────┐                          │
│                    │    Session Manager   │                          │
│                    │  (会话/加密/重连)     │                          │
│                    └──────────┬──────────┘                          │
└───────────────────────────────┼─────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
       ┌────┴────┐        ┌─────┴─────┐       ┌────┴────┐
       │ UDP 媒体 │        │ TCP 控制   │       │ UDP 发现 │
       │27185/27186│       │   27184   │       │  27183  │
       └────┬────┘        └─────┬─────┘       └────┬────┘
            │                   │                   │
════════════╪═══════════════════╪═══════════════════╪══════════════════
            │                   │                   │
       ┌────┴────┐        ┌─────┴─────┐       ┌────┴────┐
       │ 媒体编码 │        │  消息网关  │       │ 发现服务 │
       │ (保持原样)│       │  (新增)   │       │  (新增)  │
       └─────────┘        └─────┬─────┘       └─────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
        ┌─────┴─────┐    ┌─────┴─────┐    ┌─────┴─────┐
        │Notification│    │FileService│    │StatusService│
        │  Service   │    │  Service  │    │  Service  │
        └───────────┘    └───────────┘    └───────────┘

                    Android 服务端 (Java/Kotlin)
```

---

## 3. 分层设计

### 3.1 传输层 (Transport Layer)

```python
# transport/base.py
from abc import ABC, abstractmethod
from typing import Optional, Callable
from dataclasses import dataclass

@dataclass
class TransportConfig:
    host: str
    port: int
    timeout: float = 10.0
    buffer_size: int = 65536

class BaseTransport(ABC):
    """传输层基类"""

    @abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        pass

    @abstractmethod
    def send(self, data: bytes) -> int:
        """发送数据"""
        pass

    @abstractmethod
    def recv(self, size: int) -> bytes:
        """接收数据"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass


class TcpTransport(BaseTransport):
    """TCP 传输 - 用于控制通道"""
    pass


class UdpTransport(BaseTransport):
    """UDP 传输 - 用于媒体通道"""
    pass
```

### 3.2 安全层 (Security Layer)

```python
# security/crypto.py
from dataclasses import dataclass
from typing import Optional, Tuple
import hashlib
import os

@dataclass
class SecurityConfig:
    """安全配置"""
    enable_encryption: bool = True
    cipher: str = "AES-256-GCM"
    key_exchange: str = "ECDH-P256"
    pin_length: int = 6


class KeyPair:
    """密钥对管理"""

    def __init__(self):
        self._private_key = None
        self._public_key = None

    def generate(self) -> None:
        """生成 ECDH P-256 密钥对"""
        # 使用 cryptography 库
        from cryptography.hazmat.primitives.asymmetric import ec
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._public_key = self._private_key.public_key()

    def get_public_key_bytes(self) -> bytes:
        """导出公钥"""
        pass

    def compute_shared_secret(self, peer_public_key: bytes) -> bytes:
        """计算共享密钥"""
        pass


class SessionCrypto:
    """会话加密器"""

    CIPHER = "AES-256-GCM"
    NONCE_SIZE = 12
    TAG_SIZE = 16

    def __init__(self, key: bytes):
        self._key = key
        self._send_nonce = 0
        self._recv_nonce = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        """加密数据"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = self._nonce_bytes(self._send_nonce)
        self._send_nonce += 1
        aesgcm = AESGCM(self._key)
        return aesgcm.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """解密数据"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = self._nonce_bytes(self._recv_nonce)
        self._recv_nonce += 1
        aesgcm = AESGCM(self._key)
        return aesgcm.decrypt(nonce, ciphertext, None)

    def _nonce_bytes(self, counter: int) -> bytes:
        return counter.to_bytes(self.NONCE_SIZE, 'big')


class PairingManager:
    """配对管理器"""

    def __init__(self, storage_path: str):
        self._storage_path = storage_path
        self._keypair = KeyPair()

    def start_pairing(self) -> str:
        """开始配对，返回 PIN 码"""
        self._keypair.generate()
        # 生成 6 位 PIN
        pin = ''.join([str(os.urandom(1)[0] % 10) for _ in range(6)])
        return pin

    def verify_pin(self, pin: str, expected_pin: str) -> bool:
        """验证 PIN"""
        return pin == expected_pin

    def complete_pairing(self, peer_public_key: bytes) -> SessionCrypto:
        """完成配对，返回会话加密器"""
        shared_secret = self._keypair.compute_shared_secret(peer_public_key)
        # 派生会话密钥
        session_key = hashlib.sha256(shared_secret + b"scrcpy-session").digest()
        return SessionCrypto(session_key)

    def load_paired_device(self, device_id: str) -> Optional[SessionCrypto]:
        """加载已配对设备"""
        pass

    def save_paired_device(self, device_id: str, crypto: SessionCrypto) -> None:
        """保存配对信息"""
        pass
```

### 3.3 协议层 (Protocol Layer)

```python
# protocol/messages.py
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional, Any, Dict
import msgpack
import uuid

class MessageType(IntEnum):
    """消息类型"""
    REQUEST = 1      # 请求 (需要响应)
    RESPONSE = 2     # 响应
    EVENT = 3        # 事件推送
    STREAM_START = 4 # 流开始
    STREAM_DATA = 5  # 流数据
    STREAM_END = 6   # 流结束
    ERROR = 7        # 错误


class ServiceType(IntEnum):
    """服务类型"""
    CORE = 0          # 核心服务 (连接/配对)
    CONTROL = 1       # 控制服务 (触控/按键)
    NOTIFICATION = 2  # 通知服务
    FILE = 3          # 文件服务
    STATUS = 4        # 状态服务
    TELEPHONY = 5     # 电话服务
    SENSOR = 6        # 传感器服务
    CLIPBOARD = 7     # 剪贴板服务
    MEDIA = 8         # 媒体控制服务


@dataclass
class Message:
    """统一消息格式"""
    type: MessageType
    service: ServiceType
    method: str
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    params: Dict[str, Any] = field(default_factory=dict)
    data: Any = None
    error: Optional[str] = None

    def encode(self) -> bytes:
        """编码为二进制"""
        return msgpack.packb({
            't': self.type,
            's': self.service,
            'm': self.method,
            'i': self.id,
            'p': self.params,
            'd': self.data,
            'e': self.error,
        }, use_bin_type=True)

    @classmethod
    def decode(cls, data: bytes) -> 'Message':
        """从二进制解码"""
        obj = msgpack.unpackb(data, raw=False)
        return cls(
            type=MessageType(obj['t']),
            service=ServiceType(obj['s']),
            method=obj['m'],
            id=obj['i'],
            params=obj.get('p', {}),
            data=obj.get('d'),
            error=obj.get('e'),
        )


# 预定义消息
class Messages:
    """常用消息构造器"""

    @staticmethod
    def request(service: ServiceType, method: str, params: dict = None) -> Message:
        return Message(
            type=MessageType.REQUEST,
            service=service,
            method=method,
            params=params or {},
        )

    @staticmethod
    def response(request_id: str, service: ServiceType,
                 data: Any = None, error: str = None) -> Message:
        return Message(
            type=MessageType.RESPONSE if not error else MessageType.ERROR,
            service=service,
            method="response",
            id=request_id,
            data=data,
            error=error,
        )

    @staticmethod
    def event(service: ServiceType, method: str, data: Any) -> Message:
        return Message(
            type=MessageType.EVENT,
            service=service,
            method=method,
            data=data,
        )
```

### 3.4 服务层 (Service Layer)

```python
# services/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Callable, Optional
from protocol.messages import Message, ServiceType, MessageType

class BaseService(ABC):
    """服务基类"""

    service_type: ServiceType = None

    def __init__(self, client):
        self._client = client
        self._subscriptions: Dict[str, Callable] = {}

    @abstractmethod
    def handle_message(self, msg: Message) -> Optional[Message]:
        """处理消息"""
        pass

    def subscribe(self, event: str, callback: Callable) -> None:
        """订阅事件"""
        self._subscriptions[event] = callback

    def unsubscribe(self, event: str) -> None:
        """取消订阅"""
        self._subscriptions.pop(event, None)

    def _notify(self, event: str, data: Any) -> None:
        """通知订阅者"""
        if event in self._subscriptions:
            self._subscriptions[event](data)


# services/notification.py
class NotificationService(BaseService):
    """通知服务"""

    service_type = ServiceType.NOTIFICATION

    def handle_message(self, msg: Message) -> Optional[Message]:
        if msg.type == MessageType.EVENT and msg.method == "received":
            self._notify("notification", msg.data)
        return None

    async def subscribe_notifications(self, apps: list = None) -> bool:
        """订阅通知"""
        msg = Message.request(
            ServiceType.NOTIFICATION,
            "subscribe",
            {"apps": apps or ["*"]}
        )
        response = await self._client.send_and_wait(msg)
        return response.error is None

    async def unsubscribe(self) -> bool:
        """取消订阅"""
        msg = Message.request(ServiceType.NOTIFICATION, "unsubscribe")
        response = await self._client.send_and_wait(msg)
        return response.error is None


# services/file.py
class FileService(BaseService):
    """文件服务"""

    service_type = ServiceType.FILE

    def handle_message(self, msg: Message) -> Optional[Message]:
        # 文件服务主要处理请求/响应
        return None

    async def pull_file(self, remote_path: str, local_path: str,
                        progress: Callable = None) -> bool:
        """从设备拉取文件"""
        msg = Message.request(
            ServiceType.FILE,
            "pull",
            {"path": remote_path}
        )

        # 发送请求并接收流式数据
        await self._client.send(msg)

        with open(local_path, 'wb') as f:
            while True:
                chunk = await self._client.recv_stream_chunk(msg.id)
                if chunk is None:
                    break
                f.write(chunk)
                if progress:
                    progress(len(chunk))

        return True

    async def push_file(self, local_path: str, remote_path: str,
                        progress: Callable = None) -> bool:
        """推送文件到设备"""
        import os
        file_size = os.path.getsize(local_path)

        # 发送流开始消息
        msg = Message(
            type=MessageType.STREAM_START,
            service=ServiceType.FILE,
            method="push",
            params={"path": remote_path, "size": file_size},
        )
        await self._client.send(msg)

        # 发送文件数据
        with open(local_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                data_msg = Message(
                    type=MessageType.STREAM_DATA,
                    service=ServiceType.FILE,
                    method="push",
                    id=msg.id,
                    data=chunk,
                )
                await self._client.send(data_msg)
                if progress:
                    progress(len(chunk))

        # 发送流结束
        end_msg = Message(
            type=MessageType.STREAM_END,
            service=ServiceType.FILE,
            method="push",
            id=msg.id,
        )
        await self._client.send(end_msg)

        return True

    async def list_dir(self, path: str) -> list:
        """列出目录"""
        msg = Message.request(ServiceType.FILE, "list", {"path": path})
        response = await self._client.send_and_wait(msg)
        return response.data if not response.error else []

    async def delete(self, path: str) -> bool:
        """删除文件"""
        msg = Message.request(ServiceType.FILE, "delete", {"path": path})
        response = await self._client.send_and_wait(msg)
        return response.error is None


# services/status.py
class StatusService(BaseService):
    """状态服务"""

    service_type = ServiceType.STATUS

    def handle_message(self, msg: Message) -> Optional[Message]:
        if msg.type == MessageType.EVENT:
            self._notify(msg.method, msg.data)
        return None

    async def subscribe_battery(self, callback: Callable) -> None:
        """订阅电量变化"""
        self.subscribe("battery", callback)
        msg = Message.request(ServiceType.STATUS, "subscribe", {"events": ["battery"]})
        await self._client.send_and_wait(msg)

    async def subscribe_network(self, callback: Callable) -> None:
        """订阅网络状态"""
        self.subscribe("network", callback)
        msg = Message.request(ServiceType.STATUS, "subscribe", {"events": ["network"]})
        await self._client.send_and_wait(msg)

    async def get_status(self) -> dict:
        """获取完整状态"""
        msg = Message.request(ServiceType.STATUS, "get")
        response = await self._client.send_and_wait(msg)
        return response.data if not response.error else {}
```

### 3.5 客户端核心 (Client Core)

```python
# client/session.py
import asyncio
from typing import Dict, Optional, Any
from transport.base import TcpTransport
from security.crypto import SessionCrypto, PairingManager
from protocol.messages import Message, MessageType, ServiceType
from services.base import BaseService
from services.notification import NotificationService
from services.file import FileService
from services.status import StatusService

class ScrcpySession:
    """Scrcpy 会话管理器"""

    def __init__(self, host: str, port: int = 27184):
        self._transport = TcpTransport(host, port)
        self._crypto: Optional[SessionCrypto] = None
        self._services: Dict[ServiceType, BaseService] = {}
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False

        # 注册服务
        self._register_services()

    def _register_services(self):
        """注册所有服务"""
        self._services[ServiceType.NOTIFICATION] = NotificationService(self)
        self._services[ServiceType.FILE] = FileService(self)
        self._services[ServiceType.STATUS] = StatusService(self)

    @property
    def notification(self) -> NotificationService:
        return self._services[ServiceType.NOTIFICATION]

    @property
    def file(self) -> FileService:
        return self._services[ServiceType.FILE]

    @property
    def status(self) -> StatusService:
        return self._services[ServiceType.STATUS]

    async def connect(self, device_id: str = None) -> bool:
        """连接设备"""
        if not self._transport.connect():
            return False

        # 尝试加载已配对设备
        pairing = PairingManager("~/.scrcpy-assistant")
        crypto = pairing.load_paired_device(device_id)

        if crypto:
            self._crypto = crypto
        else:
            # 需要配对
            return False

        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        return True

    async def pair(self, pin: str) -> bool:
        """配对设备"""
        pairing = PairingManager("~/.scrcpy-assistant")

        # 发送公钥
        # 接收设备公钥
        # 验证 PIN
        # 完成配对

        return True

    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
        self._transport.disconnect()

    async def send(self, msg: Message) -> None:
        """发送消息"""
        data = msg.encode()
        if self._crypto:
            data = self._crypto.encrypt(data)

        # 添加长度前缀
        length = len(data).to_bytes(4, 'big')
        self._transport.send(length + data)

    async def send_and_wait(self, msg: Message, timeout: float = 10.0) -> Message:
        """发送消息并等待响应"""
        future = asyncio.Future()
        self._pending_requests[msg.id] = future

        await self.send(msg)

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(msg.id, None)
            raise TimeoutError(f"Request {msg.id} timed out")

    async def _receive_loop(self) -> None:
        """接收循环"""
        while self._running:
            try:
                # 读取长度前缀
                length_bytes = self._transport.recv(4)
                length = int.from_bytes(length_bytes, 'big')

                # 读取消息
                data = self._transport.recv(length)

                # 解密
                if self._crypto:
                    data = self._crypto.decrypt(data)

                # 解码
                msg = Message.decode(data)

                # 处理
                await self._handle_message(msg)

            except Exception as e:
                if self._running:
                    print(f"Receive error: {e}")
                break

    async def _handle_message(self, msg: Message) -> None:
        """处理接收到的消息"""
        # 响应消息 - 唤醒等待的请求
        if msg.type in (MessageType.RESPONSE, MessageType.ERROR):
            if msg.id in self._pending_requests:
                self._pending_requests[msg.id].set_result(msg)
                del self._pending_requests[msg.id]
            return

        # 事件/流消息 - 分发给对应服务
        if msg.service in self._services:
            self._services[msg.service].handle_message(msg)
```

---

## 4. Android 服务端

### 4.1 消息网关

```java
// MessageGateway.java
public class MessageGateway {
    private final ServerSocket serverSocket;
    private final Map<ServiceType, ServiceHandler> handlers;
    private final SessionCrypto crypto;

    public void start() {
        new Thread(this::acceptLoop).start();
    }

    private void acceptLoop() {
        while (running) {
            Socket client = serverSocket.accept();
            new Thread(() -> handleClient(client)).start();
        }
    }

    private void handleClient(Socket client) {
        InputStream in = client.getInputStream();
        OutputStream out = client.getOutputStream();

        while (running) {
            // 读取长度
            byte[] lengthBytes = readExact(in, 4);
            int length = ByteBuffer.wrap(lengthBytes).getInt();

            // 读取消息
            byte[] data = readExact(in, length);

            // 解密
            if (crypto != null) {
                data = crypto.decrypt(data);
            }

            // 解码
            Message msg = Message.decode(data);

            // 分发
            Message response = dispatch(msg);

            // 响应
            if (response != null) {
                send(out, response);
            }
        }
    }

    private Message dispatch(Message msg) {
        ServiceHandler handler = handlers.get(msg.getService());
        if (handler != null) {
            return handler.handle(msg);
        }
        return Message.error(msg.getId(), "Unknown service");
    }
}
```

### 4.2 通知服务

```java
// NotificationService.java
@RequiresApi(api = Build.VERSION_CODES.JELLY_BEAN_MR2)
public class NotificationService extends NotificationListenerService
                                  implements ServiceHandler {

    private MessageGateway gateway;

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (gateway == null) return;

        Notification notification = sbn.getNotification();
        Bundle extras = notification.extras;

        Message event = Message.event(
            ServiceType.NOTIFICATION,
            "received",
            Map.of(
                "package", sbn.getPackageName(),
                "title", extras.getString(Notification.EXTRA_TITLE),
                "text", extras.getCharSequence(Notification.EXTRA_TEXT),
                "time", sbn.getPostTime()
            )
        );

        gateway.broadcast(event);
    }

    @Override
    public Message handle(Message msg) {
        switch (msg.getMethod()) {
            case "subscribe":
                // 添加到订阅列表
                return Message.response(msg.getId(), Map.of("success", true));

            case "unsubscribe":
                // 从订阅列表移除
                return Message.response(msg.getId(), Map.of("success", true));

            default:
                return Message.error(msg.getId(), "Unknown method");
        }
    }
}
```

---

## 5. 使用示例

```python
# example.py
import asyncio
from client.session import ScrcpySession

async def main():
    # 创建会话
    session = ScrcpySession("192.168.1.100")

    # 配对 (首次)
    # pin = await session.start_pairing()
    # print(f"请在手机上确认 PIN: {pin}")
    # await session.complete_pairing(user_input_pin)

    # 连接
    await session.connect()

    # 订阅通知
    def on_notification(data):
        print(f"收到通知: {data['title']} - {data['text']}")

    await session.notification.subscribe_notifications()
    session.notification.subscribe("notification", on_notification)

    # 订阅电量
    def on_battery(data):
        print(f"电量: {data['level']}%")

    await session.status.subscribe_battery(on_battery)

    # 传输文件
    await session.file.pull_file("/sdcard/DCIM/test.jpg", "./test.jpg")
    await session.file.push_file("./local.txt", "/sdcard/Download/remote.txt")

    # 保持运行
    await asyncio.Event().wait()

asyncio.run(main())
```

---

## 6. 实现路线

### Phase 1: 基础框架 (1-2 周)
- [ ] 传输层 (TCP/UDP)
- [ ] 消息协议 (MessagePack)
- [ ] 基础会话管理

### Phase 2: 安全层 (1 周)
- [ ] ECDH 密钥交换
- [ ] AES-256-GCM 加密
- [ ] PIN 配对流程
- [ ] 密钥存储

### Phase 3: 核心服务 (2 周)
- [ ] 控制服务 (迁移现有功能)
- [ ] 状态服务 (电量/网络)
- [ ] 通知服务 (推送订阅)

### Phase 4: 文件服务 (1 周)
- [ ] 文件列表
- [ ] 文件传输 (推/拉)
- [ ] 截图保存

### Phase 5: 扩展服务 (按需)
- [ ] 电话服务
- [ ] 传感器服务
- [ ] 媒体控制服务

---

## 7. 兼容性

### 7.1 与原 scrcpy 的关系

| 方面 | 原 scrcpy | 新框架 |
|-----|----------|--------|
| 媒体编码 | 保持兼容 | 保持兼容 |
| 媒体通道 | 明文 UDP | **DTLS 加密** |
| 控制通道 | 明文 TCP | **TLS 1.3 加密** |
| 认证 | 无 | **PIN 配对** |

### 7.2 版本协商

```
连接建立时 (TLS 握手后):
1. 客户端发送版本 + 加密套件
2. 服务端验证并返回支持的功能
3. 协商使用最高兼容版本 + 最强加密
4. 握手失败则拒绝连接 (不允许降级)
```

### 7.3 严格安全模式

```
❌ 不支持降级到明文
❌ 不支持弱加密算法
❌ 不支持跳过认证

连接必须满足:
- TLS 1.3
- 有效证书 (已配对)
- 或完成 PIN 配对流程
```

---

## 8. 安全设计 (Security First)

### 8.1 加密策略

```
┌──────────────────────────────────────────────────────────────┐
│                    加密通道架构                               │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   PC 客户端                     Android 服务端               │
│                                                              │
│   ┌─────────┐    TLS 1.3        ┌─────────┐                 │
│   │ 控制    │════════════════════│ 控制    │                 │
│   └─────────┘   (TCP 27184)     └─────────┘                 │
│                                                              │
│   ┌─────────┐    DTLS/SRTP      ┌─────────┐                 │
│   │ 视频流  │════════════════════│ 视频流  │                 │
│   └─────────┘   (UDP 27185)     └─────────┘                 │
│                                                              │
│   ┌─────────┐    DTLS/SRTP      ┌─────────┐                 │
│   │ 音频流  │════════════════════│ 音频流  │                 │
│   └─────────┘   (UDP 27186)     └─────────┘                 │
│                                                              │
│   ══════════  加密通道  ══════════                          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 威胁模型

| 威胁 | 防护措施 | 强制性 |
|-----|---------|--------|
| 未授权访问 | PIN 配对 + 证书固定 | ✅ 强制 |
| 中间人攻击 | ECDH + 证书固定 | ✅ 强制 |
| 数据窃听 | 全链路加密 | ✅ 强制 |
| 重放攻击 | Nonce + 时间戳 | ✅ 强制 |
| 密钥泄露 | 定期轮换 + Keystore | ✅ 强制 |
| 恶意服务端 | 证书验证 | ✅ 强制 |

### 8.3 加密算法

```
密钥交换:  ECDH P-256 / X25519
对称加密:  AES-256-GCM / ChaCha20-Poly1305
哈希算法:  SHA-256 / SHA-384
TLS版本:   TLS 1.3 (禁止 TLS 1.0/1.1/1.2)
媒体加密:  DTLS 1.3 / SRTP
```

### 8.4 认证流程

```
1. 首次配对
   ┌──────┐                      ┌──────┐
   │  PC  │  1. 请求配对          │ 手机 │
   │      │ ─────────────────────►│      │
   │      │                      │      │ 显示 6 位 PIN
   │      │  2. 显示 PIN          │      │
   │      │ ◄─────────────────────│      │
   │      │                      │      │
   │      │  3. 用户输入 PIN      │      │
   │      │ ─────────────────────►│      │
   │      │                      │      │
   │      │  4. 交换公钥 (ECDH)   │      │
   │      │ ◄════════════════════►│      │
   │      │                      │      │
   │      │  5. 保存配对证书      │      │
   │      │ ◄───────────────────►│      │
   └──────┘                      └──────┘

2. 后续连接 (证书固定)
   ┌──────┐                      ┌──────┐
   │  PC  │  1. TLS 握手         │ 手机 │
   │      │ ◄════════════════════►│      │
   │      │                      │      │
   │      │  2. 验证证书 (固定)   │      │
   │      │                      │      │
   │      │  3. 建立加密通道      │      │
   │      │ ◄════════════════════►│      │
   └──────┘                      └──────┘
```

### 8.5 安全等级

| 等级 | 场景 | 配置 |
|-----|------|------|
| **最高** | 公网/不可信网络 | 双向证书 + 媒体加密 |
| **标准** | 局域网 | PIN 配对 + 控制加密 |
| ~~调试~~ | ~~开发环境~~ | ~~明文~~ ❌ 禁止 |

### 8.6 数据安全

```
传输中:  全部加密
存储中:  密钥存入 Keystore/Keychain
内存中:  敏感数据用后立即清零
日志中:  不记录敏感信息
```

---

## 9. 媒体协议设计 (RTP/SRTP)

> **状态**: 设想阶段，待实现
> **日期**: 2026-02-20

### 9.1 当前方案 vs RTP/SRTP

| 方案 | 头开销 | 加密后开销 | 优点 | 缺点 |
|------|--------|------------|------|------|
| **当前自定义** | 24B | +28B = 52B | 简单、E2E追踪内置 | 非标准、开销大 |
| **RTP + SRTP** | 12B | +4~10B = 16~22B | 标准、工具支持、开销小 | 需迁移 |

### 9.2 当前 UDP 头结构 (24字节)

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│   seq (4B)   │ timestamp(8B)│  flags (4B)  │send_time_ns(8B)│
└──────────────┴──────────────┴──────────────┴──────────────┘
     0-3            4-11          12-15          16-23

字段说明:
- seq (4B): 包序号
- timestamp (8B): PTS 时间戳
- flags (4B): 标志位 (KEY_FRAME, CONFIG, FEC_*)
- send_time_ns (8B): E2E 延迟追踪 (发送时间)
```

### 9.3 RTP 头结构 (12字节)

```
┌─────────────────────────┬─────────────────────────┐
│      Byte 0-1           │      Byte 2-3           │
├─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┼─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┤
│V│P│X│CC  │M│  PT        │      Sequence Number    │
├─────────────────────────┴─────────────────────────┤
│                  Timestamp (32-bit)               │
├───────────────────────────────────────────────────┤
│                  SSRC (32-bit)                    │
└───────────────────────────────────────────────────┘

字段说明:
- V (2b): Version = 2
- P (1b): Padding
- X (1b): Extension
- CC (4b): CSRC Count
- M (1b): Marker (关键帧标记)
- PT (7b): Payload Type (H264=96, H265=...)
- Sequence Number (16b): 包序号
- Timestamp (32b): PTS (90kHz for video)
- SSRC (32b): 流标识
```

### 9.4 SRTP 加密开销

```
RTP Packet:
┌──────────────┬──────────────────┬──────────────┐
│ RTP Header   │ Encrypted Payload│ Auth Tag     │
│    12B       │    (variable)    │   4-10B      │
└──────────────┴──────────────────┴──────────────┘

SRTP 不需要传输 nonce:
- IV 从 RTP header 派生: IV = (ssrc, seq, timestamp)
- 使用 AES-CTR 或 AES-GCM 加密
- Auth tag 用于完整性验证 (可选 4-10 字节)
```

### 9.5 迁移路径

```
Phase 1 (当前):
  自定义 UDP (24B) - 无加密

Phase 2 (中期):
  自定义 UDP (24B) + ChaCha20-Poly1305 (28B) = 52B
  - 快速实现加密
  - 开销较大

Phase 3 (长期):
  RTP (12B) + SRTP (4-10B auth tag) = 16-22B
  - 标准协议
  - 最小开销
  - Wireshark 支持
```

### 9.6 E2E 延迟追踪方案

如果迁移到 RTP/SRTP，E2E 延迟追踪有两个方案：

**方案 A: RTP 扩展头**
```
RTP Header (12B) + Extension Header (4B) + send_time_ns (8B) = 24B
- 使用 RFC 5285 One-Byte Header
- 优点: 标准
- 缺点: 多 8 字节
```

**方案 B: RTCP XR**
```
使用 RTCP Extended Report 传输延迟统计
- 不影响 RTP 头大小
- 周期性报告，非每包
- 优点: 标准方式
- 缺点: 非实时
```

**方案 C: 预共享时间同步**
```
客户端和服务端时钟同步 (NTP/PTP)
- RTP timestamp 即可计算延迟
- 无需额外字段
- 缺点: 需要时钟同步
```

### 9.7 实现参考

```python
# Python SRTP 示例 (使用 pylibsrtp)
from pylibsrtp import Session, Policy

# 创建 SRTP 会话
policy = Policy(
    key=b'01234567890123456789012345678901',  # 30 bytes for AES-256-ICM
    ssrc_type=Policy.SSRC_SPECIFIC,
    ssrc_value=0x12345678,
)
session = Session(policy)

# 加密 RTP 包
encrypted = session.protect(rtp_packet)

# 解密 SRTP 包
decrypted = session.unprotect(srtp_packet)
```

### 9.8 Android 端 SRTP 库

| 库 | 说明 |
|-----|------|
| **libsrtp** | C 库，需要 JNI |
| **Google libjingle** | WebRTC 的一部分 |
| **Pion SRTP** | Go 库，可通过 gomobile 调用 |

---

## 10. 相关文档

- [协议规范](./PROTOCOL_SPEC.md)
- [E2E 延迟分析](./E2E_LATENCY_ANALYSIS.md)
- [已知问题](./known_issues/README.md)
