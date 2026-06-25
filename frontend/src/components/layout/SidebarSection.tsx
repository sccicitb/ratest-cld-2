import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SidebarSectionProps {
  label?: string;
  collapsed?: boolean;
  className?: string;
  children: ReactNode;
}

export function SidebarSection({
  label,
  collapsed,
  className,
  children,
}: SidebarSectionProps) {
  return (
    <div className={cn("px-2", className)}>
      {label && !collapsed && (
        <p className="px-2 pb-1 pt-3 text-xs font-medium uppercase tracking-wider text-sidebar-muted">
          {label}
        </p>
      )}
      {children}
    </div>
  );
}
