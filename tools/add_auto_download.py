#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 BootLoader_Tools_v3.5.7.1.exe（BYC USB 烧录软件，仅有二进制）增加“自动下载模式”。

用法:
    python3 tools/add_auto_download.py [原始exe] [输出exe]

依赖: python3, pefile, capstone, nasm

补丁内容
========
1. 对话框(ID 102)底部增加复选框 “自动下载模式”(控件 ID 1050)，对话框高度加大。
2. 在设备接入处理函数(0x4023D0)的两个分支各挂一个钩子：
     * 设备以 BOOT 模式接入 -> 把状态机置为 3（“等待下载”），原程序随后
       自己显示“程序开始更新!”并执行完整的 下载->校验->跳转APP 流程；
     * 设备以正常(APP)模式接入 -> 直接调用“下载程序”按钮的处理函数，
       原程序会先发命令切到 BOOT，设备重新枚举后继续下载。
   触发条件：复选框已勾选 且 已选择烧录文件。
3. 包住“跳转APP”函数(0x404290)：跳转成功时记下时间戳。刚烧完的设备在
   SKIP_WINDOW_MS 内重新枚举回来时不再触发，避免同一只设备被反复烧录。

原程序里与本补丁有关的内容（逆向所得）
    0x56E0A4  DWORD 状态机: 0 空闲 / 3 等待进BOOT后下载 / 6 等待进BOOT后读取
                            5 下载校验成功 / 2 跳转APP成功
    0x56E0A8  BYTE  设备模式: 0 = BOOT, 非0 = 正常(APP)
    this+0x20      对话框 HWND
    this+0x10E20   烧录文件路径 CString（长度在 [ptr-0xC]）
    0x4043D0  读取设备信息 (esi = this)
    0x4049C0  “下载程序”按钮处理函数 (ecx = this)
    0x404290  跳转 APP (esi = this)，成功时把状态置 2
