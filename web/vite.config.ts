import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開発中は /api への通信を FastAPI に中継する。
// docker compose では API_TARGET=http://api:8000、手元で直接動かすときは localhost:8000
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": process.env.API_TARGET ?? "http://localhost:8000",
    },
    // Windows のフォルダをコンテナにマウントすると変更通知が届かないため、定期的に確認する
    watch: { usePolling: true, interval: 300 },
  },
});
