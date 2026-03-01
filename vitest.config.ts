import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Use happy-dom for DOM testing
    environment: 'happy-dom',
    // Include TypeScript test files
    include: ['static/__tests__/**/*.test.ts'],
    // Enable globals (describe, it, expect)
    globals: true,
    // Coverage configuration
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['static/**/*.ts'],
      exclude: ['static/__tests__/**', 'static/**/*.d.ts', 'static/**/*.js'],
    },
  },
  // Resolve .ts extensions
  resolve: {
    alias: {
      // Allow imports without .js extension in tests
    },
  },
});
