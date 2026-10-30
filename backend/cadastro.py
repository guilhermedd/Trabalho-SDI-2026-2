#!/usr/bin/env python3
"""
python3 cadastro.py
"""

import codecs
import json
import math
import os
import random
import re
import signal
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
print(f"[ok] diretório base: {BASE_DIR}")


# ----------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------
def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV_FILE = load_env(BASE_DIR / ".env")


def cfg(name: str, default=None):
    return os.environ.get(name, _ENV_FILE.get(name, default))


def fail(message: str):
    sys.exit(f"Erro de configuração: {message}")


def cfg_int(name: str, default, minimo=None, maximo=None) -> int:
    raw = cfg(name, default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        fail(f"{name} deve ser um número inteiro (recebido: {raw!r}).")
    if minimo is not None and value < minimo:
        fail(f"{name} deve ser >= {minimo} (recebido: {value}).")
    if maximo is not None and value > maximo:
        fail(f"{name} deve ser <= {maximo} (recebido: {value}).")
    return value


def cfg_float(name: str, default, minimo=None) -> float:
    raw = cfg(name, default)
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        fail(f"{name} deve ser um número (recebido: {raw!r}).")
    if not math.isfinite(value):
        fail(f"{name} deve ser um número finito (recebido: {raw!r}).")
    if minimo is not None and value < minimo:
        fail(f"{name} deve ser >= {minimo} (recebido: {value}).")
    return value


def cfg_bool(name: str, default: str) -> bool:
    return str(cfg(name, default)).strip().lower() in ("1", "true", "sim", "yes", "on")


SERVER_IP = cfg("SERVER_IP")
if not SERVER_IP or not cfg("SERVER_PORT"):
    fail("SERVER_IP e SERVER_PORT são obrigatórias (.env ou variáveis de ambiente).")
SERVER_PORT = cfg_int("SERVER_PORT", None, 1, 65535)

ENCODING = cfg("ENCODING", "utf-8")
try:
    codecs.lookup(ENCODING)
except LookupError:
    fail(f"ENCODING desconhecido: {ENCODING!r}.")

HTTP_HOST = cfg("HTTP_HOST", "0.0.0.0")
HTTP_PORT = cfg_int("HTTP_PORT", "8000", 1, 65535)

# Endereço em que o Cadastro recebe NEWID / NEWSERVERIP (opcional no .env).
CONTROL_HOST = cfg("CONTROL_HOST", "0.0.0.0")
CONTROL_PORT = cfg_int("CONTROL_PORT", "6001", 1, 65535)

# 1 = usuários gerados saem como NEWUSER_<ID>_<NOME>; 0 = NEWUSER_<ID>.
SEND_NAME = cfg_bool("SEND_NAME", "1")

# ID do primeiro servidor (o coordenador inicial), que já começa na lista.
INITIAL_SERVER_ID = cfg_int("INITIAL_SERVER_ID", "0", 0)

SEND_TIMEOUT = cfg_float("SEND_TIMEOUT", "3", 0.1)
SEND_RETRIES = cfg_int("SEND_RETRIES", "3", 1)
RETRY_DELAY = cfg_float("RETRY_DELAY", "1", 0)

AUTO_T0 = cfg_float("AUTO_T0", "2.0")
AUTO_R = cfg_float("AUTO_R", "0.9")
AUTO_TMIN = cfg_float("AUTO_TMIN", "0.02")

LOG_FILE = BASE_DIR / cfg("LOG_FILE", "cadastro.json")

MAX_NAME_LEN = 100
MAX_BODY = 65536
AUTO_RULES = "Requisitos: T0 > 0, 0 < r < 1, 0 < Tmin <= T0, limite inteiro >= 0"


def valid_auto_params(t0, r, tmin, limit) -> bool:
    if not all(isinstance(x, float) and math.isfinite(x) for x in (t0, r, tmin)):
        return False
    return t0 > 0 and 0 < r < 1 and 0 < tmin <= t0 and limit >= 0


if not valid_auto_params(AUTO_T0, AUTO_R, AUTO_TMIN, 0):
    fail(f"AUTO_T0/AUTO_R/AUTO_TMIN inválidos. {AUTO_RULES}.")


def find_frontend_dir() -> Path:
    """Pasta com index.html/style.css/script.js: FRONTEND_DIR, ou a própria pasta, ou ../frontend, ou ./frontend."""
    custom = cfg("FRONTEND_DIR")
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else (BASE_DIR / p)
    for candidate in (BASE_DIR, BASE_DIR.parent / "frontend", BASE_DIR / "frontend"):
        if (candidate / "index.html").exists():
            return candidate
    return BASE_DIR


FRONTEND_DIR = find_frontend_dir()

NEWUSER_RE = re.compile(r"NEWUSER_(\d{1,18})(?:_(.*))?", re.ASCII | re.DOTALL)
KILL_RE = re.compile(r"KILL_(\d{1,18})", re.ASCII)
NEWID_RE = re.compile(r"NEWID_(\d{1,18})", re.ASCII)
NEWSERVERIP_RE = re.compile(r"NEWSERVERIP_([A-Za-z0-9.:\-]+)_(\d{1,5})", re.ASCII)

FIRST_NAMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Felipe", "Gabriela", "Henrique",
    "Isabela", "Joao", "Karen", "Lucas", "Mariana", "Nicolas", "Olivia", "Pedro",
    "Queila", "Rafael", "Sofia", "Thiago", "Ursula", "Vitor", "Yasmin", "Zeca",
]
LAST_NAMES = [
    "Silva", "Souza", "Oliveira", "Santos", "Pereira", "Costa", "Rodrigues",
    "Almeida", "Nascimento", "Lima", "Araujo", "Ferreira", "Carvalho", "Gomes",
]

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/script.js": ("script.js", "application/javascript; charset=utf-8"),
}


