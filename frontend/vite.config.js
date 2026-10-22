import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local dev, /api is proxied to the FastAPI server so the frontend
// needs no VITE_API_URL. For production builds, set VITE_API_URL instead.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VIDGRAB_API || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
