import { defineConfig } from 'tsup'

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  outDir: 'dist',
  // Bundle @agent-os/shared inline so the output has no workspace:* dep at runtime.
  // All other node_modules (chalk, commander, ora, …) remain external and are
  // installed normally by npm/pnpm when someone does `npm install -g .`.
  noExternal: ['@agent-os/shared'],
  clean: true,
  dts: false,
})
