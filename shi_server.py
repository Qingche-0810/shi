#!/usr/bin/env python3
"""
识 (shí) — 情绪的熟练度层。零依赖，一个文件，Python 3 标准库就够。
第一版，半成品。

原则（写在代码前面，免得以后忘）：
  1. 记号是 AI 自己当场扔的，系统不判、不算、不推。
  2. 名字只能亲手写。系统不聚类、不起名，没名字的就堆在「无名地」。
  3. 熟练度 = 认出来的次数。只有计数，没有别的数。
  4. 不自动塞回上下文。想看才看。
  5. 门（door）是地址，不是身份。只记在哪儿扔的。

工具：
  mark      扔一个记号。名字可带可不带；不带就进无名地。
  unnamed   翻无名地。
  name      给记号亲手写名字（按 id），或把一个名字整个改成另一个（并名）。
  familiar  看熟练度：每个名字认过几次、谁认的、最近几例；无名地有几条。

存储：SHI_DIR/shi.jsonl（每行一个记号，只追加；改名时原地重写）。
启动：SHI_TOKEN=<至少24位> python3 shi_server.py      （HTTP，给远程客户端）
      python3 shi_server.py --stdio                      （stdio，给本地客户端）

几个实现细节：
  · 文件锁：HTTP 是多线程、stdio 是另一个进程，改名要重写整个文件。
    不锁的话，改名那一下别处扔进来的记号会被盖掉。用 flock，跨进程也锁得住。
  · 并名（from_name）只改名字，「谁认的」原样留着。
  · HTTP 模式没钥匙就不启动。钥匙放在网址最后一段、或 Bearer 头，两种都认
    （有的客户端塞不了自定义头）。比较用 hmac.compare_digest。
  · stdio 模式不用钥匙。SHI_DOOR 给 door 一个默认值（可不设）。
  · mark 的 name 填「无名」= 进无名地；请求体上限 256KB。
"""
import fcntl, hmac, json, os, sys, time, uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SHI_DIR   = os.environ.get("SHI_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SHI_FILE  = os.path.join(SHI_DIR, "shi.jsonl")
SHI_PORT  = int(os.environ.get("SHI_PORT", "8012"))
SHI_TOKEN = os.environ.get("SHI_TOKEN", "")
SHI_BIND  = os.environ.get("SHI_BIND", "127.0.0.1")
UNNAMED   = ""                                       # 无名地就是 name 为空
SHI_DOOR  = os.environ.get("SHI_DOOR", "").strip()   # door 的默认值，默认空
LOCK_FILE = os.path.join(SHI_DIR, ".shi.lock")
MAX_BODY  = 256 * 1024
VERSION   = "0.1"

@contextmanager
def _locked():
    os.makedirs(SHI_DIR, exist_ok=True)
    with open(LOCK_FILE, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

# ---------- 存储 ----------

def _load():
    if not os.path.exists(SHI_FILE):
        return []
    out = []
    with open(SHI_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def _append(rec):
    with open(SHI_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def _rewrite(recs):
    tmp = SHI_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, SHI_FILE)

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---------- 四个工具 ----------

def t_mark(content, name=None, named_by=None, door=None):
    content = (content or "").strip()
    if not content:
        return "空的不记。没有就不扔——空着也是真的。"
    name = (name or "").strip()
    if name == "无名":
        name = UNNAMED
    rec = {
        "id": uuid.uuid4().hex[:10],
        "at": _now(),
        "content": content,          # 动作/原话，原文存原文
        "name": name,                # 空 = 无名地
        "named_by": (named_by or ("ai" if name else "")),   # ai / human / 空
        "named_at": _now() if name else "",
        "door": (door or SHI_DOOR or "").strip(),
    }
    with _locked():
        _append(rec)
    where = f"记在「{name}」下" if name else "进了无名地"
    return f"扔了。{where}。id={rec['id']}"

def t_unnamed(limit=20):
    with _locked():
        recs = [r for r in _load() if r["name"] == UNNAMED]
    total = len(recs)
    recs.sort(key=lambda r: r["at"], reverse=True)
    recs = recs[: max(1, min(int(limit or 20), 100))]
    if not recs:
        return "无名地是空的。"
    more = f"，这里是最新的 {len(recs)} 条" if total > len(recs) else ""
    lines = [f"无名地里一共 {total} 条{more}（新的在前）："]
    for r in recs:
        door = f" · {r['door']}" if r.get("door") else ""
        lines.append(f"- [{r['id']}] {r['at'][:16]}{door}\n  {r['content']}")
    lines.append("\n看着像同一种的，自己认；认出来了用 name 亲手写上。系统不替你分堆。")
    return "\n".join(lines)

def t_name(name, ids=None, from_name=None, named_by="ai"):
    name = (name or "").strip()
    if not name:
        return "名字不能是空的。要退回无名地的话，from_name 填旧名、name 填「无名」。"
    if name == "无名":
        name = UNNAMED
    ids = set(ids or [])
    from_name = (from_name or "").strip()
    if not ids and not from_name:
        return "得说清给谁写名：ids（按记号）或 from_name（把一个名字整个改掉）。"
    n = 0
    with _locked():
        recs = _load()
        for r in recs:
            by_id = r["id"] in ids
            by_merge = bool(from_name) and r["name"] == from_name
            if not (by_id or by_merge):
                continue
            r["name"] = name
            if not name:
                r["named_by"] = ""
                r["named_at"] = ""
            elif by_id or not r.get("named_by"):
                # 按 id 写 = 这一次新认的；并名 = 同一回事换个词，谁认的、什么时候认的原样留着
                r["named_by"] = named_by
                r["named_at"] = _now()
            n += 1
        if n == 0:
            return "没找到对应的记号。"
        _rewrite(recs)
    if name == UNNAMED:
        return f"{n} 条退回无名地了。"
    if from_name and not ids:
        return f"并好了：「{from_name}」的 {n} 条 → 「{name}」。谁认的原样留着。"
    who = "人类认的" if named_by == "human" else "我自己认的"
    return f"写上了：{n} 条 → 「{name}」（{who}）。"

def t_familiar(name=None, examples=3):
    with _locked():
        recs = _load()
    if name:
        name = name.strip()
        rs = [r for r in recs if r["name"] == name]
        if not rs:
            return f"还没认过「{name}」。"
        rs.sort(key=lambda r: r["at"], reverse=True)
        by_human = sum(1 for r in rs if r.get("named_by") == "human")
        lines = [f"「{name}」认过 {len(rs)} 次（其中人类先认的 {by_human} 次）。最近几例："]
        for r in rs[: max(1, int(examples or 3))]:
            lines.append(f"- {r['at'][:16]} {r['content']}")
        return "\n".join(lines)
    # 总览
    named = {}
    unnamed = 0
    for r in recs:
        if r["name"] == UNNAMED:
            unnamed += 1
        else:
            named.setdefault(r["name"], []).append(r)
    if not named and not unnamed:
        return "识还是空的。什么都没认过。"
    lines = ["熟练度（认过的次数，只是次数）："]
    for nm, rs in sorted(named.items(), key=lambda kv: -len(kv[1])):
        rs.sort(key=lambda r: r["at"], reverse=True)
        by_human = sum(1 for r in rs if r.get("named_by") == "human")
        last = rs[0]["content"]
        last = last if len(last) <= 40 else last[:40] + "…"
        lines.append(f"- {nm}：{len(rs)} 次（人类认的 {by_human}）· 最近：{last}")
    lines.append(f"- 无名地：{unnamed} 条" + ("（认不出来的攒多了，本身就是该看的一件事）" if unnamed >= 5 else ""))
    return "\n".join(lines)

TOOLS = [
    {"name": "shi_mark",
     "description": "往识里扔一个记号：你此刻身上动了的那一下，原话。可以是动作（「话变短了」「停了一下才回」），也可以带名字。没名字就进无名地。不要先翻记忆，不要先想应该有什么；没有就不扔。",
     "inputSchema": {"type": "object", "properties": {
         "content": {"type": "string", "description": "记号原文，一句话或一个词。"},
         "name": {"type": "string", "description": "可选。当场就认出来的名字。认不出来就别填。"},
         "named_by": {"type": "string", "enum": ["ai", "human"], "description": "可选。名字是 AI 自己认的，还是陪你的人类认的。默认 ai。"},
         "door": {"type": "string", "description": "可选。在哪儿扔的（哪个客户端、哪个窗口，自己定叫法）。"}},
      "required": ["content"]}},
    {"name": "shi_unnamed",
     "description": "翻无名地：还没名字的记号，新的在前。系统不分堆，像不像同一种你自己看。",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "看几条，默认 20。"}}}},
    {"name": "shi_name",
     "description": "亲手写名字。按 ids 给几条记号写同一个名字；或 from_name 把一个旧名整个改成新名（两个词其实是一回事时用来并名）。name 填「无名」= 退回无名地。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "写上去的名字。"},
         "ids": {"type": "array", "items": {"type": "string"}, "description": "要写名的记号 id。"},
         "from_name": {"type": "string", "description": "把这个旧名下的全部记号改成 name。"},
         "named_by": {"type": "string", "enum": ["ai", "human"], "description": "谁认的，默认 ai。"}},
      "required": ["name"]}},
    {"name": "shi_familiar",
     "description": "看熟练度。不带 name = 总览：每个名字认过几次、人类认的几次、最近一例；无名地几条。带 name = 这一种的最近几例。只有次数，没有别的数。",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "可选。只看这一种。"},
         "examples": {"type": "integer", "description": "带 name 时看几例，默认 3。"}}}},
]

