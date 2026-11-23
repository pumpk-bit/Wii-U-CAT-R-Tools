using System.Text;
using System.Text.Json;

namespace CatRTools;

/// <summary>catr's JSON reports as plain indented text:
/// "key: value", nested objects indented, lists of objects as "- " items.</summary>
internal static class PlainJson
{
    public static string Format(JsonElement value)
    {
        var text = new StringBuilder();
        Write(text, value, 0);
        return text.ToString();
    }

    private static void Write(StringBuilder text, JsonElement value, int depth)
    {
        var pad = new string(' ', depth * 2);
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                foreach (var property in value.EnumerateObject())
                {
                    var label = property.Name.Replace('_', ' ');
                    if (IsScalar(property.Value) || IsScalarList(property.Value))
                    {
                        text.Append(pad).Append(label).Append(": ").AppendLine(Scalar(property.Value));
                    }
                    else if (IsEmpty(property.Value))
                    {
                        text.Append(pad).Append(label).AppendLine(": none");
                    }
                    else
                    {
                        text.Append(pad).Append(label).AppendLine(":");
                        Write(text, property.Value, depth + 1);
                    }
                }
                break;
            case JsonValueKind.Array:
                if (IsScalarList(value))
                {
                    text.Append(pad).AppendLine(Scalar(value));
                    break;
                }
                foreach (var item in value.EnumerateArray())
                {
                    if (IsScalar(item))
                    {
                        text.Append(pad).Append("- ").AppendLine(Scalar(item));
                        continue;
                    }
                    // "- " on the first line of each item, the rest indented under it
                    var inner = new StringBuilder();
                    Write(inner, item, 0);
                    var lines = inner.ToString().TrimEnd().Split(Environment.NewLine);
                    for (var i = 0; i < lines.Length; i++)
                        text.Append(pad).Append(i == 0 ? "- " : "  ").AppendLine(lines[i]);
                }
                break;
            default:
                text.Append(pad).AppendLine(Scalar(value));
                break;
        }
    }

    private static bool IsScalar(JsonElement value) =>
        value.ValueKind is not (JsonValueKind.Object or JsonValueKind.Array);

    private static bool IsEmpty(JsonElement value) =>
        value.ValueKind == JsonValueKind.Object ? !value.EnumerateObject().Any()
        : value.ValueKind == JsonValueKind.Array && value.GetArrayLength() == 0;

    private static bool IsScalarList(JsonElement value) =>
        value.ValueKind == JsonValueKind.Array && value.GetArrayLength() > 0 && value.EnumerateArray().All(IsScalar);

    private static string Scalar(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.True => "yes",
        JsonValueKind.False => "no",
        JsonValueKind.Null => "—",
        JsonValueKind.Array => string.Join(", ", value.EnumerateArray().Select(Scalar)),
        _ => value.GetRawText()
    };
}
