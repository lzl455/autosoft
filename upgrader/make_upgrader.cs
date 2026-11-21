// 把固件 .bin 装进 Upgrader_Template.exe，生成双击即可升级的 exe（不需要 Python）。
// 由 make_upgrader.ps1 通过 PowerShell 的 Add-Type 编译运行；make_upgrader.bat 在没有
// Python 时自动走这条路。逻辑与 make_upgrader.py 相同，只是不支持换图标。
// 只用 C# 3 / .NET 2.0 的写法，Windows 7 自带的 PowerShell 2.0 也能编译。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;

public static class UpgraderMaker
{
    const string CfgMagic = "BYCFWCF2";     // 与 tools/add_auto_download.py 保持一致
    const int TitleChars = 96;              // 含结尾 0
    const int PathChars = 128;              // 含结尾 0
    const string PathPrefix = "内置固件\\";
    const int MaxFw = 0x10000;
    const uint CfgAutoExit = 1;

    public class Failure : Exception
    {
        public Failure(string m) : base(m) { }
    }

    static int IndexOf(byte[] data, byte[] pat, int start)
    {
        for (int i = start; i <= data.Length - pat.Length; i++)
        {
            if (data[i] != pat[0]) continue;
            int k = 1;
            while (k < pat.Length && data[i + k] == pat[k]) k++;
            if (k == pat.Length) return i;
        }
        return -1;
    }

