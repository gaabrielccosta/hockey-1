import socket
import struct
import json
import time
import math
import select
import argparse
from config import *

# Manda as informações do estado do jogo
def send_json(sock, obj):
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)

# Consome um bytearray e rende mensagens JSON completas
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
    server.settimeout(0.5)

    clients = []
    buffers = {}
    inputs = {}

    print("[Server] Aguardando jogadores...")

    # Aceita até 2 jogadores e envia hello imediatamente ao conectar
    try:
        while len(clients) < 2:
            try:
                conn, addr = server.accept()  # agora com timeout
            except socket.timeout:
                continue  # volta pro topo do while, permitindo Ctrl+C
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
    except KeyboardInterrupt:
        print("\n[Server] Interrompido por Ctrl+C. Encerrando...")
        for c in clients:
            try: c.close()
            except: pass
        server.close()
        return

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

    # --- Geometria das goleiras ---
    # Boca do gol centralizada verticalmente
    GOAL_Y0 = HEIGHT // 2 - GOAL_H // 2
    GOAL_Y1 = HEIGHT // 2 + GOAL_H // 2

    # Linha de gol (frente da goleira) colada às “paredes internas” do rink
    LEFT_GOAL_X_FRONT = MARGIN + 40
    RIGHT_GOAL_X_FRONT = WIDTH - MARGIN - 40

    # Fundo da goleira (parede de trás) a GOAL_W de profundidade
    LEFT_GOAL_X_BACK = LEFT_GOAL_X_FRONT - GOAL_W
    RIGHT_GOAL_X_BACK = RIGHT_GOAL_X_FRONT + GOAL_W

    POST_T = BALL_SIZE / 2 + 1

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
        # (lidos, escritos, excepcionais) - só precisamos dos lidos
        rlist, _, _ = select.select(clients, [], [], 0)
        for sock in rlist:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Cliente desconectou")
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
                            # Se não for o socket atual
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
            # Mover paddles
            dy1 = (PADDLE_SPEED * dt) * (-1 if inputs[clients[0]]["up"] else (1 if inputs[clients[0]]["down"] else 0))
            dy2 = (PADDLE_SPEED * dt) * (-1 if inputs[clients[1]]["up"] else (1 if inputs[clients[1]]["down"] else 0))
            state.p1_y = clamp(state.p1_y + dy1, MARGIN, HEIGHT - MARGIN - PADDLE_H)
            state.p2_y = clamp(state.p2_y + dy2, MARGIN, HEIGHT - MARGIN - PADDLE_H)

            # Mover bola
            prev_x = state.ball_x
            prev_y = state.ball_y

            state.ball_x += state.ball_vx * dt
            state.ball_y += state.ball_vy * dt

            # Colisão com teto/solo
            top = MARGIN - 5
            bottom = HEIGHT - MARGIN + 5
            if state.ball_y - BALL_SIZE/2 < top:
                state.ball_y = top + BALL_SIZE/2
                state.ball_vy *= -1
            elif state.ball_y + BALL_SIZE/2 > bottom:
                state.ball_y = bottom - BALL_SIZE/2
                state.ball_vy *= -1

            # Colisão com paddles
            p1x = MARGIN + 80
            p2x = WIDTH - MARGIN - PADDLE_W - 80

            # Recalcula retângulo da bola antes de cada checagem
            ball_rect = (
                state.ball_x - BALL_SIZE/2,
                state.ball_y - BALL_SIZE/2,
                BALL_SIZE, BALL_SIZE
            )

            # ---------------- Paddle 1 (esquerda) ----------------
            p1_rect = paddle_rect(p1x, state.p1_y)
            if aabb_overlap(*p1_rect, *ball_rect):
                # Checa se a colisão é vertical (topo/base) ou lateral (frente/trás)
                bx, by, bw, bh = ball_rect
                px, py, pw, ph = p1_rect

                overlap_left = (bx + bw) - px
                overlap_right = (px + pw) - bx
                overlap_top = (by + bh) - py
                overlap_bottom = (py + ph) - by

                min_ox = min(overlap_left, overlap_right)
                min_oy = min(overlap_top, overlap_bottom)

                r = BALL_SIZE / 2.0

                if min_oy < min_ox:
                    # --- Colisão no TOPO/BASE do paddle: reflete vy e reposiciona fora ---
                    if overlap_top < overlap_bottom:
                        # Bateu no topo do paddle
                        state.ball_y = py - r - 0.1
                        state.ball_vy = -abs(state.ball_vy) if abs(state.ball_vy) > 1e-6 else -(BALL_SPEED * 0.6)
                    else:
                        # Bateu na base do paddle
                        state.ball_y = py + ph + r + 0.1
                        state.ball_vy =  abs(state.ball_vy) if abs(state.ball_vy) > 1e-6 else  (BALL_SPEED * 0.6)

                    # Pequeno empurrão horizontal baseado na altura do impacto (mantém sensação de controle)
                    rel = ((state.ball_y) - (state.p1_y + PADDLE_H/2)) / (PADDLE_H/2)
                    rel = clamp(rel, -1, 1)
                    state.ball_vx = clamp(state.ball_vx + rel * (BALL_SPEED_INC_ON_HIT * 0.2), -BALL_SPEED_MAX, BALL_SPEED_MAX)

                else:
                    # --- Colisão LATERAL (frente/trás)
                    if state.ball_vx < 0:
                        # Frente (bola indo para a esquerda)
                        rel = ((state.ball_y) - (state.p1_y + PADDLE_H/2)) / (PADDLE_H/2)
                        rel = clamp(rel, -1, 1)
                        ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                        speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                        state.ball_vx =  speed * math.cos(ang)
                        state.ball_vy =  speed * math.sin(ang)
                        state.ball_x = p1x + PADDLE_W + r + 1
                    else:
                        # Por trás (bola indo para a direita)
                        rel = ((state.ball_y) - (state.p1_y + PADDLE_H/2)) / (PADDLE_H/2)
                        rel = clamp(rel, -1, 1)
                        ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                        speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                        state.ball_vx = -speed * math.cos(ang)
                        state.ball_vy =  speed * math.sin(ang)
                        state.ball_x = p1x - r - 1

            # Recalcula ball_rect novamente (posições podem ter mudado)
            ball_rect = (
                state.ball_x - BALL_SIZE/2,
                state.ball_y - BALL_SIZE/2,
                BALL_SIZE, BALL_SIZE
            )

            # ---------------- Paddle 2 (direita) ----------------
            p2_rect = paddle_rect(p2x, state.p2_y)
            if aabb_overlap(*p2_rect, *ball_rect):
                bx, by, bw, bh = ball_rect
                px, py, pw, ph = p2_rect

                overlap_left = (bx + bw) - px
                overlap_right = (px + pw) - bx
                overlap_top = (by + bh) - py
                overlap_bottom = (py + ph) - by

                min_ox = min(overlap_left, overlap_right)
                min_oy = min(overlap_top, overlap_bottom)

                r = BALL_SIZE / 2.0

                if min_oy < min_ox:
                    # --- Colisão no TOPO/BASE do paddle ---
                    if overlap_top < overlap_bottom:
                        state.ball_y = py - r - 0.1
                        state.ball_vy = -abs(state.ball_vy) if abs(state.ball_vy) > 1e-6 else -(BALL_SPEED * 0.6)
                    else:
                        state.ball_y = py + ph + r + 0.1
                        state.ball_vy =  abs(state.ball_vy) if abs(state.ball_vy) > 1e-6 else  (BALL_SPEED * 0.6)

                    rel = ((state.ball_y) - (state.p2_y + PADDLE_H/2)) / (PADDLE_H/2)
                    rel = clamp(rel, -1, 1)
                    state.ball_vx = clamp(state.ball_vx + rel * (BALL_SPEED_INC_ON_HIT * 0.2), -BALL_SPEED_MAX, BALL_SPEED_MAX)

                else:
                    # --- Colisão LATERAL (frente/trás) ---
                    if state.ball_vx > 0:
                        # Frente (bola indo para a direita)
                        rel = ((state.ball_y) - (state.p2_y + PADDLE_H/2)) / (PADDLE_H/2)
                        rel = clamp(rel, -1, 1)
                        ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                        speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                        state.ball_vx = -speed * math.cos(ang)
                        state.ball_vy =  speed * math.sin(ang)
                        state.ball_x = p2x - r - 1
                    else:
                        # Por trás (bola indo para a esquerda)
                        rel = ((state.ball_y) - (state.p2_y + PADDLE_H/2)) / (PADDLE_H/2)
                        rel = clamp(rel, -1, 1)
                        ang = math.radians(BALL_ANGLE_MAX_DEG) * rel
                        speed = min(math.hypot(state.ball_vx, state.ball_vy) + BALL_SPEED_INC_ON_HIT, BALL_SPEED_MAX)
                        state.ball_vx =  speed * math.cos(ang)
                        state.ball_vy =  speed * math.sin(ang)
                        state.ball_x = p2x + pw + r + 1

            # --- GOLS por CRUZAMENTO e REBATES nas goleiras ---
            r = BALL_SIZE / 2

            bx_left_prev = prev_x - r
            bx_right_prev = prev_x + r
            bx_left_cur = state.ball_x - r
            bx_right_cur = state.ball_x + r

            by_prev_top = prev_y - r # “topo” da bola no frame anterior
            by_cur_top = state.ball_y - r
            by_prev_bot = prev_y + r # “base” da bola no frame anterior
            by_cur_bot = state.ball_y + r

            scored = False

            # 1) Gol à ESQUERDA: cruzou a linha pela frente e dentro da boca
            if state.ball_vx < 0 and bx_left_prev > LEFT_GOAL_X_FRONT and bx_left_cur <= LEFT_GOAL_X_FRONT:
                denom = (bx_left_prev - bx_left_cur) or 1e-9
                t = (bx_left_prev - LEFT_GOAL_X_FRONT) / denom
                y_cross = prev_y + t * (state.ball_y - prev_y)
                if GOAL_Y0 <= y_cross <= GOAL_Y1:
                    state.score2 += 1
                    state.reset_ball(to_left=False)
                    scored = True

            # 2) Gol à DIREITA: cruzou a linha pela frente e dentro da boca
            elif state.ball_vx > 0 and bx_right_prev < RIGHT_GOAL_X_FRONT and bx_right_cur >= RIGHT_GOAL_X_FRONT:
                denom = (bx_right_cur - bx_right_prev) or 1e-9
                t = (RIGHT_GOAL_X_FRONT - bx_right_prev) / denom
                y_cross = prev_y + t * (state.ball_y - prev_y)
                if GOAL_Y0 <= y_cross <= GOAL_Y1:
                    state.score1 += 1
                    state.reset_ball(to_left=True)
                    scored = True

            if not scored:
                # 3) Fundo da rede: rebate na parede de trás se entrar atrás do gol
                if bx_left_cur <= LEFT_GOAL_X_BACK - 30:
                    state.ball_vx = abs(state.ball_vx)
                    state.ball_x  = LEFT_GOAL_X_BACK - 30 + r + 0.1

                if bx_right_cur >= RIGHT_GOAL_X_BACK + 30:
                    state.ball_vx = -abs(state.ball_vx)
                    state.ball_x  = RIGHT_GOAL_X_BACK + 30 - r - 0.1

                # 3b) Boca do gol: rebate quando a bola vem por trás (sem contar gol)
                # Esquerda: bola está DENTRO (x < LEFT_GOAL_X_FRONT) e cruza o plano frontal indo para fora (→)
                if state.ball_vx > 0 and bx_left_prev < LEFT_GOAL_X_FRONT and bx_left_cur >= LEFT_GOAL_X_FRONT:
                    denom = (bx_left_cur - bx_left_prev) or 1e-9
                    t = (LEFT_GOAL_X_FRONT - bx_left_prev) / denom
                    y_cross = prev_y + t * (state.ball_y - prev_y)
                    if GOAL_Y0 <= y_cross <= GOAL_Y1:
                        state.ball_vx = -abs(state.ball_vx)
                        state.ball_x  = LEFT_GOAL_X_FRONT - r - 0.1

                # Direita: bola está DENTRO (x > RIGHT_GOAL_X_FRONT) e cruza o plano frontal indo para fora (←)
                if state.ball_vx < 0 and bx_right_prev > RIGHT_GOAL_X_FRONT and bx_right_cur <= RIGHT_GOAL_X_FRONT:
                    denom = (bx_right_prev - bx_right_cur) or 1e-9
                    t = (bx_right_prev - RIGHT_GOAL_X_FRONT) / denom
                    y_cross = prev_y + t * (state.ball_y - prev_y)
                    if GOAL_Y0 <= y_cross <= GOAL_Y1:
                        state.ball_vx =  abs(state.ball_vx)
                        state.ball_x  = RIGHT_GOAL_X_FRONT + r + 0.1


                # 4) Travessão e base por CRUZAMENTO VERTICAL dentro da profundidade do gol
                # Faixas finas centradas na linha de gol da frente (boca)
                left_front_band_prev = (LEFT_GOAL_X_FRONT - POST_T) <= prev_x <= (LEFT_GOAL_X_FRONT + POST_T)
                left_front_band_cur = (LEFT_GOAL_X_FRONT - POST_T) <= state.ball_x <= (LEFT_GOAL_X_FRONT + POST_T)
                right_front_band_prev = (RIGHT_GOAL_X_FRONT - POST_T) <= prev_x <= (RIGHT_GOAL_X_FRONT + POST_T)
                right_front_band_cur = (RIGHT_GOAL_X_FRONT - POST_T) <= state.ball_x <= (RIGHT_GOAL_X_FRONT + POST_T)

                near_any_front = (left_front_band_prev or left_front_band_cur or
                                right_front_band_prev or right_front_band_cur)

                if near_any_front:
                    # Bate no TRAVESSÃO (topo da boca) só se cruzar vindo de baixo pra cima
                    if state.ball_vy < 0 and by_prev_top > GOAL_Y0 and by_cur_top <= GOAL_Y0:
                        state.ball_y = GOAL_Y0 + r + 0.1
                        state.ball_vy = abs(state.ball_vy)

                    # Bate na BASE da boca só se cruzar vindo de cima pra baixo
                    elif state.ball_vy > 0 and by_prev_bot < GOAL_Y1 and by_cur_bot >= GOAL_Y1:
                        state.ball_y = GOAL_Y1 - r - 0.1
                        state.ball_vy = -abs(state.ball_vy)

        # -------- Broadcast do estado --------
        snap = state.snapshot(remaining if not state.game_over else 0)
        for c in clients:
            try:
                send_json(c, snap)
            except Exception as e:
                print(f"[Server] Falha ao enviar para cliente: {e}")
                running = False

        if state.game_over or not running:
            time.sleep(7.0)
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
