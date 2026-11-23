namespace CatRTools;

/// <summary>Title icons that `catr icons` copied to %APPDATA%\catr\icons (&lt;title&gt;-v&lt;version&gt;.tga),
/// decoded once and kept in memory.</summary>
internal static class Icons
{
    private static readonly Dictionary<string, Image?> Loaded = [];

    public static string Folder => Path.Combine(AppHost.CatrHome, "icons");

    /// <summary>A PCFS title's icon is read straight from its folder on this PC.</summary>
    public static string FileFor(TitleInfo title) => title.HostFolder is { } folder
        ? Path.Combine(folder, "meta", "iconTex.tga")
        : Path.Combine(Folder, $"{title.Id}-v{title.Version ?? 0}.tga");

    public static bool Cached(TitleInfo title) => File.Exists(FileFor(title));

    /// <summary>The icon, scaled to <paramref name="size"/>; null if not fetched (or unreadable).</summary>
    public static Image? Get(TitleInfo title, int size = 32)
    {
        var stamp = title.IsPcfs && File.Exists(FileFor(title)) ? File.GetLastWriteTimeUtc(FileFor(title)).Ticks : 0;
        var key = $"{FileFor(title)}@{size}@{stamp}";
        if (Loaded.TryGetValue(key, out var image))
            return image;
        image = null;
        try
        {
            if (File.Exists(FileFor(title)))
            {
                using var full = Tga.Read(File.ReadAllBytes(FileFor(title)));
                image = size == full.Width ? new Bitmap(full) : Scale(full, size);
            }
        }
        catch (Exception exception) when (exception is IOException or InvalidDataException or ArgumentException)
        {
        }
        if (image is not null)
            Loaded[key] = image;
        return image;
    }

    private static Bitmap Scale(Image source, int size)
    {
        var bitmap = new Bitmap(size, size);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        graphics.DrawImage(source, 0, 0, size, size);
        return bitmap;
    }
}

/// <summary>Minimal TGA reader: true-colour 24/32-bit, raw (type 2) or RLE (type 10), either origin.</summary>
internal static class Tga
{
    public static Bitmap Read(byte[] data)
    {
        if (data.Length < 18)
            throw new InvalidDataException("TGA header too short");
        int idLength = data[0], colorMapType = data[1], type = data[2];
        int width = BitConverter.ToUInt16(data, 12), height = BitConverter.ToUInt16(data, 14);
        int bpp = data[16], descriptor = data[17];
        if (colorMapType != 0 || (type != 2 && type != 10) || (bpp != 24 && bpp != 32) || width == 0 || height == 0 || width > 1024 || height > 1024)
            throw new InvalidDataException($"unsupported TGA (type {type}, {bpp} bpp, {width}x{height})");
        var bytes = bpp / 8;
        var pixels = new byte[width * height * 4];
        var source = 18 + idLength;
        var count = width * height;
        void Put(int index, int at)
        {
            if (at + bytes > data.Length) throw new InvalidDataException("TGA data truncated");
            pixels[index * 4] = data[at];          // B
            pixels[index * 4 + 1] = data[at + 1];  // G
            pixels[index * 4 + 2] = data[at + 2];  // R
            pixels[index * 4 + 3] = bytes == 4 ? data[at + 3] : (byte)255;
        }
        if (type == 2)
        {
            for (var i = 0; i < count; i++, source += bytes)
                Put(i, source);
        }
        else
        {
            for (var i = 0; i < count;)
            {
                if (source >= data.Length) throw new InvalidDataException("TGA data truncated");
                var header = data[source++];
                var run = (header & 0x7F) + 1;
                if ((header & 0x80) != 0)
                {
                    for (var k = 0; k < run && i < count; k++) Put(i++, source);
                    source += bytes;
                }
                else
                {
                    for (var k = 0; k < run && i < count; k++, source += bytes) Put(i++, source);
                }
            }
        }
        var bitmap = new Bitmap(width, height, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
        var lockData = bitmap.LockBits(new Rectangle(0, 0, width, height), System.Drawing.Imaging.ImageLockMode.WriteOnly, bitmap.PixelFormat);
        try
        {
            var topDown = (descriptor & 0x20) != 0;
            for (var y = 0; y < height; y++)
            {
                var row = topDown ? y : height - 1 - y;
                System.Runtime.InteropServices.Marshal.Copy(pixels, y * width * 4, lockData.Scan0 + row * lockData.Stride, width * 4);
            }
        }
        finally
        {
            bitmap.UnlockBits(lockData);
        }
        return bitmap;
    }
}
