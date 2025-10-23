import socket
import struct
import json
import time
import math
import select
import argparse
from config import *

# --------- Helpers de framing ---------
def send_json(sock, obj):
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)

# Consome um bytearray e rende mensagens JSON completas (se houver)
def recv_frames(buffer):
    out = []
    while True:
        if len(buffer) < 4:
            break
        (n,) = struct.unpack("!I", buffer[:4])
        if len(buffer) < 4 + n:
            break
        payload = bytes(buffer[4:4+n])
        del buffer[:4+n]
        out.append(json.loads(payload.decode("utf-8")))
    return out

# --------- Estado ---------
class GameState:
    def __init__(self):
        self.reset_full()

    def reset_full(self):
        self.p1_y = HEIGHT // 2 - PADDLE_H // 2
        self.p2_y = HEIGHT // 2 - PADDLE_H // 2
        self.score1 = 0
        self.score2 = 0
        self.ball_x = WIDTH // 2
        self.ball_y = HEIGHT // 2
        self.ball_vx = BALL_SPEED if time.time_ns() % 2 else -BALL_SPEED
        self.ball_vy = 0.0
        self.game_started_at = None
        self.game_over = False

    def reset_ball(self, to_left: bool):
        self.ball_x = WIDTH // 2
        self.ball_y = HEIGHT // 2
        self.ball_vx = -BALL_SPEED if to_left else BALL_SPEED
        self.ball_vy = 0.0

    def snapshot(self, remaining):
        return {
            "type": "state",
            "ball": {"x": self.ball_x, "y": self.ball_y},
            "p1": {"y": self.p1_y},
            "p2": {"y": self.p2_y},
            "score": {"p1": self.score1, "p2": self.score2},
            "time": max(0, int(remaining)),
            "game_over": self.game_over,
        }

# --------- Utilidades ---------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def paddle_rect(x_left, y_top):
    return (x_left, y_top, PADDLE_W, PADDLE_H)

def aabb_overlap(ax, ay, aw, ah, bx, by, bw, bh):
    return (ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by)

