import { useEffect, useState } from "react";
import {
  Check,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  X as XIcon,
} from "lucide-react";
import { useNavigate } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useAdminCreateGroup,
  useAdminCreateMcpServer,
  useAdminCreateUser,
  useAdminDeleteGroup,
  useAdminDeleteMcpServer,
  useAdminGroup,
  useAdminGroups,
  useAdminMcpServers,
  useAdminPatchGroup,
  useAdminPatchMcpServer,
  useAdminPatchUser,
  useAdminResetPassword,
  useAdminSetGroupMembers,
  useAdminSetGroupServers,
  useAdminTestMcpServer,
  useAdminUsers,
} from "@/lib/adminQueries";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import type { AdminUser, Group, MCPServer, TestMcpResult } from "@/types/admin";

function genTempPassword(): string {
  const words = ["Lemon", "Storm", "River", "Blaze", "Swift"];
  const nums = Math.floor(100 + Math.random() * 900);
  return `${words[Math.floor(Math.random() * words.length)]}${nums}!`;
}

/* ─── Route root ────────────────────────────────────────────────────────── */

export default function AdminRoute() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [tab, setTab] = useState("users");

  // Admin guard: non-admin (or not yet loaded) → redirect to home
  useEffect(() => {
    if (user !== null && !user.isAdmin) {
      navigate("/", { replace: true });
    }
  }, [user, navigate]);

  if (!user?.isAdmin) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
        <Shield className="size-5 text-brand-blue" />
        <h1 className="font-medium">Admin Console</h1>
      </header>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl p-6">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="mb-6">
              <TabsTrigger value="users">Users</TabsTrigger>
              <TabsTrigger value="groups">Groups</TabsTrigger>
              <TabsTrigger value="mcp">MCP Servers</TabsTrigger>
            </TabsList>
            <TabsContent value="users">
              <UsersTab />
            </TabsContent>
            <TabsContent value="groups">
              <GroupsTab />
            </TabsContent>
            <TabsContent value="mcp">
              <McpTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}

/* ─── Users Tab ─────────────────────────────────────────────────────────── */

