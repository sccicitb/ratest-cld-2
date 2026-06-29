import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { motion } from "framer-motion";
import {
  Database,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Trash2,
} from "lucide-react";

import { SidebarSection } from "@/components/layout/SidebarSection";
import { ProfileFooter } from "@/components/layout/ProfileFooter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useCreateSession,
  useDeleteSession,
  useRenameSession,
  useSessions,
} from "@/lib/queries";
import { cn, groupByDate } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebarStore";
import type { Session } from "@/types/chat";

export function Sidebar() {
  const isOpen = useSidebarStore((s) => s.isOpen);
  const toggle = useSidebarStore((s) => s.toggle);
  const collapsed = !isOpen;

  const { data: sessions, isLoading } = useSessions();
  const createSession = useCreateSession();
  const renameSession = useRenameSession();
  const deleteSession = useDeleteSession();
  const navigate = useNavigate();
  const location = useLocation();

  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null);

  const handleNewChat = async () => {
    const session = await createSession.mutateAsync();
    navigate(`/chat/${session.id}`);
  };

  const submitRename = async (id: string) => {
    const title = renameValue.trim();
    if (title) await renameSession.mutateAsync({ id, title });
    setRenaming(null);
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const id = pendingDelete.id;
    setPendingDelete(null);
    await deleteSession.mutateAsync(id);
    if (location.pathname === `/chat/${id}`) navigate("/", { replace: true });
  };

  const groups = sessions ? groupByDate(sessions, (s) => s.updatedAt) : [];

  return (
    <TooltipProvider delayDuration={300}>
      <motion.aside
        initial={false}
        animate={{ width: collapsed ? 64 : 280 }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
        className="flex h-dvh shrink-0 flex-col bg-sidebar text-sidebar-foreground"
      >
        {/* Header */}
        <div className="flex h-14 items-center justify-between px-3">
          {!collapsed && (
            <Link to="/" className="flex items-center gap-2 pl-1">
              <span className="flex size-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
                CA
              </span>
              <span className="font-semibold">CityA</span>
            </Link>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={toggle}
                className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              >
                {collapsed ? (
                  <PanelLeftOpen className="size-5" />
                ) : (
                  <PanelLeftClose className="size-5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {collapsed ? "Expand" : "Collapse"}
            </TooltipContent>
          </Tooltip>
        </div>

        {/* Actions */}
        <SidebarSection collapsed={collapsed} className="space-y-1">
          <SidebarAction
            collapsed={collapsed}
            icon={<MessageSquarePlus className="size-5" />}
            label="New chat"
            onClick={handleNewChat}
          />
          <SidebarAction
            collapsed={collapsed}
            icon={<Database className="size-5" />}
            label="Knowledge base"
            to="/knowledge-base"
            active={location.pathname === "/knowledge-base"}
          />
        </SidebarSection>

        {/* Recent chats */}
        <ScrollArea className="mt-1 flex-1">
          {!collapsed && (
            <div className="pb-2">
              {isLoading && (
                <div className="space-y-2 px-4 pt-4">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-7 w-full bg-sidebar-accent" />
                  ))}
                </div>
              )}
              {groups.map((group) => (
                <SidebarSection key={group.label} label={group.label}>
                  <div className="space-y-0.5">
                    {group.items.map((session) => {
                      const isActive =
                        location.pathname === `/chat/${session.id}`;
                      if (renaming === session.id) {
                        return (
                          <Input
                            key={session.id}
                            autoFocus
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onBlur={() => submitRename(session.id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") submitRename(session.id);
                              if (e.key === "Escape") setRenaming(null);
                            }}
                            className="h-8 bg-sidebar-accent text-sidebar-foreground"
                          />
                        );
                      }
                      return (
                        <ContextMenu key={session.id}>
                          <ContextMenuTrigger asChild>
                            <Link
                              to={`/chat/${session.id}`}
                              className={cn(
                                "block truncate rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-sidebar-accent",
                                isActive && "bg-sidebar-accent font-medium",
                              )}
                            >
                              {session.title}
                            </Link>
                          </ContextMenuTrigger>
                          <ContextMenuContent>
                            <ContextMenuItem
                              onSelect={() => {
                                setRenaming(session.id);
                                setRenameValue(session.title);
                              }}
                            >
                              <Pencil />
                              Rename
                            </ContextMenuItem>
                            <ContextMenuItem
                              variant="destructive"
                              onSelect={() => setPendingDelete(session)}
                            >
                              <Trash2 />
                              Delete
                            </ContextMenuItem>
                          </ContextMenuContent>
                        </ContextMenu>
                      );
                    })}
                  </div>
                </SidebarSection>
              ))}
            </div>
          )}
        </ScrollArea>

        {/* Profile */}
        <div className="border-t border-sidebar-border p-2">
          <ProfileFooter collapsed={collapsed} />
        </div>
      </motion.aside>

      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete chat?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete “{pendingDelete?.title}”. This action
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  );
}

interface SidebarActionProps {
  collapsed?: boolean;
  icon: React.ReactNode;
  label: string;
  to?: string;
  onClick?: () => void;
  active?: boolean;
}

function SidebarAction({
  collapsed,
  icon,
  label,
  to,
  onClick,
  active,
}: SidebarActionProps) {
  const className = cn(
    "flex w-full items-center gap-3 rounded-lg px-2 py-2 text-sm font-medium transition-colors hover:bg-sidebar-accent",
    active && "bg-sidebar-accent",
    collapsed && "justify-center",
  );
  const content = (
    <>
      <span className="shrink-0">{icon}</span>
      {!collapsed && <span className="truncate">{label}</span>}
    </>
  );

  const inner = to ? (
    <Link to={to} className={className}>
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={className}>
      {content}
    </button>
  );

  if (!collapsed) return inner;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{inner}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
