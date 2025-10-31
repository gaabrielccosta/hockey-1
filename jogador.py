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

# Lê o socket (não-bloqueante) e extrai mensagens completas JSON
def pump_recv(sock, buffer):
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

def notify_exit(sock):
    try:
        # Dá um tempinho pra garantir que o JSON saia do buffer
        sock.settimeout(0.3)
        send_json(sock, {"type": "bye"})
        try:
            # Sinaliza término de escrita
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    except Exception:
        # Se já caiu a conexão, seguir
        pass
    finally:
        try:
            sock.settimeout(None)
        except Exception as e:
            print(f"[Client] Erro ao fechar a conexão: {e}")


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
    pygame.display.set_caption("Hockey I")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Arial", 16, bold=True)
    bigfont = pygame.font.SysFont("Arial", 36, bold=True)

    # Conecta
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
   
    # Reduzir latência
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        print("Erro ao tentasr reduzir latência.")
        pass

    print(f"[Client] Conectando em {server_host}:{server_port} ...")
    try:
        sock.settimeout(2.0)
        sock.connect((server_host, server_port))
        sock.settimeout(None)
        sock.setblocking(False)
    except (ConnectionRefusedError, TimeoutError, OSError, socket.timeout) as e:
        print(f"[Client] Falha ao conectar ao servidor: {e}")
        pygame.quit()
        return

    # Estado local
    my_player = None
    paddles = {"p1": {"y": HEIGHT//2 - PADDLE_H//2}, "p2": {"y": HEIGHT//2 - PADDLE_H//2}}
    ball = {"x": WIDTH//2, "y": HEIGHT//2}
    score = {"p1": 0, "p2": 0}
    time_left = GAME_TIME_SECONDS
    game_over = False

    # Input (mantém estado de tecla)
    keys_state = {"up": False, "down": False}

    # Recebe hello inicial do servidor
    buffer = bytearray()
    hello_ok = False
    t0 = time.monotonic()
    while not hello_ok:
        r, _, _ = select.select([sock], [], [], 0.1)
        for _ in r:
            msgs, buffer = pump_recv(sock, buffer)
            for msg in msgs:
                if msg.get("type") == "hello":
                    my_player = msg["player"]
                    hello_ok = True
        if time.monotonic() - t0 > 5.0:
            print("[Client] Timeout aguardando hello do servidor.")
            pygame.quit()
            return

    # Loop principal
    running = True
    while running:
        # -------- Eventos --------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                notify_exit(sock)
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_UP, pygame.K_w):
                    keys_state["up"] = True
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    keys_state["down"] = True
                elif event.key == pygame.K_ESCAPE:
                    notify_exit(sock)
                    running = False

            elif event.type == pygame.KEYUP:
                if event.key in (pygame.K_UP, pygame.K_w):
                    keys_state["up"] = False
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    keys_state["down"] = False

        # Envia input atual (uma vez por frame é suficiente)
        if running:
            try:
                send_json(sock, {"type": "input", "keys": keys_state})
            except Exception as e:
                print(f"[Client] Falha ao enviar input: {e}")
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
                elif msg.get("type") == "opponent_left":
                    print("[Client] Oponente saiu. Encerrando.")
                    running = False
        except Exception as e:
            print(f"[Client] Conexão encerrada: {e}")
            running = False

        # Render
        screen.fill(COLOR_BG)

        # Linhas de campo
        pygame.draw.rect(screen, COLOR_FIELD_BORDER, (MARGIN-2, MARGIN-2, WIDTH-2*MARGIN+4, HEIGHT-2*MARGIN+4), 2)
        pygame.draw.line(screen, COLOR_FIELD_MIDLINE, (WIDTH//2, MARGIN), (WIDTH//2, HEIGHT-MARGIN), 1)

        # Paddles
        h1 = PADDLE_H // 3
        h2 = PADDLE_H // 3
        h3 = PADDLE_H - h1 - h2

        # raios seguros (não podem passar de metade de w/h do retângulo)
        r_full = min(PADDLE_W // 2, PADDLE_H // 2)
        r_top  = min(PADDLE_W // 2, h1 // 2)
        r_bot  = min(PADDLE_W // 2, h3 // 2)

        # P1 (esquerda) – canto superior e inferior arredondados no paddle inteiro
        p1x = MARGIN + 80
        p1y = int(paddles["p1"]["y"])
        pygame.draw.rect(
            screen, COLOR_PADDLE,
            (p1x, p1y, PADDLE_W, PADDLE_H),
            border_radius=r_full
        )

        # P2 (direita) – três faixas com topo e base arredondados
        p2x = WIDTH - MARGIN - PADDLE_W - 80
        p2y = int(paddles["p2"]["y"])

        # topo (arredonda só os cantos de cima)
        pygame.draw.rect(
            screen, COLOR_PADDLE2_1,
            (p2x, p2y, PADDLE_W, h1),
            border_top_left_radius=r_top, border_top_right_radius=r_top
        )

        # meio (sem arredondamento)
        pygame.draw.rect(
            screen, COLOR_PADDLE2_2,
            (p2x, p2y + h1, PADDLE_W, h2)
        )

        # base (arredonda só os cantos de baixo)
        pygame.draw.rect(
            screen, COLOR_PADDLE2_3,
            (p2x, p2y + h1 + h2, PADDLE_W, h3),
            border_bottom_left_radius=r_bot, border_bottom_right_radius=r_bot
        )

        # Goleiras
        g1x = MARGIN + 40
        g2x = WIDTH - MARGIN - GOAL_W - 40
        pygame.draw.rect(screen, COLOR_GOAL, (g1x, HEIGHT // 2 - GOAL_H // 2, GOAL_W, GOAL_H))
        pygame.draw.rect(screen, COLOR_GOAL, (g2x, HEIGHT // 2 - GOAL_H // 2, GOAL_W, GOAL_H))

        # Bola
        cx = int(ball["x"])
        cy = int(ball["y"])
        r = BALL_SIZE // 2
        pygame.draw.circle(screen, COLOR_BALL, (cx, cy), r)

        # Placar e tempo
        score_text = font.render(f"{score['p1']}  :  {score['p2']}", True, COLOR_SCORE)
        time_text  = font.render(f"Tempo: {time_left:03d}s", True, COLOR_TIME)
        screen.blit(score_text, (WIDTH//2 - score_text.get_width()//2, 8))
        screen.blit(time_text, (WIDTH - time_text.get_width() - 12, 8))

        # Etiqueta do jogador local
        who = "Você é: P1 (esquerda)" if my_player == 1 else "Você é: P2 (direita)"
        me_text = font.render(who, True, COLOR_ME)
        screen.blit(me_text, (12, 8))

        if game_over:
            over = bigfont.render("FIM DE JOGO", True, COLOR_GAMEOVER)
            screen.blit(over, (WIDTH//2 - over.get_width()//2, HEIGHT//2 - over.get_height()//2 - 20))
            winner = "Empate!"
            if score["p1"] > score["p2"]:
                winner = "Vitória do P1"
            elif score["p2"] > score["p1"]:
                winner = "Vitória do P2"
            wtxt = font.render(winner, True, COLOR_WINNER)
            screen.blit(wtxt, (WIDTH//2 - wtxt.get_width()//2, HEIGHT//2 + 20))

        pygame.display.flip()
        clock.tick(FPS)

    try:
        sock.close()
    except Exception as e:
        print(f"[Client] Erro ao fechar a conexão: {e}")
    pygame.quit()

if __name__ == "__main__":
    main()
