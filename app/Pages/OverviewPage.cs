using System.Text.Json;

namespace CatRTools;

internal sealed class OverviewPage : Page
{
    private const string Recovery = "000500101f700700";
    private readonly Label bannerTitle = new() { AutoSize = true, Font = Theme.Brand, ForeColor = Color.White, Location = new Point(22, 18) };
    private readonly Label bannerBody = new() { AutoSize = true, Font = Theme.Body, ForeColor = Theme.BannerText, Location = new Point(24, 54) };
    private readonly MetricCard console = new("CONSOLE");
    private readonly MetricCard defaultTitle = new("DEFAULT TITLE");
    private readonly MetricCard pcfs = new("PCFS SERVER");
    private readonly MetricCard serial = new("USB SERIAL SERVICE");
    private readonly MetricCard storage = new("MLC FREE", "—", "Diagnostics → Console info");
    private readonly ListView activity = new()
    {
        View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable,
        BorderStyle = BorderStyle.None, Font = Theme.Small, ShowItemToolTips = true
    };
    private readonly Button refresh;
    private bool refreshedOnce;

    public OverviewPage(AppHost host) : base(host)
    {
        Name = "Overview";
        var banner = new Panel { Height = 100, BackColor = Theme.Banner, Margin = new Padding(0, 0, 0, 14) };
        refresh = Ui.Button("Refresh", async (_, _) => await RefreshStatus());
        refresh.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        banner.Controls.AddRange([bannerTitle, bannerBody, refresh]);
        banner.Resize += (_, _) => refresh.Location = new Point(banner.Width - refresh.Width - 18, 30);

        var cards = Ui.CardRow();
        cards.Controls.AddRange([console, defaultTitle, pcfs, serial, storage]);

        var activityCard = Ui.Card("Recent activity", "catr commands run from this window, newest first.", out var body);
        activityCard.AutoSize = false;
        body.Dock = DockStyle.Top;
        activity.Columns.Add("Time", 80);
        activity.Columns.Add("Command", 420);
        activity.Columns.Add("Result", 260);
        activity.Columns.Add("Time taken", 90, HorizontalAlignment.Right);
        activity.Dock = DockStyle.Fill;
        var listHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(16, 0, 16, 12), BackColor = Theme.Surface };
        listHost.Controls.Add(activity);
        activityCard.Padding = Padding.Empty;
        body.Padding = new Padding(16, 12, 16, 4);
        activityCard.Controls.Add(listHost);
        listHost.BringToFront();
        activityCard.Resize += (_, _) => activity.Columns[1].Width = Math.Max(200, activity.ClientSize.Width - 440);

        Controls.Add(Ui.PageLayout(banner, cards, activityCard));
        host.ActivityChanged += FillActivity;
        SetBanner();
    }

    public override string Title => "Console Overview";
    public override string Subtitle => "Console, PCFS and USB serial at a glance. Refresh only reads.";

    public override async void Shown()
    {
        SetBanner();
        if (!refreshedOnce)
        {
            refreshedOnce = true;
            await RefreshStatus();
        }
    }

    private void SetBanner()
    {
        var config = AppHost.CatrConfig();
        var ip = config?.TryGetProperty("ip", out var value) == true ? value.GetString() : null;
        bannerTitle.Text = ip is null ? "No console selected" : $"CAT-R at {ip}";
        bannerBody.Text = ip is null
            ? "Settings → Find and save looks for the console and saves its IP."
            : $"Serial {(config?.TryGetProperty("serial_port", out var port) == true ? port.GetString() : "port not set")} · catr {(CatrLauncher.UsesBundle(Host.Settings) ? "built in" : CatrLauncher.FindCatrFolder(Host.Settings) ?? "not found")}";
    }

    private async Task RefreshStatus()
    {
        refresh.Enabled = false;
        try
        {
            console.Set("Checking…");
            var result = await Host.Run(["default-title"], quiet: true);
            if (result?.Json() is { } title)
            {
                var tid = title.GetProperty("default_title").GetString() ?? "?";
                console.Set("Reachable", "wupserver answered", Theme.Good);
                var maintenance = title.TryGetProperty("maintenance", out var record) && record.ValueKind == JsonValueKind.Object;
                defaultTitle.Set(tid == Recovery ? "Recovery Menu" : tid,
                    tid == Recovery ? tid : maintenance ? "maintenance switch recorded" : "not the Recovery Menu",
                    tid == Recovery ? Theme.Good : Theme.Warn);
            }
            else if (result is not null)
            {
                console.Set("No answer", result.Message(), Theme.Bad);
                defaultTitle.Set("—", "needs wupserver");
            }
            else
            {
                console.Set("Not checked", "catr was busy or not found");
            }
            await RefreshHostState();
        }
        finally
        {
            refresh.Enabled = true;
        }
    }

    private async Task RefreshHostState()
    {
        if ((await Host.Run(["pcfs", "status"], quiet: true))?.Json() is { } state)
        {
            if (state.GetProperty("server") is { ValueKind: JsonValueKind.Object } server)
            {
                var tcp = state.TryGetProperty("tcp", out var t) ? t.GetString() : null;
                pcfs.Set(tcp == "ESTABLISHED" ? "Connected" : "Running",
                    server.TryGetProperty("root", out var root) ? root.GetString() ?? "" : "",
                    tcp == "ESTABLISHED" ? Theme.Good : Theme.Warn);
            }
            else
            {
                pcfs.Set("Not running", state.TryGetProperty("keeper", out var keeper) && keeper.ValueKind == JsonValueKind.Object ? "keeper running" : "");
            }
        }
        var serialResult = SerialLog.ServiceFilePresent ? await Host.Run(["serial", "--status"], quiet: true) : null;
        if (serialResult?.Json() is { } status)
            serial.Set("Running", status.TryGetProperty("port", out var port) ? $"{port} · log shared" : "", Theme.Good);
        else
            serial.Set("Not running", "starts with the first serial command, or USB Log → Start");
        if (Host.LastInfo is { } info && info.TryGetProperty("free_space", out var space))
            foreach (var volume in space.EnumerateArray())
                if (volume.TryGetProperty("free", out var free) && volume.GetProperty("volume").GetString() == "/vol/storage_mlc01")
                    storage.Set($"{free.GetInt64() / 1073741824.0:0.0} GB",
                        volume.TryGetProperty("capacity", out var capacity) ? $"of {capacity.GetInt64() / 1073741824.0:0.0} GB" : "");
    }

    private void FillActivity()
    {
        activity.BeginUpdate();
        activity.Items.Clear();
        foreach (var entry in Host.Recent)
        {
            var item = new ListViewItem([entry.Time.ToString("HH:mm:ss"), entry.Command, entry.Outcome, $"{entry.Duration.TotalSeconds:0.0} s"])
            {
                ForeColor = entry.Ok ? Theme.Ink : Theme.Bad,
                ToolTipText = entry.Command
            };
            activity.Items.Add(item);
        }
        activity.EndUpdate();
    }
}
