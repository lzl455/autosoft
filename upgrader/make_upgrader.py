#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把固件 .bin 装进 Upgrader_Template.exe，生成可以直接双击使用的一键升级程序。

用法:
    python make_upgrader.py 固件.bin [-o 输出.exe] [--title 窗口标题] [--icon 图标.ico] [--no-exit]
                            [--template 模板.exe]

也可以把 .bin 文件直接拖到 make_upgrader.bat 上；想换图标就把 .ico 一起拖上去，
或者用 --icon 图标.ico。

只用到 Python 标准库（Python 3.6 及以上），不需要安装其它东西。

生成的 exe = 模板 + 配置区（固件长度、选项、标题、固件名）+ 文件末尾附加的固件。
默认升级成功 1 秒后自动退出；加 --no-exit 则保持打开，可以连续升级多台。
升级程序运行时以只读方式打开自身，从“文件末尾 - 固件长度”处读出固件，
交给原程序照常解析、下载、校验。
"""
import argparse
import os
import struct
import sys

CFG_MAGIC = b'BYCFWCF2'      # 与 tools/add_auto_download.py 保持一致
CFG_AUTO_EXIT = 1
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


# ---------------------------------------------------------------------------
# 图标：重建整个资源目录，放进文件最后一个节 .rsrc2
# ---------------------------------------------------------------------------
RT_ICON, RT_GROUP_ICON = 3, 14
RSRC_SECTION = b'.rsrc2\0\0'


def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def _align(v, a):
    return (v + a - 1) & ~(a - 1)


def _pe_layout(exe):
    pe = _u32(exe, 0x3C)
    if exe[:2] != b'MZ' or exe[pe:pe + 4] != b'PE\0\0':
        raise SystemExit('模板不是 exe 文件')
    nsec, optsz = _u16(exe, pe + 6), _u16(exe, pe + 20)
    opt = pe + 24
    if _u16(exe, opt) != 0x10B:
        raise SystemExit('只支持 32 位 exe')
    secs = []
    for i in range(nsec):
        h = opt + optsz + 40 * i
        name = bytes(exe[h:h + 8])
        vs, va, raw, ptr = struct.unpack_from('<IIII', exe, h + 8)
        secs.append({'name': name, 'vs': vs, 'va': va, 'raw': raw, 'ptr': ptr, 'hdr': h})
    return {'pe': pe, 'opt': opt, 'optsz': optsz, 'secs': secs,
            'sect_align': _u32(exe, opt + 32), 'file_align': _u32(exe, opt + 36),
            'hdr_size': _u32(exe, opt + 60), 'dd': opt + 96}


def _rva2off(L, rva):
    for s in L['secs']:
        if s['va'] <= rva < s['va'] + max(s['vs'], s['raw']):
            return s['ptr'] + rva - s['va']
    raise SystemExit('资源地址 0x%X 不在任何节里' % rva)


def read_resources(exe, L):
    """资源目录 -> {类型: {名称: {语言: (数据, 代码页)}}}，名称为 int 或 str。"""
    root_rva = _u32(exe, L['dd'] + 8 * 2)
    base = _rva2off(L, root_rva)

    def name_of(v):
        if v & 0x80000000:
            o = base + (v & 0x7FFFFFFF)
            return exe[o + 2:o + 2 + 2 * _u16(exe, o)].decode('utf-16le')
        return v & 0xFFFF

    def walk(off, depth):
        n = _u16(exe, base + off + 12) + _u16(exe, base + off + 14)
        out = {}
        for i in range(n):
            e = base + off + 16 + 8 * i
            key, child = name_of(_u32(exe, e)), _u32(exe, e + 4)
            if depth < 2:
                if not child & 0x80000000:
                    raise SystemExit('资源目录结构异常')
                out[key] = walk(child & 0x7FFFFFFF, depth + 1)
            else:
                rva, size, cp = struct.unpack_from('<III', exe, base + child)
                o = _rva2off(L, rva)
                out[key] = (bytes(exe[o:o + size]), cp)
        return out
    return walk(0, 0)


def _sorted(d):
    return sorted(d.items(), key=lambda kv: (0, kv[0].upper()) if isinstance(kv[0], str) else (1, kv[0]))


def write_resources(tree, base_rva):
    """把资源树序列化为放在 base_rva 处的 .rsrc 内容。"""
    dirs, leaves, names = [], [], []

    def plan(node, depth):
        items = _sorted(node)
        d = {'items': items, 'children': []}
        dirs.append(d)
        for k, v in items:
            if isinstance(k, str):
                names.append(k)
            if depth < 2:
                d['children'].append(plan(v, depth + 1))
            else:
                leaves.append(v)
                d['children'].append(('leaf', len(leaves) - 1))
        return d
    plan(tree, 0)
    # 布局: 所有目录 -> 数据项 -> 名称字符串 -> 数据
    off = 0
    for d in dirs:
        d['off'] = off
        off += 16 + 8 * len(d['items'])
    leaf_off = off
    off += 16 * len(leaves)
    name_off = {}
    for nm in names:
        if nm not in name_off:
            name_off[nm] = off
            off += 2 + 2 * len(nm)
    data_off = []
    for data, _ in leaves:
        off = _align(off, 8)
        data_off.append(off)
        off += len(data)
    out = bytearray(_align(off, 4))
    for d in dirs:
        nn = sum(1 for k, _ in d['items'] if isinstance(k, str))
        struct.pack_into('<IIHHHH', out, d['off'], 0, 0, 0, 0, nn, len(d['items']) - nn)
        for i, ((k, _), c) in enumerate(zip(d['items'], d['children'])):
            key = (0x80000000 | name_off[k]) if isinstance(k, str) else k
            child = (leaf_off + 16 * c[1]) if isinstance(c, tuple) else (0x80000000 | c['off'])
            struct.pack_into('<II', out, d['off'] + 16 + 8 * i, key, child)
    for nm, o in name_off.items():
        struct.pack_into('<H', out, o, len(nm))
        out[o + 2:o + 2 + 2 * len(nm)] = nm.encode('utf-16le')
    for i, (data, cp) in enumerate(leaves):
        struct.pack_into('<IIII', out, leaf_off + 16 * i, base_rva + data_off[i], len(data), cp, 0)
        out[data_off[i]:data_off[i] + len(data)] = data
    return bytes(out)


def parse_icon(blob, label='图标'):
    """.ico 或正方形 .png -> [(GRPICONDIRENTRY 前 12 字节, 图像数据)]"""
    if blob[:8] == b'\x89PNG\r\n\x1a\n':
        w, h = struct.unpack_from('>II', blob, 16)
        if w != h or w > 256:
            raise SystemExit('%s: PNG 必须是正方形且不超过 256x256（现在是 %dx%d），可以先转成 .ico' % (label, w, h))
        return [(struct.pack('<BBBBHHI', w % 256, h % 256, 0, 0, 1, 32, len(blob)), blob)]
    if len(blob) < 6 or struct.unpack_from('<HH', blob, 0) != (0, 1):
        raise SystemExit('%s: 不是 .ico 或 .png 文件' % label)
    n = _u16(blob, 4)
    if not 0 < n <= 64:
        raise SystemExit('%s: .ico 里的图像数量不对' % label)
    imgs = []
    for i in range(n):
        w, h, cc, _, planes, bpp, size, off = struct.unpack_from('<BBBBHHII', blob, 6 + 16 * i)
        data = blob[off:off + size]
        if len(data) != size:
            raise SystemExit('%s: .ico 文件不完整' % label)
        if data[:8] == b'\x89PNG\r\n\x1a\n' or not bpp:
            planes, bpp = 1, bpp or 32
        imgs.append((struct.pack('<BBBBHHI', w, h, cc, 0, planes or 1, bpp, size), data))
    return imgs


def set_icon(exe, icon_blob, label='图标'):
    """替换 exe 的程序图标（第一个图标组）。exe 末尾不能有附加数据。"""
    exe = bytearray(exe)
    L = _pe_layout(exe)
    last = L['secs'][-1]
    if last['ptr'] + last['raw'] != len(exe):
        raise SystemExit('exe 末尾有附加数据，不能换图标')
    tree = read_resources(exe, L)
    groups = tree.get(RT_GROUP_ICON)
    if not groups:
        raise SystemExit('exe 里没有图标资源')
    gname, langs = _sorted(groups)[0]
    lang = _sorted(langs)[0][0]
    imgs = parse_icon(icon_blob, label)
    grp = struct.pack('<HHH', 0, 1, len(imgs)) + b''.join(e + struct.pack('<H', i + 1) for i, (e, _) in enumerate(imgs))
    old_ids = set()
    for _, gl in groups.items():
        for g, _cp in gl.values():
            old_ids |= {_u16(g, 6 + 14 * i + 12) for i in range(_u16(g, 4))}
    others = {k: v for k, v in tree.get(RT_ICON, {}).items() if k not in old_ids}
    if others:
        raise SystemExit('exe 里还有别的图标在用，不能安全替换')
    tree[RT_ICON] = {i + 1: {lang: (data, 0)} for i, (_, data) in enumerate(imgs)}
    tree[RT_GROUP_ICON] = dict(groups)
    tree[RT_GROUP_ICON][gname] = {lang: (grp, 0)}

    # 去掉上次放进去的 .rsrc2，再放一个新的
    old_raw = 0
    if last['name'] == RSRC_SECTION:
        del exe[last['ptr']:]
        exe[last['hdr']:last['hdr'] + 40] = bytes(40)
        old_raw = last['raw']
        L['secs'].pop()
        last = L['secs'][-1]
    hdr = L['opt'] + L['optsz'] + 40 * len(L['secs'])
    if hdr + 40 > L['hdr_size'] or any(exe[hdr:hdr + 40]):
        raise SystemExit('节表没有空位')
    va = _align(last['va'] + max(last['vs'], last['raw']), L['sect_align'])
    data = write_resources(tree, va)
    raw = _align(len(data), L['file_align'])
    ptr = len(exe)
    exe += data + bytes(raw - len(data))
    exe[hdr:hdr + 40] = struct.pack('<8sIIIIIIHHI', RSRC_SECTION, len(data), va, raw, ptr, 0, 0, 0, 0, 0x40000040)
    struct.pack_into('<H', exe, L['pe'] + 6, len(L['secs']) + 1)
    struct.pack_into('<I', exe, L['opt'] + 56, va + _align(len(data), L['sect_align']))     # SizeOfImage
    struct.pack_into('<I', exe, L['opt'] + 8, _u32(exe, L['opt'] + 8) + raw - old_raw)      # SizeOfInitializedData
    struct.pack_into('<II', exe, L['dd'] + 8 * 2, va, len(data))                            # 资源目录
    return bytes(exe)


def fix_checksum(exe):
    exe = bytearray(exe)
    csum_off = _u32(exe, 0x3C) + 24 + 64
    struct.pack_into('<I', exe, csum_off, pe_checksum(exe, csum_off))
    return bytes(exe)


def build(template, fw, title, name, auto_exit=True, icon=None):
    exe = bytearray(template)
    if exe[:2] != b'MZ':
        raise SystemExit('模板不是 exe 文件')
    if icon is not None:
        exe = bytearray(set_icon(exe, icon))
    pos = exe.find(CFG_MAGIC)
    if pos < 0 and exe.find(b'BYCFWCF') >= 0:
        raise SystemExit('模板版本与本脚本不符，请使用同一目录下的 Upgrader_Template.exe')
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

    o_title = pos + 16
    o_hdr = o_title + 2 * TITLE_CHARS
    o_path = o_hdr + 16
    struct.pack_into('<I', exe, pos + 8, len(fw))
    struct.pack_into('<I', exe, pos + 12, CFG_AUTO_EXIT if auto_exit else 0)
    exe[o_title:o_title + 2 * TITLE_CHARS] = title.encode('utf-16le').ljust(2 * TITLE_CHARS, b'\0')
    struct.pack_into('<IIIi', exe, o_hdr, 0, len(path), PATH_CHARS - 1, -1)
    exe[o_path:o_path + 2 * PATH_CHARS] = path.encode('utf-16le').ljust(2 * PATH_CHARS, b'\0')

    exe += fw                                      # 固件必须正好在文件末尾
    return fix_checksum(exe)


def make_one(path, output, title, template, auto_exit, icon=None):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    out = output or os.path.join(os.path.dirname(os.path.abspath(path)), stem + '_升级.exe')
    title = title if title is not None else '固件升级'
    with open(path, 'rb') as f:
        fw = f.read()
    exe = build(template, fw, title, name, auto_exit, icon[1] if icon else None)
    with open(out, 'wb') as f:
        f.write(exe)
    print('固件     : %s (%d 字节)' % (path, len(fw)))
    print('窗口标题 : %s' % title[:TITLE_CHARS - 1])
    print('升级成功 : %s' % ('1 秒后自动退出' if auto_exit else '保持打开，可连续升级'))
    print('图标     : %s' % (icon[0] if icon else '默认'))
    print('已生成   : %s' % out)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='把固件 .bin 做成双击即可升级的 exe')
    ap.add_argument('bin', nargs='*', help='固件 .bin 文件（可以一次给多个）；'
                    '混在里面的 .ico/.png 会被当作图标')
    ap.add_argument('-o', '--output', help='输出 exe（只给一个 .bin 时可用；默认: 与 .bin 同目录，<名称>_升级.exe）')
    ap.add_argument('--title', help='窗口标题（默认: 固件升级）')
    ap.add_argument('--icon', help='程序图标 .ico（或不超过 256x256 的正方形 .png）')
    ap.add_argument('--no-exit', action='store_true', help='升级成功后不自动退出（连续升级多台时用）')
    ap.add_argument('--template', default=os.path.join(here, 'Upgrader_Template.exe'), help='模板 exe')
    ap.add_argument('--pause', action='store_true', help='结束前等待按回车（供 make_upgrader.bat 使用）')
    a = ap.parse_args()

    failed = 0
    icon_path = a.icon
    bins = []
    for p in a.bin:
        if os.path.splitext(p)[1].lower() in ('.ico', '.png'):
            icon_path = p
        else:
            bins.append(p)
    a.bin = bins
    try:
        icon = None
        if icon_path:
            with open(icon_path, 'rb') as f:
                icon = (icon_path, f.read())
            parse_icon(icon[1], icon_path)            # 先检查一遍，有问题早报
        if not a.bin:
            print('用法: 把固件 .bin 文件拖到 make_upgrader.bat 上（可以一次拖多个），')
            print('      会在 .bin 旁边生成 <名称>_升级.exe。')
            print('      想换图标，就把 .ico 文件和 .bin 一起拖上去。')
            failed = 1
        elif a.output and len(a.bin) > 1:
            print('错误: 一次给多个 .bin 时不能用 -o')
            failed = 1
        else:
            with open(a.template, 'rb') as f:
                template = f.read()
            for path in a.bin:
                print()
                try:
                    make_one(path, a.output, a.title, template, not a.no_exit, icon)
                except SystemExit as e:
                    print('错误: %s（%s）' % (e.code, path))
                    failed += 1
                except OSError as e:
                    print('错误: %s' % e)
                    failed += 1
    except SystemExit as e:
        print('错误: %s' % e.code)
        failed = 1
    except OSError as e:
        print('错误: 读不到文件 %s（%s）' % (e.filename, e.strerror or e))
        failed = 1
    except Exception:
        import traceback
        traceback.print_exc()
        failed = 1
    if a.pause:
        print()
        try:
            input('按回车键关闭窗口...')
        except EOFError:
            pass
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
