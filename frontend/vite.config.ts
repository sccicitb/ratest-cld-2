import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: {
    tsconfigPaths: true,
  },
  server: {
    proxy: {
      // In dev the SPA and API share an origin so the auth refresh cookie
      // can be SameSite=Lax (§2, §4 of the backend spec). In production a
      // reverse proxy does the same.
      "/api": {
        target: "http://localhost:8012",
        changeOrigin: true,
      },
    },
  },
});
