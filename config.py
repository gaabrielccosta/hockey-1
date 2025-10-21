# Configurações compartilhadas por servidor e clientes

# Porta padrão (clientes e servidor podem sobrescrever via CLI)
PORT = 50007

WIDTH, HEIGHT = 800, 480
MARGIN = 30

PADDLE_W, PADDLE_H = 14, 80
PADDLE_SPEED = 300.0  # px/s

BALL_SIZE = 12
BALL_SPEED = 320.0  # velocidade base (px/s)

FPS = 60
GAME_TIME_SECONDS = 180  # 3 minutos

# Física
BALL_SPEED_MAX = 560.0
BALL_SPEED_INC_ON_HIT = 12.0
BALL_ANGLE_MAX_DEG = 60  # limita desvio vertical ao rebater
