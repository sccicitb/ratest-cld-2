import { useSidebarStore } from "@/stores/sidebarStore";

export default function TlingaRoute() {
  const isOpen = useSidebarStore((s) => s.isOpen);

  return (
    <div className="flex h-full flex-col">
      {/* Title bar */}
      <header className="flex h-14 shrink-0 items-center border-b border-border px-4">
        <h1 className="font-medium">Tlinga</h1>
      </header>
      {/* Full-height iframe — preloads when the route mounts, stays alive
          until the user navigates away (React unmounts the component). */}
      <div className="flex-1">
        <iframe
          src="https://tlingagram.devserver.web.id/map"
          title="Tlinga"
          className="h-full w-full border-0"
          // Chrome Ignores allow-scripts on cross-origin iframes — the
          // external page is fully functional; we just can't reach into it.
        />
      </div>
    </div>
  );
}
