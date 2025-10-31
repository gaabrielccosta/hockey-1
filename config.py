# Configurações compartilhadas por servidor e clientes

# Porta padrão (clientes e servidor podem sobrescrever via CLI)
PORT = 50007

WIDTH, HEIGHT = 800, 480
MARGIN = 30

PADDLE_W, PADDLE_H = 14, 40
GOAL_W, GOAL_H = 14, 120
PADDLE_SPEED = 300.0  # px/s

BALL_SIZE = 12
BALL_SPEED = 320.0  # velocidade base (px/s)

FPS = 60
GAME_TIME_SECONDS = 180  # 3 minutos

# Física
BALL_SPEED_MAX = 560.0
BALL_SPEED_INC_ON_HIT = 12.0
BALL_ANGLE_MAX_DEG = 60  # limita desvio vertical ao rebater

# config.py
COLOR_BG = (48, 48, 54)
COLOR_FIELD_BORDER = (220, 220, 220)
COLOR_FIELD_MIDLINE = (80, 80, 80)
COLOR_PADDLE = (255, 0, 0)
COLOR_PADDLE2_1 = (40, 130, 255)
COLOR_PADDLE2_2 = (255, 255, 255)
COLOR_PADDLE2_3 = (0, 0, 0)
COLOR_GOAL = (240, 240, 240)
COLOR_BALL = (255, 204, 0)

COLOR_SCORE = (250, 250, 250)
COLOR_TIME = (180, 220, 255)
COLOR_ME = (200, 255, 200)
COLOR_GAMEOVER = (255, 120, 120)
COLOR_WINNER = (255, 220, 220)
