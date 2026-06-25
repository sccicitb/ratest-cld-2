import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";

import { cn, SUPPORTED_FILE_TYPES } from "@/lib/utils";

export function UploadDropzone({
  onFiles,
}: {
  onFiles: (files: FileList | File[]) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
      }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-6 py-10 text-center transition-colors hover:border-brand-blue/50 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        dragOver && "border-brand-blue bg-brand-blue/5",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        accept={SUPPORTED_FILE_TYPES.join(",")}
        onChange={(e) => {
          if (e.target.files) onFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <span className="flex size-12 items-center justify-center rounded-full bg-muted">
        <UploadCloud className="size-6 text-brand-blue" />
      </span>
      <p className="font-medium">
        Drag &amp; drop files, or{" "}
        <span className="text-brand-blue">browse</span>
      </p>
      <p className="text-xs text-muted-foreground">
        Supports {SUPPORTED_FILE_TYPES.join(", ")}
      </p>
    </div>
  );
}
