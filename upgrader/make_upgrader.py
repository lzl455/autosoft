#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把固件 .bin 装进 Upgrader_Template.exe，生成可以直接双击使用的一键升级程序。

用法:
    python make_upgrader.py 固件.bin [-o 输出.exe] [--title 窗口标题] [--template 模板.exe]

也可以把 .bin 文件直接拖到 make_upgrader.bat 上。

只用到 Python 标准库（Python 3.6 及以上），不需要安装其它东西。

生成的 exe = 模板 + 配置区（固件长度、标题、固件名）+ 文件末尾附加的固件。
升级程序运行时以只读方式打开自身，从“文件末尾 - 固件长度”处读出固件，
交给原程序照常解析、下载、校验。
"""
import argparse
import os
import struct
import sys

CFG_MAGIC = b'BYCFWCF1'      # 与 tools/add_auto_download.py 保持一致
TITLE_CHARS = 96             # 含结尾 0
PATH_CHARS = 128             # 含结尾 0
PATH_PREFIX = '内置固件\\'
MAX_FW = 0x10000             # 原程序一次最多读 0xC800 字节，超过的部分不会被用到


def pe_checksum(data, csum_off):
    s = 0
    n = len(data) // 2 * 2
    for (w,) in struct.iter_unpack('<H', data[:n]):
        s += w
    if len(data) % 2:
        s += data[-1]
    for i in (0, 2):                               # 不计入校验和字段本身
        s -= struct.unpack_from('<H', data, csum_off + i)[0]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s + len(data)


def build(template, fw, title, name):
    exe = bytearray(template)
    if exe[:2] != b'MZ':
        raise SystemExit('模板不是 exe 文件')
    pos = exe.find(CFG_MAGIC)
    if pos < 0 or exe.find(CFG_MAGIC, pos + 1) >= 0:
        raise SystemExit('模板里找不到唯一的配置区，请使用 Upgrader_Template.exe')
    if struct.unpack_from('<I', exe, pos + 8)[0] != 0:
        raise SystemExit('这个模板已经装过固件了，请使用原始的 Upgrader_Template.exe')
    if not 0 < len(fw) <= MAX_FW:
        raise SystemExit('固件大小 %d 字节，不在 1..%d 之间' % (len(fw), MAX_FW))

    path = PATH_PREFIX + name
    if len(path) >= PATH_CHARS:
        raise SystemExit('固件文件名太长（最多 %d 个字符）' % (PATH_CHARS - 1 - len(PATH_PREFIX)))
    title = title[:TITLE_CHARS - 1]

    o_title = pos + 12
    o_hdr = o_title + 2 * TITLE_CHARS
    o_path = o_hdr + 16
    struct.pack_into('<I', exe, pos + 8, len(fw))
    exe[o_title:o_title + 2 * TITLE_CHARS] = title.encode('utf-16le').ljust(2 * TITLE_CHARS, b'\0')
    struct.pack_into('<IIIi', exe, o_hdr, 0, len(path), PATH_CHARS - 1, -1)
    exe[o_path:o_path + 2 * PATH_CHARS] = path.encode('utf-16le').ljust(2 * PATH_CHARS, b'\0')

    exe += fw                                      # 固件必须正好在文件末尾
    pe = struct.unpack_from('<I', exe, 0x3C)[0]
    csum_off = pe + 24 + 64
    struct.pack_into('<I', exe, csum_off, pe_checksum(exe, csum_off))
    return bytes(exe)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='把固件 .bin 做成双击即可升级的 exe')
    ap.add_argument('bin', help='固件 .bin 文件')
    ap.add_argument('-o', '--output', help='输出 exe（默认: 与 .bin 同目录，<名称>_升级.exe）')
    ap.add_argument('--title', help='窗口标题（默认: 固件升级 - <文件名>）')
    ap.add_argument('--template', default=os.path.join(here, 'Upgrader_Template.exe'), help='模板 exe')
    a = ap.parse_args()

    name = os.path.basename(a.bin)
    stem = os.path.splitext(name)[0]
    out = a.output or os.path.join(os.path.dirname(os.path.abspath(a.bin)), stem + '_升级.exe')
    title = a.title if a.title is not None else '固件升级 - ' + name
    with open(a.template, 'rb') as f:
        template = f.read()
    with open(a.bin, 'rb') as f:
        fw = f.read()
    exe = build(template, fw, title, name)
    with open(out, 'wb') as f:
        f.write(exe)
    print('固件     : %s (%d 字节)' % (a.bin, len(fw)))
    print('窗口标题 : %s' % title[:TITLE_CHARS - 1])
    print('已生成   : %s' % out)


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None) and not isinstance(e.code, int):
            print('错误: %s' % e.code, file=sys.stderr)
            sys.exit(1)
        raise
