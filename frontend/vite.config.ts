import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // API ходит через прокси, чтобы в браузере не было CORS и смены хоста
      "/api": {
        target: process.env.API_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
