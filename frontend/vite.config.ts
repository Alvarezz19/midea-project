import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: '/static/workbench/',
  plugins: [react()],
  build: {
    outDir: '../app/static/workbench',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          antd: ['antd', '@ant-design/icons'],
          query: ['@tanstack/react-query', 'zustand'],
          flow: ['@xyflow/react'],
          charts: ['echarts']
        }
      }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
    fileParallelism: false,
    testTimeout: 30000,
    exclude: ['node_modules/**', 'dist/**', 'tests/e2e/**']
  }
});
