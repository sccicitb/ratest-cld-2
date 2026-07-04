import { useMemo, useState } from "react";
import { AlertCircle, ChevronDown, Database, Globe, Search } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { FileCard } from "@/components/kb/FileCard";
import { TagEditor } from "@/components/kb/TagEditor";
import { UploadDropzone } from "@/components/kb/UploadDropzone";
import { UploadTaskCard } from "@/components/kb/UploadTaskCard";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useFileUpload } from "@/hooks/useFileUpload";
import * as api from "@/lib/api";
import { useKnowledgeBaseFiles } from "@/lib/queries";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import type { FileStatus } from "@/types/kb";

const STATUS_FILTERS: { label: string; value: FileStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Ready", value: "ready" },
  { label: "Indexing", value: "indexing" },
  { label: "Error", value: "error" },
];

export default function KnowledgeBaseRoute() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<FileStatus | "all">("all");
  const { data: files, isLoading } = useKnowledgeBaseFiles();
  const { addFiles, tasks, cancelTask, retryTask, removeTask } =
    useFileUpload();

  const isAdmin = useAuthStore((s) => s.user?.isAdmin ?? false);

  // Admin: all groups (enabled only when admin to avoid 403 for regular users)
  const { data: adminGroups = [] } = useQuery({
    queryKey: ["admin", "groups"],
    queryFn: api.adminListGroups,
    enabled: isAdmin,
  });

  // Regular user: own groups
  const { data: myGroups = [] } = useQuery({
    queryKey: ["my-groups"],
    queryFn: api.getMyGroups,
    enabled: !isAdmin,
  });

  // Admin upload state
  const [adminGroupId, setAdminGroupId] = useState<string | null>(null);
  const [isPublic, setIsPublic] = useState(false);
  const [adminTags, setAdminTags] = useState<string[]>([]);

  // Regular user multi-group selection (null = use first group)
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  // Effective group id for regular users
  const effectiveUserGroupId = useMemo(() => {
    if (isAdmin) return null;
    if (myGroups.length === 0) return null;
    if (myGroups.length === 1) return myGroups[0].id;
    // Multiple groups: selectedGroupId if set and valid, else first
    const valid = myGroups.some((g) => g.id === selectedGroupId);
    return valid ? selectedGroupId : myGroups[0].id;
  }, [isAdmin, myGroups, selectedGroupId]);

  // groupId → name map for FileCard badges
  const groupMap = useMemo(() => {
    const map = new Map<string, string>();
    const source = isAdmin ? adminGroups : myGroups;
    for (const g of source) map.set(g.id, g.name);
    return map;
  }, [isAdmin, adminGroups, myGroups]);

  // Current filing opts derived from role state
  const currentOpts = useMemo(() => {
    if (isAdmin) {
      return { groupId: adminGroupId, isPublic, tags: adminTags };
    }
    return { groupId: effectiveUserGroupId };
  }, [isAdmin, adminGroupId, isPublic, adminTags, effectiveUserGroupId]);

  // Admin guard: must select a group OR enable Public
  const canUpload = !isAdmin || adminGroupId !== null || isPublic;

  const handleFiles = (fileList: FileList | File[]) => {
    if (!canUpload) return;
    addFiles(fileList, currentOpts);
  };

  const filtered = useMemo(() => {
    if (!files) return [];
    return files.filter((f) => {
      const matchesSearch = f.name
        .toLowerCase()
        .includes(search.toLowerCase());
      const matchesStatus = status === "all" || f.status === status;
      return matchesSearch && matchesStatus;
    });
  }, [files, search, status]);

  const activeTasks = tasks.filter((t) => t.status !== "done");

  // ─── Upload panel ────────────────────────────────────────────────────────── //

  const renderUploadPanel = () => {
    // Regular user — no groups: hide dropzone, show guidance
    if (!isAdmin && myGroups.length === 0) {
      return (
        <div className="rounded-xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground">
          Ask an admin to add you to a group to upload files.
        </div>
      );
    }

    // Regular user — exactly one group: auto-file, read-only info
    if (!isAdmin && myGroups.length === 1) {
      const group = myGroups[0];
      return (
        <>
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Filed under</span>
              <Badge variant="outline">{group.name}</Badge>
              {group.defaultTags.length > 0 && (
                <>
                  <span className="text-muted-foreground">·</span>
                  {group.defaultTags.map((tag) => (
                    <Badge key={tag} variant="secondary">
                      {tag}
                    </Badge>
                  ))}
                </>
              )}
            </div>
          </div>
          <UploadDropzone onFiles={handleFiles} />
        </>
      );
    }

    // Regular user — multiple groups: group picker, read-only default tags
    if (!isAdmin && myGroups.length > 1) {
      const activeGroup = myGroups.find((g) => g.id === effectiveUserGroupId);
      return (
        <>
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Label htmlFor="user-group-select">Group</Label>
              <div className="relative">
                <select
                  id="user-group-select"
                  value={effectiveUserGroupId ?? ""}
                  onChange={(e) => setSelectedGroupId(e.target.value || null)}
                  className="h-8 appearance-none rounded-md border border-input bg-background px-2 pr-7 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {myGroups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              </div>
              {activeGroup && activeGroup.defaultTags.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-muted-foreground">Default tags:</span>
                  {activeGroup.defaultTags.map((tag) => (
                    <Badge key={tag} variant="secondary">
                      {tag}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </div>
          <UploadDropzone onFiles={handleFiles} />
        </>
      );
    }

    // Admin: full controls — group picker, Public toggle, free tags
    return (
      <>
        <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            {/* Group picker */}
            <div className="flex items-center gap-2">
              <Label htmlFor="admin-group-select">Group</Label>
              <div className="relative">
                <select
                  id="admin-group-select"
                  value={adminGroupId ?? ""}
                  onChange={(e) =>
                    setAdminGroupId(e.target.value || null)
                  }
                  className="h-8 appearance-none rounded-md border border-input bg-background px-2 pr-7 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="">— No group —</option>
                  {adminGroups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              </div>
            </div>

            {/* Public toggle */}
            <div className="flex items-center gap-2">
              <Switch
                id="is-public"
                checked={isPublic}
                onCheckedChange={setIsPublic}
              />
              <Label
                htmlFor="is-public"
                className="flex cursor-pointer items-center gap-1"
              >
                <Globe className="size-3.5" />
                Public
              </Label>
            </div>
          </div>

          {/* Free tags */}
          <div className="flex items-start gap-2 text-sm">
            <Label className="pt-1 shrink-0">Tags</Label>
            <TagEditor tags={adminTags} onChange={setAdminTags} />
          </div>
        </div>

        {/* Dropzone — disabled when neither group nor Public is set */}
        {canUpload ? (
          <UploadDropzone onFiles={handleFiles} />
        ) : (
          <div className="flex cursor-not-allowed flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground opacity-60">
            <AlertCircle className="size-5" />
            Choose a group or mark the file Public before uploading.
          </div>
        )}
      </>
    );
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
        <Database className="size-5 text-brand-blue" />
        <h1 className="font-medium">Knowledge Base</h1>
        {files && (
          <Badge variant="secondary" className="ml-1">
            {files.length}
          </Badge>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          {/* Role-based upload panel */}
          <div className="space-y-3">{renderUploadPanel()}</div>

          {/* Active upload tasks */}
          {activeTasks.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Uploads
              </h2>
              {activeTasks.map((task) => (
                <UploadTaskCard
                  key={task.id}
                  task={task}
                  onCancel={cancelTask}
                  onRetry={retryTask}
                  onRemove={removeTask}
                />
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-xs flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search files…"
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-1">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setStatus(f.value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    status === f.value
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* File grid */}
          {isLoading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-40 w-full" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
              No files match your filters.
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((file) => (
                <FileCard
                  key={file.id}
                  file={file}
                  groupName={
                    file.groupId != null
                      ? groupMap.get(file.groupId)
                      : undefined
                  }
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
