#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 BootLoader_Tools_v3.5.7.1.exe（BYC USB 烧录软件，仅有二进制）增加“自动下载模式”，
或生成“一键升级程序”的模板。

用法:
    python3 tools/add_auto_download.py [原始exe] [输出exe]
        -> BootLoader_Tools_v3.5.7.1_AutoDL.exe（带“自动下载模式”复选框）
    python3 tools/add_auto_download.py --upgrader [原始exe] [输出exe]
        -> upgrader/Upgrader_Template.exe（一键升级程序模板，
           再用 upgrader/make_upgrader.py 把 .bin 装进去）

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
4. 记住勾选状态：保存在注册表 HKCU\\Software\\BYC\\BootLoader_Tools 的
   AutoDownload (DWORD) 里。点击复选框时立即写入（挂在对话框 WindowProc 转给
   基类的那个 call 上），OnInitDialog 里打开设备之前读回并设置复选框。

一键升级模板（--upgrader）在上面 1~3 的基础上：
5. 固件附加在 exe 文件末尾（由 make_upgrader.py 写入），模板里留有一块以
   CFG_MAGIC 开头的配置区：固件长度、选项、窗口标题、伪造的 CString（内容为
   “内置固件\\<文件名>”）。
6. “打开文件”处理函数(0x4026B0)里：CFileDialog::DoModal 直接返回 IDOK；
   给烧录文件路径赋值时换成上面的伪造 CString。于是原程序照常解析固件、
   显示版本和校验和。
7. 打开烧录文件的 3 处 CreateFileW 换成钩子：文件名等于“内置固件\\...”时，
   改为以只读共享方式打开 exe 自身，并把文件指针移到“文件末尾 - 固件长度”。
   之后原程序自己的 ReadFile/CloseHandle 读到的就是固件，长度也与原文件一致。
8. 不再需要复选框，自动下载始终开启；设备接入时先载入内置固件再开始下载。
   启动时已经连着的设备：OnInitDialog 结束后投递一条 WM_COMMAND(KICK_ID)，
   在消息循环里载入固件并开始下载（避免在窗口显示之前就开始烧录）。
9. 精简界面：对话框加宽变矮，只留进度条和右侧百分比，其余控件（包括操作信息、
   连接状态）隐藏（仍在，原程序照常读写）；加上标题栏关闭按钮；标题改为配置区里的标题。
   百分比：设置进度条位置的 5 处 SendMessageW(PBM_SETPOS) 换成钩子，发完后把
   位置写到百分比标签并立即重绘（下载时原程序不跑消息循环）。
   进度条一直显示；右侧标签平时显示百分比，没插设备时显示“请插入USB设备”（进度条清零），
   升级成功后显示“升级成功”。设备接入处理函数的断开分支(0x402474 处的 GetDlgItem)
   和两个接入分支各挂一次。升级途中切到 BOOT (状态 3/6) 时设备会短暂断开，不提示；
   升级成功后跳回 APP 的那一次断开也不提示，保留“升级成功”。
   美化：对话框字体换成微软雅黑，窗口背景白色 (WM_CTLCOLORDLG)，右侧标签用放大 4/3 的
   粗体（按对话框字体算，跟随 DPI），文字颜色按内容区分 (WM_CTLCOLORSTATIC)：
   百分比深灰、“请插入USB设备”蓝色、“升级成功”绿色。
11. 升级失败时进度条变红 (PBM_SETSTATE PBST_ERROR)，右侧标签换成“重试”按钮。失败判定:
    “下载程序”里调用下载流程(0x404B2F)后状态不是 5，或跳转 APP(0x404B6C)后状态不是 2。
    点“重试”: 设备还连着就重新载入固件并下载，已拔出就显示“请插入USB设备”。
    重新下载、设备拔出或接入时恢复正常显示。
   程序图标换成 upgrader/upgrader_icon.ico（资源目录整体重建到新增的 .rsrc 节，原节改名 .oldrsrc，
   用的是 upgrader/make_upgrader.py 里的 set_icon，生成升级程序时还可以再换）。
10. 升级成功后自动退出（配置区选项 CFG_AUTO_EXIT）：“下载程序”流程里跳转 APP 成功
    (0x404B6C) 时显示“升级成功”，启动一个 EXIT_DELAY_MS 的定时器；定时器到点后
    投递 WM_COMMAND(IDCANCEL)，与点“退出”相同。“读取程序”后的跳转(0x404212)不触发。

原程序里与本补丁有关的内容（逆向所得）
    0x56E0A4  DWORD 状态机: 0 空闲 / 3 等待进BOOT后下载 / 6 等待进BOOT后读取
                            5 下载校验成功 / 2 跳转APP成功
    0x56E0A8  BYTE  设备模式: 0 = BOOT, 非0 = 正常(APP)
    this+0x20      对话框 HWND
    this+0x10E20   烧录文件路径 CString（长度在 [ptr-0xC]）
    0x4043D0  读取设备信息 (esi = this)
    0x4049C0  “下载程序”按钮处理函数 (ecx = this)
    0x404290  跳转 APP (esi = this)，成功时把状态置 2
    0x4024D0  查找并打开设备 (edi = this)，OnInitDialog 在 0x40223A 调用
    0x4068DB  基类 WindowProc (ecx = this; msg, wParam, lParam)，
              对话框自己的 WindowProc(0x4025A0, edi = this) 在 0x402611 调用
    0x4026B0  “打开文件”按钮处理函数 (ecx = this)：选文件、读文件、按芯片解析
    0x41592D  CFileDialog::DoModal，在 0x40271F 调用
    0x4013C0  CString::operator=(const CString&)，在 0x402782 给路径赋值
              源串的 StringMgr 与目标不同时只读源串的数据和 [data-0xC] 长度
    0x4027C1 / 0x402ADB / 0x403411  call [CreateFileW] 打开烧录文件
    0x56E060  DWORD 芯片 ID（决定读取长度）