    static uint U32(byte[] b, int o)
    {
        return (uint)(b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24));
    }

    static void Put32(byte[] b, int o, uint v)
    {
        b[o] = (byte)v; b[o + 1] = (byte)(v >> 8); b[o + 2] = (byte)(v >> 16); b[o + 3] = (byte)(v >> 24);
    }

    static void PutWide(byte[] b, int o, string s, int chars)
    {
        byte[] w = Encoding.Unicode.GetBytes(s);
        Array.Clear(b, o, 2 * chars);
        Array.Copy(w, 0, b, o, w.Length);
    }

    static uint PeChecksum(byte[] data, int csumOff)
    {
        ulong s = 0;
        int n = data.Length / 2 * 2;
        for (int i = 0; i < n; i += 2)
        {
            if (i == csumOff || i == csumOff + 2) continue;   // 不计入校验和字段本身
            s += (uint)(data[i] | (data[i + 1] << 8));
        }
        if ((data.Length & 1) != 0) s += data[data.Length - 1];
        while ((s >> 16) != 0) s = (s & 0xFFFF) + (s >> 16);
        return (uint)(s + (ulong)data.Length);
    }

    // 生成升级程序的全部字节（与 make_upgrader.py 的 build() 一致，图标保持模板里的）
    public static byte[] Build(byte[] template, byte[] fw, string title, string name, bool autoExit)
    {
        if (template.Length < 0x40 || template[0] != 'M' || template[1] != 'Z')
            throw new Failure("模板不是 exe 文件");
        byte[] magic = Encoding.ASCII.GetBytes(CfgMagic);
        int pos = IndexOf(template, magic, 0);
        if (pos < 0 && IndexOf(template, Encoding.ASCII.GetBytes("BYCFWCF"), 0) >= 0)
            throw new Failure("模板版本与本脚本不符，请使用同一目录下的 Upgrader_Template.exe");
        if (pos < 0 || IndexOf(template, magic, pos + 1) >= 0)
            throw new Failure("模板里找不到唯一的配置区，请使用 Upgrader_Template.exe");
        if (U32(template, pos + 8) != 0)
            throw new Failure("这个模板已经装过固件了，请使用原始的 Upgrader_Template.exe");
        if (fw.Length < 1 || fw.Length > MaxFw)
            throw new Failure(string.Format("固件大小 {0} 字节，不在 1..{1} 之间", fw.Length, MaxFw));
        string path = PathPrefix + name;
        if (path.Length >= PathChars)
            throw new Failure(string.Format("固件文件名太长（最多 {0} 个字符）", PathChars - 1 - PathPrefix.Length));
        if (title.Length > TitleChars - 1) title = title.Substring(0, TitleChars - 1);

        byte[] exe = new byte[template.Length + fw.Length];
        Array.Copy(template, exe, template.Length);
        int oTitle = pos + 16;
        int oHdr = oTitle + 2 * TitleChars;
        int oPath = oHdr + 16;
        Put32(exe, pos + 8, (uint)fw.Length);
        Put32(exe, pos + 12, autoExit ? CfgAutoExit : 0u);
        PutWide(exe, oTitle, title, TitleChars);
        Put32(exe, oHdr, 0);                      // 伪 CStringData: StringMgr
        Put32(exe, oHdr + 4, (uint)path.Length);  // 长度
        Put32(exe, oHdr + 8, PathChars - 1);      // 容量
        Put32(exe, oHdr + 12, 0xFFFFFFFF);        // 引用计数 -1（锁定）
        PutWide(exe, oPath, path, PathChars);
        Array.Copy(fw, 0, exe, template.Length, fw.Length);   // 固件必须正好在文件末尾

        int csumOff = (int)U32(exe, 0x3C) + 24 + 64;
        Put32(exe, csumOff, PeChecksum(exe, csumOff));
        return exe;
    }

    // 处理拖进来的文件；生成的 exe 放在 outDir（upgrader 文件夹）。返回进程退出码。
    public static int Run(string outDir, string[] args, bool interactive)
    {
        int failed = 0;
        string made = null;
        List<string> bins = new List<string>();
        foreach (string a in args)
        {
            string ext = Path.GetExtension(a).ToLowerInvariant();
            if (ext == ".ico" || ext == ".png")
                Console.WriteLine("提示: 换图标需要安装 Python（用 make_upgrader.py），这次先用默认图标。");
            else
                bins.Add(a);
        }
        try
        {
            if (bins.Count == 0)
            {
                Console.WriteLine("用法: 把固件 .bin 文件拖到 make_upgrader.bat 上（可以一次拖多个），");
                Console.WriteLine("      生成的 <名称>_升级.exe 会放在 upgrader 文件夹里。");
                failed = 1;
            }
            else
            {
                byte[] template = File.ReadAllBytes(Path.Combine(outDir, "Upgrader_Template.exe"));
                foreach (string bin in bins)
                {
                    Console.WriteLine();
                    try
                    {
                        string name = Path.GetFileName(bin);
                        string outPath = Path.Combine(outDir, Path.GetFileNameWithoutExtension(bin) + "_升级.exe");
                        byte[] fw = File.ReadAllBytes(bin);
                        File.WriteAllBytes(outPath, Build(template, fw, "固件升级", name, false));
                        Console.WriteLine("固件     : {0} ({1} 字节)", bin, fw.Length);
                        Console.WriteLine("窗口标题 : 固件升级");
                        Console.WriteLine("升级成功 : 保持打开，由用户关闭");
                        Console.WriteLine("已生成   : {0}", outPath);
                        Console.WriteLine("请双击运行上面这个文件进行升级（不是 Upgrader_Template.exe）。");
                        made = outPath;
                    }
                    catch (Failure e)
                    {
                        Console.WriteLine("错误: {0}（{1}）", e.Message, bin);
                        failed++;
                    }
                    catch (IOException e)
                    {
                        Console.WriteLine("错误: {0}", e.Message);
                        failed++;
                    }
                    catch (UnauthorizedAccessException e)
                    {
                        Console.WriteLine("错误: {0}", e.Message);
                        failed++;
                    }
                }
            }
        }
        catch (Exception e)
        {
            Console.WriteLine("错误: {0}", e.Message);
            failed = 1;
        }
        if (interactive)
        {
            if (made != null && Environment.OSVersion.Platform == PlatformID.Win32NT)
            {
                try { Process.Start("explorer.exe", "/select,\"" + Path.GetFullPath(made) + "\""); }
                catch (Exception) { }
            }
            Console.WriteLine();
            Console.Write("按回车键关闭窗口...");
            try { Console.ReadLine(); } catch (Exception) { }
        }
        return failed != 0 ? 1 : 0;
    }
}
