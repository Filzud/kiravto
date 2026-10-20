"""Хеш фото по содержимому (dHash), а не по ссылке: ссылка меняется при перезаливке."""
from __future__ import annotations

import io
from typing import Callable

import httpx
from PIL import Image


def dhash(image_bytes: bytes, size: int = 8) -> str:
    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def make_fetcher(timeout: float = 20.0) -> Callable[[str], bytes]:
    # фото лежат на CDN avatars.avto.ru, он отдаёт картинки обычному клиенту
    client = httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True)

    def fetch(url: str) -> bytes:
        r = client.get(url)
        r.raise_for_status()
        return r.content

    return fetch