# ----------------------------------------------------------------------
# Estado compartilhado e log
# ----------------------------------------------------------------------

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.coordinator = (SERVER_IP, SERVER_PORT)
        self.users = {}
        self.server_ids = {INITIAL_SERVER_ID}
        self.next_user_id = 1 

    def get_coordinator(self):
        with self.lock:
            return self.coordinator

    def set_coordinator(self, ip, port):
        with self.lock:
            self.coordinator = (ip, port)


state = State()
_log_lock = threading.Lock()


def log_event(metrica: str, valor=1, **dados):
    """
    Registra em JSON Lines no formato do enunciado:
    {"ts", "componente", "id", "metrica", "valor"} + campos de detalhe.
    """
    registro = {"ts": round(time.time(), 3), "componente": "cadastro", "id": 0,
                "metrica": metrica, "valor": valor, **dados}
    linha = json.dumps(registro, ensure_ascii=False) + "\n"
    with _log_lock:
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(linha)
        except OSError as exc:
            print(f"[aviso] não foi possível gravar o log: {exc}")


def allocate_user_id() -> int:
    """Próximo ID livre para usuários gerados (nunca repete um ID já tentado)."""
    with state.lock:
        while state.next_user_id in state.users:
            state.next_user_id += 1
        uid = state.next_user_id
        state.next_user_id += 1
        return uid


def random_newuser() -> str:
    """NEWUSER com ID livre; inclui um nome aleatório se SEND_NAME=1."""
    uid = allocate_user_id()
    if not SEND_NAME:
        return f"NEWUSER_{uid}"
    return f"NEWUSER_{uid}_{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


# ----------------------------------------------------------------------
# Validação das mensagens
# ----------------------------------------------------------------------
class InvalidMessage(ValueError):
    pass


def sanitize_name(name: str) -> str:
    """Troca "_" e caracteres de controle (inclusive quebras de linha) por espaço."""
    name = "".join(ch if ch.isprintable() else " " for ch in name.replace("_", " "))
    return " ".join(name.split())


