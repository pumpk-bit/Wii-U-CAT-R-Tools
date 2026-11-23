namespace CatRTools;

internal sealed class InstallPage : Page
{
    private readonly TextBox source = Ui.TextBox(placeholder: "Install image folder, .wumad file, or /vol/... console path");
    private readonly Label sourceNote = Ui.Note("");
    private readonly CheckBox keep = Ui.Check("Keep the uploaded staging copy (--keep)");
    private readonly CheckBox sizeOnly = Ui.Check("Faster upload: compare sizes only (--size-only)");
    private readonly Button install;
    private readonly OutputPanel output = new("INSTALL OUTPUT");

    public InstallPage(AppHost host) : base(host)
    {
        Name = "Install";
        source.Text = host.Settings.LastInstallSource ?? "";
        keep.Checked = host.Settings.InstallKeep;
        sizeOnly.Checked = host.Settings.InstallSizeOnly;
        source.TextChanged += (_, _) => Describe();

        var card = Ui.Card("Install source", "A PC folder is uploaded to staging (usr/tmp/catr) first, then MCP installs it onto MLC. A .wumad is converted with makecfmaster.", out var body);
        body.Controls.Add(Ui.PathRow(source,
            Ui.Button("Folder…", (_, _) =>
            {
                if (Ui.PickFolder(this, "Choose an install image folder", Directory.Exists(source.Text) ? source.Text : null) is { } folder)
                    source.Text = folder;
            }),
            Ui.Button(".wumad…", (_, _) =>
            {
                using var dialog = new OpenFileDialog { Filter = "Wii U master image (*.wumad)|*.wumad|All files|*.*", Title = "Choose a .wumad" };
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    source.Text = dialog.FileName;
            })));
        body.Controls.Add(sourceNote);
        body.Controls.Add(Ui.Row(keep, sizeOnly));
        body.Controls.Add(Ui.Note("Size-only skips reading every file back. MCP does not check content hashes on install, so a damaged upload would install as is; title.tmd is always read back."));
        install = Ui.Button("Install…", async (_, _) => await Install(), primary: true);
        body.Controls.Add(Ui.Row(install));
        Controls.Add(Ui.PageLayout(card, output));
        Describe();
    }

    public override string Title => "Install";
    public override string Subtitle => "Install a title image onto the console's MLC with catr install.";

    private void Describe()
    {
        var path = source.Text.Trim();
        (sourceNote.Text, sourceNote.ForeColor) = path switch
        {
            "" => ("No source chosen.", Theme.Muted),
            _ when path.StartsWith("/vol/", StringComparison.Ordinal) => ("Console path: installed from there, nothing is uploaded.", Theme.Muted),
            _ when path.EndsWith(".wumad", StringComparison.OrdinalIgnoreCase) && File.Exists(path) =>
                ($".wumad, {new FileInfo(path).Length / 1048576.0:0.0} MB: converted to an install image, then uploaded.", Theme.Muted),
            _ when Directory.Exists(path) => Folder(path),
            _ => ("Not found.", Theme.Bad)
        };
    }

    private static (string, Color) Folder(string path)
    {
        var files = Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories).Select(f => new FileInfo(f)).ToList();
        var image = File.Exists(Path.Combine(path, "title.tmd"));
        var megabytes = files.Sum(f => f.Length) / 1048576.0;
        return image
            ? ($"Install image: {files.Count} files, {megabytes:0.0} MB (upload ~{Math.Max(1, megabytes * 2.7):0} s with read-back).", Theme.Muted)
            : ($"{files.Count} files, {megabytes:0.0} MB, but no title.tmd here; catr will refuse a folder that isn't an install image.", Theme.Warn);
    }

    private async Task Install()
    {
        var path = source.Text.Trim();
        if (path.Length == 0)
        {
            Ui.Error(this, "Choose an install image folder or a .wumad first.");
            return;
        }
        Host.Settings.LastInstallSource = path;
        Host.Settings.InstallKeep = keep.Checked;
        Host.Settings.InstallSizeOnly = sizeOnly.Checked;
        Host.Settings.Save();
        var args = new List<string> { "install", path };
        if (keep.Checked) args.Add("--keep");
        if (sizeOnly.Checked) args.Add("--size-only");
        install.Enabled = false;
        try
        {
            var result = await Host.Run(args, output.Box,
                confirm: $"Install onto the console's MLC?\n\n{path}\n\nAn installed title with the same ID is replaced." +
                         (sizeOnly.Checked ? "\n\nSize-only: files are not read back (except title.tmd)." : ""));
            if (result?.Ok == true)
                Host.TitlesStale = true;
        }
        finally
        {
            install.Enabled = true;
        }
    }
}
