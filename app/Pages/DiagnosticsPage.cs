using System.Text.Json;

namespace CatRTools;

internal sealed class DiagnosticsPage : Page
{
    private readonly MetricCard console = new("CONSOLE", "—", "Console info reads it");
    private readonly MetricCard titleCard = new("DEFAULT TITLE");
    private readonly MetricCard mlc = new("MLC FREE");
    private readonly MetricCard serial = new("RUNNING TITLE SDK", "—", "cos sdkversion (needs serial)");
    private readonly Label warnings = Ui.Note("", Theme.Warn);
    private readonly OutputPanel output = new();

    public DiagnosticsPage(AppHost host) : base(host)
    {
        Name = "Diagnostics";
        var tiles = Ui.CardRow();
        tiles.Controls.AddRange([console, titleCard, mlc, serial]);
        var card = Ui.Card("Read-only checks", "These read the console (wupserver, or the COS shell over serial); none of them change it.", out var body);
        body.Controls.Add(Ui.Row(
            Ui.Button("Console info", async (_, _) => await Info(), primary: true),
            Ui.Button("IOSU syslog (last 300)", async (_, _) => await Host.Run(["syslog", "--tail", "300"], output.Box)),
            Ui.Button("Threads", async (_, _) => await Host.Run(["debug", "threads"], output.Box)),
            Ui.Button("Copy crash logs", async (_, _) => await Host.Run(["crashlogs"], output.Box)),
            Ui.Button("Open logs folder", (_, _) => Ui.OpenFolder(this, LogsFolder)),
            Ui.Button("USB Log", (_, _) => Host.ShowLog())));
        body.Controls.Add(warnings);
        Controls.Add(Ui.PageLayout(tiles, card, output));
    }

    public override string Title => "Diagnostics";
    public override string Subtitle => "Console identity, storage, logs and threads, read with catr.";

    // catr crashlogs writes to the workspace's logs folder.
    private string? LogsFolder => CatrLauncher.Workspace(Host.Settings) is { } workspace
        ? Path.Combine(workspace, "logs") : null;

    private async Task Info()
    {
        var result = await Host.Run(["info"], output.Box);
        if (result?.Json() is not { } info) return;
        Host.LastInfo = info;
        if (info.TryGetProperty("console_error", out var error))
            console.Set("No answer", error.GetString() ?? "", Theme.Bad);
        else if (info.TryGetProperty("probe", out var probe))
            console.Set(info.GetProperty("ip").GetString() ?? "", $"{(probe.TryGetProperty("status", out var s) ? s.GetString() : "")} · catr {info.GetProperty("toolkit").GetString()}", Theme.Good);
        if (info.TryGetProperty("default_title", out var tid))
            titleCard.Set(tid.GetString() == "000500101f700700" ? "Recovery Menu" : tid.GetString() ?? "", tid.GetString() ?? "",
                tid.GetString() == "000500101f700700" ? Theme.Good : Theme.Warn);
        if (info.TryGetProperty("free_space", out var space))
            foreach (var volume in space.EnumerateArray())
                if (volume.GetProperty("volume").GetString() == "/vol/storage_mlc01" && volume.TryGetProperty("free", out var free))
                    mlc.Set($"{free.GetInt64() / 1073741824.0:0.0} GB", $"of {volume.GetProperty("capacity").GetInt64() / 1073741824.0:0.0} GB");
        if (info.TryGetProperty("sdkversion", out var sdk) && sdk.TryGetProperty("text", out var text))
            serial.Set(Last(text.GetString()), "running title, not the installed OS");
        else if (info.TryGetProperty("serial_error", out var serialError))
            serial.Set("—", serialError.GetString() ?? "");
        var list = info.TryGetProperty("warnings", out var w) ? w.EnumerateArray().Select(x => x.GetString()).ToList() : [];
        warnings.Text = list.Count == 0 ? "" : "Warnings: " + string.Join(" · ", list);
    }

    /// <summary>The reply is the echoed command, a header and "SDKVer:21213", separated by \r.
    /// 21213 is SDK 2.12.13.</summary>
    private static string Last(string? text)
    {
        var match = System.Text.RegularExpressions.Regex.Match(text ?? "", @"SDKVer:\s*(\d+)");
        if (!match.Success) return "—";
        var number = int.Parse(match.Groups[1].Value);
        return number >= 10000 ? $"{number / 10000}.{number / 100 % 100:00}.{number % 100:00}" : number.ToString();
    }
}