def normalize_message(raw: str):
    """
    Valida e normaliza uma mensagem NEWUSER/KILL.
    Retorna (tipo, id, nome, mensagem_normalizada) ou lança InvalidMessage.
    """
    text = raw.strip()

    m = KILL_RE.fullmatch(text)
    if m:
        server_id = int(m.group(1))
        return "KILL", server_id, None, f"KILL_{server_id}"

    m = NEWUSER_RE.fullmatch(text)
    if m:
        user_id = int(m.group(1))
        if user_id <= 0:
            raise InvalidMessage("O ID do usuário deve ser maior que zero")
        if m.group(2) is None:
            return "NEWUSER", user_id, None, f"NEWUSER_{user_id}"
        name = sanitize_name(m.group(2))
        if not name:
            raise InvalidMessage("O nome não pode ficar vazio (ou envie NEWUSER_<ID>)")
        if len(name) > MAX_NAME_LEN:
            raise InvalidMessage(f"O nome pode ter no máximo {MAX_NAME_LEN} caracteres")
        message = f"NEWUSER_{user_id}_{name}"
        try:
            message.encode(ENCODING)
        except UnicodeEncodeError:
            raise InvalidMessage(f"O nome tem caracteres que não existem em {ENCODING}")
        return "NEWUSER", user_id, name, message

    raise InvalidMessage("Formato inválido. Use NEWUSER_<ID>_<NOME>, NEWUSER_<ID> ou KILL_<ID>")


# ----------------------------------------------------------------------
# Envio ao coordenador
# ----------------------------------------------------------------------
def read_reply(sock: socket.socket) -> str:
    """Lê uma linha de resposta. Sem resposta dentro do timeout -> ""."""
    buf = b""
    try:
        while b"\n" not in buf and len(buf) < 4096:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    return buf.split(b"\n", 1)[0].decode(ENCODING, errors="replace").strip()


def send_to_coordinator(message: str):
    """
    Envia uma linha ao coordenador atual via TCP.
    Em falha de conexão, tenta de novo com o endereço mais recente (o
    coordenador pode ter mudado por causa de uma eleição).
    Retorna (ok, resposta_ou_erro).
    """
    last_error = "nenhuma tentativa realizada"
    payload = (message + "\n").encode(ENCODING)
    for attempt in range(1, SEND_RETRIES + 1):
        host, port = state.get_coordinator()
        destino = f"{host}:{port}"
        try:
            with socket.create_connection((host, port), timeout=SEND_TIMEOUT) as sock:
                sock.settimeout(SEND_TIMEOUT)
                sock.sendall(payload)
                reply = read_reply(sock)
        except OSError as exc:
            last_error = f"coordenador {destino} indisponível ({exc})"
            log_event("falha_envio", mensagem=message, destino=destino,
                      tentativa=attempt, erro=str(exc))
            if attempt < SEND_RETRIES:
                time.sleep(RETRY_DELAY)
            continue

        log_event("enviado", mensagem=message, destino=destino,
                  tentativa=attempt, resposta=reply)
        if reply.startswith("ERROR_"):
            return False, reply
        return True, reply
    return False, last_error


def dispatch(raw: str):
    """
    Valida, normaliza e envia NEWUSER/KILL, atualizando as listas:
      * NEWUSER enviado com sucesso -> usuário entra na lista;
      * KILL enviado com sucesso    -> ID do servidor sai da lista.
    Retorna (ok, mensagem_enviada, resposta_ou_erro). Lança InvalidMessage.
    """
    kind, ident, name, message = normalize_message(raw)

    ok, info = send_to_coordinator(message)
    if not ok:
        return ok, message, info

    if kind == "NEWUSER":
        with state.lock:
            state.users[ident] = name
        log_event("usuario_adicionado", usuario=ident, nome=name)
    else:  # KILL
        with state.lock:
            removed = ident in state.server_ids
            state.server_ids.discard(ident)
        if removed:
            log_event("servidor_removido", servidor=ident)
            print(f"[ok] servidor {ident} removido da lista (KILL)")
        else:
            print(f"[aviso] KILL_{ident} enviado, mas o servidor {ident} não estava na lista")
    return ok, message, info