function UsersTab() {
  const { data: users = [], isLoading, error } = useAdminUsers();
  const { data: groups = [] } = useAdminGroups();
  const createUserMutation = useAdminCreateUser();
  const patchUserMutation = useAdminPatchUser();
  const resetPasswordMutation = useAdminResetPassword();

  const [newOpen, setNewOpen] = useState(false);
  const [resetResult, setResetResult] = useState<{ userId: string; pw: string } | null>(null);
  const [createResult, setCreateResult] = useState<{ email: string; pw: string } | null>(null);

  // New user form
  const [nEmail, setNEmail] = useState("");
  const [nDisplay, setNDisplay] = useState("");
  const [nAdmin, setNAdmin] = useState(false);

  const handleCreate = async () => {
    if (!nEmail.trim() || !nDisplay.trim()) return;
    const pw = genTempPassword();
    const user = await createUserMutation.mutateAsync({
      email: nEmail.trim(),
      displayName: nDisplay.trim(),
      password: pw,
      isAdmin: nAdmin,
    });
    setCreateResult({ email: user.email, pw });
    setNewOpen(false);
    setNEmail("");
    setNDisplay("");
    setNAdmin(false);
  };

  const handleReset = async (userId: string) => {
    const { tempPassword } = await resetPasswordMutation.mutateAsync(userId);
    setResetResult({ userId, pw: tempPassword });
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error) {
    return <p className="text-sm text-destructive">Failed to load users.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{users.length} users</p>
        <Button size="sm" onClick={() => setNewOpen(true)}>
          <Plus className="size-4" /> New user
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name / Email</TableHead>
            <TableHead>Groups</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {users.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={5}
                className="py-8 text-center text-sm text-muted-foreground"
              >
                No users yet.
              </TableCell>
            </TableRow>
          )}
          {users.map((u) => (
            <UserRow
              key={u.id}
              user={u}
              groups={groups}
              onDisable={() =>
                patchUserMutation.mutate({ id: u.id, data: { disabled: true } })
              }
              onEnable={() =>
                patchUserMutation.mutate({ id: u.id, data: { disabled: false } })
              }
              onReset={() => void handleReset(u.id)}
            />
          ))}
        </TableBody>
      </Table>

      {/* New user dialog */}
      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New user</DialogTitle>
            <DialogDescription>
              Provision an account. A temporary password will be shown after creation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>Email</Label>
              <Input
                type="email"
                placeholder="user@example.com"
                value={nEmail}
                onChange={(e) => setNEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Display name</Label>
              <Input
                placeholder="Full name"
                value={nDisplay}
                onChange={(e) => setNDisplay(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={nAdmin} onCheckedChange={setNAdmin} id="new-admin" />
              <Label htmlFor="new-admin">Admin</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreate()}
              disabled={!nEmail.trim() || !nDisplay.trim() || createUserMutation.isPending}
            >
              {createUserMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                "Create"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset password result dialog */}
      <Dialog open={!!resetResult} onOpenChange={(o) => !o && setResetResult(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Password reset</DialogTitle>
            <DialogDescription>
              Copy the temporary password and send it to the user.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md bg-muted px-4 py-3 font-mono text-sm">
            {resetResult?.pw}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (resetResult) void navigator.clipboard.writeText(resetResult.pw);
              }}
            >
              Copy
            </Button>
            <Button onClick={() => setResetResult(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create result dialog */}
      <Dialog open={!!createResult} onOpenChange={(o) => !o && setCreateResult(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>User created</DialogTitle>
            <DialogDescription>
              Account <strong>{createResult?.email}</strong> created. Temporary password:
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md bg-muted px-4 py-3 font-mono text-sm">
            {createResult?.pw}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (createResult) void navigator.clipboard.writeText(createResult.pw);
              }}
            >
              Copy
            </Button>
            <Button onClick={() => setCreateResult(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface UserRowProps {
  user: AdminUser;
  groups: Group[];
  onDisable: () => void;
  onEnable: () => void;
  onReset: () => void;
}

function UserRow({ user, groups, onDisable, onEnable, onReset }: UserRowProps) {
  const userGroups = groups.filter((g) => user.groupIds.includes(g.id));
  return (
    <TableRow className={user.disabled ? "opacity-50" : ""}>
      <TableCell>
        <div>
          <p className="font-medium">{user.displayName}</p>
          <p className="text-xs text-muted-foreground">{user.email}</p>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1">
          {userGroups.map((g) => (
            <Badge key={g.id} variant="secondary" className="text-xs">
              {g.name}
            </Badge>
          ))}
          {userGroups.length === 0 && (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </div>
      </TableCell>
      <TableCell>
        {user.isAdmin && <Badge className="text-xs">Admin</Badge>}
      </TableCell>
      <TableCell>
        <Badge
          variant={user.disabled ? "destructive" : "outline"}
          className="text-xs"
        >
          {user.disabled ? "Disabled" : "Active"}
        </Badge>
      </TableCell>
      <TableCell>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7">
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {user.disabled ? (
              <DropdownMenuItem onSelect={onEnable}>Enable</DropdownMenuItem>
            ) : (
              <DropdownMenuItem onSelect={onDisable} variant="destructive">
                Disable
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onReset}>
              <RefreshCw className="size-4" /> Reset password
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

/* ─── Groups Tab ────────────────────────────────────────────────────────── */

function GroupsTab() {
  const { data: groups = [], isLoading, error } = useAdminGroups();
  const { data: users = [] } = useAdminUsers();
  const { data: mcpServers = [] } = useAdminMcpServers();
  const createGroupMutation = useAdminCreateGroup();
  const setGroupServersMutation = useAdminSetGroupServers();
  const deleteGroupMutation = useAdminDeleteGroup();

  const [newOpen, setNewOpen] = useState(false);
  const [editGroupId, setEditGroupId] = useState<string | null>(null);
  const [membersGroupId, setMembersGroupId] = useState<string | null>(null);

  // New group form
  const [nName, setNName] = useState("");
  const [nTagInput, setNTagInput] = useState("");
  const [nTags, setNTags] = useState<string[]>([]);
  const [nMcpIds, setNMcpIds] = useState<string[]>([]);

  const addTag = (
    tag: string,
    setter: React.Dispatch<React.SetStateAction<string[]>>,
    inputSetter: React.Dispatch<React.SetStateAction<string>>,
  ) => {
    const t = tag.trim().toLowerCase().replace(/\s+/g, "-");
    if (t) setter((prev) => (prev.includes(t) ? prev : [...prev, t]));
    inputSetter("");
  };

  const handleCreate = async () => {
    if (!nName.trim()) return;
    const group = await createGroupMutation.mutateAsync({
      name: nName.trim(),
      defaultTags: nTags,
    });
    if (nMcpIds.length > 0) {
      await setGroupServersMutation.mutateAsync({ id: group.id, serverIds: nMcpIds });
    }
    setNewOpen(false);
    setNName("");
    setNTags([]);
    setNMcpIds([]);
    setNTagInput("");
  };

  const membersGroup = membersGroupId
    ? (groups.find((g) => g.id === membersGroupId) ?? null)
    : null;

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error) {
    return <p className="text-sm text-destructive">Failed to load groups.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{groups.length} groups</p>
        <Button size="sm" onClick={() => setNewOpen(true)}>
          <Plus className="size-4" /> New group
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Members</TableHead>
            <TableHead>Tags</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={4}
                className="py-8 text-center text-sm text-muted-foreground"
              >
                No groups yet.
              </TableCell>
            </TableRow>
          )}
          {groups.map((g) => (
            <TableRow key={g.id}>
              <TableCell className="font-medium">{g.name}</TableCell>
              <TableCell>
                <Badge variant="secondary">{g.memberCount}</Badge>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {g.defaultTags.map((t) => (
                    <Badge key={t} variant="secondary" className="text-xs">
                      {t}
                    </Badge>
                  ))}
                </div>
              </TableCell>
              <TableCell>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="size-7">
                      <MoreHorizontal className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => setEditGroupId(g.id)}>
                      Edit group
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setMembersGroupId(g.id)}>
                      Manage members
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => deleteGroupMutation.mutate(g.id)}
                    >
                      <Trash2 className="size-4" /> Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {/* New group dialog */}
      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New group</DialogTitle>
          </DialogHeader>
          <GroupForm
            name={nName}
            onNameChange={setNName}
            tagInput={nTagInput}
            onTagInputChange={setNTagInput}
            tags={nTags}
            onAddTag={() => addTag(nTagInput, setNTags, setNTagInput)}
            onRemoveTag={(t) => setNTags((prev) => prev.filter((x) => x !== t))}
            mcpIds={nMcpIds}
            mcpServers={mcpServers}
            onToggleMcp={(id) =>
              setNMcpIds((prev) =>
                prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
              )
            }
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreate()}
              disabled={!nName.trim() || createGroupMutation.isPending}
            >
              {createGroupMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                "Create"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit group dialog — fetches its own GroupDetail */}
      {editGroupId && (
        <EditGroupDialog
          groupId={editGroupId}
          mcpServers={mcpServers}
          onClose={() => setEditGroupId(null)}
        />
      )}

      {/* Manage members dialog */}
      {membersGroup && (
        <MembersDialog
          group={membersGroup}
          users={users}
          onClose={() => setMembersGroupId(null)}
        />
      )}
    </div>
  );
}

/* ─── Edit Group Dialog (fetches GroupDetail for current mcpServerIds) ──── */

function EditGroupDialog({
  groupId,
  mcpServers,
  onClose,
}: {
  groupId: string;
  mcpServers: MCPServer[];
  onClose: () => void;
}) {
  const { data: detail, isLoading } = useAdminGroup(groupId);
  const patchGroupMutation = useAdminPatchGroup();
  const setGroupServersMutation = useAdminSetGroupServers();

  const [name, setName] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [mcpIds, setMcpIds] = useState<string[]>([]);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (detail && !initialized) {
      setName(detail.name);
      setTags([...detail.defaultTags]);
      setMcpIds([...detail.mcpServerIds]);
      setInitialized(true);
    }
  }, [detail, initialized]);

  const addTag = () => {
    const t = tagInput.trim().toLowerCase().replace(/\s+/g, "-");
    if (t) setTags((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setTagInput("");
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    await patchGroupMutation.mutateAsync({ id: groupId, data: { name: name.trim(), defaultTags: tags } });
    await setGroupServersMutation.mutateAsync({ id: groupId, serverIds: mcpIds });
    onClose();
  };

  const isPending = patchGroupMutation.isPending || setGroupServersMutation.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit group</DialogTitle>
        </DialogHeader>
        {isLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <GroupForm
            name={name}
            onNameChange={setName}
            tagInput={tagInput}
            onTagInputChange={setTagInput}
            tags={tags}
            onAddTag={addTag}
            onRemoveTag={(t) => setTags((prev) => prev.filter((x) => x !== t))}
            mcpIds={mcpIds}
            mcpServers={mcpServers}
            onToggleMcp={(id) =>
              setMcpIds((prev) =>
                prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
              )
            }
          />
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={!name.trim() || isPending || isLoading}
          >
            {isPending ? <Loader2 className="size-4 animate-spin" /> : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Group Form ────────────────────────────────────────────────────────── */

interface GroupFormProps {
  name: string;
  onNameChange: (v: string) => void;
  tagInput: string;
  onTagInputChange: (v: string) => void;
  tags: string[];
  onAddTag: () => void;
  onRemoveTag: (t: string) => void;
  mcpIds: string[];
  mcpServers: MCPServer[];
  onToggleMcp: (id: string) => void;
}

function GroupForm({
  name,
  onNameChange,
  tagInput,
  onTagInputChange,
  tags,
  onAddTag,
  onRemoveTag,
  mcpIds,
  mcpServers,
  onToggleMcp,
}: GroupFormProps) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label>Name</Label>
        <Input
          placeholder="e.g. Accounting · Store A"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label>Tags</Label>
        <div className="flex gap-2">
          <Input
            placeholder="tag name, press Enter"
            value={tagInput}
            onChange={(e) => onTagInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onAddTag();
              }
            }}
            className="flex-1"
          />
          <Button type="button" variant="outline" size="sm" onClick={onAddTag}>
            Add
          </Button>
        </div>
        <div className="flex flex-wrap gap-1 pt-1">
          {tags.map((t) => (
            <Badge
              key={t}
              variant="secondary"
              className="cursor-pointer gap-1 text-xs"
              onClick={() => onRemoveTag(t)}
            >
              {t} <XIcon className="size-2.5" />
            </Badge>
          ))}
        </div>
      </div>
      <div className="space-y-1">
        <Label>MCP Servers granted</Label>
        <div className="flex flex-wrap gap-2">
          {mcpServers.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => onToggleMcp(m.id)}
              className={cn(
                "rounded-md border px-2 py-1 text-xs transition-colors",
                mcpIds.includes(m.id)
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border hover:bg-muted",
              )}
            >
              {m.name}
            </button>
          ))}
          {mcpServers.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No MCP servers configured yet.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Members Dialog ────────────────────────────────────────────────────── */

interface MembersDialogProps {
  group: Group;
  users: AdminUser[];
  onClose: () => void;
}

function MembersDialog({ group, users, onClose }: MembersDialogProps) {
  const setGroupMembersMutation = useAdminSetGroupMembers();
  const [selected, setSelected] = useState<string[]>(
    users.filter((u) => u.groupIds.includes(group.id)).map((u) => u.id),
  );

  const toggle = (uid: string) =>
    setSelected((prev) =>
      prev.includes(uid) ? prev.filter((x) => x !== uid) : [...prev, uid],
    );

  const handleSave = async () => {
    await setGroupMembersMutation.mutateAsync({ id: group.id, userIds: selected });
    onClose();
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Manage members — {group.name}</DialogTitle>
          <DialogDescription>
            Toggle which users belong to this group.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {users.map((u) => (
            <button
              key={u.id}
              type="button"
              onClick={() => toggle(u.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors hover:bg-muted",
                selected.includes(u.id) && "bg-muted",
              )}
            >
              <span
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded border",
                  selected.includes(u.id)
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border",
                )}
              >
                {selected.includes(u.id) && <Check className="size-3" />}
              </span>
              <span className="flex-1 text-sm">
                {u.displayName}
                <span className="ml-1 text-xs text-muted-foreground">{u.email}</span>
              </span>
            </button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={setGroupMembersMutation.isPending}
          >
            {setGroupMembersMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              "Save members"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ─── MCP Servers Tab ───────────────────────────────────────────────────── */

interface TestRecord {
  testing: boolean;
  result: TestMcpResult | null;
}

function McpTab() {
  const { data: mcpServers = [], isLoading, error } = useAdminMcpServers();
  const createMcpMutation = useAdminCreateMcpServer();
  const patchMcpMutation = useAdminPatchMcpServer();
  const deleteMcpMutation = useAdminDeleteMcpServer();
  const testMcpMutation = useAdminTestMcpServer();

  // Per-server test state (not stored in RQ — it's transient UI state)
  const [testRecords, setTestRecords] = useState<Record<string, TestRecord>>({});

  const [newOpen, setNewOpen] = useState(false);
  const [nName, setNName] = useState("");
  const [nUrl, setNUrl] = useState("");
  const [nAuth, setNAuth] = useState<"none" | "bearer">("none");
  const [nToken, setNToken] = useState("");

  const handleCreate = async () => {
    if (!nName.trim() || !nUrl.trim()) return;
    await createMcpMutation.mutateAsync({
      name: nName.trim(),
      url: nUrl.trim(),
      authType: nAuth,
      token: nAuth === "bearer" ? nToken : undefined,
    });
    setNewOpen(false);
    setNName("");
    setNUrl("");
    setNAuth("none");
    setNToken("");
  };

  const handleTest = async (id: string) => {
    setTestRecords((prev) => ({ ...prev, [id]: { testing: true, result: null } }));
    try {
      const result = await testMcpMutation.mutateAsync(id);
      setTestRecords((prev) => ({ ...prev, [id]: { testing: false, result } }));
    } catch {
      setTestRecords((prev) => ({
        ...prev,
        [id]: { testing: false, result: { ok: false, error: "Connection failed" } },
      }));
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error) {
    return <p className="text-sm text-destructive">Failed to load MCP servers.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{mcpServers.length} servers</p>
        <Button size="sm" onClick={() => setNewOpen(true)}>
          <Plus className="size-4" /> Add server
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>URL</TableHead>
            <TableHead>Auth</TableHead>
            <TableHead>Enabled</TableHead>
            <TableHead>Connection</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {mcpServers.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={6}
                className="py-8 text-center text-sm text-muted-foreground"
              >
                No MCP servers yet.
              </TableCell>
            </TableRow>
          )}
          {mcpServers.map((m) => (
            <McpRow
              key={m.id}
              server={m}
              testRecord={testRecords[m.id] ?? { testing: false, result: null }}
              onToggle={() =>
                patchMcpMutation.mutate({ id: m.id, data: { enabled: !m.enabled } })
              }
              onTest={() => void handleTest(m.id)}
              onDelete={() => deleteMcpMutation.mutate(m.id)}
            />
          ))}
        </TableBody>
      </Table>

      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add MCP server</DialogTitle>
            <DialogDescription>
              Register a Model Context Protocol server for this workspace.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>Name</Label>
              <Input
                placeholder="e.g. satudata-garut"
                value={nName}
                onChange={(e) => setNName(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>URL</Label>
              <Input
                placeholder="https://example.com/mcp"
                value={nUrl}
                onChange={(e) => setNUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Transport</Label>
              <p className="text-sm text-muted-foreground">streamable-http</p>
            </div>
            <div className="space-y-1">
              <Label>Auth type</Label>
              <div className="flex gap-2">
                {(["none", "bearer"] as const).map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => setNAuth(a)}
                    className={cn(
                      "rounded-md border px-3 py-1 text-sm transition-colors",
                      nAuth === a
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border hover:bg-muted",
                    )}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>
            {nAuth === "bearer" && (
              <div className="space-y-1">
                <Label>Token</Label>
                <Input
                  type="password"
                  placeholder="Bearer token"
                  value={nToken}
                  onChange={(e) => setNToken(e.target.value)}
                />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreate()}
              disabled={!nName.trim() || !nUrl.trim() || createMcpMutation.isPending}
            >
              {createMcpMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                "Add"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface McpRowProps {
  server: MCPServer;
  testRecord: TestRecord;
  onToggle: () => void;
  onTest: () => void;
  onDelete: () => void;
}

function McpRow({ server, testRecord, onToggle, onTest, onDelete }: McpRowProps) {
  return (
    <TableRow>
      <TableCell className="font-medium">{server.name}</TableCell>
      <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">
        {server.url}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="text-xs">
          {server.authType}
        </Badge>
      </TableCell>
      <TableCell>
        <Switch checked={server.enabled} onCheckedChange={onToggle} />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onTest}
            disabled={testRecord.testing}
          >
            {testRecord.testing ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              "Test"
            )}
          </Button>
          {testRecord.result?.ok && (
            <Badge
              variant="outline"
              className="gap-1 border-green-500 text-xs text-green-600"
            >
              <Check className="size-3" /> Connected
            </Badge>
          )}
          {testRecord.result && !testRecord.result.ok && (
            <Badge variant="destructive" className="gap-1 text-xs">
              <XIcon className="size-3" /> {testRecord.result.error ?? "Failed"}
            </Badge>
          )}
        </div>
      </TableCell>
      <TableCell>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7">
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 className="size-4" /> Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}