# --------- Servidor ---------
def main():
    parser = argparse.ArgumentParser(description="Hockey I - Servidor")
    parser.add_argument("--host", default="0.0.0.0", help="Endereço de escuta (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=PORT, help=f"Porta TCP (default: {PORT})")
    args = parser.parse_args()

    listen_host = args.host
    listen_port = args.port

    print(f"[Server] Iniciando em {listen_host}:{listen_port}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port))
    server.listen(2)
    server.setblocking(True)

    clients = []
    buffers = {}
    inputs = {}

    print("[Server] Aguardando jogadores...")

    # Aceita até 2 jogadores e envia hello imediatamente ao conectar
    while len(clients) < 2:
        conn, addr = server.accept()
        # reduzir latência
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        conn.setblocking(False)
        clients.append(conn)
        buffers[conn] = bytearray()
        inputs[conn] = {"up": False, "down": False}

        player_id = len(clients)  # 1 ou 2
        print(f"[Server] Cliente conectado: {addr} -> player {player_id} (total {len(clients)}/2)")

        send_json(conn, {
            "type": "hello",
            "player": player_id,
            "width": WIDTH,
            "height": HEIGHT,
            "waiting": len(clients) < 2
        })

    # Ambos conectados: avisa início de partida
    for c in clients:
        try:
            send_json(c, {"type": "match_start"})
        except:
            pass

    print("[Server] Dois jogadores conectados. Iniciando jogo!")

    state = GameState()
    state.game_started_at = time.monotonic()
    last_time = time.monotonic()

    running = True
    while running:
        # Cálculo de tempo
        now = time.monotonic()
        dt = now - last_time
        last_time = now

        elapsed = now - state.game_started_at
        remaining = GAME_TIME_SECONDS - elapsed
        if remaining <= 0 and not state.game_over:
            state.game_over = True

        # -------- Receber entradas --------
        rlist, _, _ = select.select(clients, [], [], 0)
        for sock in rlist:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("cliente desconectou")
                buffers[sock] += chunk
                for msg in recv_frames(buffers[sock]):
                    if msg.get("type") == "input":
                        inp = msg.get("keys", {})
                        inputs[sock]["up"] = bool(inp.get("up", False))
                        inputs[sock]["down"] = bool(inp.get("down", False))
                    elif msg.get("type") == "bye":
                        print(f"[Server] Cliente pediu para sair: {sock.getpeername()}")
                        # Avisa o outro cliente (se existir)
                        for oc in clients:
                            if oc is not sock:
                                try:
                                    send_json(oc, {"type": "opponent_left"})
                                except Exception as e:
                                    print(f"[Servidor] Erro ao tentar avisar jogador que o outro saiu: {e}")
                        # Encerra a partida imediatamente
                        running = False
                        break
            except Exception as e:
                print(f"[Server] Erro/saída do cliente: {e}")
                running = False

        if not running:
            break

        # -------- Atualizar jogo --------
        if not state.game_over:
            # mover paddles
            dy1 = (PADDLE_SPEED * dt) * (-1 if inputs[clients[0]]["up"] else (1 if inputs[clients[0]]["down"] else 0))
            dy2 = (PADDLE_SPEED * dt) * (-1 if inputs[clients[1]]["up"] else (1 if inputs[clients[1]]["down"] else 0))
            state.p1_y = clamp(state.p1_y + dy1, MARGIN, HEIGHT - MARGIN - PADDLE_H)
            state.p2_y = clamp(state.p2_y + dy2, MARGIN, HEIGHT - MARGIN - PADDLE_H)

            # mover bola
            state.ball_x += state.ball_vx * dt
            state.ball_y += state.ball_vy * dt

            # colisão com teto/solo
            top = MARGIN - 10
            bottom = HEIGHT - MARGIN + 10
            if state.ball_y - BALL_SIZE/2 < top:
                state.ball_y = top + BALL_SIZE/2
                state.ball_vy *= -1
            elif state.ball_y + BALL_SIZE/2 > bottom:
                state.ball_y = bottom - BALL_SIZE/2
                state.ball_vy *= -1

            # colisão com paddles
            p1x = MARGIN
            p2x = WIDTH - MARGIN - PADDLE_W

            # Recalcula retângulo da bola antes de cada checagem
            ball_rect = (
                state.ball_x - BALL_SIZE/2,
                state.ball_y - BALL_SIZE/2,
                BALL_SIZE, BALL_SIZE
            )

            # paddle 1 (esquerda)
            p1_rect = paddle_rect(p1x, state.p1_y)
            if aabb_overlap(*p1_rect, *ball_rect) and state.ball_vx < 0:
                rel = ((state.ball_y) - (state.p1_y + PADDLE_H/2)) / (PADDLE_H/2)
                rel = clamp(rel, -1, 1)
                ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                state.ball_vx =  speed * math.cos(ang)
                state.ball_vy =  speed * math.sin(ang)
                state.ball_x = p1x + PADDLE_W + BALL_SIZE/2 + 1

            # Recalcula ball_rect novamente (posições podem ter mudado)
            ball_rect = (
                state.ball_x - BALL_SIZE/2,
                state.ball_y - BALL_SIZE/2,
                BALL_SIZE, BALL_SIZE
            )

            # paddle 2 (direita)
            p2_rect = paddle_rect(p2x, state.p2_y)
            if aabb_overlap(*p2_rect, *ball_rect) and state.ball_vx > 0:
                rel = ((state.ball_y) - (state.p2_y + PADDLE_H/2)) / (PADDLE_H/2)
                rel = clamp(rel, -1, 1)
                ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                state.ball_vx = -speed * math.cos(ang)
                state.ball_vy =  speed * math.sin(ang)
                state.ball_x = p2x - BALL_SIZE/2 - 1

            # gol?
            if state.ball_x < 0:
                state.score2 += 1
                state.reset_ball(to_left=False)
            elif state.ball_x > WIDTH:
                state.score1 += 1
                state.reset_ball(to_left=True)

        # -------- Broadcast do estado --------
        snap = state.snapshot(remaining if not state.game_over else 0)
        for c in clients:
            try:
                send_json(c, snap)
            except Exception as e:
                print(f"[Server] Falha ao enviar para cliente: {e}")
                running = False

        if state.game_over or not running:
            time.sleep(1.0)
            break

        # Tick ~FPS
        frame_budget = 1.0 / FPS
        spent = time.monotonic() - now
        if spent < frame_budget:
            time.sleep(frame_budget - spent)

    print("[Server] Encerrando conexões.")
    for c in clients:
        try:
            c.close()
        except:
            pass
    server.close()

if __name__ == "__main__":
    main()
