using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace CatRTools;

internal sealed class CatrResult
{
    public required IReadOnlyList<string> Arguments { get; init; }
    public int ExitCode { get; init; }
    public string Stdout { get; init; } = "";
    public string Stderr { get; init; } = "";
    public bool Stopped { get; init; }
    public TimeSpan Duration { get; init; }
    public bool Ok => ExitCode == 0 && !Stopped;

    /// <summary>catr's JSON output (commands with --json, and the ones that always print JSON).</summary>
    public JsonElement? Json()
    {
        var text = Stdout.Trim();
        var start = text.IndexOfAny(['{', '[']);
        if (start < 0)
            return null;
        try
        {
            using var document = JsonDocument.Parse(text[start..]);
            return document.RootElement.Clone();
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>catr's own error line ("catr: ..."), else the last stderr line.</summary>
    public string Message()
    {
        var lines = Stderr.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        return lines.LastOrDefault(l => l.StartsWith("catr:", StringComparison.Ordinal))?[5..].Trim()
               ?? lines.LastOrDefault() ?? (Stopped ? "stopped" : $"exit code {ExitCode}");
    }
}

internal sealed class CatrBusyException(string running) : Exception($"catr is busy: {running}")
{
    public string Running { get; } = running;
}

/// <summary>Runs catr as a child process, streaming its output. One console command at a time:
/// wupserver and the COS shell serve one client, so console commands are serialized here.
/// Host-only commands (PCFS status, serial status, --version) may run alongside.</summary>
internal sealed class CatrRunner(AppSettings settings)
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private Process? current;

    public string? Running { get; private set; }
    public event Action? RunningChanged;

    /// <summary>catr commands that only read host state and never talk to the console.</summary>
    public static bool HostOnly(IReadOnlyList<string> args) => args switch
    {
        ["--version", ..] => true,
        ["pcfs", "status", ..] => true,
        ["serial", "--status", ..] => true,
        ["spark", "list", ..] => true,
        _ => false
    };

    public async Task<CatrResult> RunAsync(IReadOnlyList<string> args, Action<string, bool>? onLine = null)
    {
        var exclusive = !HostOnly(args);
        if (exclusive && !gate.Wait(0))
            throw new CatrBusyException(Running ?? "another command");
        var context = SynchronizationContext.Current;
        void Post(Action action)
        {
            if (context is null) action();
            else context.Post(_ => action(), null);
        }
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();
        var clock = Stopwatch.StartNew();
        Process? process = null;
        var stopped = false;
        try
        {
            var info = CatrLauncher.StartInfo(settings, args);
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.RedirectStandardInput = true;   // closed at once: catr sees no terminal and asks for --yes
            info.CreateNoWindow = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            info.Environment["PYTHONIOENCODING"] = "utf-8";
            info.Environment["PYTHONUNBUFFERED"] = "1";
            process = new Process { StartInfo = info, EnableRaisingEvents = true };
            process.OutputDataReceived += (_, e) =>
            {
                if (e.Data is null) return;
                lock (stdout) stdout.AppendLine(e.Data);
                if (onLine is not null) Post(() => onLine(e.Data, false));
            };
            process.ErrorDataReceived += (_, e) =>
            {
                if (e.Data is null) return;
                lock (stderr) stderr.AppendLine(e.Data);
                if (onLine is not null) Post(() => onLine(e.Data, true));
            };
            if (exclusive)
            {
                current = process;
                Running = CatrLauncher.Describe(args);
                RunningChanged?.Invoke();
            }
            try
            {
                process.Start();
            }
            catch (Win32Exception exception)
            {
                throw new InvalidOperationException(
                    $"Could not start {info.FileName}: {exception.Message}. Install Python 3 (with the py launcher), or set CATR_PYTHON to a python.exe.", exception);
            }
            process.StandardInput.Close();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            await process.WaitForExitAsync();
            process.WaitForExit();   // drains the redirected streams
            stopped = Stopping;
            string Read(StringBuilder builder) { lock (builder) return builder.ToString(); }
            return new CatrResult
            {
                Arguments = args,
                ExitCode = process.ExitCode,
                Stdout = Read(stdout),
                Stderr = Read(stderr),
                Stopped = stopped,
                Duration = clock.Elapsed
            };
        }
        finally
        {
            process?.Dispose();
            if (exclusive)
            {
                current = null;
                Running = null;
                Stopping = false;
                gate.Release();
                RunningChanged?.Invoke();
            }
        }
    }

    private bool Stopping { get; set; }

    /// <summary>Ends the running console command and its python child (py launcher tree).</summary>
    public void Stop()
    {
        var process = current;
        if (process is null)
            return;
        Stopping = true;
        try
        {
            process.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException)
        {
            // already exited
        }
    }
}