"""
import argparse
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
FN_OPEN_DEVICE = 0x4024D0
FN_BASE_WNDPROC = 0x4068DB
FN_OPEN_FILE = 0x4026B0
FN_FILE_DOMODAL = 0x41592D
FN_CSTR_ASSIGN = 0x4013C0
FN_CWND_GETDLGITEM = 0x40C5CE
FN_DOWNLOAD = 0x403190
G_STATE = 0x56E0A4
G_CHIP = 0x56E060
IAT_SendDlgItemMessageW = 0x51E6CC
IAT_GetTickCount = 0x51E374
IAT_CheckDlgButton = 0x51E6C8
IAT_RegQueryValueExW = 0x51E000
IAT_RegOpenKeyExW = 0x51E004
IAT_RegCreateKeyExW = 0x51E008
IAT_RegSetValueExW = 0x51E00C
IAT_RegCloseKey = 0x51E020
IAT_CreateFileW = 0x51E204
IAT_CloseHandle = 0x51E208
IAT_lstrcmpW = 0x51E23C
IAT_GetModuleFileNameW = 0x51E2A8
IAT_SetFilePointer = 0x51E3D0
IAT_SetWindowTextW = 0x51E6C0
IAT_PostMessageW = 0x51E830
IAT_SendMessageW = 0x51E844
IAT_GetDlgItem = 0x51E754
IAT_UpdateWindow = 0x51E84C
IAT_GetObjectW = 0x51E1A8
IAT_CreateFontIndirectW = 0x51E0C4
IAT_SetTextColor = 0x51E1B0
IAT_SetBkMode = 0x51E194
IAT_GetStockObject = 0x51E0AC
IAT_ShowWindow = 0x51E6B8
IAT_SetTimer = 0x51E5E8
IAT_KillTimer = 0x51E5E4

# 被改写的 call 指令（均为 E8 rel32，不带重定位）: 地址 -> (原目标, 新目标标号)
CALL_SITES = {
    0x4023FE: (FN_GET_INFO, 'hook_boot'),   # BOOT 模式接入分支
    0x402446: (FN_GET_INFO, 'hook_app'),    # 正常模式接入分支
    0x404212: (FN_JUMP_APP, 'hook_jump'),   # 读取程序成功后跳转 APP
    0x404B6C: (FN_JUMP_APP, 'hook_jump'),   # 下载程序成功后跳转 APP
    0x40223A: (FN_OPEN_DEVICE, 'hook_init'),        # OnInitDialog: 首次打开设备之前
    0x402611: (FN_BASE_WNDPROC, 'hook_wndproc'),    # WindowProc: 转给基类之前
}
# 一键升级模板额外改写的 call（E8 rel32）
UPG_CALL_SITES = {
    0x404B6C: (FN_JUMP_APP, 'hook_jump_dl'),        # 下载成功后跳转 APP -> 自动退出 / 失败显示重试
    0x404B2F: (FN_DOWNLOAD, 'hook_download'),       # 下载流程 -> 失败显示重试
    0x402474: (FN_CWND_GETDLGITEM, 'hook_disc'),    # 设备断开 -> 显示“请插入USB设备”
    0x40271F: (FN_FILE_DOMODAL, 'hook_domodal'),    # 不弹选择文件对话框
    0x402782: (FN_CSTR_ASSIGN, 'hook_assign'),      # 路径 = “内置固件\<名称>”
}
# 一键升级模板额外改写的 call [IAT]（FF 15 abs32，6 字节，带重定位）-> call rel32 + nop
UPG_IAT_SITES = {
    0x4027C1: (IAT_CreateFileW, 'hook_createfile'),
    0x402ADB: (IAT_CreateFileW, 'hook_createfile'),
    0x403411: (IAT_CreateFileW, 'hook_createfile'),
    0x403168: (IAT_SendMessageW, 'hook_setpos'),    # 进度条 PBM_SETPOS
    0x40321C: (IAT_SendMessageW, 'hook_setpos'),
    0x403E8F: (IAT_SendMessageW, 'hook_setpos'),
    0x4040D3: (IAT_SendMessageW, 'hook_setpos'),
    0x404A1F: (IAT_SendMessageW, 'hook_setpos'),
}

# ---- 新增内容 --------------------------------------------------------------
DLG_ID = 102
CHK_ID = 1050
CHK_TEXT = '自动下载模式（设备接入后自动开始下载）'
CHK_RECT = (6, 141, 230, 12)          # x, y, cx, cy (DLU)
DLG_NEW_CY = 158                      # 原 142
SKIP_WINDOW_MS = 15000
REG_KEY = r'Software\BYC\BootLoader_Tools'    # 在 HKEY_CURRENT_USER 下
REG_VALUE = 'AutoDownload'

# 一键升级模板
KICK_ID = 1051                        # 启动后投递的 WM_COMMAND id
UPG_DLG_TITLE = '固件升级'             # 资源里的标题（配置区标题为空时显示）
PCT_ID = 1052                         # 百分比标签
UPG_DLG_SIZE = (300, 44)              # 精简后的对话框大小 (DLU)
UPG_LAYOUT = {                        # 留下的控件: 识别方式 -> (x, y, cx, cy)
    1007: (14, 16, 182, 12),          # 进度条
}
PCT_RECT = (200, 8, 88, 28)           # 进度条右侧标签（右对齐、垂直居中）
UPG_FONT = (9, 400, 'Microsoft YaHei')   # 对话框字体: 磅值, 粗细, 字体名（Vista 起自带）
# 标签颜色 (COLORREF = 0x00BBGGRR)
CLR_PERCENT = 0x0037291F              # #1F2937 深灰
CLR_PROMPT = 0x00EB6325               # #2563EB 蓝
CLR_DONE = 0x004AA316                 # #16A34A 绿
RETRY_ID = 1054                       # 失败后出现的“重试”按钮
RETRY_RECT = (226, 13, 62, 18)
STR_NOUSB = '请插入USB设备'
STR_DONE = '升级成功'
EXIT_TIMER_ID = 0xB1C5
EXIT_DELAY_MS = 1000
CFG_AUTO_EXIT = 1                     # 配置区选项位: 升级成功后自动退出
UPG_SECTION = b'.upg'
CFG_MAGIC = b'BYCFWCF2'               # 配置区: magic(8) 固件长度(4) 选项(4) 标题 CString头(16) 路径
TITLE_CHARS = 96                      # 含结尾 0
PATH_CHARS = 128                      # 含结尾 0
PATH_PREFIX = '内置固件\\'

STUB_ASM = r'''
bits 32
org STUB_VA

; ---- BOOT 模式接入: 替换 0x4023FE 处的 call 读取设备信息 (esi = this) ----
hook_boot:
    call    FN_GET_INFO
%if UPGRADER
    call    ui_connected
    cmp     byte [G_INIT], 0
    jne     .done                       ; 启动时的首次连接交给 KICK 消息
%endif
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
%if UPGRADER
    call    load_fw
%endif
    mov     dword [G_STATE], 3          ; 原程序随后会自己调用下载流程
.done:
    ret

; ---- 正常模式接入: 替换 0x402446 处的 call 读取设备信息 (esi = this) ----
hook_app:
    call    FN_GET_INFO
%if UPGRADER
    call    ui_connected
    cmp     byte [G_INIT], 0
    jne     .done
%endif
    call    recently_done
    test    eax, eax
    jnz     .done
    call    auto_ready
    test    eax, eax
    jz      .done
%if UPGRADER
    call    load_fw
%endif
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

%if UPGRADER
; ---- eax != 0: 已内置固件 ----
auto_ready:
    mov     eax, [fw_len]
    neg     eax
    sbb     eax, eax
    ret

; ---- 载入内置固件：调用“打开文件”处理函数 (esi = this) ----
load_fw:
    mov     ecx, esi
    jmp     FN_OPEN_FILE

; ---- OnInitDialog: 替换 0x40223A 处的 call 打开设备 (edi = this) ----
hook_init:
    mov     eax, [edi + 0x20]
    mov     [G_HWND], eax
    call    init_label_font
    mov     byte [G_INIT], 1
    call    FN_OPEN_DEVICE
    mov     byte [G_INIT], 0
    cmp     word [cfg_title], 0
    je      .post
    push    cfg_title
    push    dword [edi + 0x20]
    call    [IAT_SetWindowTextW]
.post:
    push    0
    push    KICK_ID
    push    0x111                       ; WM_COMMAND
    push    dword [edi + 0x20]
    call    [IAT_PostMessageW]
    ret

; ---- 右侧标签换成放大 4/3 的粗体（以对话框字体为基准，自动跟随 DPI）----
init_label_font:
    sub     esp, 92                     ; LOGFONTW
    push    0
    push    0
    push    0x31                        ; WM_GETFONT
    push    dword [G_HWND]
    call    [IAT_SendMessageW]
    test    eax, eax
    jz      .out
    mov     ecx, esp
    push    ecx
    push    92
    push    eax
    call    [IAT_GetObjectW]
    test    eax, eax
    jz      .out
    mov     eax, [esp]                  ; lfHeight（通常为负数）
    shl     eax, 2
    cdq
    mov     ecx, 3
    idiv    ecx
    mov     [esp], eax
    mov     dword [esp + 16], 700       ; lfWeight = FW_BOLD
    mov     byte [esp + 26], 5          ; lfQuality = CLEARTYPE_QUALITY
    push    esp
    call    [IAT_CreateFontIndirectW]
    test    eax, eax
    jz      .out
    push    eax
    push    PCT_ID
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    pop     ecx
    test    eax, eax
    jz      .out
    push    1
    push    ecx
    push    0x30                        ; WM_SETFONT
    push    eax
    call    [IAT_SendMessageW]
.out:
    add     esp, 92
    ret

; ---- WindowProc: 替换 0x402611 处转给基类的 call ----
; ecx = edi = this, [esp+4] = msg, [esp+8] = wParam, [esp+12] = lParam
hook_wndproc:
    cmp     dword [esp + 4], 0x136      ; WM_CTLCOLORDLG -> 白色背景
    je      .white
    cmp     dword [esp + 4], 0x138      ; WM_CTLCOLORSTATIC
    jne     .timer
    push    PCT_ID
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    mov     ecx, edi
    cmp     eax, [esp + 12]             ; 只管右侧标签
    jne     .pass
    mov     eax, CLR_PERCENT
    cmp     byte [G_LBL], 1
    jne     .c2
    mov     eax, CLR_PROMPT
.c2:
    cmp     byte [G_LBL], 2
    jne     .c3
    mov     eax, CLR_DONE_X             ; 见 assemble(): 避免立即数落在映像地址范围内
    and     eax, 0x00FFFFFF
.c3:
    push    eax
    push    dword [esp + 12]            ; hdc
    call    [IAT_SetTextColor]
    push    1                           ; TRANSPARENT
    push    dword [esp + 12]
    call    [IAT_SetBkMode]
.white:
    push    0                           ; WHITE_BRUSH
    call    [IAT_GetStockObject]
    ret     12
.timer:
    cmp     dword [esp + 4], 0x113      ; WM_TIMER
    jne     .cmd
    cmp     dword [esp + 8], EXIT_TIMER_ID
    jne     .pass
    push    EXIT_TIMER_ID
    push    dword [edi + 0x20]
    call    [IAT_KillTimer]
    push    0
    push    2                           ; IDCANCEL = 点“退出”
    push    0x111
    push    dword [edi + 0x20]
    call    [IAT_PostMessageW]
    mov     ecx, edi
    jmp     .pass
.cmd:
    cmp     dword [esp + 4], 0x111      ; WM_COMMAND
    jne     .pass
    cmp     dword [esp + 8], RETRY_ID   ; 点了“重试”
    jne     .kick
    call    clear_fail
    cmp     byte [edi + 0x10E28], 0
    jne     .go
    call    ui_disconnected             ; 设备已拔出 -> 提示插入
    mov     ecx, edi
    jmp     .pass
.kick:
    cmp     dword [esp + 8], KICK_ID
    jne     .pass
%ifdef SELFTEST
.go:
    mov     dword [G_CHIP], 0x96FF00    ; 自测: 假装连着 BYC951，只载入固件
    push    esi
    mov     esi, edi
    call    load_fw
    pop     esi
%else
    cmp     byte [edi + 0x10E28], 0     ; 启动时设备已连接?
    je      .pass
.go:
    call    auto_ready
    test    eax, eax
    jz      .pass
    push    esi
    mov     esi, edi
    call    load_fw
    mov     ecx, edi
    call    FN_ON_DOWNLOAD
    pop     esi
%endif
    mov     ecx, edi
.pass:
    jmp     FN_BASE_WNDPROC

; ---- 替换 0x404B6C 处的跳转 APP（只在“下载程序”流程里, esi = this）----
hook_jump_dl:
    call    hook_jump
    cmp     dword [G_STATE], 2          ; 跳转成功 = 升级成功
    jne     .fail
    mov     byte [G_UI], 2
    mov     byte [G_LBL], 2
    push    str_done
    call    set_label
    test    byte [cfg_flags], CFG_AUTO_EXIT
    jz      .done
    push    0
    push    EXIT_DELAY_MS
    push    EXIT_TIMER_ID
    push    dword [esi + 0x20]
    call    [IAT_SetTimer]
.done:
    ret
.fail:
    jmp     on_fail

; ---- 替换 0x404B2F 处“下载程序”里对下载流程的调用（ebx = esi = this）----
hook_download:
    call    clear_fail
    call    FN_DOWNLOAD
    cmp     dword [G_STATE], 5          ; 5 = 下载校验成功
    je      .ok
    call    on_fail
.ok:
    ret

; ---- 升级失败：进度条变红，右侧标签换成“重试”按钮 ----
on_fail:
    mov     byte [G_FAIL], 1
    mov     eax, 2                      ; PBST_ERROR
    jmp     fail_ui

; ---- 恢复正常显示（没在失败状态时什么都不做）----
clear_fail:
    cmp     byte [G_FAIL], 0
    je      .r
    mov     byte [G_FAIL], 0
    mov     eax, 1                      ; PBST_NORMAL
    jmp     fail_ui
.r:
    ret

; ---- eax = 进度条状态: 2 失败(显示重试按钮) / 1 正常(显示标签) ----
fail_ui:
    push    esi
    mov     esi, eax
    push    1007
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    test    eax, eax
    jz      .btn
    push    0
    push    esi
    push    0x410                       ; PBM_SETSTATE
    push    eax
    call    [IAT_SendMessageW]
.btn:
    xor     eax, eax                    ; SW_HIDE
    cmp     esi, 2
    jne     .set
    mov     al, 5                       ; SW_SHOW
.set:
    push    eax
    push    eax
    push    RETRY_ID
    call    show_ctl
    pop     eax
    xor     eax, 5
    push    eax
    push    PCT_ID
    call    show_ctl
    pop     esi
    ret

; ---- [esp+4] = 控件 ID, [esp+8] = ShowWindow 参数 ----
show_ctl:
    push    dword [esp + 4]
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    test    eax, eax
    jz      .r
    push    dword [esp + 8]
    push    eax
    call    [IAT_ShowWindow]
.r:
    ret     8

; ---- 替换 0x402474 处的 CWnd::GetDlgItem(-1)（设备断开分支, ecx = this）----
hook_disc:
    push    dword [esp + 4]
    call    FN_CWND_GETDLGITEM
    push    eax
    mov     eax, [G_STATE]
    cmp     eax, 3                      ; 正在切到 BOOT（会短暂断开）
    je      .keep
    cmp     eax, 6
    je      .keep
    call    ui_disconnected
.keep:
    pop     eax
    ret     4

; ---- 设备断开：进度条清零，标签显示“请插入USB设备” ----
; G_UI: 0 平时, 1 显示着“请插入USB设备”, 2 刚升级成功, 3 刚升级成功且设备已跳回 APP 断开过一次
ui_disconnected:
    cmp     byte [G_UI], 2
    jne     .hint
    mov     byte [G_UI], 3              ; 升级成功后跳回 APP 的断开，保留“升级成功”
    ret
.hint:
    call    clear_fail
    mov     byte [G_UI], 1
    push    1007
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    test    eax, eax
    jz      .label
    push    0
    push    0
    push    0x402                       ; PBM_SETPOS 0（直接发，不经过钩子）
    push    eax
    call    [IAT_SendMessageW]
.label:
    mov     byte [G_LBL], 1
    push    str_nousb
    call    set_label
    ret

; ---- 设备接入：如果正显示“请插入USB设备”，换回 0% ----
ui_connected:
    call    clear_fail
    mov     al, [G_UI]
    mov     byte [G_UI], 0
    cmp     al, 1
    jne     .r
    mov     byte [G_LBL], 0
    push    str_zero
    call    set_label
.r:
    ret

; ---- [esp+4] = 文字，写到进度条右侧标签并立即重绘 ----
set_label:
    push    PCT_ID
    push    dword [G_HWND]
    call    [IAT_GetDlgItem]
    test    eax, eax
    jz      .r
    push    eax                         ; UpdateWindow 的参数
    push    dword [esp + 8]
    push    eax
    call    [IAT_SetWindowTextW]
    call    [IAT_UpdateWindow]
.r:
    ret     4

; ---- 替换设置进度条位置的 call [SendMessageW]: 同时更新百分比标签 ----
hook_setpos:
    push    dword [esp + 16]
    push    dword [esp + 16]
    push    dword [esp + 16]
    push    dword [esp + 16]
    call    [IAT_SendMessageW]
    push    eax
    cmp     dword [esp + 12], 0x402     ; PBM_SETPOS
    jne     .ret
    mov     eax, [esp + 16]             ; 位置 0~100
    call    show_percent
.ret:
    pop     eax
    ret     16

; ---- eax = 百分比，写到 PCT_ID 标签并立即重绘 ----
show_percent:
    push    ebx
    mov     ebx, G_PCTBUF
    mov     ecx, 10
    cmp     eax, 100
    jb      .lt100
    mov     dword [ebx], 0x00300031     ; "10"
    mov     dword [ebx + 4], 0x00250030 ; "0%"
    mov     word [ebx + 8], 0
    jmp     .set
.lt100:
    cmp     eax, 10
    jb      .one
    xor     edx, edx
    div     ecx
    add     eax, '0'
    mov     [ebx], ax
    add     edx, '0'
    mov     [ebx + 2], dx
    mov     dword [ebx + 4], '%'
    jmp     .set
.one:
    add     eax, '0'
    mov     [ebx], ax
    mov     dword [ebx + 2], '%'
.set:
    mov     byte [G_LBL], 0
    push    ebx
    call    set_label
    pop     ebx
    ret

; ---- 替换 0x40271F 处的 CFileDialog::DoModal: 直接返回 IDOK ----
hook_domodal:
    mov     eax, 1
    ret

; ---- 替换 0x402782 处的 CString 赋值: 改用内置固件的伪 CString ----
; ecx = &this->path, [esp+4] = GetPathName 的结果（忽略）
hook_assign:
    push    fw_path                     ; 伪 CString 对象 = 指向数据的指针
    mov     eax, esp
    push    eax
    call    FN_CSTR_ASSIGN
    pop     ecx
    ret     4

; ---- 替换 call [CreateFileW]: 打开内置固件 ----
hook_createfile:
    push    fw_path
    push    dword [esp + 8]             ; lpFileName
    call    [IAT_lstrcmpW]
    test    eax, eax
    jnz     .real
    call    open_fw
    ret     28
.real:
    jmp     [IAT_CreateFileW]

; ---- eax = 指向固件开头的 exe 自身句柄，失败为 -1 ----
open_fw:
    sub     esp, 0x410
    mov     eax, esp
    push    0x208
    push    eax
    push    0
    call    [IAT_GetModuleFileNameW]
    test    eax, eax
    jz      .fail
    cmp     eax, 0x208
    jae     .fail
    mov     eax, esp
    push    0
    push    0x80                        ; FILE_ATTRIBUTE_NORMAL
    push    3                           ; OPEN_EXISTING
    push    0
    push    1                           ; FILE_SHARE_READ
    push    0x80000000                  ; GENERIC_READ
    push    eax
    call    [IAT_CreateFileW]
    cmp     eax, -1
    je      .out
    push    eax
    push    2                           ; FILE_END
    push    0
    mov     ecx, [fw_len]
    neg     ecx
    push    ecx
    push    eax
    call    [IAT_SetFilePointer]
    cmp     eax, -1
    pop     eax
    jne     .out
    push    eax
    call    [IAT_CloseHandle]
.fail:
    or      eax, -1
.out:
    add     esp, 0x410
    ret

%else
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

; ---- OnInitDialog: 替换 0x40223A 处的 call 打开设备 (edi = this) ----
hook_init:
    call    load_state
    push    eax
    push    CHK_ID
    push    dword [edi + 0x20]
    call    [IAT_CheckDlgButton]
    jmp     FN_OPEN_DEVICE

; ---- WindowProc: 替换 0x402611 处转给基类的 call ----
; ecx = edi = this, [esp+4] = msg, [esp+8] = wParam, [esp+12] = lParam
hook_wndproc:
    cmp     dword [esp + 4], 0x111      ; WM_COMMAND
    jne     .pass
    cmp     dword [esp + 8], CHK_ID     ; 复选框的 BN_CLICKED (通知码 0)
    jne     .pass
    call    save_state
.pass:
    jmp     FN_BASE_WNDPROC

; ---- eax = 注册表里保存的勾选状态 (0/1)，读不到按 0 ----
load_state:
    push    ebp
    mov     ebp, esp
    sub     esp, 12                     ; [ebp-4] hkey, [ebp-8] 值, [ebp-12] 长度
    and     dword [ebp - 8], 0
    lea     eax, [ebp - 4]
    push    eax                         ; phkResult
    push    1                           ; KEY_QUERY_VALUE
    push    0
    push    str_key
    push    0x80000001                  ; HKEY_CURRENT_USER
    call    [IAT_RegOpenKeyExW]
    test    eax, eax
    jnz     .done
    mov     dword [ebp - 12], 4
    lea     eax, [ebp - 12]
    push    eax                         ; lpcbData
    lea     eax, [ebp - 8]
    push    eax                         ; lpData
    push    0                           ; lpType
    push    0
    push    str_value
    push    dword [ebp - 4]
    call    [IAT_RegQueryValueExW]
    test    eax, eax
    jz      .close
    and     dword [ebp - 8], 0
.close:
    push    dword [ebp - 4]
    call    [IAT_RegCloseKey]
.done:
    xor     eax, eax
    cmp     dword [ebp - 8], 0
    setne   al
    leave
    ret

; ---- 把复选框当前状态写入注册表 (edi = this)，保持 ecx 不变 ----
save_state:
    push    ebp
    mov     ebp, esp
    sub     esp, 8                      ; [ebp-4] hkey, [ebp-8] 值
    push    ecx
    push    0
    push    0
    push    0xF0                        ; BM_GETCHECK
    push    CHK_ID
    push    dword [edi + 0x20]
    call    [IAT_SendDlgItemMessageW]
    and     eax, 1
    mov     [ebp - 8], eax
    push    0                           ; lpdwDisposition
    lea     eax, [ebp - 4]
    push    eax                         ; phkResult
    push    0                           ; lpSecurityAttributes
    push    2                           ; KEY_SET_VALUE
    push    0                           ; dwOptions
    push    0                           ; lpClass
    push    0
    push    str_key
    push    0x80000001                  ; HKEY_CURRENT_USER
    call    [IAT_RegCreateKeyExW]
    test    eax, eax
    jnz     .done
    push    4
    lea     eax, [ebp - 8]
    push    eax
    push    4                           ; REG_DWORD
    push    0
    push    str_value
    push    dword [ebp - 4]
    call    [IAT_RegSetValueExW]
    push    dword [ebp - 4]
    call    [IAT_RegCloseKey]
.done:
    pop     ecx
    leave
    ret
%endif

; ---- 只读数据（紧跟在代码后面）----
    align   4, db 0xCC
stub_data:
%if UPGRADER
cfg_magic:  db CFG_MAGIC_STR
fw_len:     dd 0
cfg_flags:  dd 0
cfg_title:  times TITLE_CHARS dw 0
fw_hdr:     dd 0, 0, 0, -1              ; 伪 CStringData: StringMgr, 长度, 容量, 引用(-1=锁定)
fw_path:    times PATH_CHARS dw 0
str_nousb:  dw NOUSB_CHARS, 0
str_done:   dw DONE_CHARS, 0
str_zero:   dw '0', '%', 0
%else
str_key:    dw REG_KEY_CHARS, 0
str_value:  dw REG_VALUE_CHARS, 0
%endif
'''


def align(v, a):
    return (v + a - 1) & ~(a - 1)


def assemble(stub_va, g_tick, g_init=0, upgrader=False, selftest=False):
    g_hwnd, g_pctbuf = g_init + 4, g_init + 8
    """用 nasm 汇编桩代码，返回 (机器码, {标号: VA})。"""
    defs = {'STUB_VA': stub_va, 'G_TICK': g_tick, 'G_INIT': g_init, 'CHK_ID': CHK_ID,
            'SKIP_WINDOW_MS': SKIP_WINDOW_MS, 'UPGRADER': int(upgrader), 'KICK_ID': KICK_ID,
            'TITLE_CHARS': TITLE_CHARS, 'PATH_CHARS': PATH_CHARS, 'CFG_AUTO_EXIT': CFG_AUTO_EXIT,
            'EXIT_TIMER_ID': EXIT_TIMER_ID, 'EXIT_DELAY_MS': EXIT_DELAY_MS,
            'G_HWND': g_hwnd, 'G_PCTBUF': g_pctbuf, 'G_UI': g_init + 24, 'G_LBL': g_init + 25, 'G_FAIL': g_init + 26,
            'RETRY_ID': RETRY_ID,
            'PCT_ID': PCT_ID, 'CLR_PERCENT': CLR_PERCENT, 'CLR_PROMPT': CLR_PROMPT,
            # 颜色立即数如果恰好落在映像地址范围内，会被当成地址加上重定位；高字节置 FF 再 and 掉
            'CLR_DONE_X': CLR_DONE | 0xFF000000}
    for k in ('CLR_PERCENT', 'CLR_PROMPT'):
        assert not IMAGE_BASE <= defs[k] < IMAGE_BASE + 0x200000, k
    defs.update({k: v for k, v in globals().items() if k.startswith(('FN_', 'IAT_', 'G_')) and k not in defs})
    chars = lambda t: ', '.join(str(ord(c)) for c in t)
    with tempfile.TemporaryDirectory() as td:
        src, out, mapf = (os.path.join(td, n) for n in ('stub.asm', 'stub.bin', 'stub.map'))
        with open(src, 'w') as f:
            f.write('[map symbols %s]\n' % mapf)
            for k, v in defs.items():
                f.write('%%define %s 0x%X\n' % (k, v))
            f.write('%%define REG_KEY_CHARS %s\n' % chars(REG_KEY))
            f.write('%%define REG_VALUE_CHARS %s\n' % chars(REG_VALUE))
            f.write("%%define CFG_MAGIC_STR '%s'\n" % CFG_MAGIC.decode())
            f.write('%%define NOUSB_CHARS %s\n' % chars(STR_NOUSB))
            f.write('%%define DONE_CHARS %s\n' % chars(STR_DONE))
            if selftest:
                f.write('%define SELFTEST 1\n')
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
        if ins.bytes == b'\xcc':                # 代码与数据之间的对齐填充
            total += 1
            continue
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


def remove_relocs(blocks, rvas):
    """删掉指定位置的 HIGHLOW 重定位项（被改写成 call rel32 的 call [IAT]）。"""
    for rva in rvas:
        page, ent = rva & ~0xFFF, (3 << 12) | (rva & 0xFFF)
        for i, (p, ents) in enumerate(blocks):
            if p == page:
                assert ent in ents, '0x%X 处没有重定位' % rva
                real = [e for e in ents if e >> 12 and e != ent]
                blocks[i][1] = real + ([0] if len(real) % 2 else [])
                break
        else:
            raise AssertionError('0x%X 处没有重定位' % rva)
    blocks[:] = [b for b in blocks if any(e >> 12 for e in b[1])]


def _sz(b, o):
    if struct.unpack_from('<H', b, o)[0] == 0xFFFF:
        return b[o:o + 4], o + 4
    e = o
    while struct.unpack_from('<H', b, e)[0]:
        e += 2
    return b[o:e + 2], e + 2


def parse_dialog(b):
    """DLGTEMPLATEEX -> (头部字段, [控件])，各字符串保留原始字节。"""
    assert struct.unpack_from('<HH', b, 0) == (1, 0xFFFF)
    d = {'fixed': bytearray(b[:26])}
    o = 26
    d['menu'], o = _sz(b, o)
    d['cls'], o = _sz(b, o)
    d['title'], o = _sz(b, o)
    assert struct.unpack_from('<I', b, 12)[0] & 0x40           # DS_SETFONT
    f, o2 = o + 6, o + 6
    _, o2 = _sz(b, f)
    d['font'] = b[o:o2]
    o = o2
    items = []
    for _ in range(struct.unpack_from('<H', b, 16)[0]):
        o = align(o, 4)
        it = {'fixed': bytearray(b[o:o + 24])}
        o += 24
        it['cls'], o = _sz(b, o)
        it['text'], o = _sz(b, o)
        n = struct.unpack_from('<H', b, o)[0]
        it['extra'] = b[o:o + 2 + n]
        o += 2 + n
        items.append(it)
    assert o == len(b)
    return d, items


def serialize_dialog(d, items):
    out = bytearray(d['fixed'])
    struct.pack_into('<H', out, 16, len(items))
    out += d['menu'] + d['cls'] + d['title'] + d['font']
    for it in items:
        out += b'\0' * (align(len(out), 4) - len(out))
        out += it['fixed'] + it['cls'] + it['text'] + it['extra']
    return bytes(out)


def wz(text):
    return text.encode('utf-16le') + b'\0\0'


def build_dialog_upgrader(old):
    """一键升级模板: 加宽变矮，只留进度条 + 百分比，其余隐藏；加关闭按钮。"""
    d, items = parse_dialog(old)
    assert serialize_dialog(d, items) == old, '对话框模板往返不一致'
    assert len(items) == 14 and struct.unpack_from('<h', d['fixed'], 24)[0] == 142
    d['title'] = wz(UPG_DLG_TITLE)
    struct.pack_into('<I', d['fixed'], 12, struct.unpack_from('<I', d['fixed'], 12)[0] | 0x00080000)  # WS_SYSMENU
    struct.pack_into('<hh', d['fixed'], 22, *UPG_DLG_SIZE)
    kept = set()
    for it in items:
        cid = struct.unpack_from('<I', it['fixed'], 20)[0]
        key = cid if cid in UPG_LAYOUT else it['text'][:-2].decode('utf-16le')
        st = struct.unpack_from('<I', it['fixed'], 8)[0]
        if key in UPG_LAYOUT and key not in kept:
            kept.add(key)
            struct.pack_into('<hhhh', it['fixed'], 12, *UPG_LAYOUT[key])
        else:
            struct.pack_into('<I', it['fixed'], 8, st & ~0x10000000)                 # 去掉 WS_VISIBLE
    assert kept == set(UPG_LAYOUT), kept
    d['font'] = struct.pack('<HHBB', UPG_FONT[0], UPG_FONT[1], 0, 1) + wz(UPG_FONT[2])   # DEFAULT_CHARSET
    pct = {'fixed': bytearray(struct.pack('<IIIhhhhI', 0, 0, 0x50020202, *PCT_RECT, PCT_ID)),   # SS_RIGHT|SS_CENTERIMAGE
           'cls': struct.pack('<HH', 0xFFFF, 0x0082), 'text': wz('0%'), 'extra': b'\0\0'}
    retry = {'fixed': bytearray(struct.pack('<IIIhhhhI', 0, 0, 0x40010000, *RETRY_RECT, RETRY_ID)),  # 先隐藏
             'cls': struct.pack('<HH', 0xFFFF, 0x0080), 'text': wz('重试'), 'extra': b'\0\0'}
    return serialize_dialog(d, items + [pct, retry])


def build(src, dst, upgrader=False, selftest=False):
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

    # ---- 1. 变量: .data 末尾(未初始化区)借几个字节 -------------------------
    data = sec['.data']
    g_tick_rva = align(data.VirtualAddress + data.Misc_VirtualSize, 4)
    g_init_rva = g_tick_rva + 4
    data_vsize = (g_init_rva + 28 if upgrader else g_tick_rva + 4) - data.VirtualAddress   # G_INIT, G_HWND, G_PCTBUF[16], G_UI
    assert data_vsize <= align(data.Misc_VirtualSize, 0x1000)
    g_tick, g_init = IMAGE_BASE + g_tick_rva, IMAGE_BASE + g_init_rva

    # ---- 2. 桩代码 ---------------------------------------------------------
    #   自动下载版: 放在 .text 末尾到页边界之间的空隙
    #   一键升级模板: 放进新增的 .upg 节（.text 的空隙放不下配置区）
    text = sec['.text']
    if upgrader:
        stub_rva = align(last.VirtualAddress + last.Misc_VirtualSize, 0x1000)
    else:
        stub_rva = align(text.VirtualAddress + text.Misc_VirtualSize, 16)
    stub_va = IMAGE_BASE + stub_rva
    code, labels = assemble(stub_va, g_tick, g_init, upgrader, selftest)
    code_len = labels['stub_data'] - stub_va     # 之后是数据，不参与反汇编
    stub_end = stub_rva + len(code)
    t = body['.text']
    if upgrader:
        cfg = labels['cfg_magic']
        assert (labels['fw_len'], labels['cfg_flags'], labels['cfg_title'], labels['fw_hdr'], labels['fw_path']) == \
            (cfg + 8, cfg + 12, cfg + 16, cfg + 16 + 2 * TITLE_CHARS, cfg + 32 + 2 * TITLE_CHARS), '配置区布局不对'
        body['.upg'] = bytearray(code) + b'\0' * (align(len(code), FILE_ALIGN) - len(code))
    else:
        assert stub_end <= sec['.rdata'].VirtualAddress, '.text 空隙不够'
        assert not any(t[text.Misc_VirtualSize:]), '.text 尾部不是空的'
        t += b'\0' * (align(stub_end - text.VirtualAddress, FILE_ALIGN) - len(t))
        t[stub_rva - text.VirtualAddress:stub_end - text.VirtualAddress] = code

    call_sites = {**CALL_SITES, **(UPG_CALL_SITES if upgrader else {})}
    iat_sites = UPG_IAT_SITES if upgrader else {}
    for site, (old_target, label) in call_sites.items():
        o = site - IMAGE_BASE - text.VirtualAddress
        assert t[o] == 0xE8 and site + 5 + struct.unpack_from('<i', t, o + 1)[0] == old_target, hex(site)
        struct.pack_into('<i', t, o + 1, labels[label] - (site + 5))
    for site, (iat, label) in iat_sites.items():
        o = site - IMAGE_BASE - text.VirtualAddress
        assert t[o:o + 2] == b'\xff\x15' and struct.unpack_from('<I', t, o + 2)[0] == iat, hex(site)
        t[o:o + 6] = b'\xe8' + struct.pack('<i', labels[label] - (site + 5)) + b'\x90'

    # ---- 3. 重定位: 桩代码中的绝对地址 -------------------------------------
    reloc_dir = opt.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_BASERELOC']]
    reloc = sec['.reloc']
    assert reloc_dir.VirtualAddress == reloc.VirtualAddress
    r = body['.reloc']
    old_table = bytes(r[:reloc_dir.Size])
    blocks = parse_relocs(old_table)
    assert build_relocs(blocks) == old_table, '重定位表往返不一致'
    assert not any(r[reloc_dir.Size:]), '.reloc 尾部不是空的'
    remove_relocs(blocks, [a + 2 - IMAGE_BASE for a in iat_sites])
    patched = set(a - IMAGE_BASE + i for a in list(call_sites) + list(iat_sites) for i in range(6))
    for page, ents in blocks:
        for e in ents:
            if e >> 12:
                assert not {page + (e & 0xFFF) + i for i in range(4)} & patched, '被改写的 call 上有重定位'
    new_relocs = [va - IMAGE_BASE for va in find_abs_relocs(code[:code_len], stub_va,
                                                                max(opt.SizeOfImage, stub_rva + align(len(code), 0x1000)))]
    add_relocs(blocks, new_relocs)
    new_table = build_relocs(blocks)
    assert len(new_table) <= reloc.Misc_VirtualSize and len(new_table) <= len(r)
    r[:len(new_table)] = new_table
    r[len(new_table):] = b'\0' * (len(r) - len(new_table))

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
    old_dlg = bytes(body['.rsrc'][soff:soff + entry.Size])
    dlg = build_dialog_upgrader(old_dlg) if upgrader else build_dialog(old_dlg)
    dlg_off = align(rsrc.Misc_VirtualSize, 4)
    rsrc_vsize = dlg_off + len(dlg)
    assert rsrc_vsize <= reloc.VirtualAddress - rsrc.VirtualAddress, '.rsrc 空隙不够'
    rs = body['.rsrc']
    rs += b'\0' * (align(rsrc_vsize, FILE_ALIGN) - len(rs))
    rs[dlg_off:dlg_off + len(dlg)] = dlg
    ent_off = entry.get_file_offset() - rsrc.PointerToRawData
    struct.pack_into('<II', rs, ent_off, rsrc.VirtualAddress + dlg_off, len(dlg))

    # ---- 5. 重新排布文件并修正 PE 头 ---------------------------------------
    names = list(body)
    new_ptr, off = {}, opt.SizeOfHeaders
    for n in names:
        new_ptr[n] = off
        off += len(body[n])
    vsize = {'.data': data_vsize, '.rsrc': rsrc_vsize}
    if not upgrader:
        vsize['.text'] = stub_end - text.VirtualAddress
    for n, s in sec.items():
        ho = s.get_file_offset()
        struct.pack_into('<I', hdr, ho + 8, vsize.get(n, s.Misc_VirtualSize))
        struct.pack_into('<I', hdr, ho + 16, len(body[n]))
        struct.pack_into('<I', hdr, ho + 20, new_ptr[n])

    oo = opt.get_file_offset()
    size_of_code = opt.SizeOfCode + len(body['.text']) - text.SizeOfRawData
    if upgrader:
        ho = last.get_file_offset() + 40
        assert ho + 40 <= opt.SizeOfHeaders and not any(hdr[ho:ho + 40]), '节表后面没有空位'
        hdr[ho:ho + 40] = struct.pack('<8sIIIIIIHHI', UPG_SECTION, len(code), stub_rva, len(body['.upg']),
                                      new_ptr['.upg'], 0, 0, 0, 0, 0x60000020)   # CODE|EXECUTE|READ
        fo = pe.FILE_HEADER.get_file_offset()
        struct.pack_into('<H', hdr, fo + 2, pe.FILE_HEADER.NumberOfSections + 1)
        struct.pack_into('<I', hdr, oo + 56, stub_rva + align(len(code), 0x1000))  # SizeOfImage
        size_of_code += len(body['.upg'])
    struct.pack_into('<I', hdr, oo + 4, size_of_code)
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

    out = bytes(hdr) + b''.join(bytes(body[n]) for n in names)
    if upgrader:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, '..', 'upgrader'))
        import make_upgrader
        with open(os.path.join(here, '..', 'upgrader', 'upgrader_icon.ico'), 'rb') as f:
            out = make_upgrader.set_icon(out, f.read())
    chk = pefile.PE(data=out)
    assert not chk.get_warnings(), chk.get_warnings()
    chk.OPTIONAL_HEADER.CheckSum = chk.generate_checksum()
    out = chk.write()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, 'wb') as f:
        f.write(out)

    print('桩代码   : 0x%X..0x%X (代码 %d + 数据 %d 字节), 重定位 %d 处'
          % (stub_va, IMAGE_BASE + stub_end, code_len, len(code) - code_len, len(new_relocs)))
    print('变量     : 0x%X%s' % (g_tick, ', 0x%X' % g_init if upgrader else ''))
    print('对话框   : RVA 0x%X, %d 字节' % (rsrc.VirtualAddress + dlg_off, len(dlg)))
    print('输出     : %s (%d 字节)' % (os.path.normpath(dst), len(out)))
    print('SHA-256  : %s' % hashlib.sha256(out).hexdigest())
    return labels


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='生成自动下载版或一键升级模板')
    ap.add_argument('--upgrader', action='store_true', help='生成一键升级程序模板')
    ap.add_argument('--selftest', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('src', nargs='?', default=os.path.join(here, '..', 'BootLoader_Tools_v3.5.7.1.exe'))
    ap.add_argument('dst', nargs='?')
    a = ap.parse_args()
    if a.selftest and not a.upgrader:
        ap.error('--selftest 只用于 --upgrader')
    dst = a.dst or os.path.join(here, '..', *(('upgrader', 'Upgrader_Template.exe') if a.upgrader
                                              else ('BootLoader_Tools_v3.5.7.1_AutoDL.exe',)))
    build(a.src, dst, a.upgrader, a.selftest)


if __name__ == '__main__':
    main()
