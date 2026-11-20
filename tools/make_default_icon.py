#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成一键升级程序的默认图标 upgrader/upgrader_icon.ico：
蓝色渐变圆角底 + 白色芯片 + 绿色向上箭头（“给芯片升级固件”）；
32 像素以下简化为白色向上箭头 + 底部横条。

用法: python3 tools/make_default_icon.py      （需要 Pillow）

16~64 像素保存为 32 位 BMP（带透明），128 和 256 像素保存为 PNG。
"""
import io
import os
import struct

from PIL import Image, ImageDraw

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
BG_TL, BG_BR = (0x4F, 0x8B, 0xFF), (0x1B, 0x3F, 0xC4)    # 背景: 左上亮蓝 -> 右下深蓝
CHIP = (255, 255, 255, 255)
ARROW_TOP, ARROW_BOT = (0x22, 0xC5, 0x5E), (0x12, 0x9A, 0x48)  # 芯片上的绿色箭头


def _lerp(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _gradient(n, c1, c2, diagonal=True):
    g = Image.new('RGBA', (n, n))
    px = g.load()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2 * (n - 1)) if diagonal else y / (n - 1)
            px[x, y] = _lerp(c1, c2, t) + (255,)
    return g


def _arrow(d, cx, top, bot, half_w, shaft_half, head_frac=0.48, fill=None):
    head_bot = top + (bot - top) * head_frac
    d.polygon([(cx, top), (cx + half_w, head_bot), (cx - half_w, head_bot)], fill=fill)
    d.rectangle([cx - shaft_half, head_bot - 1, cx + shaft_half, bot], fill=fill)


def render(size):
    """>= 32 像素: 蓝色圆角底 + 白色芯片（四边引脚）+ 芯片上绿色向上箭头；
    更小的尺寸芯片细节看不清，画成白色向上箭头 + 底部横条。"""
    ss = 8 if size <= 64 else 4
    n = size * ss
    img = Image.new('RGBA', (n, n), (0, 0, 0, 0))

    # 背景圆角方块 + 顶部柔和光泽
    pad = n * (0.02 if size <= 24 else 0.05)
    box = [pad, pad, n - pad, n - pad]
    mask = Image.new('L', (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=n * 0.22, fill=255)
    img.paste(_gradient(n, BG_TL, BG_BR), (0, 0), mask)
    hl = Image.new('L', (n, n), 0)
    hd = ImageDraw.Draw(hl)
    for y in range(int(n * 0.55)):                       # 顶部向下渐隐的柔和光泽
        hd.line([(0, y), (n, y)], fill=round(38 * (1 - y / (n * 0.55)) ** 1.6))
    hl = Image.composite(hl, Image.new('L', (n, n), 0), mask)
    img = Image.composite(Image.new('RGBA', (n, n), (255, 255, 255, 255)), img, hl)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)

    cx = n / 2
    if size < 32:
        w = (255, 255, 255, 255)
        bold = 1.2 if size <= 20 else 1.0
        _arrow(d, cx, n * 0.16, n * 0.66, n * 0.30 * bold, n * 0.11 * bold, 0.6, w)
        bar_h = n * 0.09 * bold
        d.rounded_rectangle([n * 0.24, n * 0.74, n * 0.76, n * 0.74 + bar_h], radius=bar_h / 2, fill=w)
        return img.resize((size, size), Image.LANCZOS)

    # 芯片阴影
    c0, c1 = n * 0.27, n * 0.73
    sh = Image.new('L', (n, n), 0)
    ImageDraw.Draw(sh).rounded_rectangle([c0, c0 + n * 0.025, c1, c1 + n * 0.025], radius=n * 0.07, fill=70)
    img = Image.composite(Image.new('RGBA', (n, n), (0x0A, 0x1E, 0x66, 255)), img, sh)
    d = ImageDraw.Draw(img)
    # 引脚: 每边 3 根
    pin_len, pin_w = n * 0.09, n * 0.055
    for i in range(3):
        p = c0 + (c1 - c0) * (0.25 + 0.25 * i)
        d.rounded_rectangle([p - pin_w / 2, c0 - pin_len, p + pin_w / 2, c0 + 2], radius=pin_w * 0.3, fill=CHIP)
        d.rounded_rectangle([p - pin_w / 2, c1 - 2, p + pin_w / 2, c1 + pin_len], radius=pin_w * 0.3, fill=CHIP)
        d.rounded_rectangle([c0 - pin_len, p - pin_w / 2, c0 + 2, p + pin_w / 2], radius=pin_w * 0.3, fill=CHIP)
        d.rounded_rectangle([c1 - 2, p - pin_w / 2, c1 + pin_len, p + pin_w / 2], radius=pin_w * 0.3, fill=CHIP)
    # 芯片本体
    d.rounded_rectangle([c0, c0, c1, c1], radius=n * 0.07, fill=CHIP)
    # 绿色向上箭头（渐变）
    am = Image.new('L', (n, n), 0)
    _arrow(ImageDraw.Draw(am), cx, n * 0.335, n * 0.665, n * 0.155, n * 0.058, 0.5, 255)
    img.paste(_gradient(n, ARROW_TOP, ARROW_BOT, diagonal=False), (0, 0), am)
    return img.resize((size, size), Image.LANCZOS)


def dib(img):
    """32 位 BITMAPINFOHEADER + BGRA（自底向上）+ AND 掩码。"""
    s = img.size[0]
    px = img.load()
    xor = bytearray()
    for y in range(s - 1, -1, -1):
        for x in range(s):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    row = ((s + 31) // 32) * 4
    andm = bytearray()
    for y in range(s - 1, -1, -1):
        bits = bytearray(row)
        for x in range(s):
            if px[x, y][3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        andm += bits
    hdr = struct.pack('<IiiHHIIiiII', 40, s, 2 * s, 1, 32, 0, len(xor) + len(andm), 0, 0, 0, 0)
    return hdr + bytes(xor) + bytes(andm)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, '..', 'upgrader', 'upgrader_icon.ico')
    images = []
    for s in SIZES:
        img = render(s)
        if s >= 128:
            b = io.BytesIO()
            img.save(b, 'PNG', optimize=True)
            images.append((s, b.getvalue()))
        else:
            images.append((s, dib(img)))
    data = struct.pack('<HHH', 0, 1, len(images))
    off = 6 + 16 * len(images)
    for s, blob in images:
        data += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(blob), off)
        off += len(blob)
    data += b''.join(blob for _, blob in images)
    with open(out, 'wb') as f:
        f.write(data)
    print('已生成 %s (%d 字节)' % (os.path.normpath(out), len(data)))


if __name__ == '__main__':
    main()
