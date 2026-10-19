import { defineConfig } from "vitest/config";

export default defineConfig({
  oxc: { jsx: "react-jsx" },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
  },
});