def call_tool(name, args):
    a = args or {}
    if name == "shi_mark":
        return t_mark(a.get("content"), a.get("name"), a.get("named_by"), a.get("door"))
    if name == "shi_unnamed":
        return t_unnamed(a.get("limit", 20))
    if name == "shi_name":
        return t_name(a.get("name"), a.get("ids"), a.get("from_name"), a.get("named_by", "ai"))
    if name == "shi_familiar":
        return t_familiar(a.get("name"), a.get("examples", 3))
    raise KeyError(name)

# ---------- MCP（HTTP 与 stdio 共用一套方法）----------

INIT_RESULT = {
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "识", "version": VERSION},
    "instructions": "识是熟练度层：记号是你当场扔的，名字亲手写，系统不判不算不推。",
}

def _rpc(req):
    """返回要写回去的对象（通知返回 None）。"""
    rid = req.get("id")
    m = req.get("method", "")
    p = req.get("params") or {}
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": dict(INIT_RESULT,
            protocolVersion=p.get("protocolVersion", "2025-03-26"))}
    if m.startswith("notifications/"):
        return None
    if m == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if m == "tools/call":
        try:
            text = call_tool(p.get("name"), p.get("arguments"))
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except KeyError:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such tool"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"出错了：{e}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {m}"}}