"""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_IMM, X86_OP_MEM

SRC_SHA256 = 'b4ded0865cb48a3a32d26869caf3763804f25c7e179db9ab98ba45a98c5dafbf'

IMAGE_BASE = 0x400000
FILE_ALIGN = 0x200

# ---- 原程序中的地址 --------------------------------------------------------
FN_GET_INFO = 0x4043D0
FN_ON_DOWNLOAD = 0x4049C0
FN_JUMP_APP = 0x404290
G_STATE = 0x56E0A4
IAT_SendDlgItemMessageW = 0x51E6CC
IAT_GetTickCount = 0x51E374

# 被改写的 call 指令（均为 E8 rel32，不带重定位）: 地址 -> (原目标, 新目标标号)
CALL_SITES = {
    0x4023FE: (FN_GET_INFO, 'hook_boot'),   # BOOT 模式接入分支
    0x402446: (FN_GET_INFO, 'hook_app'),    # 正常模式接入分支
    0x404212: (FN_JUMP_APP, 'hook_jump'),   # 读取程序成功后跳转 APP
    0x404B6C: (FN_JUMP_APP, 'hook_jump'),   # 下载程序成功后跳转 APP
}

# ---- 新增内容 --------------------------------------------------------------
DLG_ID = 102
CHK_ID = 1050
CHK_TEXT = '自动下载模式（设备接入后自动开始下载）'
CHK_RECT = (6, 141, 230, 12)          # x, y, cx, cy (DLU)
DLG_NEW_CY = 158                      # 原 142
SKIP_WINDOW_MS = 15000

STUB_ASM = r'''
bits 32
org STUB_VA

; ---- BOOT 模式接入: 替换 0x4023FE 处的 call 读取设备信息 (esi = this) ----
hook_boot:
    call    FN_GET_INFO
    call    recently_done
    test    eax, eax
    jnz     .done                       ; 刚烧完的设备又回来了 -> 不触发
    mov     eax, [G_STATE]
    cmp     eax, 3
    je      .done                       ; 已经在等待下载
    cmp     eax, 6
    je      .done                       ; 用户点的是“读取程序”
    call    auto_ready
    test    eax, eax
    jz      .done
    mov     dword [G_STATE], 3          ; 原程序随后会自己调用下载流程
.done:
    ret

; ---- 正常模式接入: 替换 0x402446 处的 call 读取设备信息 (esi = this) ----
hook_app:
    call    FN_GET_INFO
    call    recently_done
    test    eax, eax
    jnz     .done
    call    auto_ready
    test    eax, eax
    jz      .done
    mov     ecx, esi
    call    FN_ON_DOWNLOAD              ; 发切换命令, 状态置 3
.done:
    ret

; ---- 包住“跳转APP”: 成功(状态==2)时记录时间戳 ----
hook_jump:
    call    FN_JUMP_APP
    cmp     dword [G_STATE], 2
    jne     .done
    call    [IAT_GetTickCount]
    or      eax, 1                      ; 保证非 0
    mov     [G_TICK], eax
.done:
    ret

; ---- eax != 0: 距上次跳转APP成功不足 SKIP_WINDOW_MS（时间戳用后即清）----
recently_done:
    xor     eax, eax
    xchg    eax, [G_TICK]
    test    eax, eax
    jz      .ret
    push    eax
    call    [IAT_GetTickCount]
    pop     ecx
    sub     eax, ecx
    cmp     eax, SKIP_WINDOW_MS
    sbb     eax, eax                    ; 未超时 -> -1, 否则 0
.ret:
    ret

; ---- eax != 0: 已选烧录文件 且 勾选了“自动下载模式” (esi = this) ----
auto_ready:
    mov     eax, [esi + 0x10E20]        ; 烧录文件路径 CString
    cmp     dword [eax - 0x0C], 0       ; 长度
    je      .no
    push    0
    push    0
    push    0xF0                        ; BM_GETCHECK
    push    CHK_ID
    push    dword [esi + 0x20]          ; 对话框 HWND
    call    [IAT_SendDlgItemMessageW]
    and     eax, 1                      ; BST_CHECKED
    ret
.no:
    xor     eax, eax
    ret
'''


def align(v, a):
    return (v + a - 1) & ~(a - 1)


def assemble(stub_va, g_tick):
    """用 nasm 汇编桩代码，返回 (机器码, {标号: VA})。"""
    defs = {
        'STUB_VA': stub_va, 'G_TICK': g_tick, 'G_STATE': G_STATE,
        'FN_GET_INFO': FN_GET_INFO, 'FN_ON_DOWNLOAD': FN_ON_DOWNLOAD,
        'FN_JUMP_APP': FN_JUMP_APP, 'CHK_ID': CHK_ID,
        'IAT_GetTickCount': IAT_GetTickCount,
        'IAT_SendDlgItemMessageW': IAT_SendDlgItemMessageW,
        'SKIP_WINDOW_MS': SKIP_WINDOW_MS,
    }
    with tempfile.TemporaryDirectory() as td:
        src, out, mapf = (os.path.join(td, n) for n in ('stub.asm', 'stub.bin', 'stub.map'))
        with open(src, 'w') as f:
            f.write('[map symbols %s]\n' % mapf)
            for k, v in defs.items():
                f.write('%%define %s 0x%X\n' % (k, v))
            f.write(STUB_ASM)
        subprocess.check_call(['nasm', '-f', 'bin', '-o', out, src])
        code = open(out, 'rb').read()
        labels = {}
        for line in open(mapf):
            p = line.split()
            if len(p) == 3 and not p[2].startswith('.'):
                try:
                    labels[p[2]] = int(p[0], 16)
                except ValueError:
                    pass
    return code, labels


def find_abs_relocs(code, va, image_size):
    """找出桩代码里所有绝对地址操作数（需要 HIGHLOW 重定位）的 VA。"""
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    lo, hi = IMAGE_BASE, IMAGE_BASE + image_size
    relocs, total = [], 0
    for ins in md.disasm(code, va):
        total += ins.size
        branch = ins.mnemonic == 'call' or ins.mnemonic.startswith('j')
        for op in ins.operands:
            if op.type == X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
                if lo <= (op.mem.disp & 0xFFFFFFFF) < hi:
                    relocs.append(ins.address + ins.disp_offset)
            elif op.type == X86_OP_IMM and not branch and ins.imm_size == 4:
                if lo <= (op.imm & 0xFFFFFFFF) < hi:
                    relocs.append(ins.address + ins.imm_offset)
    assert total == len(code), '桩代码未能完整反汇编'
    return relocs


def parse_relocs(blob):
    blocks, o = [], 0
    while o < len(blob):
        page, size = struct.unpack_from('<II', blob, o)
        if size == 0:
            break
        blocks.append([page, list(struct.unpack_from('<%dH' % ((size - 8) // 2), blob, o + 8))])
        o += size
    return blocks


def build_relocs(blocks):
    out = bytearray()
    for page, ents in blocks:
        out += struct.pack('<II', page, 8 + 2 * len(ents)) + struct.pack('<%dH' % len(ents), *ents)
    return bytes(out)


def add_relocs(blocks, rvas):
    """把 HIGHLOW 重定位项并入（按页有序的）重定位表。"""
    for rva in sorted(rvas):
        page, ent = rva & ~0xFFF, (3 << 12) | (rva & 0xFFF)
        for i, (p, ents) in enumerate(blocks):
            if p == page:
                real = sorted([e for e in ents if e >> 12] + [ent], key=lambda e: e & 0xFFF)
                blocks[i][1] = real + ([0] if len(real) % 2 else [])   # 块长按 4 字节对齐
                break
            if p > page:
                blocks.insert(i, [page, [ent, 0]])
                break
        else:
            blocks.append([page, [ent, 0]])


def build_dialog(old):
    """在原 DLGTEMPLATEEX 末尾追加一个自动复选框。"""
    ver, sig = struct.unpack_from('<HH', old, 0)
    assert (ver, sig) == (1, 0xFFFF), '不是 DLGTEMPLATEEX'
    n, cy = struct.unpack_from('<H', old, 16)[0], struct.unpack_from('<h', old, 24)[0]
    assert (n, cy) == (14, 142), '对话框模板与预期不符'
    new = bytearray(old)
    new += b'\0' * (align(len(new), 4) - len(new))
    struct.pack_into('<H', new, 16, n + 1)
    struct.pack_into('<h', new, 24, DLG_NEW_CY)
    style = 0x50010003                 # WS_CHILD|WS_VISIBLE|WS_TABSTOP|BS_AUTOCHECKBOX
    new += struct.pack('<IIIhhhhI', 0, 0, style, *CHK_RECT, CHK_ID)
    new += struct.pack('<HH', 0xFFFF, 0x0080)                    # 窗口类: BUTTON
    new += CHK_TEXT.encode('utf-16le') + b'\0\0'
    new += struct.pack('<H', 0)                                  # 无附加数据
    return bytes(new)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'BootLoader_Tools_v3.5.7.1.exe')
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, '..', 'BootLoader_Tools_v3.5.7.1_AutoDL.exe')

    raw = open(src, 'rb').read()
    if hashlib.sha256(raw).hexdigest() != SRC_SHA256:
        sys.exit('输入文件不是预期的 BootLoader_Tools_v3.5.7.1.exe（SHA-256 不符）')

    pe = pefile.PE(data=raw)
    opt = pe.OPTIONAL_HEADER
    assert opt.ImageBase == IMAGE_BASE and opt.FileAlignment == FILE_ALIGN
    sec = {s.Name.rstrip(b'\0').decode(): s for s in pe.sections}
    assert list(sec) == ['.text', '.rdata', '.data', '.rsrc', '.reloc']
    last = pe.sections[-1]
    assert last.PointerToRawData + last.SizeOfRawData == len(raw), '文件尾部有附加数据'
    body = {n: bytearray(raw[s.PointerToRawData:s.PointerToRawData + s.SizeOfRawData]) for n, s in sec.items()}
    hdr = bytearray(raw[:opt.SizeOfHeaders])

    def rva2sec(rva):
        for n, s in sec.items():
            if s.VirtualAddress <= rva < s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData):
                return n, rva - s.VirtualAddress
        raise ValueError(hex(rva))

    # ---- 1. 变量: .data 末尾(未初始化区)借 4 字节 -------------------------
    data = sec['.data']
    g_tick_rva = align(data.VirtualAddress + data.Misc_VirtualSize, 4)
    data_vsize = g_tick_rva + 4 - data.VirtualAddress
    assert data_vsize <= align(data.Misc_VirtualSize, 0x1000)
    g_tick = IMAGE_BASE + g_tick_rva

    # ---- 2. 桩代码: 放在 .text 末尾到页边界之间的空隙 ----------------------
    text = sec['.text']
    stub_rva = align(text.VirtualAddress + text.Misc_VirtualSize, 16)
    stub_va = IMAGE_BASE + stub_rva
    code, labels = assemble(stub_va, g_tick)
    stub_end = stub_rva + len(code)
    assert stub_end <= sec['.rdata'].VirtualAddress, '.text 空隙不够'
    t = body['.text']
    assert not any(t[text.Misc_VirtualSize:]), '.text 尾部不是空的'
    t += b'\0' * (align(stub_end - text.VirtualAddress, FILE_ALIGN) - len(t))
    t[stub_rva - text.VirtualAddress:stub_end - text.VirtualAddress] = code

    for site, (old_target, label) in CALL_SITES.items():
        o = site - IMAGE_BASE - text.VirtualAddress
        assert t[o] == 0xE8 and site + 5 + struct.unpack_from('<i', t, o + 1)[0] == old_target, hex(site)
        struct.pack_into('<i', t, o + 1, labels[label] - (site + 5))

    # ---- 3. 重定位: 桩代码中的绝对地址 -------------------------------------
    reloc_dir = opt.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_BASERELOC']]
    reloc = sec['.reloc']
    assert reloc_dir.VirtualAddress == reloc.VirtualAddress
    r = body['.reloc']
    old_table = bytes(r[:reloc_dir.Size])
    blocks = parse_relocs(old_table)
    assert build_relocs(blocks) == old_table, '重定位表往返不一致'
    assert not any(r[reloc_dir.Size:]), '.reloc 尾部不是空的'
    patched = set(a - IMAGE_BASE + i for a in CALL_SITES for i in range(1, 5))
    for page, ents in blocks:
        for e in ents:
            if e >> 12:
                assert not {page + (e & 0xFFF) + i for i in range(4)} & patched, '被改写的 call 上有重定位'
    new_relocs = [va - IMAGE_BASE for va in find_abs_relocs(code, stub_va, opt.SizeOfImage)]
    add_relocs(blocks, new_relocs)
    new_table = build_relocs(blocks)
    assert len(new_table) <= reloc.Misc_VirtualSize and len(new_table) <= len(r)
    r[:len(new_table)] = new_table

    # ---- 4. 资源: 新对话框模板追加到 .rsrc 末尾 ----------------------------
    rsrc = sec['.rsrc']
    entry = None
    for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if e.id == pefile.RESOURCE_TYPE['RT_DIALOG']:
            for x in e.directory.entries:
                if x.id == DLG_ID:
                    entry = x.directory.entries[0].data.struct
    assert entry is not None
    sname, soff = rva2sec(entry.OffsetToData)
    assert sname == '.rsrc'
    dlg = build_dialog(bytes(body['.rsrc'][soff:soff + entry.Size]))
    dlg_off = align(rsrc.Misc_VirtualSize, 4)
    rsrc_vsize = dlg_off + len(dlg)
    assert rsrc_vsize <= reloc.VirtualAddress - rsrc.VirtualAddress, '.rsrc 空隙不够'
    rs = body['.rsrc']
    rs += b'\0' * (align(rsrc_vsize, FILE_ALIGN) - len(rs))
    rs[dlg_off:dlg_off + len(dlg)] = dlg
    ent_off = entry.get_file_offset() - rsrc.PointerToRawData
    struct.pack_into('<II', rs, ent_off, rsrc.VirtualAddress + dlg_off, len(dlg))

    # ---- 5. 重新排布文件并修正 PE 头 ---------------------------------------
    new_ptr, off = {}, opt.SizeOfHeaders
    for n in sec:
        new_ptr[n] = off
        off += len(body[n])
    vsize = {'.text': stub_end - text.VirtualAddress, '.data': data_vsize, '.rsrc': rsrc_vsize}
    for n, s in sec.items():
        ho = s.get_file_offset()
        struct.pack_into('<I', hdr, ho + 8, vsize.get(n, s.Misc_VirtualSize))
        struct.pack_into('<I', hdr, ho + 16, len(body[n]))
        struct.pack_into('<I', hdr, ho + 20, new_ptr[n])

    oo = opt.get_file_offset()
    struct.pack_into('<I', hdr, oo + 4, opt.SizeOfCode + len(body['.text']) - text.SizeOfRawData)
    struct.pack_into('<I', hdr, oo + 8, opt.SizeOfInitializedData + len(body['.rsrc']) - rsrc.SizeOfRawData)
    dd = {d.name: d for d in opt.DATA_DIRECTORY}
    struct.pack_into('<I', hdr, dd['IMAGE_DIRECTORY_ENTRY_RESOURCE'].get_file_offset() + 4, rsrc_vsize)
    struct.pack_into('<I', hdr, dd['IMAGE_DIRECTORY_ENTRY_BASERELOC'].get_file_offset() + 4, len(new_table))

    # 调试目录里记的是文件偏移，随节一起移动
    for d in getattr(pe, 'DIRECTORY_ENTRY_DEBUG', []):
        st = d.struct
        if st.PointerToRawData:
            n, o = rva2sec(st.AddressOfRawData)
            assert sec[n].PointerToRawData + o == st.PointerToRawData
            en, eo = None, st.get_file_offset()
            for k, s in sec.items():
                if s.PointerToRawData <= eo < s.PointerToRawData + s.SizeOfRawData:
                    en, eo = k, eo - s.PointerToRawData
            struct.pack_into('<I', body[en], eo + 24, new_ptr[n] + o)

    out = bytes(hdr) + b''.join(bytes(body[n]) for n in sec)
    chk = pefile.PE(data=out)
    chk.OPTIONAL_HEADER.CheckSum = chk.generate_checksum()
    out = chk.write()
    with open(dst, 'wb') as f:
        f.write(out)

    print('桩代码   : 0x%X..0x%X (%d 字节), 重定位 %d 处' % (stub_va, IMAGE_BASE + stub_end, len(code), len(new_relocs)))
    print('变量     : 0x%X' % g_tick)
    print('对话框   : RVA 0x%X, %d 字节' % (rsrc.VirtualAddress + dlg_off, len(dlg)))
    print('输出     : %s (%d 字节)' % (os.path.normpath(dst), len(out)))
    print('SHA-256  : %s' % hashlib.sha256(out).hexdigest())


if __name__ == '__main__':
    main()