# ----------------------------------------------------------------------
# Servidor de controle (mensagens vindas do coordenador)
# ----------------------------------------------------------------------
def handle_control(line: str, peer: str) -> str:
    m = NEWID_RE.fullmatch(line)
    if m:
        server_id = int(m.group(1))
        with state.lock:
            new = server_id not in state.server_ids
            state.server_ids.add(server_id)
        log_event("newid_recebido", servidor=server_id, novo=new, origem=peer)
        if new:
            print(f"[ok] servidor {server_id} adicionado à lista (NEWID)")
        return f"ACK_NEWID_{server_id}"

    m = NEWSERVERIP_RE.fullmatch(line)
    if m:
        ip, port = m.group(1), int(m.group(2))
        if not 0 < port < 65536:
            return "ERROR_INVALID_PORT"
        state.set_coordinator(ip, port)
        log_event("novo_coordenador", ip=ip, porta=port, origem=peer)
        print(f"[ok] novo coordenador: {ip}:{port}")
        return f"ACK_NEWSERVERIP_{ip}_{port}"

    log_event("comando_invalido", linha=line, origem=peer)
    return "ERROR_INVALID_COMMAND"


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        try:
            for raw in self.rfile:
                line = raw.decode(ENCODING, errors="replace").strip()
                if not line:
                    continue
                reply = handle_control(line, peer)
                self.wfile.write((reply + "\n").encode(ENCODING, errors="replace"))
                self.wfile.flush()
        except OSError:
            pass 


class ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ----------------------------------------------------------------------
# Envio automático de usuários (intervalo decrescente)
# ----------------------------------------------------------------------
class AutoSender:
    """
    Envia NEWUSER com intervalo T_n = max(T_min, T_0 * r^n), em que n é o
    número de reduções já realizadas. O intervalo é medido entre o início
    de um envio e o início do próximo.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.running = False
        self.sent = 0
        self.failed = 0
        self.next_interval = None
        self.last_message = ""
        self.last_error = ""

    def start(self, t0, r, tmin, limit):
        with self.lock:
            if self.running:
                return False
            self.stop_event = threading.Event()
            self.running = True
            self.sent = 0
            self.failed = 0
            self.next_interval = None
            self.last_message = ""
            self.last_error = ""
            threading.Thread(target=self._run, args=(t0, r, tmin, limit, self.stop_event),
                             daemon=True).start()
            return True

    def stop(self):
        with self.lock:
            self.stop_event.set()

    def _run(self, t0, r, tmin, limit, stop_event):
        n = 0
        try:
            while not stop_event.is_set():
                started = time.monotonic()
                message = random_newuser()
                try:
                    ok, message, info = dispatch(message)
                except InvalidMessage as exc: 
                    ok, info = False, str(exc)

                interval = max(tmin, t0 * (r ** n))
                n += 1
                with self.lock:
                    self.last_message = message
                    self.next_interval = interval
                    if ok:
                        self.sent += 1
                        self.last_error = ""
                    else:
                        self.failed += 1
                        self.last_error = info
                    reached = limit > 0 and self.sent >= limit
                if reached:
                    break
                stop_event.wait(max(0.0, interval - (time.monotonic() - started)))
        finally:
            with self.lock:
                self.running = False

    def status(self):
        with self.lock:
            return {
                "running": self.running,
                "stopping": self.running and self.stop_event.is_set(),
                "sent": self.sent,
                "failed": self.failed,
                "next_interval": self.next_interval,
                "last_message": self.last_message,
                "last_error": self.last_error,
            }


auto_sender = AutoSender()


# ----------------------------------------------------------------------
# HTTP: frontend + API
# ----------------------------------------------------------------------

def full_status():
    host, port = state.get_coordinator()
    with state.lock:
        users = [{"id": uid, "nome": nome} for uid, nome in sorted(state.users.items())]
        server_ids = sorted(state.server_ids)
    return {
        "coordinator": f"{host}:{port}",
        "servers": server_ids,
        "users": users,
        "users_sent": len(users),
        "auto": auto_sender.status(),
        "defaults": {"t0": AUTO_T0, "r": AUTO_R, "tmin": AUTO_TMIN},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass 

    def _cors(self):
        """Permite o frontend aberto em outra porta local (ex.: Live Server :5500)."""
        origin = self.headers.get("Origin", "")
        if re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("tamanho de corpo inválido")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("o corpo deve ser um objeto JSON")
        return data

    def _send_result(self, ok: bool, message: str, info: str):
        if ok:
            self._json(200, {"ok": True, "sent": message, "reply": info})
        else:
            self._json(502, {"ok": False, "sent": message, "error": info})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            self._json(200, full_status())
            return
        if path not in STATIC_FILES:
            self.send_error(404, "Not found")
            return
        filename, content_type = STATIC_FILES[path]
        try:
            data = (FRONTEND_DIR / filename).read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/message", "/api/random", "/api/kill-random", "/api/auto"):
            self._json(404, {"ok": False, "error": "Rota não encontrada"})
            return
        try:
            body = self._read_json()
        except ValueError:
            self._json(400, {"ok": False, "error": "JSON inválido"})
            return

        if path == "/api/message":
            message = body.get("message")
            if not isinstance(message, str):
                self._json(400, {"ok": False, "error": "Campo 'message' ausente"})
                return
            try:
                ok, sent, info = dispatch(message)
            except InvalidMessage as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._send_result(ok, sent, info)
            return

        if path == "/api/random":
            ok, sent, info = dispatch(random_newuser())
            self._send_result(ok, sent, info)
            return

        if path == "/api/kill-random":
            # Sorteia um dos IDs da lista de servidores e envia KILL_<ID>.
            with state.lock:
                server_ids = sorted(state.server_ids)
            if not server_ids:
                self._json(409, {"ok": False, "error": "Nenhum servidor na lista para matar"})
                return
            ok, sent, info = dispatch(f"KILL_{random.choice(server_ids)}")
            self._send_result(ok, sent, info)
            return

        action = body.get("action")
        if action == "stop":
            auto_sender.stop()
            self._json(200, {"ok": True})
            return
        if action != "start":
            self._json(400, {"ok": False, "error": "Ação inválida (use start ou stop)"})
            return
        try:
            t0 = float(body.get("t0", AUTO_T0))
            r = float(body.get("r", AUTO_R))
            tmin = float(body.get("tmin", AUTO_TMIN))
            limit_raw = body.get("limit", 0)
            if isinstance(limit_raw, bool) or float(limit_raw) != int(float(limit_raw)):
                raise ValueError
            limit = int(float(limit_raw))
        except (TypeError, ValueError, OverflowError):
            self._json(400, {"ok": False, "error": AUTO_RULES})
            return
        if not valid_auto_params(t0, r, tmin, limit):
            self._json(400, {"ok": False, "error": AUTO_RULES})
            return
        if not auto_sender.start(t0, r, tmin, limit):
            self._json(409, {"ok": False, "error": "Envio automático já em execução"})
            return
        self._json(200, {"ok": True})


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main():
    signal.signal(signal.SIGTERM, signal.default_int_handler) 

    try:
        control = ControlServer((CONTROL_HOST, CONTROL_PORT), ControlHandler)
    except OSError as exc:
        fail(f"não foi possível abrir a porta de controle {CONTROL_PORT}: {exc}")
    try:
        http = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    except OSError as exc:
        control.server_close()
        fail(f"não foi possível abrir a porta HTTP {HTTP_PORT}: {exc}")

    threading.Thread(target=control.serve_forever, daemon=True).start()

    print(f"Frontend:            http://localhost:{HTTP_PORT}")
    print(f"Porta de controle:   {CONTROL_PORT} (NEWID / NEWSERVERIP)")
    print(f"Coordenador inicial: {SERVER_IP}:{SERVER_PORT} (servidor {INITIAL_SERVER_ID})")
    print(f"Frontend (arquivos): {FRONTEND_DIR}")
    print(f"Log:                 {LOG_FILE.name}")
    log_event("inicio", coordenador=f"{SERVER_IP}:{SERVER_PORT}",
              servidor_inicial=INITIAL_SERVER_ID, porta_controle=CONTROL_PORT)

    try:
        http.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        auto_sender.stop()
        http.server_close()
        control.shutdown()
        control.server_close()
        log_event("fim")


if __name__ == "__main__":
    main()