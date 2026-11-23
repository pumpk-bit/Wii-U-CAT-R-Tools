using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace CatRTools;

internal static partial class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        FirstRun.SeedConfig();   // config.json next to the exe, before any catr runs
        var settings = AppSettings.Load();
        if (args.Length == 0)
            _ = Task.Run(() => Bundle.Folder);   // unpack the built-in catr while the window opens
        if (args.Length > 0)
            return RunInConsole(settings, args);

        ApplicationConfiguration.Initialize();
        Theme.Apply(Environment.GetEnvironmentVariable("CATRTOOLS_THEME") is { Length: > 0 } forced ? forced : settings.Theme);
        var form = new MainForm(settings);
        if (Environment.GetEnvironmentVariable("CATRTOOLS_SNAPSHOT") is { Length: > 0 } snapshot)
            form.Shown += async (_, _) => await form.Snapshot(snapshot);
        Application.Run(form);
        return 0;
    }

    /// <summary>CatR-Tools ARGS: run catr ARGS in the terminal that started us. This is a GUI program,
    /// so cmd and PowerShell don't wait for it: their prompt can come back before the output ends.</summary>
    private static int RunInConsole(AppSettings settings, string[] args)
    {
        var attached = AttachConsole(AttachParentProcess);
        if (!attached)
            AllocConsole();   // started from Explorer or a shortcut with arguments
        try
        {
            using var process = Process.Start(CatrLauncher.StartInfo(settings, args))!;
            process.WaitForExit();
            if (!attached)
            {
                Console.Write($"\n[catr exited with {process.ExitCode}; press Enter to close]");
                Console.ReadLine();
            }
            return process.ExitCode;
        }
        catch (Exception exception) when (exception is InvalidOperationException or Win32Exception)
        {
            Console.Error.WriteLine($"CatR-Tools: {exception.Message}");
            return 1;
        }
    }

    private const int AttachParentProcess = -1;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AllocConsole();
}
