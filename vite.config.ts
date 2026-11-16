import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend lives in web/ and builds to dist/, which Vercel serves from its CDN.
// In development, /api is proxied to the Flask app (python local_server.py --api-only).
export default defineConfig({
  root: "web",
  plugins: [react()],
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:5000", changeOrigin: false } },
  },
});
