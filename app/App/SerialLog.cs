using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace CatRTools;

/// <summary>Reads the shared USB serial history from catr's serial service, as `catr log` does:
/// one JSON line {"op": "log", "cursor": N, "token": ...} to 127.0.0.1, one JSON line back.
/// Only the read-only "log" and "status" ops are used here.</summary>
internal static class SerialLog
{
    public sealed record Chunk(string Text, long Cursor, bool Gap, string? Error);

    public static string EndpointFile => Path.Combine(AppHost.CatrHome, "serial.json");

    public static bool ServiceFilePresent => File.Exists(EndpointFile);

    public static async Task<Chunk> ReadAsync(long? cursor, CancellationToken token)
    {
        var request = new Dictionary<string, object> { ["op"] = "log" };
        if (cursor is not null)
            request["cursor"] = cursor.Value;
        using var reply = await RpcAsync(request, token);
        var root = reply.RootElement;
        return new Chunk(
            root.TryGetProperty("text", out var text) ? text.GetString() ?? "" : "",
            root.TryGetProperty("cursor", out var next) ? next.GetInt64() : cursor ?? 0,
            root.TryGetProperty("gap", out var gap) && gap.ValueKind == JsonValueKind.True,
            root.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String ? error.GetString() : null);
    }

    private static async Task<JsonDocument> RpcAsync(Dictionary<string, object> request, CancellationToken token)
    {
        using var endpoint = JsonDocument.Parse(await File.ReadAllTextAsync(EndpointFile, token));
        request["token"] = endpoint.RootElement.GetProperty("token").GetString() ?? "";
        var port = endpoint.RootElement.GetProperty("port").GetInt32();
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(10));
        using var client = new TcpClient { NoDelay = true };   // PC-local socket, not wupserver
        await client.ConnectAsync("127.0.0.1", port, timeout.Token);
        await using var stream = client.GetStream();
        await stream.WriteAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(request) + "\n"), timeout.Token);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var line = await reader.ReadLineAsync(timeout.Token) ?? throw new IOException("serial service closed without a reply");
        var reply = JsonDocument.Parse(line);
        if (reply.RootElement.TryGetProperty("rpc_error", out var failure))
        {
            reply.Dispose();
            throw new IOException(failure.GetString());
        }
        return reply;
    }
}
