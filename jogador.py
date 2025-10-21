import socket
import struct
import json
import pygame
import select
import time
import argparse
from config import *

# ------------- Helpers framing -------------
def send_json(sock, obj):
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)

def pump_recv(sock, buffer):
    """Lê o socket (não-bloqueante) e extrai mensagens completas JSON."""
    msgs = []
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("conexão fechada")
            buffer += chunk
            while True:
                if len(buffer) < 4:
                    break
                (n,) = struct.unpack("!I", buffer[:4])
                if len(buffer) < 4 + n:
                    break
                payload = bytes(buffer[4:4+n])
                del buffer[:4+n]
                msgs.append(json.loads(payload.decode("utf-8")))
            if len(chunk) < 4096:
                break
    except BlockingIOError:
        pass
    return msgs, buffer

def main():
    parser = argparse.ArgumentParser(description="Hockey I - Cliente")
    parser.add_argument("--server", default="127.0.0.1",
                        help="IP/host do servidor (ex.: 192.168.0.10)")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"Porta TCP do servidor (default: {PORT})")
    args = parser.parse_args()

    server_host = args.server
    server_port = args.port

    pygame.init()
    pygame.display.set_caption("Hockey I (Cliente)")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 22, bold=True)
    bigfont = pygame.font.SysFont("Arial", 36, bold=True)

    # Conecta
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Opcional: reduzir latência
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    print(f"[client] Conectando em {server_host}:{server_port} ...")
    sock.connect((server_host, server_port))
    sock.setblocking(False)

    # Estado local
    my_player = None
    paddles = {"p1": {"y": HEIGHT//2 - PADDLE_H//2}, "p2": {"y": HEIGHT//2 - PADDLE_H//2}}
    ball = {"x": WIDTH//2, "y": HEIGHT//2}
    score = {"p1": 0, "p2": 0}
    time_left = GAME_TIME_SECONDS
    game_over = False

    # Input (mantém estado de tecla)
    keys_state = {"up": False, "down": False}

    # Recebe hello inicial
    buffer = bytearray()
    hello_ok = False
    t0 = time.monotonic()
    while not hello_ok:
        r, _, _ = select.select([sock], [], [], 0.1)
        for _s in r:
            msgs, buffer = pump_recv(sock, buffer)
            for m in msgs:
                if m.get("type") == "hello":
                    my_player = m["player"]
                    hello_ok = True
        if time.monotonic() - t0 > 5.0:
            print("[client] Timeout aguardando hello do servidor.")
            pygame.quit()
            return

    # Loop principal
    running = True
    while running:
        # -------- eventos --------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_UP, pygame.K_w):
                    keys_state["up"] = True
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    keys_state["down"] = True

            elif event.type == pygame.KEYUP:
                if event.key in (pygame.K_UP, pygame.K_w):
                    keys_state["up"] = False
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    keys_state["down"] = False

        # Envia input atual (uma vez por frame é suficiente)
        try:
            send_json(sock, {"type": "input", "keys": keys_state})
        except Exception as e:
            print(f"[client] Falha ao enviar input: {e}")
            running = False

        # Recebe estados (podem chegar múltiplos por frame)
        try:
            msgs, buffer = pump_recv(sock, buffer)
            for msg in msgs:
                if msg.get("type") == "state":
                    ball = msg["ball"]
                    paddles["p1"] = msg["p1"]
                    paddles["p2"] = msg["p2"]
                    score = msg["score"]
                    time_left = msg["time"]
                    game_over = msg.get("game_over", False)
        except Exception as e:
            print(f"[client] Conexão encerrada: {e}")
            running = False

        # -------- render --------
        screen.fill((14, 14, 18))

        # linhas de campo
        pygame.draw.rect(screen, (220, 220, 220), (MARGIN-2, MARGIN-2, WIDTH-2*MARGIN+4, HEIGHT-2*MARGIN+4), 2)
        pygame.draw.line(screen, (80, 80, 80), (WIDTH//2, MARGIN), (WIDTH//2, HEIGHT-MARGIN), 1)

        # paddles
        p1x = MARGIN
        p2x = WIDTH - MARGIN - PADDLE_W
        pygame.draw.rect(screen, (240, 240, 240), (p1x, int(paddles["p1"]["y"]), PADDLE_W, PADDLE_H))
        pygame.draw.rect(screen, (240, 240, 240), (p2x, int(paddles["p2"]["y"]), PADDLE_W, PADDLE_H))

        # bola
        pygame.draw.rect(screen, (255, 204, 0),
                         (int(ball["x"] - BALL_SIZE/2), int(ball["y"] - BALL_SIZE/2), BALL_SIZE, BALL_SIZE))

        # placar e tempo
        score_text = font.render(f"{score['p1']}  :  {score['p2']}", True, (250, 250, 250))
        time_text = font.render(f"Tempo: {time_left:03d}s", True, (180, 220, 255))
        screen.blit(score_text, (WIDTH//2 - score_text.get_width()//2, 8))
        screen.blit(time_text, (WIDTH - time_text.get_width() - 12, 8))

        # etiqueta do jogador local
        who = "Você é: P1 (esquerda)" if my_player == 1 else "Você é: P2 (direita)"
        me_text = font.render(who, True, (200, 255, 200))
        screen.blit(me_text, (12, 8))

        if game_over:
            over = bigfont.render("FIM DE JOGO", True, (255, 120, 120))
            screen.blit(over, (WIDTH//2 - over.get_width()//2, HEIGHT//2 - over.get_height()//2 - 20))
            winner = "Empate!"
            if score["p1"] > score["p2"]:
                winner = "Vitória do P1"
            elif score["p2"] > score["p1"]:
                winner = "Vitória do P2"
            wtxt = font.render(winner, True, (255, 220, 220))
            screen.blit(wtxt, (WIDTH//2 - wtxt.get_width()//2, HEIGHT//2 + 20))

        pygame.display.flip()
        clock.tick(FPS)

    try:
        sock.close()
    except:
        pass
    pygame.quit()

if __name__ == "__main__":
    main()
