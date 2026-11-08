import logging

# httpx INFO 로그는 요청 URL 전체를 찍어 텔레그램 봇 토큰(api.telegram.org/bot<TOKEN>/...)이 노출된다.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
