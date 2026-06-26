/**
 * SSE stream readers. `sseLines` is for the chat `StreamEvent`s;
 * `uploadLines` is for the upload-stream events (§6.1/§8.3 of the spec).
 * Both parse the standard `data: {json}\n\n` framing.
 */

export async function* sseLines(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<Record<string, unknown>> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep = 0;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const line = raw.split("\n").find((l) => l.startsWith("data:"));
        if (line) yield JSON.parse(line.slice(5).trim());
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Same wire format — separate name for clarity at call sites. */
export const uploadLines = sseLines;
