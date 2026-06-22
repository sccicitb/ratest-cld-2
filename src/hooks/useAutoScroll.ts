import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Auto-scrolls a container to the bottom when `deps` change, unless the user
 * has manually scrolled up.
 */
export function useAutoScroll<T>(deps: T) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const checkAtBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 80;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    setIsAtBottom(atBottom);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
    setIsAtBottom(true);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", checkAtBottom, { passive: true });
    return () => el.removeEventListener("scroll", checkAtBottom);
  }, [checkAtBottom]);

  useEffect(() => {
    if (isAtBottom) scrollToBottom("auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps]);

  return { scrollRef, isAtBottom, scrollToBottom };
}
