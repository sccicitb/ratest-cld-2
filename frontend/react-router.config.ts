import type { Config } from "@react-router/dev/config";

export default {
  // SPA mode — no server-side rendering. All API calls are mocked client-side.
  ssr: false,
  // Place app source (routes, root, entry) under src/.
  appDirectory: "src",
} satisfies Config;