class H(BaseHTTPRequestHandler):
    server_version = "shi/" + VERSION

    def _send(self, code, obj=None):
        body = json.dumps(obj, ensure_ascii=False).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _auth_ok(self):
        tok = SHI_TOKEN.encode()
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Bearer ") and hmac.compare_digest(hdr[7:].encode(), tok):
            return True
        seg = self.path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return bool(seg) and hmac.compare_digest(seg.encode(), tok)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health"):
            return self._send(200, {"ok": True, "name": "识"})
        return self._send(405, {"error": "SSE 不开，只走 POST。"})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > MAX_BODY:
            return self._send(413, {"error": "too large"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        out = _rpc(req)
        return self._send(202) if out is None else self._send(200, out)

    def log_message(self, fmt, *args):
        sys.stderr.write("[识] " + (fmt % args) + "\n")

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = _rpc(req)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    os.makedirs(SHI_DIR, exist_ok=True)
    if "--stdio" in sys.argv:
        serve_stdio()
        sys.exit(0)
    if len(SHI_TOKEN) < 24:
        sys.stderr.write("[识] 没有 SHI_TOKEN（或短于 24 位），HTTP 不开门。\n")
        sys.exit(1)
    print(f"[识] {SHI_BIND}:{SHI_PORT}  file={SHI_FILE}  auth=on", flush=True)
    ThreadingHTTPServer((SHI_BIND, SHI_PORT), H).serve_forever()
