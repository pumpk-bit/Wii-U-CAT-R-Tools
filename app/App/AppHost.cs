using System.Text.Json;
using System.Text.RegularExpressions;

namespace CatRTools;

internal sealed record Activity(DateTime Time, string Command, string Outcome, bool Ok, TimeSpan Duration);

/// <summary>What the pages share: settings, the catr runner, activity, and the title cache.</summary>
internal sealed partial class AppHost
{
    public AppHost(AppSettings settings)
    {
        Settings = settings;
        Runner = new CatrRunner(settings);
        Titles = TitleCache.Load();
    }

    public AppSettings Settings { get; }
    public CatrRunner Runner { get; }
    public required Form Owner { get; init; }
    public required Action ShowLog { get; init; }
    public required Action<string> ShowPage { get; init; }
    public required Action<string> SetStatus { get; init; }
    public List<Activity> Recent { get; } = [];
    public event Action? ActivityChanged;
    public TitleCache Titles { get; set; }
    public event Action? TitlesChanged;
    public bool TitlesStale { get; set; }

    /// <summary>The last `catr info` report (Diagnostics), for the Overview storage tile.</summary>
    public JsonElement? LastInfo { get; set; }

    public void NotifyTitles() => TitlesChanged?.Invoke();

    /// <summary>CATRTOOLS_SNAPSHOT layout check: no background console reads.</summary>
    public static bool SnapshotMode { get; } = Environment.GetEnvironmentVariable("CATRTOOLS_SNAPSHOT") is { Length: > 0 };

    /// <summary>Commands waiting for the icon download to pause; it doesn't start a batch meanwhile.</summary>
    public int Waiting { get; private set; }

    /// <summary>catr's config.json (console IP, serial port), as catr find saved it.</summary>
    public static JsonElement? CatrConfig()
    {
        var path = CatrLauncher.ConfigFile;
        try
        {
            if (!File.Exists(path))
                return null;
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            return document.RootElement.Clone();
        }
        catch (Exception exception) when (exception is IOException or JsonException)
        {
            return null;
        }
    }

    public static string CatrHome => CatrLauncher.CatrHome;

