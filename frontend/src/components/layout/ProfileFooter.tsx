import { useState } from "react";
import { useNavigate } from "react-router";
import { KeyRound, LogOut, Settings } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { logout } from "@/lib/auth";
import { useChangePassword } from "@/lib/adminQueries";
import { getInitials } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";

export function ProfileFooter({ collapsed }: { collapsed?: boolean }) {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const changePasswordMutation = useChangePassword();

  const [changePwOpen, setChangePwOpen] = useState(false);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [pwError, setPwError] = useState<string | null>(null);
  const [pwSuccess, setPwSuccess] = useState(false);

  if (!user) return null;

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const openChangePw = () => {
    setOldPw("");
    setNewPw("");
    setPwError(null);
    setPwSuccess(false);
    setChangePwOpen(true);
  };

  const handleChangePw = async () => {
    setPwError(null);
    try {
      await changePasswordMutation.mutateAsync({ oldPassword: oldPw, newPassword: newPw });
      setPwSuccess(true);
    } catch (e: unknown) {
      setPwError(
        e instanceof Error ? e.message : "Failed to change password",
      );
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
            aria-label="Account menu"
          >
            <Avatar className="size-8 shrink-0">
              <AvatarFallback className="bg-brand-blue text-xs text-white">
                {getInitials(user.displayName)}
              </AvatarFallback>
            </Avatar>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-sidebar-foreground">
                  {user.displayName}
                </p>
                <p className="truncate text-xs text-sidebar-muted">
                  {user.email}
                </p>
              </div>
            )}
            {!collapsed && (
              <Settings className="size-4 shrink-0 text-sidebar-muted" />
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" className="w-56">
          <DropdownMenuLabel>{user.displayName}</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled>
            <Settings />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={openChangePw}>
            <KeyRound />
            Change password
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={handleLogout}>
            <LogOut />
            Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Change-password dialog */}
      <Dialog open={changePwOpen} onOpenChange={(o) => !o && setChangePwOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change password</DialogTitle>
            <DialogDescription>
              Enter your current password and choose a new one.
            </DialogDescription>
          </DialogHeader>
          {pwSuccess ? (
            <p className="text-sm text-green-600">
              Password changed successfully.
            </p>
          ) : (
            <div className="space-y-3">
              <div className="space-y-1">
                <Label>Current password</Label>
                <Input
                  type="password"
                  value={oldPw}
                  onChange={(e) => setOldPw(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <div className="space-y-1">
                <Label>New password</Label>
                <Input
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              {pwError && (
                <p className="text-sm text-destructive">{pwError}</p>
              )}
            </div>
          )}
          <DialogFooter>
            {pwSuccess ? (
              <Button onClick={() => setChangePwOpen(false)}>Close</Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => setChangePwOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={() => void handleChangePw()}
                  disabled={
                    !oldPw || !newPw || changePasswordMutation.isPending
                  }
                >
                  {changePasswordMutation.isPending ? "Saving…" : "Change password"}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
