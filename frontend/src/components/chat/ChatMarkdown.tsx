import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Shared renderer keeps streamed and saved replies visually consistent. */
export function ChatMarkdown({ children }: { children: string }) {
  return (
    <div className="prose-chat min-w-0 max-w-full">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="chat-table-scroll" role="region" aria-label="Tabel hasil chat" tabIndex={0}>
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