    /// <summary>Runs catr ARGS, echoing into <paramref name="output"/>.
    /// <paramref name="confirm"/>: ask this first, then pass --yes (catr can't prompt without a terminal).
    /// If catr still asks for --yes or a typed --confirm, the user is asked and the command rerun.
    /// <paramref name="yes"/>: pass --yes without asking (the user's click is the confirmation).
    /// JSON on stdout is shown as plain "key: value" text (<paramref name="showJson"/> false: not at all).</summary>
    public async Task<CatrResult?> Run(IReadOnlyList<string> args, OutputBox? output = null,
        string? confirm = null, bool quiet = false, bool yes = false, bool showJson = true,
        Action<string>? onStdoutLine = null, bool record = true)
    {
        // The background icon fetch gives way: wait for its current batch instead of "busy".
        if (!CatrRunner.HostOnly(args) && args is not ["icons", ..] && Runner.Running?.StartsWith("catr icons", StringComparison.Ordinal) == true)
        {
            Waiting++;
            try
            {
                output?.Line("[waiting for the icon download to pause…]", Theme.TerminalDim);
                for (var waited = 0; Runner.Running is not null && waited < 30000; waited += 100)
                    await Task.Delay(100);
            }
            finally
            {
                Waiting--;
            }
        }
        var arguments = args.ToList();
        if (confirm is not null && !Ui.Confirm(Owner, confirm))
            return null;
        if ((confirm is not null || yes) && !arguments.Contains("--yes"))
            arguments.Add("--yes");
        for (var attempt = 0; attempt < 3; attempt++)
        {
            var command = CatrLauncher.Describe(arguments);
            output?.Line($"> {command}", Theme.TerminalCommand);
            if (!quiet)
                SetStatus($"Running {command}");
            CatrResult result;
            var json = false;
            var started = false;
            try
            {
                result = await Runner.RunAsync(arguments, (line, error) =>
                {
                    if (!error)
                    {
                        onStdoutLine?.Invoke(line);
                        // JSON output is held back and shown plain at the end.
                        if (!started && line.Trim().Length > 0)
                        {
                            started = true;
                            json = line.TrimStart().StartsWith('{') || line.TrimStart().StartsWith('[');
                        }
                        if (json) return;
                    }
                    output?.Line(line, error ? Theme.TerminalDim : null);
                });
            }
            catch (CatrBusyException busy)
            {
                output?.Line($"[not started: catr is busy with {busy.Running}]", Theme.TerminalError);
                Ui.Error(Owner, $"Another catr command is still running:\n\n{busy.Running}\n\nWait for it, or stop it from the status bar.");
                return null;
            }
            catch (InvalidOperationException exception)
            {
                output?.Line($"[not started: {exception.Message}]", Theme.TerminalError);
                Ui.Error(Owner, exception.Message);
                return null;
            }
            if (json && output is not null && showJson)
            {
                if (result.Json() is { } value)
                    output.Append(PlainJson.Format(value));
                else
                    output.Append(result.Stdout);
            }
            // pcfs status exits 1 when no server is connected; that is a status, not a failure.
            var status = arguments is ["pcfs", "status", ..] && result.Json() is not null;
            var outcome = result.Stopped ? "stopped" : result.Ok ? "ok" : status ? "no server connected" : result.Message();
            output?.Line(result.Stopped ? "[stopped]" : $"[exit {result.ExitCode} · {result.Duration.TotalSeconds:0.0} s]",
                result.Ok || status ? Theme.TerminalDim : Theme.TerminalError);
            if (record)
                Record(new Activity(DateTime.Now, command, outcome, result.Ok || status, result.Duration));
            if (status)
                return result;
            if (!quiet)
                SetStatus(result.Stopped ? $"Stopped {command}" : result.Ok ? $"Done: {command}" : $"Failed: {command} ({outcome})");
            if (result.Ok || result.Stopped)
                return result;

            // catr refused because nobody could answer its prompt: ask here, then rerun.
            var question = ConfirmQuestion().Match(result.Stderr);
            if (question.Success && !arguments.Contains("--yes"))
            {
                if (!Ui.Confirm(Owner, $"catr asks:\n\n{question.Groups[1].Value.Trim()}\n\nRun again with --yes?"))
                    return result;
                arguments.Add("--yes");
                continue;
            }
            var typed = TypedQuestion().Match(result.Stderr);
            if (typed.Success && !arguments.Contains("--confirm"))
            {
                var phrase = typed.Groups[2].Value;
                var answer = TypedConfirmForm.Ask(Owner, typed.Groups[1].Value.Trim(), phrase);
                if (answer != phrase)
                    return result;
                arguments.AddRange(["--confirm", phrase]);
                continue;
            }
            return result;
        }
        return null;
    }

    private void Record(Activity activity)
    {
        Recent.Insert(0, activity);
        if (Recent.Count > 100)
            Recent.RemoveAt(Recent.Count - 1);
        ActivityChanged?.Invoke();
    }

    [GeneratedRegex(@"catr: (.+?): needs confirmation; pass --yes")]
    private static partial Regex ConfirmQuestion();

    [GeneratedRegex("catr: (.+?): needs a typed confirmation; pass --confirm \"([^\"]+)\"")]
    private static partial Regex TypedQuestion();
}

/// <summary>For catr's typed confirmations (repair, restore of the Recovery Menu).</summary>
internal sealed class TypedConfirmForm : Form
{
    private readonly TextBox answer = Ui.TextBox(mono: true);

    private TypedConfirmForm(string question, string phrase)
    {
        Text = "Typed confirmation";
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = MaximizeBox = false;
        ClientSize = new Size(520, 210);
        BackColor = Theme.Surface;
        Font = Theme.Body;
        var layout = Ui.Column(new Padding(18));
        layout.Dock = DockStyle.Fill;
        layout.Controls.Add(new Label { Text = question, AutoSize = true, Dock = DockStyle.Fill, Font = Theme.BodyBold, ForeColor = Theme.Ink });
        layout.Controls.Add(Ui.Note($"Type  {phrase}  to go ahead."));
        answer.Dock = DockStyle.Fill;
        layout.Controls.Add(answer);
        var ok = Ui.Button("Run", (_, _) => { DialogResult = DialogResult.OK; Close(); }, primary: true);
        var cancel = Ui.Button("Cancel", (_, _) => Close());
        layout.Controls.Add(Ui.Row(ok, cancel));
        AcceptButton = ok;
        CancelButton = cancel;
        Controls.Add(layout);
        Theme.Style(this);
        Theme.TitleBar(this);
    }

    public static string? Ask(IWin32Window owner, string question, string phrase)
    {
        using var form = new TypedConfirmForm(question, phrase);
        return form.ShowDialog(owner) == DialogResult.OK ? form.answer.Text.Trim() : null;
    }
}
